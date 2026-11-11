"""Cài lỗi có chủ đích vào SQL chuẩn, kiểm tra bộ kiểm tra KG có bắt đúng mã lỗi không.
    python tests/test_checks_mutations.py
"""
import json, os, re, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from spatialrepair.knowledge.kg import KG            # noqa: E402
from spatialrepair.detection.checks import check_sql  # noqa: E402

kg = KG(os.path.join(ROOT, "kg", "kg_spatial.json"))
d = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(ROOT, "data_source", "benchmark", "floodsql_443.jsonl"), encoding="utf-8")}
find = lambda pat: next(i for i, r in d.items() if re.search(pat, r["sql"]))  # noqa: E731
T = []
i = find(r"ST_Contains\(\w+\.geometry,\s*ST_Point"); s = d[i]["sql"]
m = re.search(r"ST_Contains\((\w+\.geometry),\s*(ST_Point\([^)]*\))\)", s)
T.append(("S2", i, s.replace(m.group(0), f"ST_Contains({m.group(2)}, {m.group(1)})")))
T.append(("S3", i, re.sub(r"ST_Point\((\w+)\.LON,\s*(\w+)\.LAT\)", r"ST_Point(\1.LAT, \2.LON)", s, count=1)))
T.append(("G", i, s.replace(".geometry", ".geom", 1)))
for tbl in ("hospitals", "schools"):
    j = find(tbl + r" \w+ .*ST_Point"); sj = d[j]["sql"]
    T.append(("S4", j, re.sub(r"ST_Point\((\w+)\.LON,\s*\w+\.LAT\)", r"\1.geometry", sj, count=1)))
j = find(r"ST_Area\(ST_Intersection"); sj = d[j]["sql"]
T.append(("S6", j, sj.replace("ST_Area(", "ST_Area_Spheroid(", 1)))
T.append(("S6", j, sj.replace("ST_Area(ST_Intersection(", "ST_Area(ST_Transform(ST_Intersection(", 1)
          .replace(")))", ")), 'EPSG:4326', 'EPSG:3857'))", 1)))
T.append(("S6w", j, sj.replace("ST_Area(", "1e6*ST_Area(", 1)))
j = find(r"STATEFP\s*=\s*'48'"); T.append(("K1", j, re.sub(r"STATEFP\s*=\s*'48'", "STATEFP = '47'", d[j]["sql"], count=1)))
j = find(r"COUNTYFP\s*=\s*'201'"); T.append(("K1", j, re.sub(r"COUNTYFP\s*=\s*'201'", "COUNTYFP = '999'", d[j]["sql"], count=1)))
j = find(r"LEFT\((\w+)\.GEOID,\s*5\)\s*=\s*\w+\.GEOID"); T.append(("K2", j, re.sub(r"LEFT\((\w+)\.GEOID,\s*5\)\s*=", r"\1.GEOID =", d[j]["sql"], count=1)))
j = find(r"GEOID LIKE '48201%'"); T.append(("K3", j, d[j]["sql"].replace("GEOID LIKE '48201%'", "CAST(GEOID AS BIGINT) = 48201", 1)))
j = find(r"CITY\s*=\s*'MIAMI'"); T.append(("K5", j, d[j]["sql"].replace("'MIAMI'", "'Miami'", 1)))
j = find(r"TYPE\s*=\s*'CRITICAL ACCESS'"); T.append(("K5", j, d[j]["sql"].replace("'CRITICAL ACCESS'", "'CRITICAL'", 1)))
j = find(r"JOIN zcta \w+ ON ST_\w+\(ST_Point"); sj = d[j]["sql"]
m = re.search(r"JOIN zcta (\w+) ON (ST_\w+\(ST_Point\((\w+)\.LON[^)]*\)[^)]*\))", sj)
T.append(("S8", j, sj.replace(m.group(2), f"{m.group(1)}.GEOID = {m.group(3)}.ZIP", 1)))
j = find(r"JOIN census_tracts \w+ ON ST_Intersects\(\w+\.geometry"); sj = d[j]["sql"]
T.append(("S1", j, sj.replace("ST_Intersects(", "ST_Overlaps(ST_Centroid(", 1).replace(".geometry,", ".geometry),", 1)))

ok = 0
for code, i, sq in T:
    fs = check_sql(sq, kg, d[i]["question"])
    codes = sorted({f.code + ("" if f.level == "error" else "w") for f in fs})
    hit = code in codes and sq != d[i]["sql"]
    ok += hit
    print(("OK  " if hit else "MISS"), f"{code:4s} {i:9s} báo: {codes}")
print(f"\n{ok}/{len(T)} đột biến bị bắt đúng")
