"""Đồ thị tri thức (KG) cho Spatial SQL trên FloodSQL-Bench.

Dựng từ: metadata_parquet.json (+ tùy chọn: dữ liệu thật trong DuckDB) + danh mục hàm DuckDB Spatial viết tay.
KHÔNG dùng SQL chuẩn của benchmark (tránh rò rỉ đáp án). Thiết kế: docs/kg_design.md
"""
import json, os, re
from collections import defaultdict

from spatialrepair import ROOT
META = os.path.join(ROOT, "data_source", "metadata", "metadata_parquet.json")

# ----------------------------------------------------------------------------------------------
# Danh mục hàm không gian (viết tay từ tài liệu DuckDB Spatial + spatial_function_notes trong metadata)
# args: loại hình học chấp nhận ở từng đối số. "ANY" = điểm hoặc đa giác.
# ----------------------------------------------------------------------------------------------
SPATIAL_FUNCTIONS = {
    "ST_Intersects": dict(kind="predicate", args=["ANY", "ANY"], symmetric=True,
                          meaning="hai hình có chung ít nhất 1 điểm (giao phần trong, chứa, hoặc chạm biên) — vị từ tổng quát nhất"),
    "ST_Overlaps":   dict(kind="predicate", args=["POLYGON", "POLYGON"], symmetric=True,
                          meaning="hai đa giác chồng một phần phần trong, không hình nào chứa hẳn hình kia"),
    "ST_Touches":    dict(kind="predicate", args=["ANY", "ANY"], symmetric=True,
                          meaning="chỉ chạm biên, phần trong không giao (kề nhau)"),
    "ST_Contains":   dict(kind="predicate", args=["POLYGON", "ANY"], symmetric=False, inverse="ST_Within",
                          meaning="hình 1 chứa trọn hình 2"),
    "ST_Within":     dict(kind="predicate", args=["ANY", "POLYGON"], symmetric=False, inverse="ST_Contains",
                          meaning="hình 1 nằm trọn trong hình 2"),
    "ST_Covers":     dict(kind="predicate", args=["POLYGON", "ANY"], symmetric=False, inverse="ST_CoveredBy",
                          meaning="như Contains nhưng tính cả điểm nằm trên biên"),
    "ST_CoveredBy":  dict(kind="predicate", args=["ANY", "POLYGON"], symmetric=False, inverse="ST_Covers",
                          meaning="như Within nhưng tính cả điểm nằm trên biên"),
    "ST_Disjoint":   dict(kind="predicate", args=["ANY", "ANY"], symmetric=True, meaning="không có điểm chung"),
    "ST_Equals":     dict(kind="predicate", args=["ANY", "ANY"], symmetric=True, meaning="trùng nhau về hình học"),
    "ST_Crosses":    dict(kind="predicate", args=["ANY", "ANY"], symmetric=False, meaning="cắt ngang (ít dùng với đa giác–đa giác)"),
    "ST_DWithin":    dict(kind="predicate", args=["ANY", "ANY", "NUMBER"], symmetric=True, unit_arg=2,
                          meaning="khoảng cách <= ngưỡng; ngưỡng tính theo đơn vị của CRS (ở đây là độ)"),
    "ST_Area":       dict(kind="measure", args=["POLYGON"], returns="area",
                          meaning="diện tích theo đơn vị CRS (ở đây là độ vuông); hỏi diện tích phần giao thì bọc ST_Intersection"),
    "ST_Perimeter":  dict(kind="measure", args=["POLYGON"], returns="length", meaning="chu vi theo đơn vị CRS"),
    "ST_Length":     dict(kind="measure", args=["ANY"], returns="length", meaning="độ dài theo đơn vị CRS"),
    "ST_Distance":   dict(kind="measure", args=["ANY", "ANY"], returns="length", meaning="khoảng cách theo đơn vị CRS (độ)"),
    "ST_Area_Spheroid":     dict(kind="measure", args=["POLYGON"], returns="area_m2", changes_unit=True,
                                 meaning="diện tích trên mặt cầu, đơn vị m² — KHÁC đơn vị của dữ liệu"),
    "ST_Distance_Spheroid": dict(kind="measure", args=["POINT", "POINT"], returns="length_m", changes_unit=True,
                                 meaning="khoảng cách trên mặt cầu, đơn vị m"),
    "ST_Distance_Sphere":   dict(kind="measure", args=["POINT", "POINT"], returns="length_m", changes_unit=True,
                                 meaning="khoảng cách trên mặt cầu, đơn vị m"),
    "ST_Point":        dict(kind="constructor", args=["NUMBER:x=lon", "NUMBER:y=lat"], returns="POINT",
                            meaning="dựng điểm; đối số 1 = x = kinh độ (LON), đối số 2 = y = vĩ độ (LAT)"),
    "ST_Intersection": dict(kind="constructor", args=["ANY", "ANY"], returns="GEOMETRY", symmetric=True,
                            meaning="phần hình học chung của hai hình"),
    "ST_Union":        dict(kind="constructor", args=["ANY", "ANY"], returns="GEOMETRY"),
    "ST_Union_Agg":    dict(kind="constructor", args=["ANY"], returns="GEOMETRY"),
    "ST_Centroid":     dict(kind="constructor", args=["ANY"], returns="POINT"),
    "ST_Envelope":     dict(kind="constructor", args=["ANY"], returns="POLYGON"),
    "ST_Buffer":       dict(kind="constructor", args=["ANY", "NUMBER"], returns="POLYGON",
                            meaning="vùng đệm; bán kính theo đơn vị CRS (độ)"),
    "ST_Transform":    dict(kind="crs", args=["ANY", "TEXT", "TEXT"], returns="GEOMETRY", changes_unit=True,
                            meaning="đổi CRS — benchmark yêu cầu dùng geometry gốc, không đổi CRS"),
    "ST_FlipCoordinates": dict(kind="crs", args=["ANY"], returns="GEOMETRY", meaning="đổi chỗ x và y"),
    "ST_IsValid":      dict(kind="validity", args=["ANY"], returns="BOOLEAN", meaning="hình học hợp lệ về topo"),
    "ST_MakeValid":    dict(kind="validity", args=["ANY"], returns="GEOMETRY"),
    "ST_X": dict(kind="accessor", args=["POINT"], returns="lon"), "ST_Y": dict(kind="accessor", args=["POINT"], returns="lat"),
    "ST_XMin": dict(kind="accessor", args=["ANY"], returns="lon"), "ST_XMax": dict(kind="accessor", args=["ANY"], returns="lon"),
    "ST_YMin": dict(kind="accessor", args=["ANY"], returns="lat"), "ST_YMax": dict(kind="accessor", args=["ANY"], returns="lat"),
    "ST_GeomFromWKB":  dict(kind="constructor", args=["BLOB"], returns="GEOMETRY"),
}

GRAIN = {"census_tracts": "tract", "claims": "claim_event", "county": "county", "cre": "tract", "floodplain": "hazard_zone",
         "hospitals": "facility_point", "nri": "tract", "schools": "facility_point", "svi": "tract", "zcta": "zcta"}

GEOID_SYSTEMS = {  # (bảng, cột) -> hệ mã
    "tract_geoid": dict(length=11, parts="STATEFP(2)+COUNTYFP(3)+TRACT(6)", dtype="VARCHAR", keep_leading_zeros=True),
    "county_fips": dict(length=5, parts="STATEFP(2)+COUNTYFP(3)", dtype="VARCHAR", keep_leading_zeros=True),
    "state_fips": dict(length=2, parts="STATEFP(2)", dtype="VARCHAR", keep_leading_zeros=True),
    "county_fp": dict(length=3, parts="COUNTYFP(3)", dtype="VARCHAR", keep_leading_zeros=True),
    "zcta5": dict(length=5, parts="ZCTA(5)", dtype="VARCHAR", keep_leading_zeros=True),
    "zip": dict(length=5, parts="ZIP(5)", dtype="VARCHAR", keep_leading_zeros=True),
}
CODE_OF = {("census_tracts", "GEOID"): "tract_geoid", ("claims", "GEOID"): "tract_geoid", ("cre", "GEOID"): "tract_geoid",
           ("nri", "GEOID"): "tract_geoid", ("svi", "GEOID"): "tract_geoid", ("county", "GEOID"): "county_fips",
           ("hospitals", "COUNTYFIPS"): "county_fips", ("zcta", "GEOID"): "zcta5",
           ("hospitals", "ZIP"): "zip", ("schools", "ZIP"): "zip"}


def _role(table, col, ctype, meta_t):
    c, t = col.upper(), ctype.upper()
    keys = [k.upper() for k in meta_t.get("key_identifier", [])]
    if c == "GEOMETRY":
        return "geometry" if "geometry" in meta_t.get("spatial_identifier", []) else "geometry_unusable"
    if "BLOB" in t: return "other"
    if c == "LON": return "coord_x"
    if c == "LAT": return "coord_y"
    if c in keys or (table, col) in CODE_OF: return "geo_key"
    if c in ("STATEFP", "COUNTYFP"): return "geo_code_part"
    if c in ("FIPS", "ST", "STCNTY") or (table == "cre" and c in ("STATE", "COUNTY", "TRACT")):
        return "numeric_geo_code" if ("INT" in t or "DOUBLE" in t) else "geo_label"
    if re.match(r"^(EP|MP)_", c) or re.search(r"_PE$|_PM$", c): return "percentage"
    if re.match(r"^(EPL|RPL|SPL)_", c): return "percentile_or_score"
    if t.startswith("DECIMAL"): return "money"
    if c.endswith("_ID") or c in ("ID", "GFID", "GEO_ID"): return "identifier"
    if t == "DATE": return "date"
    if c in ("NAME", "COUNTY", "CITY", "ADDRESS", "LOCATION", "STATE", "ST_ABBR"): return "name"
    if t == "VARCHAR": return "category"
    return "measure"


class KGBuilder:
    def __init__(self):
        self.nodes, self.edges = {}, []

    def node(self, nid, ntype, **attrs):
        if nid in self.nodes:
            self.nodes[nid].update(attrs)
        else:
            self.nodes[nid] = {"id": nid, "type": ntype, **attrs}
        return nid

    def edge(self, src, dst, etype, **attrs):
        self.edges.append({"src": src, "dst": dst, "type": etype, **attrs})

    # ---------------- Bước A: từ metadata ----------------
    def from_metadata(self, meta, spatial=True):
        g = meta["_global"]
        for t, mt in meta.items():
            if t == "_global":
                continue
            self.node(f"table:{t}", "Table", name=t, row_count=mt.get("row_count"), grain=GRAIN.get(t),
                      layer=mt.get("layer_category"), description=mt.get("_meta"))
            for c in mt["schema"]:
                col, ctype = c["column_name"], c["column_type"]
                role = _role(t, col, ctype, mt)
                attrs = dict(table=t, name=col, dtype=ctype, nullable=c.get("null") == "YES",
                             description=c.get("description"))
                if spatial:
                    attrs["role"] = role
                elif role not in ("geometry", "geometry_unusable", "coord_x", "coord_y"):
                    attrs["role"] = "key" if role == "geo_key" else "attribute"
                cid = self.node(f"col:{t}.{col}", "Column", **attrs)
                self.edge(f"table:{t}", cid, "HAS_COLUMN")

        # nối khóa trực tiếp (có ở cả 2 biến thể)
        for j in g["join_rules"]["key_based"]["direct"]:
            a, b = j["pair"]
            self.edge(f"col:{a}", f"col:{b}", "KEY_JOIN", transform_src=None, transform_dst=None, symmetric=True)
        if not spatial:
            return self

        # nối khóa địa lý qua LEFT(.,5)
        for j in g["join_rules"]["key_based"]["concat"]:
            a, b = j["pair"]
            m = re.match(r"LEFT\((\w+)\.(\w+),\s*(\d+)\)", a)
            self.edge(f"col:{m.group(1)}.{m.group(2)}", f"col:{b}", "KEY_JOIN",
                      transform_src=f"LEFT(x,{m.group(3)})", transform_dst=None, symmetric=True)

        # CRS
        self.node("crs:EPSG:4326", "CRS", name="WGS 84 (EPSG:4326 / OGC:CRS84)", kind="geographic", unit="degree",
                  area_unit="degree^2", axis={"x": "longitude", "y": "latitude"},
                  note="metadata mô tả ST_Area là 'square meters' — không khớp CRS; dùng độ/độ².")

        # nguồn hình học
        for t, mt in meta.items():
            if t == "_global":
                continue
            sp = mt.get("spatial_identifier", [])
            cols = {c["column_name"] for c in mt["schema"]}
            if "geometry" in sp:
                gid = self.node(f"geom:{t}", "GeometrySource", table=t, geom_kind="POLYGON",
                                valid_expr=f"{{alias}}.geometry", invalid_exprs=[])
            elif {"LAT", "LON"} <= cols:
                bad = [f"{{alias}}.geometry"] if "geometry" in cols else []
                gid = self.node(f"geom:{t}", "GeometrySource", table=t, geom_kind="POINT",
                                valid_expr="ST_Point({alias}.LON, {alias}.LAT)", invalid_exprs=bad,
                                invalid_exprs_reason="cột geometry không dùng được (rỗng) hoặc không tồn tại; dựng điểm từ LON, LAT")
            else:
                continue
            self.edge(f"table:{t}", gid, "HAS_GEOMETRY")
            self.edge(gid, "crs:EPSG:4326", "IN_CRS")

        # nối không gian
        preds = {"point_polygon": ["ST_Contains(polygon, point)", "ST_Within(point, polygon)", "ST_Intersects"],
                 "polygon_polygon": ["ST_Intersects", "ST_Overlaps", "ST_Touches", "ST_Contains", "ST_Within"]}
        for kind, pairs in g["join_rules"]["spatial"].items():
            for j in pairs:
                ta = re.findall(r"\b(" + "|".join(GRAIN) + r")\.", j["pair"][0])[0]
                tb = re.findall(r"\b(" + "|".join(GRAIN) + r")\.", j["pair"][1])[0]
                self.edge(f"table:{ta}", f"table:{tb}", "SPATIAL_JOIN", join_kind=kind, predicates=preds[kind],
                          src_expr=j["pair"][0], dst_expr=j["pair"][1], symmetric=True)

        # hàm không gian
        for fn, d in SPATIAL_FUNCTIONS.items():
            self.node(f"fn:{fn}", "SpatialFunction", name=fn, **d)
            if d.get("inverse"):
                self.edge(f"fn:{fn}", f"fn:{d['inverse']}", "INVERSE_OF", note="đổi chỗ 2 đối số thì tương đương")

        # hệ mã địa lý
        for sid, d in GEOID_SYSTEMS.items():
            self.node(f"codesys:{sid}", "CodeSystem", name=sid, **d)
        for (t, c), sid in CODE_OF.items():
            self.edge(f"col:{t}.{c}", f"codesys:{sid}", "CODED_BY")
        for t, mt in meta.items():
            if t == "_global":
                continue
            cols = {c["column_name"] for c in mt["schema"]}
            if "STATEFP" in cols: self.edge(f"col:{t}.STATEFP", "codesys:state_fips", "CODED_BY")
            if "COUNTYFP" in cols: self.edge(f"col:{t}.COUNTYFP", "codesys:county_fp", "CODED_BY")

        # mã bang + vài mã quận có trong metadata
        notes = " ".join(g.get("notes", []))
        for abbr, code in re.findall(r"\b(TX|FL|LA)=(\d{2})\b", notes):
            self.node(f"code:state:{code}", "GeoCode", level="state", code=code, abbr=abbr,
                      name={"TX": "Texas", "FL": "Florida", "LA": "Louisiana"}[abbr])

        # miền giá trị
        self.node("dom:floodplain.FLD_ZONE", "ValueDomain", non_flood_values=["X", "AREA NOT INCLUDED"],
                  rule="câu hỏi về vùng ngập thì loại FLD_ZONE NOT IN ('X','AREA NOT INCLUDED')")
        self.edge("col:floodplain.FLD_ZONE", "dom:floodplain.FLD_ZONE", "HAS_DOMAIN")
        for cid, n in list(self.nodes.items()):
            if n["type"] == "Column" and n.get("role") == "percentage":
                did = self.node(f"dom:{n['table']}.{n['name']}", "ValueDomain", range=[0, 100],
                                rule=f"{n['name']} IS NOT NULL AND {n['name']} BETWEEN 0 AND 100")
                self.edge(cid, did, "HAS_DOMAIN")
            if n["type"] == "Column" and n.get("name") == "CITY":
                did = self.node(f"dom:{n['table']}.CITY", "ValueDomain", case="UPPER", rule="CITY lưu chữ hoa")
                self.edge(cid, did, "HAS_DOMAIN")

        # luật chung
        rules = {
            "crs_preserve": ("Dùng geometry gốc (EPSG:4326, đơn vị độ). Không ST_Transform, không ép geography, "
                             "không dùng *_Spheroid, không nhân hệ số đổi đơn vị trừ khi câu hỏi yêu cầu.", "required"),
            "geometry_valid": ("Hình học dùng trong phép không gian nên được lọc ST_IsValid().", "recommended"),
            "point_from_lonlat": ("Bảng điểm (hospitals, schools) dựng hình học bằng ST_Point(LON, LAT).", "required"),
            "spatial_join_dedup": ("Join không gian là nhiều-nhiều; đếm thực thể thì dùng COUNT(DISTINCT key) hoặc GROUP BY.", "recommended"),
            "geoid_as_text": ("GEOID là VARCHAR, giữ số 0 đầu; không CAST sang số; trong SVI dùng GEOID, không dùng FIPS (BIGINT).", "required"),
            "decimal_cast": ("Cột DECIMAL/BIGINT dùng trong SUM/AVG/MIN/MAX nên CAST AS DOUBLE; cột đã là DOUBLE thì không.", "recommended"),
            "null_filter": ("Gộp trên cột có thể NULL thì thêm IS NOT NULL; NULL là thiếu dữ liệu, không phải 0.", "recommended"),
            "ratio_vs_percent": ("Tỉ lệ (ratio) trong [0,1]; phần trăm = tỉ lệ × 100.", "recommended"),
            "zcta_spatial": ("Nối với ZCTA bằng đa giác, không so khớp mã ZIP dạng chữ.", "required"),
            "county_join_key": ("Nối tract-level với county qua LEFT(GEOID,5) = county.GEOID.", "required"),
        }
        for rid, (text, level) in rules.items():
            self.node(f"rule:{rid}", "Rule", text=text, level=level)
        self.edge("rule:crs_preserve", "crs:EPSG:4326", "APPLIES_TO")
        for fn in ("ST_Transform", "ST_Area_Spheroid", "ST_Distance_Spheroid", "ST_Distance_Sphere"):
            self.edge("rule:crs_preserve", f"fn:{fn}", "APPLIES_TO")
        self.edge("rule:point_from_lonlat", "geom:hospitals", "APPLIES_TO")
        self.edge("rule:point_from_lonlat", "geom:schools", "APPLIES_TO")
        self.edge("rule:geoid_as_text", "col:svi.FIPS", "APPLIES_TO")
        self.edge("rule:zcta_spatial", "table:zcta", "APPLIES_TO")
        return self

    # ---------------- Bước B: kiểm tra dữ liệu thật ----------------
    def from_db(self, con, max_distinct=60):
        report = {}
        for nid, n in list(self.nodes.items()):
            if n["type"] != "Table":
                continue
            t = n["name"]
            try:
                real = {r[0]: r[1] for r in con.execute(f"DESCRIBE {t}").fetchall()}
            except Exception as e:  # noqa: BLE001  (view chưa có / thiếu parquet)
                report[t] = {"error": str(e)[:120]}
                continue
            for col, typ in real.items():
                cid = f"col:{t}.{col}"
                if cid in self.nodes:
                    self.nodes[cid]["dtype_db"] = typ
            if "geometry" in real:
                gtype = str(real["geometry"])
                if gtype.upper().startswith("GEOMETRY"):   # duckdb 1.x: GEOMETRY ; 2.x: GEOMETRY('EPSG:4326')
                    m = re.search(r"\('?([^')]+)'?\)", gtype)
                    crs = m.group(1) if m else None
                    gt = con.execute(f"SELECT ST_GeometryType(geometry)::VARCHAR, COUNT(*) FROM {t} GROUP BY 1").fetchall()
                    bb = con.execute(f"SELECT MIN(ST_XMin(geometry)),MAX(ST_XMax(geometry)),MIN(ST_YMin(geometry)),MAX(ST_YMax(geometry)) FROM {t}").fetchone()
                    inv = con.execute(f"SELECT COUNT(*) FROM {t} WHERE geometry IS NOT NULL AND NOT ST_IsValid(geometry)").fetchone()[0]
                    nulls = con.execute(f"SELECT COUNT(*) FROM {t} WHERE geometry IS NULL").fetchone()[0]
                    info = dict(geometry_types=dict(gt), bbox=list(bb), invalid_count=inv, null_count=nulls, crs_db=crs)
                    if f"geom:{t}" in self.nodes:
                        self.nodes[f"geom:{t}"].update(info)
                    else:
                        # có hình học thật nhưng metadata không khai báo là lớp không gian (vd claims)
                        self.nodes[f"col:{t}.geometry"].update(role="geometry_aux", **info,
                            note="có hình học thật nhưng không nằm trong luật nối không gian của benchmark")
                    if crs:
                        self.nodes["crs:EPSG:4326"].setdefault("confirmed_by_db", []).append(t)
                    report[t] = {"geometry": gtype, "types": dict(gt), "invalid": inv, "null": nulls}
                else:
                    nonempty = con.execute(f"SELECT COUNT(*) FROM {t} WHERE octet_length(geometry) > 0").fetchone()[0]
                    self.nodes[f"col:{t}.geometry"].update(role="geometry_unusable", nonempty_rows=nonempty)
                    report[t] = {"geometry": real["geometry"], "nonempty_rows": nonempty}
            # cột %/percentile: giá trị rác (vd -999 = thiếu dữ liệu)
            for col, typ in real.items():
                n2 = self.nodes.get(f"col:{t}.{col}")
                if not n2 or n2.get("role") not in ("percentage", "percentile_or_score") or typ not in ("DOUBLE", "BIGINT", "INTEGER", "FLOAT"):
                    continue
                mn, mx, neg = con.execute(f'SELECT MIN("{col}"), MAX("{col}"), SUM(CASE WHEN "{col}" < 0 THEN 1 ELSE 0 END) FROM {t}').fetchone()
                n2.update(min=mn, max=mx)
                if neg:
                    did = self.node(f"dom:{t}.{col}", "ValueDomain")
                    sent = [r[0] for r in con.execute(f'SELECT DISTINCT "{col}" FROM {t} WHERE "{col}" < 0 LIMIT 5').fetchall()]
                    self.nodes[did].update(sentinel_values=sent, sentinel_count=neg,
                                           rule=f"giá trị {sent} nghĩa là thiếu dữ liệu; gộp/so sánh phải loại ({col} >= 0 hoặc BETWEEN 0 AND 100)")
                    if not any(e["src"] == f"col:{t}.{col}" and e["dst"] == did for e in self.edges):
                        self.edge(f"col:{t}.{col}", did, "HAS_DOMAIN")
            # giá trị của cột phân loại
            for col, typ in real.items():
                cid = f"col:{t}.{col}"
                n2 = self.nodes.get(cid)
                if not n2 or typ != "VARCHAR" or n2.get("role") not in ("category", "geo_code_part"):
                    continue
                k = con.execute(f'SELECT COUNT(DISTINCT "{col}") FROM {t}').fetchone()[0]
                if k <= max_distinct:
                    vals = [r[0] for r in con.execute(f'SELECT DISTINCT "{col}" FROM {t} WHERE "{col}" IS NOT NULL ORDER BY 1').fetchall()]
                    did = self.node(f"dom:{t}.{col}", "ValueDomain", values=vals)
                    if not any(e["src"] == cid and e["dst"] == did for e in self.edges):
                        self.edge(cid, did, "HAS_DOMAIN")
        # bảng mã quận
        for geoid, sfp, cfp, name in con.execute("SELECT GEOID, STATEFP, COUNTYFP, NAME FROM county ORDER BY GEOID").fetchall():
            cid = self.node(f"code:county:{geoid}", "GeoCode", level="county", code=geoid, statefp=sfp,
                            countyfp=cfp, name=name)
            self.edge(cid, f"code:state:{sfp}", "PART_OF")
        report["county_codes"] = sum(1 for n in self.nodes.values() if n.get("level") == "county")
        return report

    def to_json(self, variant):
        return {"meta": {"variant": variant, "n_nodes": len(self.nodes), "n_edges": len(self.edges),
                         "sources": ["metadata_parquet.json", "DuckDB Spatial docs (viết tay)"]},
                "nodes": list(self.nodes.values()), "edges": self.edges}


# ----------------------------------------------------------------------------------------------
# API tra cứu dùng cho bộ phát hiện / sửa lỗi
# ----------------------------------------------------------------------------------------------
class KG:
    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))
        self.meta = d["meta"]
        self.nodes = {n["id"]: n for n in d["nodes"]}
        self.out, self.inc = defaultdict(list), defaultdict(list)
        for e in d["edges"]:
            self.out[e["src"]].append(e)
            self.inc[e["dst"]].append(e)

    @property
    def is_spatial(self):
        return self.meta["variant"] == "spatial"

    def tables(self):
        return [n["name"] for n in self.nodes.values() if n["type"] == "Table"]

    def columns(self, table):
        return {self.nodes[e["dst"]]["name"]: self.nodes[e["dst"]] for e in self.out[f"table:{table}"] if e["type"] == "HAS_COLUMN"}

    def column(self, table, col):
        cols = {k.lower(): v for k, v in self.columns(table).items()}
        return cols.get(col.lower())

    def find_table_of_column(self, col):
        return [n["table"] for n in self.nodes.values() if n["type"] == "Column" and n["name"].lower() == col.lower()]

    def geometry_source(self, table):
        n = self.nodes.get(f"geom:{table}")
        return n

    def geom_kind(self, table):
        n = self.geometry_source(table)
        return n["geom_kind"] if n else None

    def spatial_join(self, ta, tb):
        for e in self.out[f"table:{ta}"] + self.out[f"table:{tb}"]:
            if e["type"] == "SPATIAL_JOIN" and {e["src"], e["dst"]} == {f"table:{ta}", f"table:{tb}"}:
                return e
        return None

    def key_joins(self, ta, tb):
        res = []
        for e in self.edges_of_type("KEY_JOIN"):
            s, d = self.nodes.get(e["src"]), self.nodes.get(e["dst"])
            if s and d and {s["table"], d["table"]} == {ta, tb}:
                res.append(e)
        return res

    def edges_of_type(self, etype):
        return [e for es in self.out.values() for e in es if e["type"] == etype]

    def function(self, name):
        for k, n in self.nodes.items():
            if n["type"] == "SpatialFunction" and n["name"].lower() == name.lower():
                return n
        return None

    def code_system(self, table, col):
        for e in self.out[f"col:{table}.{col}"]:
            if e["type"] == "CODED_BY":
                return self.nodes[e["dst"]]
        return None

    def domain(self, table, col):
        for e in self.out[f"col:{table}.{col}"]:
            if e["type"] == "HAS_DOMAIN":
                return self.nodes[e["dst"]]
        return None

    def county_code(self, name, statefp=None):
        name = name.lower().replace(" county", "").replace(" parish", "").strip()
        return [n for n in self.nodes.values() if n["type"] == "GeoCode" and n.get("level") == "county"
                and n["name"].lower() == name and (statefp is None or n["statefp"] == statefp)]

    def rule(self, rid):
        return self.nodes.get(f"rule:{rid}")

    def crs(self):
        return self.nodes.get("crs:EPSG:4326")
