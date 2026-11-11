"""Bộ kiểm tra Spatial SQL dựa trên KG (phiên bản 0 của bộ PHÁT HIỆN lỗi).

check_sql(sql, kg, question=None) -> list[Finding]
Mỗi Finding có: code (S1..S9, K1..K5, G), level ('error' = chắc chắn sai theo KG, 'warn' = đáng ngờ), msg, node (đoạn SQL).
Các mã lỗi: xem mục "Các loại lỗi" trong README.md.
"""
import re
from dataclasses import dataclass, field
import sqlglot
from sqlglot import exp

UNIT_WORDS = re.compile(r"\b(km|kilomet|mile|meter|metre|feet|foot|square|sq\.?\s|acre|hectare)", re.I)
PREDICATES = {"st_intersects", "st_overlaps", "st_touches", "st_contains", "st_within", "st_covers",
              "st_coveredby", "st_disjoint", "st_equals", "st_crosses", "st_dwithin"}


@dataclass
class Finding:
    code: str
    level: str
    msg: str
    node: str = ""
    extra: dict = field(default_factory=dict)


def _fname(node):
    """Tên hàm (chữ thường) của 1 nút sqlglot: ST_Contains, LEFT, ..."""
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    if isinstance(node, exp.Func):
        n = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
        return n.lower()
    return None


def _args(node):
    if isinstance(node, exp.Anonymous):
        return list(node.expressions)
    return [a for a in node.args.values() if isinstance(a, exp.Expression)] + \
           [x for a in node.args.values() if isinstance(a, list) for x in a]


class Ctx:
    def __init__(self, tree, kg):
        self.kg = kg
        self.alias = {}       # alias -> bảng gốc
        self.derived = set()  # alias của CTE / subquery
        known = {t.lower() for t in kg.tables()}
        for t in tree.find_all(exp.Table):
            name = t.name.lower()
            al = (t.alias or t.name).lower()
            if name in known:
                self.alias[al] = name
            else:
                self.derived.add(al)
        for sq in tree.find_all(exp.Subquery):
            if sq.alias:
                self.derived.add(sq.alias.lower())
        for c in tree.find_all(exp.CTE):
            self.derived.add(c.alias.lower())
        self.base_tables = sorted(set(self.alias.values()))
        self.select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}

    def table_of(self, col: exp.Column):
        """Trả về (bảng gốc, nút cột KG) hoặc (None, None) nếu không xác định được."""
        q = col.table.lower() if col.table else None
        if q:
            t = self.alias.get(q)
            return (t, self.kg.column(t, col.name)) if t else (None, None)
        hits = [t for t in self.base_tables if self.kg.column(t, col.name)]
        if len(hits) == 1:
            return hits[0], self.kg.column(hits[0], col.name)
        return (hits[0] if hits else None), (self.kg.column(hits[0], col.name) if hits else None)


def geom_kind(node, ctx):
    """Suy ra loại hình học của 1 biểu thức: POINT / POLYGON / ANY / INVALID / None (không biết)."""
    if isinstance(node, exp.Column):
        t, c = ctx.table_of(node)
        if c is None:
            return None
        if c.get("role") == "geometry":
            return ctx.kg.geom_kind(t) or "ANY"
        if c.get("role") == "geometry_aux":
            types = set((c.get("geometry_types") or {}).keys())
            return "POINT" if types == {"POINT"} else "POLYGON" if types <= {"POLYGON", "MULTIPOLYGON"} and types else "ANY"
        if c.get("role") == "geometry_unusable":
            return "INVALID"
        return None
    f = _fname(node)
    if f in ("st_point", "st_centroid", "st_point2d"):
        return "POINT"
    if f in ("st_makevalid", "st_transform", "st_flipcoordinates"):
        a = _args(node)
        return geom_kind(a[0], ctx) if a else None
    if f in ("st_intersection", "st_union", "st_union_agg", "st_envelope", "st_buffer", "st_extent"):
        return "ANY"
    return None


def _is_unit_question(question):
    return bool(question and UNIT_WORDS.search(question))


def _key_side(node, ctx):
    """Một vế của phép nối khóa: trả về (bảng, cột, hệ mã sau biến đổi) hoặc None."""
    trans = None
    if isinstance(node, (exp.Left, exp.Substring)) or _fname(node) in ("left", "substring", "substr"):
        args = _args(node)
        col = next((a for a in args if isinstance(a, exp.Column)), None)
        n = next((a for a in args if isinstance(a, exp.Literal) and a.is_int), None)
        if col is None or n is None:
            return None
        trans = int(n.name) if _fname(node) == "left" or isinstance(node, exp.Left) else int(args[-1].name) if isinstance(args[-1], exp.Literal) else None
        node = col
    if isinstance(node, exp.Cast):
        node = node.this
    if not isinstance(node, exp.Column):
        return None
    t, c = ctx.table_of(node)
    if not c:
        return None
    cs = ctx.kg.code_system(t, c["name"])
    sysname = cs["name"] if cs else None
    if trans is not None and sysname == "tract_geoid":
        sysname = {5: "county_fips", 2: "state_fips"}.get(trans, f"tract_prefix{trans}")
    elif trans is not None and sysname == "county_fips" and trans == 2:
        sysname = "state_fips"
    if sysname is None and c.get("role") == "geo_code_part":
        sysname = c["name"].upper()
    return t, c, sysname


def check_sql(sql, kg, question=None):
    out = []
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception as e:  # noqa: BLE001
        return [Finding("G", "error", f"không parse được SQL: {e}")]
    ctx = Ctx(tree, kg)
    spatial = kg.is_spatial

    # ---- G / S4: cột không tồn tại ----
    for col in tree.find_all(exp.Column):
        if col.name.lower() in ctx.select_aliases and not col.table:
            continue
        q = col.table.lower() if col.table else None
        if q and q in ctx.derived:
            continue
        t, c = ctx.table_of(col)
        if q and q not in ctx.alias:
            out.append(Finding("G", "error", f"bí danh '{col.table}' không trỏ tới bảng nào", col.sql(dialect="duckdb")))
        elif t and c is None and (q or len(ctx.base_tables) == 1):
            code = "S4" if col.name.lower() == "geometry" else "G"
            out.append(Finding(code, "error", f"bảng {t} không có cột {col.name}", col.sql(dialect="duckdb"),
                               {"rule": "unknown_column", "table": t}))
        elif not q and t is None and ctx.base_tables and not ctx.derived and col.name.lower() not in ctx.select_aliases:
            out.append(Finding("G", "error", f"không bảng nào trong câu có cột {col.name}", col.sql(dialect="duckdb"),
                               {"rule": "unknown_column", "table": ctx.base_tables[0] if len(ctx.base_tables) == 1 else None}))

    if not spatial:
        return out

    # ---- duyệt các lời gọi hàm ----
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        f = _fname(node)
        if not f:
            continue
        args = _args(node)
        fn = kg.function(f)
        # S3: ST_Point(LON, LAT)
        if f == "st_point" and len(args) >= 2:
            roles = []
            for a in args[:2]:
                if isinstance(a, exp.Column):
                    _, c = ctx.table_of(a)
                    roles.append(c.get("role") if c else None)
                else:
                    roles.append(None)
            if roles == ["coord_y", "coord_x"]:
                out.append(Finding("S3", "error", "ST_Point(LAT, LON): đảo trục; đúng là ST_Point(LON, LAT)", node.sql()))
            elif roles[0] == "coord_y" or roles[1] == "coord_x":
                out.append(Finding("S3", "error", "ST_Point dùng sai cột tọa độ", node.sql()))
        # S4: dùng hình học không hợp lệ; S2/S1: kiểu đối số của vị từ
        if fn and fn.get("kind") in ("predicate", "measure", "constructor", "validity", "accessor") and f != "st_point":
            kinds = [geom_kind(a, ctx) for a in args]
            for a, k in zip(args, kinds):
                if k == "INVALID":
                    out.append(Finding("S4", "error", f"{a.sql()} rỗng/không dùng được; dựng điểm bằng ST_Point(LON, LAT)", node.sql()))
            if fn.get("kind") == "predicate" and len(kinds) >= 2:
                spec = fn["args"]
                bad = [i for i in range(2) if spec[i] == "POLYGON" and kinds[i] == "POINT"]
                if bad:
                    inv = fn.get("inverse")
                    if f in ("st_contains", "st_within", "st_covers", "st_coveredby") and kinds[1 - bad[0]] == "POLYGON":
                        out.append(Finding("S2", "error", f"{fn['name']} đảo chiều đối số (điểm không thể chứa đa giác); "
                                           f"đổi chỗ đối số hoặc dùng {inv}", node.sql()))
                    else:
                        out.append(Finding("S1", "error", f"{fn['name']} không áp dụng cho điểm", node.sql()))
        # S6: đổi CRS / đơn vị
        if fn and fn.get("changes_unit") and not _is_unit_question(question):
            out.append(Finding("S6", "error", f"{fn['name']} đổi CRS/đơn vị, trái luật crs_preserve (câu hỏi không yêu cầu đơn vị)", node.sql()))
    for cast in tree.find_all(exp.Cast):
        to = cast.to.sql().lower()
        if "geography" in to and not _is_unit_question(question):
            out.append(Finding("S6", "error", "ép sang geography làm đổi đơn vị", cast.sql()))
        inner = cast.this
        if isinstance(inner, exp.Column) and re.search(r"int|double|float|decimal|numeric", to):
            _, c = ctx.table_of(inner)
            if c and c.get("role") == "geo_key" and any(s.startswith("0") for s in
                    {n["code"] for n in kg.nodes.values() if n.get("level") == "state"}) or (c and c.get("role") == "geo_key" and isinstance(cast.parent, (exp.EQ, exp.Like))):
                out.append(Finding("K3", "error", f"CAST {c['table']}.{c['name']} sang số làm mất số 0 đầu", cast.sql()))
    for m in list(tree.find_all(exp.Mul)) + list(tree.find_all(exp.Div)):
        has_measure = any(_fname(x) in ("st_area", "st_distance", "st_length", "st_perimeter") for x in m.find_all(exp.Func))
        lit = [x for x in (m.this, m.expression) if isinstance(x, exp.Literal) and not x.is_string]
        if has_measure and lit and not _is_unit_question(question):
            out.append(Finding("S6", "warn", "nhân/chia kết quả đo với hằng số — có thể là đổi đơn vị tự ý", m.sql()))

    states = {n["code"] for n in kg.nodes.values() if n["type"] == "GeoCode" and n.get("level") == "state"}
    # ---- nối (S8, K2, K3) ----
    conds = []
    for j in tree.find_all(exp.Join):
        if j.args.get("on"):
            conds.append(j.args["on"])
    for w in tree.find_all(exp.Where):
        conds.append(w.this)
    for cond in conds:
        for eq in cond.find_all(exp.EQ):
            a, b = _key_side(eq.this, ctx), _key_side(eq.expression, ctx)
            if not a or not b or a[0] == b[0]:
                continue
            (ta, ca, sa), (tb, cb, sb) = a, b
            if "numeric_geo_code" in (ca.get("role"), cb.get("role")):
                # chỉ thực sự sai khi có mã bắt đầu bằng 0 (bị mất khi đổi sang số). TX/FL/LA = 48/12/22 -> không mất.
                zero_risk = any(s.startswith("0") for s in states)
                out.append(Finding("K3", "error" if zero_risk else "warn",
                                   "nối bằng mã địa lý kiểu số (vd svi.FIPS); nên dùng GEOID dạng chữ"
                                   + ("" if zero_risk else " (trên dữ liệu này chưa làm sai kết quả vì không có mã bắt đầu bằng 0)"), eq.sql()))
            elif sa and sb and sa != sb:
                code = "S8" if {"zip", "zcta5"} == {sa, sb} else "K2"
                msg = ("so khớp ZIP với ZCTA dạng chữ; ZCTA phải nối bằng đa giác" if code == "S8"
                       else f"nối 2 hệ mã khác nhau: {ca['table']}.{ca['name']} ({sa}) = {cb['table']}.{cb['name']} ({sb}); "
                            f"tract→county cần LEFT(GEOID,5)")
                out.append(Finding(code, "error", msg, eq.sql()))
    # nối không gian giữa 2 bảng không có SPATIAL_JOIN
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        f = _fname(node)
        if f in PREDICATES:
            tabs = []
            for a in _args(node)[:2]:
                ts = {ctx.table_of(c)[0] for c in a.find_all(exp.Column)} - {None}
                tabs.append(ts)
            if len(tabs) == 2 and len(tabs[0]) == 1 and len(tabs[1]) == 1:
                ta, tb = next(iter(tabs[0])), next(iter(tabs[1]))
                if ta != tb and not kg.spatial_join(ta, tb):
                    out.append(Finding("S8", "error", f"không có quan hệ nối không gian hợp lệ giữa {ta} và {tb}", node.sql()))

    # ---- mã địa lý (K1) và miền giá trị (K5) ----
    states = {n["code"] for n in kg.nodes.values() if n["type"] == "GeoCode" and n.get("level") == "state"}
    has_counties = any(n.get("level") == "county" for n in kg.nodes.values())
    for cmp in list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.Like)):
        col, lit = cmp.this, cmp.expression
        if not isinstance(lit, exp.Literal) or not lit.is_string:
            continue
        val = lit.name
        side = _key_side(col, ctx)
        if not side:
            continue
        t, c, sysname = side
        v = val.rstrip("%") if isinstance(cmp, exp.Like) else val
        if sysname in ("STATEFP", "state_fips") and len(v) == 2 and v not in states:
            out.append(Finding("K1", "error", f"mã bang '{v}' không có trong dữ liệu (TX=48, FL=12, LA=22)", cmp.sql()))
        if sysname in ("tract_geoid", "county_fips") and len(v) >= 5 and has_counties:
            if not kg.nodes.get(f"code:county:{v[:5]}"):
                out.append(Finding("K1", "error", f"mã quận '{v[:5]}' không tồn tại", cmp.sql()))
            elif v[:2] not in states:
                out.append(Finding("K1", "error", f"mã bang '{v[:2]}' không tồn tại", cmp.sql()))
        if sysname in ("tract_geoid", "county_fips") and 2 <= len(v) < 5 and v[:2] not in states:
            out.append(Finding("K1", "error", f"mã bang '{v[:2]}' không tồn tại", cmp.sql()))
        dom = kg.domain(t, c["name"])
        if dom and isinstance(cmp, exp.EQ) and c.get("role") not in ("geo_code_part", "geo_key"):  # mã vùng do K1 lo
            if dom.get("case") == "UPPER" and val != val.upper():
                out.append(Finding("K5", "error", f"{c['name']} lưu chữ hoa; '{val}' → '{val.upper()}'", cmp.sql()))
            if dom.get("values") and val not in dom["values"]:
                out.append(Finding("K5", "error", f"giá trị '{val}' không có trong {t}.{c['name']}", cmp.sql()))
    # K2: so mã với chuỗi sai độ dài (không bao giờ đúng)
    for cmp in tree.find_all(exp.EQ):
        lit, lhs = cmp.expression, cmp.this
        if not (isinstance(lit, exp.Literal) and lit.is_string and lit.name.isdigit()):
            continue
        if isinstance(lhs, exp.Left) or _fname(lhs) == "left":
            n = _args(lhs)[-1] if not isinstance(lhs, exp.Left) else lhs.expression
            if isinstance(n, exp.Literal) and n.is_int and int(n.name) != len(lit.name):
                out.append(Finding("K2", "error", f"so {lhs.sql()} ({n.name} ký tự) với '{lit.name}' ({len(lit.name)} ký tự) — không bao giờ khớp", cmp.sql()))
        elif isinstance(lhs, exp.Column):
            t, c = ctx.table_of(lhs)
            cs = kg.code_system(t, c["name"]) if c else None
            if cs and cs.get("length") and cs["length"] != len(lit.name):
                out.append(Finding("K2", "error", f"{c['table']}.{c['name']} dài {cs['length']} ký tự nhưng so với '{lit.name}'"
                                   f" ({len(lit.name)} ký tự) — cần LEFT(...,{len(lit.name)}) hoặc LIKE", cmp.sql()))

    # K4: gộp trên cột có giá trị rác (vd -999) mà không loại
    AGG = (exp.Avg, exp.Sum, exp.Min, exp.Max)
    filters = []
    for node in list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having)) + [j.args["on"] for j in tree.find_all(exp.Join) if j.args.get("on")]:
        filters.append(node)
    def _excluded(col_name):
        for f in filters:
            for c in f.find_all(exp.GT, exp.GTE, exp.Between, exp.NEQ, exp.LT, exp.LTE, exp.EQ):
                cols = [x for x in c.find_all(exp.Column) if x.name.lower() == col_name.lower()]
                if not cols:
                    continue
                if isinstance(c, exp.Between):
                    lo = c.args.get("low")
                    if isinstance(lo, exp.Literal) and float(lo.name) >= 0:
                        return True
                elif isinstance(c, (exp.GT, exp.GTE, exp.EQ)) and isinstance(c.expression, (exp.Literal, exp.Neg)):
                    v = c.expression.sql()
                    try:
                        if float(v) >= -1e-9 or (isinstance(c, exp.GT) and float(v) > -999):
                            return True
                    except ValueError:
                        pass
                elif isinstance(c, (exp.LT, exp.LTE)) and isinstance(c.this, (exp.Literal,)):
                    return True
                elif isinstance(c, exp.NEQ):
                    return True
        return False
    seen = set()
    for agg in tree.find_all(*AGG):
        for col in agg.find_all(exp.Column):
            t, c = ctx.table_of(col)
            dom = kg.domain(t, c["name"]) if c else None
            if dom and dom.get("sentinel_values") and (t, c["name"]) not in seen and not _excluded(c["name"]):
                seen.add((t, c["name"]))
                # 'error' (từ 2026-11-06): -999 = thiếu dữ liệu; metadata yêu cầu lọc; gộp mà không lọc cho kết quả vô lý
                # (vd percentile trung bình âm). Lưu ý: ~14 SQL chuẩn cũng không lọc (10 câu bị ảnh hưởng thật, đã nằm trong
                # suspect_gold) -> báo cáo kèm bảng "bỏ SQL chuẩn nghi sai". Xem scripts/1_chuan_bi/audit_gold.py.
                out.append(Finding("K4", "error", f"{t}.{c['name']} có giá trị rác {dom['sentinel_values']} (thiếu dữ liệu) nhưng không bị loại trước khi gộp",
                                   agg.sql(dialect="duckdb"), {"rule": "sentinel", "column": col.sql(dialect="duckdb")}))

    # COUNTYFP + STATEFP cùng lúc -> quận phải tồn tại
    if has_counties:
        lits = {}
        for cmp in tree.find_all(exp.EQ):
            if isinstance(cmp.this, exp.Column) and isinstance(cmp.expression, exp.Literal):
                lits.setdefault(cmp.this.name.upper(), set()).add(cmp.expression.name)
        for s in lits.get("STATEFP", ()):
            for cf in lits.get("COUNTYFP", ()):
                if len(lits.get("STATEFP")) == 1 and not kg.nodes.get(f"code:county:{s}{cf}"):
                    out.append(Finding("K1", "error", f"không có quận COUNTYFP='{cf}' trong bang '{s}'", f"STATEFP='{s}' AND COUNTYFP='{cf}'"))

    # ---- S7 (mức "suspect"): phép dựng hình ST_Intersection / ST_Union trên bảng có hình KHÔNG hợp lệ (theo KG: floodplain
    # có 3225 hình hỏng) mà không lọc ST_IsValid. Chỉ là nghi vấn: nhiều SQL chuẩn cũng làm vậy mà vẫn chạy được. Bộ sửa
    # chỉ coi đây là LỖI khi chạy thử thật sự ném TopologyException (xem engine.repair).
    isvalid_on = set()
    for n in tree.walk():
        n = n[0] if isinstance(n, tuple) else n
        if _fname(n) == "st_isvalid":
            for c in n.find_all(exp.Column):
                isvalid_on.add(((c.table or "").lower(), c.name.lower()))
    flagged = set()
    for n in tree.walk():
        n = n[0] if isinstance(n, tuple) else n
        if _fname(n) not in ("st_intersection", "st_union", "st_difference"):
            continue
        for c in n.find_all(exp.Column):
            t, cn = ctx.table_of(c)
            g = kg.geometry_source(t) if t else None
            if cn and cn.get("role") == "geometry" and g and (g.get("invalid_count") or 0) > 0:
                key = ((c.table or "").lower(), c.name.lower())
                if key not in isvalid_on and key not in flagged:
                    flagged.add(key)
                    out.append(Finding("S7", "suspect", f"{t} có {g['invalid_count']} hình không hợp lệ; dùng {c.sql()} trong phép giao hình mà không lọc ST_IsValid",
                                       n.sql(dialect="duckdb"), {"rule": "invalid_geom_construct", "column": c.sql(dialect="duckdb")}))

    # ---- cảnh báo mềm (không tính là lỗi) ----
    uses_spatial = any(_fname(n[0] if isinstance(n, tuple) else n) in PREDICATES for n in tree.walk())
    if uses_spatial:
        if not any(_fname(n[0] if isinstance(n, tuple) else n) == "st_isvalid" for n in tree.walk()):
            out.append(Finding("S7", "warn", "join không gian nhưng không lọc ST_IsValid"))
        for cnt in tree.find_all(exp.Count):
            if isinstance(cnt.this, exp.Star):
                out.append(Finding("S9", "warn", "COUNT(*) sau join không gian có thể đếm trùng", cnt.sql()))
    return out
