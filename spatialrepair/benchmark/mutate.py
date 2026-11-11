"""Toán tử đột biến: cài lỗi có kiểm soát vào SQL chuẩn, mỗi mã lỗi (S1..S9, K1..K5) một hàm.

generate(sql, kg) -> list[Mutant]
Mutant: code, op (tên phép cài lỗi), sql (câu đã cài lỗi), before/after (đoạn bị đổi), note.
Đáp án sửa đúng của mọi đột biến = SQL chuẩn ban đầu.
Các mã lỗi: xem mục "Các loại lỗi" trong README.md
"""
from dataclasses import dataclass, asdict
import sqlglot
from sqlglot import exp
from spatialrepair.detection.checks import Ctx, geom_kind, _fname, _args, PREDICATES

POLY_PRED_ALT = {"st_intersects": ["ST_Overlaps", "ST_Touches", "ST_Contains"],
                 "st_overlaps": ["ST_Intersects", "ST_Touches"],
                 "st_touches": ["ST_Intersects", "ST_Overlaps"],
                 "st_contains": ["ST_Intersects", "ST_Within"],
                 "st_within": ["ST_Intersects", "ST_Contains"]}
POINT_PRED_ALT = {"st_intersects": ["ST_Touches"], "st_contains": ["ST_Touches"], "st_within": ["ST_Touches"]}


@dataclass
class Mutant:
    code: str
    op: str
    sql: str
    before: str
    after: str
    note: str = ""

    def as_dict(self):
        return asdict(self)


def _parse(sql):
    return sqlglot.parse_one(sql, read="duckdb")


def _out(tree):
    return tree.sql(dialect="duckdb")


def _targets(tree, pred):
    return [n for n in tree.walk() if pred(n[0] if isinstance(n, tuple) else n)]


def _each(sql, pred, edit):
    """Với mỗi nút thỏa pred trong câu SQL: copy cây, sửa nút thứ i bằng edit(node, tree) -> list[(after_node, op, note)]."""
    base = _parse(sql)
    n_targets = len(_targets(base, pred))
    res = []
    for i in range(n_targets):
        probe = _parse(sql)
        node = _targets(probe, pred)[i]
        node = node[0] if isinstance(node, tuple) else node
        for variant in edit(node, probe) or []:
            t = _parse(sql)
            nd = _targets(t, pred)[i]
            nd = nd[0] if isinstance(nd, tuple) else nd
            before = nd.sql(dialect="duckdb")
            new, op, note = variant(nd, t)
            if new is None:
                continue
            if new is not nd:
                nd.replace(new)
            res.append((t, before, new.sql(dialect="duckdb") if isinstance(new, exp.Expression) else str(new), op, note))
    return res


def _remove_conjunct(node):
    """Bỏ 1 điều kiện khỏi chuỗi AND. Trả về True nếu bỏ được."""
    p = node.parent
    while isinstance(p, exp.Paren):
        node, p = p, p.parent
    if isinstance(p, exp.And):
        other = p.expression if p.this is node else p.this
        p.replace(other.copy())
        return True
    if isinstance(p, exp.Where):
        p.pop()
        return True
    return False


# ---------------------------------------------------------------------------------------------
def gen_S1(sql, kg):
    """Chọn sai hàm quan hệ không gian."""
    tree = _parse(sql); ctx = Ctx(tree, kg)
    pred = lambda n: _fname(n) in POLY_PRED_ALT and len(_args(n)) == 2   # noqa: E731

    def edit(node, t):
        c = Ctx(t, kg)
        kinds = [geom_kind(a, c) for a in _args(node)[:2]]
        table = POINT_PRED_ALT if "POINT" in kinds else POLY_PRED_ALT
        alts = table.get(_fname(node), [])
        out = []
        for alt in alts:
            def v(nd, tt, alt=alt):
                return exp.Anonymous(this=alt, expressions=[a.copy() for a in _args(nd)]), f"{_fname(nd)}->{alt.lower()}", ""
            out.append(v)
        return out
    return [Mutant("S1", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S2(sql, kg):
    """Đảo chiều đối số của ST_Contains / ST_Within."""
    pred = lambda n: _fname(n) in ("st_contains", "st_within") and len(_args(n)) == 2   # noqa: E731

    def edit(node, t):
        def v(nd, tt):
            a, b = _args(nd)
            return exp.Anonymous(this=nd.name, expressions=[b.copy(), a.copy()]), "swap_args", ""
        return [v]
    return [Mutant("S2", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S3(sql, kg):
    """Đảo LAT/LON trong ST_Point."""
    pred = lambda n: _fname(n) == "st_point" and len(_args(n)) == 2   # noqa: E731

    def edit(node, t):
        def v(nd, tt):
            a, b = _args(nd)
            return exp.Anonymous(this="ST_Point", expressions=[b.copy(), a.copy()]), "swap_lon_lat", ""
        return [v]
    return [Mutant("S3", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S4(sql, kg):
    """Dùng cột geometry (rỗng/không tồn tại) thay vì ST_Point(LON, LAT)."""
    pred = lambda n: _fname(n) == "st_point" and len(_args(n)) == 2 and isinstance(_args(n)[0], exp.Column)   # noqa: E731

    def edit(node, t):
        def v(nd, tt):
            col = _args(nd)[0]
            return exp.column("geometry", table=col.table or None), "point_to_geometry_col", ""
        return [v]
    return [Mutant("S4", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S5(sql, kg):
    """Nhầm diện tích phần giao với diện tích cả hình."""
    pred = lambda n: _fname(n) == "st_area" and _args(n) and _fname(_args(n)[0]) == "st_intersection"   # noqa: E731

    def edit(node, t):
        out = []
        for k in (0, 1):
            def v(nd, tt, k=k):
                inner = _args(_args(nd)[0])
                return exp.Anonymous(this="ST_Area", expressions=[inner[k].copy()]), f"drop_intersection_keep_arg{k}", ""
            out.append(v)
        return out
    return [Mutant("S5", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S6(sql, kg):
    """Tự ý đổi CRS / đơn vị."""
    pred = lambda n: _fname(n) == "st_area"   # noqa: E731

    def edit(node, t):
        def spheroid(nd, tt):
            return exp.Anonymous(this="ST_Area_Spheroid", expressions=[a.copy() for a in _args(nd)]), "area_spheroid", "đổi sang m²"

        def transform(nd, tt):
            g = _args(nd)[0].copy()
            tr = exp.Anonymous(this="ST_Transform", expressions=[g, exp.Literal.string("EPSG:4326"),
                                                                    exp.Literal.string("EPSG:3857"), exp.true()])
            return exp.Anonymous(this="ST_Area", expressions=[tr]), "area_transform_3857", "đổi sang Web Mercator (m²)"
        return [spheroid, transform]
    return [Mutant("S6", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S7(sql, kg):
    """Bỏ toàn bộ bộ lọc ST_IsValid."""
    tree = _parse(sql)
    nodes = [n for n in tree.find_all(exp.Anonymous) if n.name.lower() == "st_isvalid"]
    if not nodes:
        return []
    before = "; ".join(n.sql(dialect="duckdb") for n in nodes)
    removed = 0
    for n in nodes:
        target = n.parent if isinstance(n.parent, exp.Not) else n
        removed += _remove_conjunct(target)
    if not removed:
        return []
    return [Mutant("S7", "drop_isvalid", _out(tree), before, "(bỏ)", f"bỏ {removed} điều kiện ST_IsValid")]


def gen_S8(sql, kg):
    """Thay nối không gian điểm–ZCTA bằng so khớp mã ZIP dạng chữ."""
    tree = _parse(sql)
    ctx = Ctx(tree, kg)
    pred = lambda n: _fname(n) in PREDICATES and len(_args(n)) >= 2   # noqa: E731

    def edit(node, t):
        c = Ctx(t, kg)
        args = _args(node)[:2]
        zc = next((a for a in args if isinstance(a, exp.Column) and c.table_of(a)[0] == "zcta"), None)
        pt = next((a for a in args if _fname(a) == "st_point"), None)
        if zc is None or pt is None:
            return []
        alias = _args(pt)[0].table if isinstance(_args(pt)[0], exp.Column) else None
        def v(nd, tt):
            return exp.EQ(this=exp.column("GEOID", table=zc.table or None), expression=exp.column("ZIP", table=alias)), "zip_text_join", ""
        return [v]
    return [Mutant("S8", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_S9(sql, kg):
    """COUNT(DISTINCT x) -> COUNT(*) trong câu có nối không gian."""
    tree = _parse(sql)
    if not any(_fname(n[0] if isinstance(n, tuple) else n) in PREDICATES for n in tree.walk()):
        return []
    pred = lambda n: isinstance(n, exp.Count) and isinstance(n.this, exp.Distinct)   # noqa: E731

    def edit(node, t):
        return [lambda nd, tt: (exp.Count(this=exp.Star()), "count_distinct_to_star", "")]
    return [Mutant("S9", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def _county_codes(kg, statefp):
    return sorted(n["countyfp"] for n in kg.nodes.values() if n.get("level") == "county" and n.get("statefp") == statefp)


def gen_K1(sql, kg):
    """Sai mã vùng: (a) mã không tồn tại, (b) mã có thật nhưng SAI so với câu hỏi."""
    states = sorted(n["code"] for n in kg.nodes.values() if n.get("level") == "state")
    out = []

    def is_code_cmp(n):
        if not isinstance(n, (exp.EQ, exp.Like)) or not isinstance(n.expression, exp.Literal) or not n.expression.is_string:
            return False
        col = n.this
        name = col.name.upper() if isinstance(col, exp.Column) else ""
        if isinstance(col, exp.Left) or _fname(col) == "left":
            name = "GEOIDPREFIX"
        return name in ("STATEFP", "COUNTYFP", "GEOID", "COUNTYFIPS", "GEOIDPREFIX")

    tree = _parse(sql)
    lits = {}
    for c in tree.find_all(exp.EQ):
        if isinstance(c.this, exp.Column) and c.this.name.upper() == "STATEFP" and isinstance(c.expression, exp.Literal):
            lits["STATEFP"] = c.expression.name

    def edit(node, t):
        val = node.expression.name
        core = val.rstrip("%")
        col = node.this
        name = col.name.upper() if isinstance(col, exp.Column) else "GEOIDPREFIX"
        variants = []
        if name == "STATEFP" and core in states:
            other = next(s for s in states if s != core)
            variants += [("state_wrong_valid", other), ("state_invalid", "47" if core != "47" else "49")]
        elif name == "COUNTYFP" and lits.get("STATEFP"):
            cs = _county_codes(kg, lits["STATEFP"])
            if core in cs:
                other = cs[(cs.index(core) + 1) % len(cs)]
                variants += [("county_wrong_valid", other), ("county_invalid", "998")]
        elif len(core) >= 5 and core[:2] in states:
            cs = _county_codes(kg, core[:2])
            if core[2:5] in cs:
                other = cs[(cs.index(core[2:5]) + 1) % len(cs)]
                variants += [("county_wrong_valid", core[:2] + other + core[5:]), ("county_invalid", core[:2] + "998" + core[5:])]
        elif len(core) == 2 and core in states:
            other = next(s for s in states if s != core)
            variants += [("state_wrong_valid", other), ("state_invalid", "47")]
        fns = []
        for op, newcore in variants:
            def v(nd, tt, op=op, newcore=newcore):
                newval = newcore + ("%" if nd.expression.name.endswith("%") else "")
                return nd.__class__(this=nd.this.copy(), expression=exp.Literal.string(newval)), op, \
                    ("cần đối chiếu câu hỏi mới phát hiện được" if "valid" in op and "invalid" not in op else "")
            fns.append(v)
        return fns
    return [Mutant("K1", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, is_code_cmp, edit)]


def gen_K2(sql, kg):
    """Cắt GEOID sai: bỏ LEFT(.,5) hoặc cắt sai độ dài."""
    pred = lambda n: (isinstance(n, exp.Left) or _fname(n) == "left") and isinstance(n.parent, exp.EQ)   # noqa: E731

    def edit(node, t):
        def drop(nd, tt):
            return nd.this.copy(), "drop_left", ""

        def wrong_len(nd, tt):
            ln = nd.expression
            new = 2 if (isinstance(ln, exp.Literal) and ln.name == "5") else 5
            return exp.Left(this=nd.this.copy(), expression=exp.Literal.number(new)), f"left_len_{new}", ""
        return [drop, wrong_len]
    return [Mutant("K2", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_K3(sql, kg):
    """Ép GEOID sang số trong phép nối."""
    def pred(n):
        return isinstance(n, exp.EQ) and isinstance(n.this, exp.Column) and isinstance(n.expression, exp.Column) \
            and n.this.name.upper() == "GEOID" and n.expression.name.upper() == "GEOID"

    def edit(node, t):
        def v(nd, tt):
            cast = lambda c: exp.Cast(this=c.copy(), to=exp.DataType.build("BIGINT"))   # noqa: E731
            return exp.EQ(this=cast(nd.this), expression=cast(nd.expression)), "cast_geoid_bigint", ""
        return [v]
    return [Mutant("K3", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


def gen_K4(sql, kg):
    """Bỏ bộ lọc miền giá trị: FLD_ZONE NOT IN (...) hoặc BETWEEN 0 AND 100 của cột %."""
    out = []
    tree = _parse(sql)
    for i, n in enumerate(list(tree.find_all(exp.Not, exp.Between))):
        t = _parse(sql)
        nd = list(t.find_all(exp.Not, exp.Between))[i]
        ok = False
        if isinstance(nd, exp.Not) and isinstance(nd.this, exp.In) and "fld_zone" in nd.sql().lower():
            ok, op = True, "drop_fld_zone_filter"
        if isinstance(nd, exp.Between) and nd.args.get("low") is not None and nd.args["low"].sql() == "0" \
                and nd.args.get("high") is not None and nd.args["high"].sql() == "100":
            ok, op = True, "drop_pct_range_filter"
        if ok:
            before = nd.sql(dialect="duckdb")
            if _remove_conjunct(nd):
                out.append(Mutant("K4", op, _out(t), before, "(bỏ)"))
    return out


def gen_K5(sql, kg):
    """Sai dạng giá trị chữ: CITY không viết hoa, giá trị phân loại không có trong miền."""
    tree = _parse(sql); ctx = Ctx(tree, kg)

    def pred(n):
        if not (isinstance(n, exp.EQ) and isinstance(n.this, exp.Column) and isinstance(n.expression, exp.Literal)
                and n.expression.is_string):
            return False
        return n.this.name.upper() in ("CITY", "FLD_ZONE", "TYPE") or n.this.name.upper().endswith("R")

    def edit(node, t):
        c = Ctx(t, kg)
        tbl, col = c.table_of(node.this)
        if not col:
            return []
        dom = kg.domain(tbl, col["name"])
        if not dom or col.get("role") in ("geo_code_part", "geo_key"):
            return []
        val = node.expression.name

        def v(nd, tt):
            new = val.title() if val.isupper() else val.lower()
            if new == val:
                return None, "", ""
            return exp.EQ(this=nd.this.copy(), expression=exp.Literal.string(new)), "wrong_case", ""
        return [v]
    return [Mutant("K5", op, _out(t), b, a, n) for t, b, a, op, n in _each(sql, pred, edit)]


GENERATORS = {"S1": gen_S1, "S2": gen_S2, "S3": gen_S3, "S4": gen_S4, "S5": gen_S5, "S6": gen_S6, "S7": gen_S7,
              "S8": gen_S8, "S9": gen_S9, "K1": gen_K1, "K2": gen_K2, "K3": gen_K3, "K4": gen_K4, "K5": gen_K5}


def generate(sql, kg, codes=None):
    out = []
    for code, g in GENERATORS.items():
        if codes and code not in codes:
            continue
        try:
            out += g(sql, kg)
        except Exception as e:  # noqa: BLE001
            out.append(Mutant(code, "GENERATOR_ERROR", "", "", "", str(e)[:200]))
    # bỏ trùng và bỏ đột biến không đổi gì
    seen, res = set(), []
    base = _out(_parse(sql))
    for m in out:
        if m.op == "GENERATOR_ERROR" or (m.sql and m.sql != base and m.sql not in seen):
            seen.add(m.sql)
            res.append(m)
    return res
