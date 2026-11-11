"""Các PHÉP SỬA của SpatialRepair — mỗi mã lỗi (S1..S9, K1..K5) có 1 hàm fix_<mã>.

Mỗi hàm nhận (sql, finding, ý_câu_hỏi, kg) và trả về danh sách ứng viên [(sql_mới, mô tả phép sửa)].
Phép sửa luôn CỤC BỘ: chỉ thay đúng nút AST mà bộ phát hiện chỉ ra (đổi chỗ đối số, đổi tên hàm, thay hằng số mã vùng,
bọc LEFT(...,5), thêm DISTINCT, ...). Bộ điều phối (engine.py) quyết định thử phép nào, giữ ứng viên nào.
"""
import sqlglot
from sqlglot import exp
from spatialrepair.detection.checks import Ctx, geom_kind, _fname, _args, PREDICATES

KEY_OF = {"census_tracts": "GEOID", "zcta": "GEOID", "county": "GEOID", "floodplain": "GFID",
          "schools": "UNIQUE_ID", "hospitals": "UNIQUE_ID", "claims": "id"}


def _p(sql):
    return sqlglot.parse_one(sql, read="duckdb")


def _r(tree):
    return tree.sql(dialect="duckdb")


def _find(tree, node_sql):
    """Tìm nút trong cây có SQL đúng bằng node_sql (đoạn SQL mà Finding chỉ tới)."""
    for n in tree.walk():
        n = n[0] if isinstance(n, tuple) else n
        if isinstance(n, exp.Expression) and n.sql(dialect="duckdb") == node_sql:
            return n
    return None


def _alias_of(ctx, table):
    for a, t in ctx.alias.items():
        if t == table:
            return a
    return None


def _point_expr(alias):
    return exp.Anonymous(this="ST_Point", expressions=[exp.column("LON", table=alias), exp.column("LAT", table=alias)])


def _fn(name, args):
    return exp.Anonymous(this=name, expressions=[a.copy() for a in args])


# ---------------------------------------------------------------------------------------------
# Phép sửa cho từng mã lỗi: nhận (cây đã copy, Finding, ý câu hỏi) -> list[(cây mới, mô tả)]
# ---------------------------------------------------------------------------------------------
def _variants(sql, node_sql, builders):
    """builders: list[(fn(node, tree) -> exp mới hoặc None, mô tả)]; mỗi builder áp lên 1 bản copy."""
    out = []
    for build, desc in builders:
        t = _p(sql)
        n = _find(t, node_sql)
        if n is None:
            continue
        new = build(n, t)
        if new is None:
            continue
        if new is not n:
            n.replace(new)
        out.append((_r(t), desc))
    return out


def fix_S1(sql, f, it, kg):
    allowed = f.extra.get("allowed") or []
    t = _p(sql); n = _find(t, f.node)
    if n is None:
        return []
    ctx = Ctx(t, kg)
    a = _args(n)[:2]
    kinds = [geom_kind(x, ctx) for x in a]
    b = []
    rel = it.relations if it else set()
    if "POINT" in kinds:     # điểm–đa giác: dùng "đa giác chứa điểm"
        poly_i = 0 if kinds[0] != "POINT" else 1
        b.append((lambda nd, tt, i=poly_i: _fn("ST_Contains", [_args(nd)[i], _args(nd)[1 - i]]), "ST_Contains(đa giác, điểm)"))
        b.append((lambda nd, tt: _fn("ST_Intersects", _args(nd)[:2]), "ST_Intersects"))
    else:
        prefer = []
        if "intersect" in rel: prefer += ["ST_Intersects"]
        if "overlap" in rel: prefer += ["ST_Intersects", "ST_Overlaps"]
        if "touch" in rel: prefer += ["ST_Touches"]
        if rel & {"within", "contain"}:
            prefer += ["ST_Contains", "ST_Within"]
        for name in prefer or [x.replace("st_", "ST_").title().replace("St_", "ST_") for x in allowed]:
            nm = name if name.startswith("ST_") else "ST_" + name[3:]
            b.append((lambda nd, tt, nm=nm: _fn(nm, _args(nd)[:2]), nm))
            if nm in ("ST_Contains", "ST_Within"):
                b.append((lambda nd, tt, nm=nm: _fn(nm, list(reversed(_args(nd)[:2]))), nm + " (đảo đối số)"))
    seen, res = set(), []
    for s, d in _variants(sql, f.node, b):
        if s not in seen and s != sql:
            seen.add(s); res.append((s, "S1: " + d))
    return res


def fix_S2(sql, f, it, kg):
    return [(s, "S2: " + d) for s, d in _variants(sql, f.node, [
        (lambda nd, tt: _fn(nd.name, list(reversed(_args(nd)[:2]))), "đổi chỗ 2 đối số"),
        (lambda nd, tt: _fn({"st_contains": "ST_Within", "st_within": "ST_Contains", "st_covers": "ST_CoveredBy",
                              "st_coveredby": "ST_Covers"}.get(_fname(nd), nd.name), _args(nd)[:2]), "Contains<->Within")])]


def fix_S3(sql, f, it, kg):
    t = _p(sql)
    nodes = [x[0] if isinstance(x, tuple) else x for x in t.walk()]
    for n in [x for x in nodes if _fname(x) == "st_point" and len(_args(x)) == 2]:
        a, b = _args(n)[:2]
        if isinstance(a, exp.Column) and a.name.upper() == "LAT":
            n.replace(_fn("ST_Point", [b, a]))
    s = _r(t)
    return [(s, "S3: ST_Point(LON, LAT)")] if s != sql else []


def fix_S4(sql, f, it, kg):
    t = _p(sql); ctx = Ctx(t, kg); changed = False
    for col in list(t.find_all(exp.Column)):
        if col.name.lower() != "geometry":
            continue
        tb, c = ctx.table_of(col)
        g = kg.geometry_source(tb) if tb else None
        if g and g.get("geom_kind") == "POINT":
            col.replace(_point_expr(col.table or _alias_of(ctx, tb)))
            changed = True
    return [(_r(t), "S4: .geometry -> ST_Point(LON, LAT)")] if changed else []


def fix_S5(sql, f, it, kg):
    t = _p(sql); n = _find(t, f.node)
    if n is None:
        return []
    inner = _args(n)[0]
    if _fname(inner) == "st_intersection":      # cả hình -> bỏ phần giao
        return [(s, "S5: " + d) for s, d in _variants(sql, f.node, [
            (lambda nd, tt: _fn("ST_Area", [_args(_args(nd)[0])[0]]), "diện tích cả hình (vế 1)"),
            (lambda nd, tt: _fn("ST_Area", [_args(_args(nd)[0])[1]]), "diện tích cả hình (vế 2)")])]
    # phần giao: tìm hình còn lại trong vị từ nối với hình đang tính diện tích
    ctx = Ctx(t, kg)
    tabs = {ctx.table_of(c)[0] for c in inner.find_all(exp.Column)} - {None}
    partners = []
    for p in t.walk():
        p = p[0] if isinstance(p, tuple) else p
        if _fname(p) in PREDICATES:
            a = _args(p)[:2]
            for i in (0, 1):
                ti = {ctx.table_of(c)[0] for c in a[i].find_all(exp.Column)} - {None}
                if ti == tabs:
                    partners.append(a[1 - i].sql(dialect="duckdb"))
    res = []
    for ps in dict.fromkeys(partners):
        def build(nd, tt, ps=ps):
            return _fn("ST_Area", [_fn("ST_Intersection", [_args(nd)[0], sqlglot.parse_one(ps, read="duckdb")])])
        res += [(s, "S5: diện tích phần giao") for s, _ in _variants(sql, f.node, [(build, "")])]
    return res


def fix_S6(sql, f, it, kg):
    t = _p(sql)
    for n in list(t.find_all(exp.Anonymous)):
        nm = n.name.lower()
        if nm in ("st_area_spheroid", "st_perimeter_spheroid", "st_length_spheroid"):
            n.replace(_fn(n.name.replace("_Spheroid", "").replace("_SPHEROID", ""), _args(n)))
        elif nm in ("st_distance_spheroid", "st_distance_sphere"):
            n.replace(_fn("ST_Distance", _args(n)))
    for n in list(t.find_all(exp.Anonymous)):
        if n.name.lower() == "st_transform":
            n.replace(_args(n)[0].copy())
    for c in list(t.find_all(exp.Cast)):
        if "geography" in c.to.sql().lower():
            c.replace(c.this.copy())
    s = _r(t)
    return [(s, "S6: bỏ đổi CRS/đơn vị")] if s != sql else []


def fix_S8(sql, f, it, kg):
    t = _p(sql); n = _find(t, f.node)
    if n is None or not isinstance(n, exp.EQ):
        return []
    ctx = Ctx(t, kg)
    sides = [n.this, n.expression]
    z = next((s for s in sides if isinstance(s, exp.Column) and ctx.table_of(s)[0] == "zcta"), None)
    pt = next((s for s in sides if isinstance(s, exp.Column) and s is not z), None)
    if z is None or pt is None:
        return []
    zalias = z.table or _alias_of(ctx, "zcta")
    return [(s, "S8: nối ZCTA bằng đa giác") for s, _ in _variants(sql, f.node, [
        (lambda nd, tt: _fn("ST_Contains", [exp.column("geometry", table=zalias), _point_expr(pt.table)]), "")])]


def _outer_alias(t, ent, key):
    """Bí danh ở truy vấn NGOÀI CÙNG trỏ tới thực thể ent: bảng gốc, hoặc CTE/subquery lấy từ ent và có cột key."""
    if not isinstance(t, exp.Select):
        return None
    ctes = {}
    for c in t.find_all(exp.CTE):
        body = c.this
        if any(x.name.lower() == ent for x in body.find_all(exp.Table)) and \
                any((e.alias_or_name or "").lower() == key.lower() for e in getattr(body, "expressions", [])):
            ctes[c.alias.lower()] = True
    refs = []
    fr = t.args.get("from") or t.args.get("from_")
    if fr is not None:
        refs.append(fr.this)
    refs += [j.this for j in t.args.get("joins") or []]
    for r in refs:
        if isinstance(r, exp.Table):
            nm = r.name.lower()
            if nm == ent or nm in ctes:
                return (r.alias or r.name)
        elif isinstance(r, exp.Subquery) and r.alias:
            body = r.this
            if any(x.name.lower() == ent for x in body.find_all(exp.Table)) and \
                    any((e.alias_or_name or "").lower() == key.lower() for e in getattr(body, "expressions", [])):
                return r.alias
    return None


def fix_S9(sql, f, it, kg):
    ent = it.count_entity if it else None
    t = _p(sql)
    key = KEY_OF.get(ent)
    al = _outer_alias(t, ent, key) if ent and key else None
    if not al or not kg.column(ent, key):
        return []
    return [(s, f"S9: COUNT(DISTINCT {al}.{key})") for s, _ in _variants(sql, f.node, [
        (lambda nd, tt: exp.Count(this=exp.Distinct(expressions=[exp.column(key, table=al)])), "")])]


def fix_K1(sql, f, it, kg):
    exp_codes = f.extra.get("expected") or []
    if not exp_codes and it:
        exp_codes = sorted(it.counties) if len(it.counties) == 1 else sorted(it.states) if len(it.states) == 1 else []
    t = _p(sql); n = _find(t, f.node)
    if n is None:
        import re
        m = re.search(r"COUNTYFP='(\d+)'", f.node or "")
        if m:
            n = next((x for x in t.find_all(exp.EQ) if isinstance(x.this, exp.Column) and x.this.name.upper() == "COUNTYFP"
                      and isinstance(x.expression, exp.Literal) and x.expression.name == m.group(1)), None)
            if n is not None:
                f = type(f)(f.code, f.level, f.msg, n.sql(dialect="duckdb"), f.extra)
    if n is None or not isinstance(n.expression, exp.Literal):
        return []
    old = n.expression.name
    core, pct = old.rstrip("%"), old.endswith("%")
    col = n.this
    name = col.name.upper() if isinstance(col, exp.Column) else "LEFT"
    res = []
    for e in exp_codes:
        if name == "STATEFP":
            new = e[:2]
        elif name == "COUNTYFP":
            new = e[2:5] if len(e) == 5 else None
        elif len(core) >= 5 and len(e) == 5:
            new = e + core[5:]
        elif len(core) == 2:
            new = e[:2]
        else:
            new = None
        if not new or new == core:
            continue
        lit = new + ("%" if pct else "")
        res += [(s, f"K1: '{old}' -> '{lit}'") for s, _ in _variants(sql, f.node, [
            (lambda nd, tt, lit=lit: nd.__class__(this=nd.this.copy(), expression=exp.Literal.string(lit)), "")])]
    return res


def fix_K2(sql, f, it, kg):
    t = _p(sql); n = _find(t, f.node)
    if n is None or not isinstance(n, exp.EQ):
        return []
    lhs, rhs = n.this, n.expression
    b = []
    if isinstance(rhs, exp.Literal) and rhs.is_string:
        L = len(rhs.name)
        inner = lhs.this if isinstance(lhs, exp.Left) else (_args(lhs)[0] if _fname(lhs) == "left" else lhs)
        b.append((lambda nd, tt, inner=inner, L=L: exp.EQ(this=exp.Left(this=inner.copy(), expression=exp.Literal.number(L)),
                                                          expression=nd.expression.copy()), f"LEFT(...,{L})"))
    else:
        ctx = Ctx(t, kg)
        for side in (0, 1):
            col = (lhs, rhs)[side]
            other = (lhs, rhs)[1 - side]
            base = col.this if isinstance(col, exp.Left) else col
            if not isinstance(base, exp.Column) or not isinstance(other, (exp.Column, exp.Left)):
                continue
            tb, c = ctx.table_of(base)
            cs = kg.code_system(tb, c["name"]) if c else None
            ob = other.this if isinstance(other, exp.Left) else other
            ot, oc = ctx.table_of(ob) if isinstance(ob, exp.Column) else (None, None)
            ocs = kg.code_system(ot, oc["name"]) if oc else None
            if isinstance(col, exp.Left) and cs and ocs:
                L = ocs["length"]
                def build2(nd, tt, side=side, L=L, base=base):
                    newc = base.copy() if L == cs["length"] else exp.Left(this=base.copy(), expression=exp.Literal.number(L))
                    return exp.EQ(this=newc, expression=nd.expression.copy()) if side == 0 else exp.EQ(this=nd.this.copy(), expression=newc)
                b.append((build2, f"sửa LEFT(...) theo độ dài {L}"))
                continue
            if cs and ocs and cs["length"] > ocs["length"]:
                L = ocs["length"]
                def build(nd, tt, side=side, L=L, base=base):
                    newc = exp.Left(this=base.copy(), expression=exp.Literal.number(L))
                    a, bb = nd.this.copy(), nd.expression.copy()
                    return exp.EQ(this=newc, expression=bb) if side == 0 else exp.EQ(this=a, expression=newc)
                b.append((build, f"LEFT(...,{L})"))
    return [(s, "K2: " + d) for s, d in _variants(sql, f.node, b)]


def fix_K3(sql, f, it, kg):
    t = _p(sql)
    for c in list(t.find_all(exp.Cast)):
        if isinstance(c.this, exp.Column) and c.this.name.upper() in ("GEOID", "COUNTYFIPS", "ZIP"):
            c.replace(c.this.copy())
    s = _r(t)
    return [(s, "K3: bỏ CAST mã địa lý")] if s != sql else []


def fix_K4_sentinel(sql, f, it, kg):
    """Cột có giá trị rác (-999 = thiếu dữ liệu) bị gộp mà không lọc -> thêm `<cột> >= 0` vào WHERE của SELECT chứa phép gộp."""
    col = f.extra.get("column")
    t = _p(sql); agg = _find(t, f.node)
    if not col or agg is None:
        return []
    sel = agg.find_ancestor(exp.Select)
    if sel is None:
        return []
    sel.where(sqlglot.parse_one(f"{col} >= 0", read="duckdb"), copy=False)
    return [(_r(t), f"K4: lọc giá trị rác {col} >= 0")]


def fix_K4(sql, f, it, kg):
    import re
    if f.extra.get("rule") == "sentinel":
        return fix_K4_sentinel(sql, f, it, kg)
    m = re.search(r"\[([^\]]*)\]", f.msg)
    vals = re.findall(r"'([^']+)'", m.group(1)) if m else []
    if not vals:
        return []
    t = _p(sql); ctx = Ctx(t, kg)
    target = None
    for al, tb in ctx.alias.items():
        for cname, cn in kg.columns(tb).items():
            dom = kg.domain(tb, cname)
            dv = set((dom or {}).get("values") or []) | set((dom or {}).get("non_flood_values") or [])
            if dom and all(v in dv for v in vals):
                target = (al, cname)
    if not target or not isinstance(t, exp.Select):
        return []
    cond = exp.Not(this=exp.In(this=exp.column(target[1], table=target[0]), expressions=[exp.Literal.string(v) for v in vals]))
    t = t.where(cond)
    return [(_r(t), f"K4: loại {vals}")]


def fix_K5(sql, f, it, kg):
    t = _p(sql); n = _find(t, f.node)
    if n is None or not isinstance(n.expression, exp.Literal):
        return []
    ctx = Ctx(t, kg)
    tb, c = ctx.table_of(n.this) if isinstance(n.this, exp.Column) else (None, None)
    dom = kg.domain(tb, c["name"]) if c else None
    v = n.expression.name
    cands = []
    if dom and dom.get("values"):
        cands = [x for x in dom["values"] if x.lower() == v.lower()]
    if not cands:
        cands = [v.upper()]
    return [(s, f"K5: '{v}' -> '{x}'") for x in cands for s, _ in _variants(sql, f.node, [
        (lambda nd, tt, x=x: exp.EQ(this=nd.this.copy(), expression=exp.Literal.string(x)), "")])]



def fix_G(sql, f, it, kg):
    """Cột / bí danh không tồn tại (dựa trên lược đồ trong KG):
      0. bí danh không có trong câu: nếu là TÊN bảng đã được đặt bí danh khác (nri -> n) hoặc chỉ một bảng trong câu có
         cột đó -> dùng bí danh đúng
      1. STATEFP ở bảng không có cột này nhưng có GEOID theo mã FIPS -> LEFT(<bí danh>.GEOID, 2)
      2. cột có ở đúng MỘT bảng khác trong câu -> gắn lại đúng bí danh
      3. tên gần giống một cột của bảng đó (gõ sai) -> đổi tên
    Mọi chỗ dùng cùng cột sai đều được thay cùng lúc."""
    import difflib
    t = _p(sql); ctx = Ctx(t, kg)
    col = _find(t, f.node)
    if not isinstance(col, exp.Column):
        return []
    name, key = col.name, col.sql(dialect="duckdb")
    alias = (col.table or "").lower() or None
    out = []

    def variant(build, desc):
        t2 = _p(sql)
        hits = [c for c in t2.find_all(exp.Column) if c.sql(dialect="duckdb") == key]
        for c2 in hits:
            new = build(c2)
            if isinstance(c2.parent, exp.Select) and c2 in c2.parent.expressions and not isinstance(new, exp.Column):
                new = exp.alias_(new, c2.name)            # giữ tên cột đầu ra (CTE / truy vấn ngoài còn dùng)
            c2.replace(new)
        if hits:
            out.append((_r(t2), "G: " + desc))

    if alias and alias not in ctx.alias:                       # 0. bí danh lạ
        same = [a for a, tb in ctx.alias.items() if tb.lower() == alias]
        owners = list(dict.fromkeys(a for a, tb in ctx.alias.items() if kg.column(tb, name)))
        target = same[0] if len(same) == 1 else (owners[0] if len(owners) == 1 else None)
        if target:
            variant(lambda c, a=target: exp.column(c.name, table=a), f"bí danh {alias} không tồn tại -> {target}")
        return out
    if f.extra.get("rule") != "unknown_column":
        return []
    table = ctx.alias.get(alias) if alias else f.extra.get("table")
    # 1. STATEFP -> LEFT(GEOID, 2)
    if name.upper() == "STATEFP" and table and kg.column(table, "GEOID"):
        cs = kg.code_system(table, "GEOID")
        if cs and cs["name"] in ("tract_geoid", "county_fips"):
            variant(lambda c: exp.Left(this=exp.column("GEOID", table=c.table or None), expression=exp.Literal.number(2)),
                    f"{table} không có STATEFP -> LEFT(GEOID, 2)")
    # 2. gắn lại đúng bảng
    owners = list(dict.fromkeys(a for a, tb in ctx.alias.items() if kg.column(tb, name) and a != alias))
    if len(owners) == 1:
        variant(lambda c, a=owners[0]: exp.column(kg.column(ctx.alias[a], name)["name"], table=a),
                f"{name} thuộc {ctx.alias[owners[0]]} -> {owners[0]}.{name}")
    # 3. gõ sai tên cột
    if table:
        cols = list(kg.columns(table).keys())
        close = difflib.get_close_matches(name.upper(), [c.upper() for c in cols], n=1, cutoff=0.85)
        if close:
            real = next(c for c in cols if c.upper() == close[0])
            variant(lambda c, real=real: exp.column(real, table=c.table or None), f"{name} -> {real} (gõ sai tên cột)")
    return out


def fix_S7(sql, f, it, kg):
    """Bảng có hình hỏng dùng trong phép giao hình mà không lọc -> thêm ST_IsValid(<cột hình>) vào WHERE."""
    col = f.extra.get("column")
    t = _p(sql); n = _find(t, f.node)
    if not col or n is None:
        return []
    sel = n.find_ancestor(exp.Select)
    if sel is None:
        return []
    sel.where(sqlglot.parse_one(f"ST_IsValid({col})", read="duckdb"), copy=False)
    return [(_r(t), f"S7: lọc ST_IsValid({col})")]


FIXERS = {"G": fix_G, "S7": fix_S7, "S1": fix_S1, "S2": fix_S2, "S3": fix_S3, "S4": fix_S4, "S5": fix_S5, "S6": fix_S6, "S8": fix_S8,
          "S9": fix_S9, "K1": fix_K1, "K2": fix_K2, "K3": fix_K3, "K4": fix_K4, "K5": fix_K5}


