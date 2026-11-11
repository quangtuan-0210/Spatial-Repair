"""Bộ SỬA của SpatialRepair (bước 3-5 của luồng sửa).

repair(sql, question, kg, executor=None) -> RepairResult
  3. Tạo ứng viên : mỗi lỗi (Finding) -> 1..n phép sửa cục bộ trên AST
  4. Kiểm chứng   : ứng viên phải parse được, KHÔNG còn lỗi mức error theo detect(); (tùy chọn) chạy được trên DuckDB
  5. Chọn/từ chối : nhiều lỗi -> tìm kiếm chùm (beam); chọn ứng viên ít phép sửa nhất, theo thứ tự ưu tiên;
                    không có ứng viên đạt -> TỪ CHỐI, trả lại SQL gốc.
Không gọi LLM.
"""
from dataclasses import dataclass, field
import sqlglot
from sqlglot import exp
from spatialrepair.detection.checks import _fname, _args
from spatialrepair.detection.intent import detect, extract_intent
from spatialrepair.repair.fixers import FIXERS, _p, _r, _fn


@dataclass
class RepairResult:
    sql_in: str
    sql_out: str
    changed: bool
    abstained: bool
    reason: str
    findings: list = field(default_factory=list)       # lỗi phát hiện ban đầu (error)
    edits: list = field(default_factory=list)          # các phép sửa đã áp dụng
    n_candidates: int = 0
    remaining: list = field(default_factory=list)      # lỗi còn lại nếu từ chối


# ---------------------------------------------------------------------------------------------
def _errors(sql, question, kg, use_intent):
    return [f for f in detect(sql, question, kg, use_intent) if f.level == "error"]


def repair(sql, question, kg, use_intent=True, beam=4, max_depth=4, executor=None):
    """executor: hàm(sql) -> dict(status=...) để kiểm chứng ứng viên bằng DuckDB (None = bỏ qua)."""
    try:
        base = _r(_p(sql))
    except Exception as e:  # noqa: BLE001
        return RepairResult(sql, sql, False, True, f"không parse được: {e}")
    it = extract_intent(question, kg) if use_intent else None
    errs0 = _errors(base, question, kg, use_intent)
    # Lỗi hình học hỏng chỉ lộ ra khi chạy: nếu DuckDB ném TopologyException thì các nghi vấn S7 (bảng có hình không
    # hợp lệ dùng trong ST_Intersection mà chưa lọc ST_IsValid) được nâng thành lỗi để sửa.
    if executor is not None:
        r0 = executor(sql)
        if r0.get("status") == "ok" and base != sql:
            r0 = executor(base)
        if r0.get("status") == "error" and "TopologyException" in (r0.get("error") or ""):
            sus = [f for f in detect(base, question, kg, use_intent) if f.code == "S7" and f.level == "suspect"]
            for f in sus:
                f.level = "error"
            errs0 = sus + errs0
    if not errs0:
        return RepairResult(sql, sql, False, False, "không phát hiện lỗi -> giữ nguyên")
    frontier = [(base, [], errs0)]
    done, tried, seen = [], 0, {base}
    for depth in range(max_depth):
        nxt = []
        for cur, edits, errs in frontier:
            cands, f = [], None
            for f in errs:                       # sửa lỗi đầu tiên có phép sửa áp dụng được
                fx = FIXERS.get(f.code)
                cands = fx(cur, f, it, kg) if fx else []
                if cands:
                    break
            for s, desc in cands:
                if s in seen:
                    continue
                seen.add(s); tried += 1
                try:
                    e2 = _errors(s, question, kg, use_intent)
                except Exception:  # noqa: BLE001
                    continue
                # chỉ nhận ứng viên làm giảm lỗi, hoặc ít nhất làm hết lỗi đang sửa
                if len(e2) >= len(errs) and any(x.code == f.code and x.node == f.node for x in e2):
                    continue
                item = (s, edits + [desc], e2)
                (done if not e2 else nxt).append(item)
        if done:
            break
        nxt.sort(key=lambda x: (len(x[2]), len(x[1])))
        frontier = nxt[:beam]
        if not frontier:
            break
    if not done:
        rem = [f"{f.code}:{f.msg[:80]}" for f in (frontier[0][2] if frontier else errs0)]
        return RepairResult(sql, sql, False, True, "không có ứng viên hết lỗi -> từ chối sửa",
                            [f.__dict__ for f in errs0], [], tried, rem)
    done.sort(key=lambda x: len(x[1]))
    if executor is not None:
        for s, edits, _ in done:
            r = executor(s)
            if r.get("status") == "ok":
                return RepairResult(sql, s, True, False, "đã sửa (đã chạy thử)", [f.__dict__ for f in errs0], edits, tried)
        return RepairResult(sql, sql, False, True, "ứng viên không chạy được -> từ chối sửa", [f.__dict__ for f in errs0], [], tried)
    s, edits, _ = done[0]
    return RepairResult(sql, s, True, False, "đã sửa", [f.__dict__ for f in errs0], edits, tried)


def canonical(sql):
    """Dạng chuẩn để so 2 câu SQL: ST_Within(a,b)->ST_Contains(b,a), ST_CoveredBy->ST_Covers; các vị từ đối xứng
    (Intersects/Overlaps/Touches/Equals/Disjoint) sắp xếp đối số; bỏ dấu chấm phẩy."""
    t = _p(sql)
    nodes = [x[0] if isinstance(x, tuple) else x for x in t.walk()]
    for n in nodes:
        f = _fname(n)
        if f in ("st_within", "st_coveredby") and len(_args(n)) == 2:
            a, b = _args(n)
            n.replace(_fn("ST_Contains" if f == "st_within" else "ST_Covers", [b, a]))
    nodes = [x[0] if isinstance(x, tuple) else x for x in t.walk()]
    for n in nodes:
        f = _fname(n)
        if f in ("st_intersects", "st_overlaps", "st_touches", "st_equals", "st_disjoint", "st_intersection") and len(_args(n)) == 2:
            a, b = sorted(_args(n), key=lambda x: x.sql())
            n.replace(_fn(n.sql_name() if not isinstance(n, exp.Anonymous) else n.name, [a, b]))
    return _r(t).upper()
