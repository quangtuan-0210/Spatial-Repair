"""Dò và xử lý "bằng điểm" (tie) ở các câu ORDER BY ... LIMIT.

Ý tưởng: với câu SQL chuẩn có ORDER BY ... LIMIT n [OFFSET k], tạo một câu "thăm dò" (probe):
  - giữ nguyên SELECT, FROM, WHERE, GROUP BY, ORDER BY
  - thêm các biểu thức sắp xếp vào SELECT (cột __k0, __k1, ...)
  - bỏ LIMIT/OFFSET
Chạy probe sẽ có toàn bộ danh sách đã sắp xếp KÈM giá trị dùng để sắp xếp.
Từ đó biết ở mỗi vị trí 1..n, những dòng nào là đáp án hợp lệ (các dòng có cùng giá trị sắp xếp).
"""
import math
import sqlglot
from sqlglot import exp

MAX_GROUP_ROWS = 5000  # giới hạn số dòng lưu cho mỗi nhóm bằng điểm


def _int_or_none(node):
    if node is None:
        return None
    e = node.expression if isinstance(node, (exp.Limit, exp.Offset)) else node
    try:
        return int(e.name)
    except Exception:  # noqa: BLE001
        return None


def build_probe(sql):
    """Trả về (probe_sql, n_out, limit, offset) hoặc None nếu câu không có ORDER BY ... LIMIT ở ngoài cùng."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    order, limit = tree.args.get("order"), tree.args.get("limit")
    if not isinstance(tree, exp.Select) or order is None or limit is None:
        return None
    n_lim = _int_or_none(limit)
    n_off = _int_or_none(tree.args.get("offset")) or 0
    if n_lim is None:
        return None
    probe = tree.copy()
    sel = probe.expressions
    n_out = len(sel)
    aliases = {e.alias_or_name.lower(): i for i, e in enumerate(sel) if e.alias}
    new_cols = []
    for j, o in enumerate(probe.args["order"].expressions):
        key = o.this
        if isinstance(key, exp.Literal) and key.is_int:            # ORDER BY 2
            key = sel[int(key.name) - 1].unalias().copy()
        elif isinstance(key, exp.Column) and not key.table and key.name.lower() in aliases:
            key = sel[aliases[key.name.lower()]].unalias().copy()   # ORDER BY alias
        else:
            key = key.copy()
        new_cols.append(exp.alias_(key, f"__k{j}"))
    probe.set("expressions", sel + new_cols)
    probe.set("limit", None)
    probe.set("offset", None)
    return probe.sql(dialect="duckdb"), n_out, n_lim, n_off


def _num(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def keys_equal(a, b, rel_tol=1e-9, abs_tol=1e-12):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        x, y = _num(x), _num(y)
        if isinstance(x, float) and isinstance(y, float):
            if not (math.isclose(x, y, rel_tol=rel_tol, abs_tol=abs_tol) or (math.isnan(x) and math.isnan(y))):
                return False
        elif x != y:
            return False
    return True


def analyze_probe(rows, n_out, limit, offset):
    """rows: kết quả probe (đã sắp xếp), mỗi dòng = n_out cột đầu ra + các cột khóa.
    Trả về dict tie_info cho câu hỏi."""
    total = len(rows)
    out = [list(r[:n_out]) for r in rows]
    key = [list(r[n_out:]) for r in rows]
    # gom các dòng liên tiếp có cùng khóa thành nhóm
    group_of, groups = [], []
    for i in range(total):
        if i and keys_equal(key[i], key[i - 1]):
            groups[-1]["idx"].append(i)
        else:
            groups.append({"key": key[i], "idx": [i]})
        group_of.append(len(groups) - 1)
    start, end = offset, min(offset + limit, total)
    need = sorted({group_of[i] for i in range(start, end)})
    boundary = (end < total and end > 0 and group_of[end - 1] == group_of[end]) or \
               (start > 0 and start < total and group_of[start - 1] == group_of[start])
    internal = any(group_of[i] == group_of[i + 1] for i in range(start, end - 1))
    info = {
        "limit": limit, "offset": offset, "n_out": n_out, "total_rows": total,
        "expected_len": max(0, end - start),
        "boundary_tie": bool(boundary), "internal_tie": bool(internal),
        "positions": [need.index(group_of[i]) for i in range(start, end)],  # vị trí -> chỉ số nhóm
        "groups": [], "truncated": False,
    }
    for g in need:
        idx = groups[g]["idx"]
        if len(idx) > MAX_GROUP_ROWS:
            info["truncated"] = True
        info["groups"].append({"key": groups[g]["key"], "size": len(idx),
                               "rows": [out[i] for i in idx[:MAX_GROUP_ROWS]]})
    info["has_tie"] = info["boundary_tie"] or info["internal_tie"]
    return info


def valid_under_ties(pred_rows_norm, info, row_eq):
    """pred_rows_norm: kết quả dự đoán đã normalize. Đúng nếu:
    - số dòng = expected_len
    - dòng thứ i thuộc nhóm bằng điểm của vị trí i
    - không dùng một dòng của nhóm nhiều lần hơn số lần nó xuất hiện."""
    if len(pred_rows_norm) != info["expected_len"]:
        return False
    used = [[False] * len(g["rows"]) for g in info["groups"]]
    for i, pr in enumerate(pred_rows_norm):
        g = info["positions"][i]
        rows = info["groups"][g]["rows"]
        hit = next((k for k, r in enumerate(rows) if not used[g][k] and row_eq(pr, r)), None)
        if hit is None:
            return False
        used[g][hit] = True
    return True
