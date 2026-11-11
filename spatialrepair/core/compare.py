"""So sánh kết quả 2 câu SQL (dùng cho Execution Accuracy).
Quy tắc:
- Đưa mọi giá trị về dạng JSON giống cách tác giả lưu gold_result (json.dumps(default=str)).
- Chuỗi trông như số (Decimal bị đổi thành str) -> số thực.
- Số thực so với sai số tương đối REL_TOL (mặc định 1e-6).
- Nếu câu SQL chuẩn có ORDER BY -> so theo thứ tự; nếu không -> so như tập hợp có lặp (multiset).
- Không so tên cột, chỉ so số cột và giá trị.
"""
import json, math, re

REL_TOL = 1e-6
ABS_TOL = 1e-9


def _norm_value(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            return s
    return str(v)


def normalize(rows):
    rows = json.loads(json.dumps(rows, ensure_ascii=False, default=str))
    return [[_norm_value(v) for v in (r if isinstance(r, list) else [r])] for r in (rows or [])]


def _eq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def _row_eq(r1, r2):
    return len(r1) == len(r2) and all(_eq(a, b) for a, b in zip(r1, r2))


def _sort_key(row):
    out = []
    for v in row:
        if v is None:
            out.append((0, ""))
        elif isinstance(v, float):
            out.append((1, f"{v:.6g}"))
        else:
            out.append((2, str(v)))
    return out


def has_order_by(sql):
    s = re.sub(r"\([^()]*\)", "", sql or "")  # bỏ bớt ngoặc lồng 1 cấp
    return re.search(r"\border\s+by\b", s, re.I) is not None


def results_match(pred_rows, gold_rows, ordered=False):
    p, g = normalize(pred_rows), normalize(gold_rows)
    if len(p) != len(g):
        return False
    if not p:
        return True
    if len(p[0]) != len(g[0]):
        return False
    if ordered:
        return all(_row_eq(a, b) for a, b in zip(p, g))
    p, g = sorted(p, key=_sort_key), sorted(g, key=_sort_key)
    return all(_row_eq(a, b) for a, b in zip(p, g))


def execution_verdict(pred_rows, gold_rows, gold_sql, tie_info=None):
    """Chấm 1 câu: 'match' (khớp y hệt gold), 'match_tie' (khác gold nhưng là đáp án hợp lệ
    khi có bằng điểm), hoặc 'mismatch'. tie_info = analysis/tie_info.json[id] (nếu có).
    Cả 'match' và 'match_tie' đều tính là ĐÚNG khi đo Execution Accuracy."""
    if results_match(pred_rows, gold_rows, ordered=has_order_by(gold_sql)):
        return "match"
    if tie_info and "error" not in tie_info and tie_info.get("has_tie"):
        from spatialrepair.core.ties import valid_under_ties
        if valid_under_ties(normalize(pred_rows), tie_info, _row_eq):
            return "match_tie"
    return "mismatch"


def load_tie_info(root):
    import os
    p = os.path.join(root, "analysis", "tie_info.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
