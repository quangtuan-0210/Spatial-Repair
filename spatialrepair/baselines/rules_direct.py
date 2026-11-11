"""Phương pháp đối chứng số 4: "AST + luật không gian trực tiếp" (KHÔNG dùng KG, KHÔNG đọc câu hỏi).

Các luật được viết cứng theo tên bảng/cột phổ biến — đúng kiểu một kỹ sư tự viết tay khi không có KG:
  S3  ST_Point(x.LAT, x.LON)              -> ST_Point(x.LON, x.LAT)
  S4  <bảng điểm>.geometry                -> ST_Point(LON, LAT)   (bảng điểm viết cứng: hospitals, schools)
  S2  ST_Contains(ST_Point(..), Y)        -> ST_Contains(Y, ST_Point(..));  ST_Within(Y, ST_Point(..)) tương tự
  S6  bỏ ST_Transform, *_Spheroid, ép geography
  S8  zcta.GEOID = x.ZIP                  -> ST_Contains(zcta.geometry, ST_Point(x.LON, x.LAT))
  K2  LEFT(c, n) = '<chuỗi dài m>' (n≠m)  -> LEFT(c, m)
  K3  CAST(GEOID AS số)                   -> GEOID
  K5  CITY = 'Houston'                     -> CITY = 'HOUSTON'
So với KG: không có bảng mã vùng (K1), không có đồ thị nối khóa (K2 khi nối), không có miền giá trị, không đối chiếu câu hỏi.
"""
import sqlglot
from sqlglot import exp
from spatialrepair.detection.checks import _fname, _args

POINT_TABLES = {"hospitals", "schools"}


def _fn(name, args):
    return exp.Anonymous(this=name, expressions=[a.copy() for a in args])


def _nodes(t):
    return [x[0] if isinstance(x, tuple) else x for x in t.walk()]


def rules_direct(sql):
    """Trả về (sql_mới, danh sách luật đã áp dụng). Không đổi gì -> trả nguyên câu."""
    try:
        t = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return sql, []
    applied = []
    alias = {}
    for tb in t.find_all(exp.Table):
        alias[(tb.alias or tb.name).lower()] = tb.name.lower()
    # S3
    for n in _nodes(t):
        if _fname(n) == "st_point" and len(_args(n)) == 2:
            a, b = _args(n)[:2]
            if isinstance(a, exp.Column) and a.name.upper() == "LAT" and isinstance(b, exp.Column) and b.name.upper() == "LON":
                n.replace(_fn("ST_Point", [b, a])); applied.append("S3")
    # S4
    for c in list(t.find_all(exp.Column)):
        if c.name.lower() == "geometry" and alias.get((c.table or "").lower()) in POINT_TABLES:
            c.replace(_fn("ST_Point", [exp.column("LON", table=c.table), exp.column("LAT", table=c.table)])); applied.append("S4")
    # S2
    for n in _nodes(t):
        f = _fname(n)
        if f in ("st_contains", "st_within") and len(_args(n)) == 2:
            a, b = _args(n)[:2]
            if (f == "st_contains" and _fname(a) == "st_point") or (f == "st_within" and _fname(b) == "st_point"):
                n.replace(_fn(n.name if isinstance(n, exp.Anonymous) else f.upper(), [b, a])); applied.append("S2")
    # S6
    for n in _nodes(t):
        f = _fname(n)
        if f in ("st_area_spheroid", "st_perimeter_spheroid", "st_length_spheroid"):
            n.replace(_fn("ST_" + f[3:].split("_spheroid")[0].title(), _args(n))); applied.append("S6")
        elif f in ("st_distance_spheroid", "st_distance_sphere"):
            n.replace(_fn("ST_Distance", _args(n))); applied.append("S6")
    for n in _nodes(t):
        if _fname(n) == "st_transform":
            n.replace(_args(n)[0].copy()); applied.append("S6")
    for c in list(t.find_all(exp.Cast)):
        if "geography" in c.to.sql().lower():
            c.replace(c.this.copy()); applied.append("S6")
    # S8
    for eq in list(t.find_all(exp.EQ)):
        l, r = eq.this, eq.expression
        if isinstance(l, exp.Column) and isinstance(r, exp.Column):
            for z, p in ((l, r), (r, l)):
                if alias.get((z.table or "").lower()) == "zcta" and z.name.upper() == "GEOID" and p.name.upper() == "ZIP":
                    eq.replace(_fn("ST_Contains", [exp.column("geometry", table=z.table),
                                                   _fn("ST_Point", [exp.column("LON", table=p.table), exp.column("LAT", table=p.table)])]))
                    applied.append("S8"); break
    # K2
    for eq in list(t.find_all(exp.EQ)):
        l, r = eq.this, eq.expression
        if isinstance(r, exp.Literal) and r.is_string and r.name.isdigit() and (isinstance(l, exp.Left) or _fname(l) == "left"):
            n = l.expression if isinstance(l, exp.Left) else _args(l)[-1]
            if isinstance(n, exp.Literal) and n.is_int and int(n.name) != len(r.name):
                inner = l.this if isinstance(l, exp.Left) else _args(l)[0]
                l.replace(exp.Left(this=inner.copy(), expression=exp.Literal.number(len(r.name)))); applied.append("K2")
    # K3
    for c in list(t.find_all(exp.Cast)):
        if isinstance(c.this, exp.Column) and c.this.name.upper() in ("GEOID", "COUNTYFIPS", "ZIP") and \
                any(k in c.to.sql().upper() for k in ("INT", "DOUBLE", "DECIMAL", "NUMERIC", "FLOAT")):
            c.replace(c.this.copy()); applied.append("K3")
    # K5
    for eq in list(t.find_all(exp.EQ)):
        if isinstance(eq.this, exp.Column) and eq.this.name.upper() == "CITY" and isinstance(eq.expression, exp.Literal) \
                and eq.expression.is_string and eq.expression.name != eq.expression.name.upper():
            eq.set("expression", exp.Literal.string(eq.expression.name.upper())); applied.append("K5")
    if not applied:
        return sql, []
    return t.sql(dialect="duckdb"), applied
