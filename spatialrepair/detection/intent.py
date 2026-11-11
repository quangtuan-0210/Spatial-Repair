"""Đối chiếu ý câu hỏi với SQL (bước 'khớp ý câu hỏi' của bộ phát hiện/sửa).

Dùng cho các lỗi mà KG một mình không bắt được vì SQL sai vẫn "hợp lệ":
  S1  câu hỏi nói "nằm trong / giao / chồng / kề" nhưng SQL dùng hàm quan hệ khác nghĩa
  S2  (đa giác–đa giác) câu hỏi nói A chứa B nhưng SQL viết B chứa A
  S5  câu hỏi hỏi diện tích phần giao nhưng SQL tính diện tích cả hình (và ngược lại)
  S9  câu hỏi "how many <thực thể>" nhưng SQL COUNT(*) sau nối không gian (đếm trùng)
  K1  mã bang/quận trong SQL khác với mã/tên nêu trong câu hỏi
Chỉ dùng: câu hỏi + KG + nghĩa của hàm. KHÔNG dùng SQL chuẩn.
"""
import re
from dataclasses import dataclass, field
import sqlglot
from sqlglot import exp
from spatialrepair.detection.checks import Finding, Ctx, geom_kind, _fname, _args, PREDICATES

STATE_WORDS = {"texas": "48", "tx": "48", "florida": "12", "fl": "12", "louisiana": "22", "la": "22"}
ENTITY_WORDS = [  # (regex, bảng) — thứ tự quan trọng: cụm dài trước
    (r"census[\s_]+tracts?|\btracts?\b", "census_tracts"),
    (r"\bzctas?\b|zip code tabulation areas?", "zcta"),
    (r"\bfloodplains?\b|flood hazard (?:zones?|areas?|polygons?)|fema flood\w*|flood zones?", "floodplain"),
    (r"\bcount(?:y|ies)\b|\bparish(?:es)?\b", "county"),
    (r"\bschools?\b|\bcolleges?\b", "schools"),
    (r"\bhospitals?\b", "hospitals"),
    (r"\bnfip\b|\bclaims?\b", "claims"),
]
REL = {  # loại quan hệ trong câu hỏi -> các hàm SQL chấp nhận được (theo nghĩa hàm trong KG)
    "contain": {"st_contains", "st_within", "st_covers", "st_coveredby"},
    "within": {"st_contains", "st_within", "st_covers", "st_coveredby"},
    "intersect": {"st_intersects"},
    "overlap": {"st_overlaps", "st_intersects"},
    "touch": {"st_touches"},
}
REL_RE = {
    "contain": r"\bcontain(?:s|ing)?\b|\bcontained\b(?!\s+(?:with)?in\b|\s+by\b)",
    "within": r"\b(?:within|inside|located in|lie in|lies in|falls? (?:with)?in|situated in)\b|\bcontained\s+(?:with)?in\b|\bcontained by\b",
    "intersect": r"\bintersect(?:s|ed|ing|ion)?\b",
    "overlap": r"\boverlap(?:s|ped|ping)?\b",
    "touch": r"\btouch(?:es|ed|ing)?\b|\badjacent\b|\bshares? (?:a )?(?:common )?boundar|\bborder(?:s|ing)?\b|\bneighbo",
}
POLY = {"census_tracts", "zcta", "floodplain", "county"}


@dataclass
class Intent:
    states: set = field(default_factory=set)
    counties: set = field(default_factory=set)       # mã 5 chữ số
    relations: set = field(default_factory=set)
    entities: list = field(default_factory=list)     # (vị trí, bảng) theo thứ tự xuất hiện
    count_entity: str = None                          # "how many <X>" -> bảng X
    count_pairs: bool = False
    area_intersection: bool = False
    area_whole: bool = False
    containment: tuple = None                         # (bảng chứa, bảng được chứa) nếu rõ
    units: bool = False


def _find_entities(q):
    hits = []
    for rx, t in ENTITY_WORDS:
        for m in re.finditer(rx, q, re.I):
            if not any(a <= m.start() < b for a, b, _ in hits):
                hits.append((m.start(), m.end(), t))
    return sorted(hits)


def _strip_ids(q):
    """Bỏ phần '(identified by ...)' khi tìm thực thể/quan hệ để tránh nhầm 'county table', 'census_tract table'."""
    return re.sub(r"\([^)]*\)", " ", q)


def extract_intent(question, kg):
    q = question or ""
    it = Intent()
    low = q.lower()
    # ---- mã vùng ----
    for m in re.finditer(r"statefp\s*(?:=|is)?\s*'?(\d{2})\b", low):
        it.states.add(m.group(1))
    for w, code in STATE_WORDS.items():
        if re.search(r"\b" + w + r"\b", low if w not in ("la", "tx", "fl") else q.lower()):
            if w in ("la", "tx", "fl") and not re.search(r"\b" + w.upper() + r"\b", q):
                continue
            it.states.add(code)
    for m in re.finditer(r"(?:geoid|countyfips|fips)[^0-9']{0,40}'?(\d{5})(\d*)'?", low):
        it.counties.add(m.group(1))
    cf = re.findall(r"countyfp\s*(?:=|is)?\s*'?(\d{3})\b", low)
    for c in cf:
        for s in (it.states or set()):
            it.counties.add(s + c)
    # tên quận: "<Tên> County" / "<Tên> Parish"
    for m in re.finditer(r"((?:[A-Z][\w\.\-']*\s){1,3})(?:County|Parish)\b", q):
        name = m.group(1).strip()
        for n in kg.county_code(name):
            if not it.states or n["statefp"] in it.states:
                it.counties.add(n["code"])
    # ---- thực thể, quan hệ ----
    body = _strip_ids(q)
    bl = body.lower()
    it.entities = [(a, t) for a, _, t in _find_entities(body)]
    for k, rx in REL_RE.items():
        if re.search(rx, bl):
            it.relations.add(k)
    # câu hỏi đếm: "how many X", và cả "number of X", "count of X", "the most X", "fewest X" (vd "which county has the most schools")
    m = re.search(r"\b(?:how many|(?:largest|highest|greatest|smallest|lowest|total)?\s*(?:number|count) of|the most|fewest|the least number of)\s+(?:distinct\s+|unique\s+)?(?:pairs?\b)?", bl)
    if m:
        it.count_pairs = "pair" in m.group(0)
        after = [t for a, t in it.entities if a >= m.end()]
        it.count_entity = after[0] if after else None
    if re.search(r"\barea\b", bl):
        if re.search(r"not just|entire|whole|full area", low):
            it.area_whole = True
        if re.search(r"overlap\w*\s+(?:\w+\s+){0,3}area|area\s+(?:of|where)\s+(?:\w+\s+){0,6}(?:overlap|intersect)|intersect\w*\s+(?:\w+\s+){0,3}area|largest\s+(?:\w+\s+){0,3}area|portion|percentage of (?:each )?\w+[’']?s? (?:total )?area", bl):
            it.area_intersection = True
    # ---- chiều chứa (đa giác–đa giác) ----
    for k in ("contain", "within"):
        m = re.search(REL_RE[k], bl)
        if not m:
            continue
        before = [t for a, t in it.entities if a < m.start() and t in POLY]
        after = [t for a, t in it.entities if a > m.start() and t in POLY]
        if before and after and before[-1] != after[0]:
            it.containment = (before[-1], after[0]) if k == "contain" else (after[0], before[-1])
        break
    it.units = bool(re.search(r"\b(km|kilomet|mile|meter|metre|feet|acre|hectare|square)\b", bl))
    return it


# ----------------------------------------------------------------------------------------------
def _sql_codes(tree, ctx):
    """Mã bang/quận dùng trong SQL: trả về (states, counties, nodes)."""
    states, counties, where = set(), set(), {}
    stlit = None
    for c in tree.find_all(exp.EQ):
        if isinstance(c.this, exp.Column) and c.this.name.upper() == "STATEFP" and isinstance(c.expression, exp.Literal):
            stlit = c.expression.name
    for c in list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.Like)):
        if not isinstance(c.expression, exp.Literal) or not c.expression.is_string:
            continue
        v = c.expression.name.rstrip("%")
        col = c.this
        name = col.name.upper() if isinstance(col, exp.Column) else ("LEFT" if (isinstance(col, exp.Left) or _fname(col) == "left") else "")
        if name == "LEFT":
            inner = next(col.find_all(exp.Column), None)
            name = inner.name.upper() if inner is not None else ""
        if not v.isdigit():
            continue
        if name == "STATEFP" and len(v) == 2:
            states.add(v); where[v] = c
        elif name == "COUNTYFP" and len(v) == 3 and stlit:
            counties.add(stlit + v); where[stlit + v] = c
        elif name in ("GEOID", "COUNTYFIPS") and len(v) >= 5:
            counties.add(v[:5]); states.add(v[:2]); where[v[:5]] = c; where.setdefault(v[:2], c)
        elif name in ("GEOID",) and len(v) == 2:
            states.add(v); where[v] = c
    return states, counties, where


def check_intent(sql, question, kg, intent=None):
    out = []
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return out
    it = intent or extract_intent(question, kg)
    ctx = Ctx(tree, kg)
    preds = [n for n in tree.walk() if _fname(n[0] if isinstance(n, tuple) else n) in PREDICATES]
    preds = [p[0] if isinstance(p, tuple) else p for p in preds]

    # ---- K1: mã vùng ----
    s_states, s_counties, where = _sql_codes(tree, ctx)
    if it.states and s_states - it.states:
        for s in s_states - it.states:
            out.append(Finding("K1", "error", f"SQL lọc bang '{s}' nhưng câu hỏi nói bang {sorted(it.states)}",
                               where[s].sql(dialect="duckdb"), {"source": "intent", "expected": sorted(it.states)}))
    if it.counties and s_counties - it.counties:
        for c in s_counties - it.counties:
            out.append(Finding("K1", "error", f"SQL lọc quận '{c}' nhưng câu hỏi nói quận {sorted(it.counties)}",
                               where[c].sql(dialect="duckdb"), {"source": "intent", "expected": sorted(it.counties)}))

    # ---- K4: câu hỏi yêu cầu loại trừ giá trị ----
    m = re.search(r"exclud\w*\s+([^)—\n]*)", question or "", re.I)
    if m:
        vals = re.findall(r"'([^']+)'", m.group(1))
        if vals:
            neg = " ".join(n.sql(dialect="duckdb").upper() for n in tree.find_all(exp.Not, exp.NEQ))
            miss = [v for v in vals if f"'{v.upper()}'" not in neg]
            if miss:
                out.append(Finding("K4", "error", f"câu hỏi yêu cầu loại {miss} nhưng SQL không loại", "", {"source": "intent"}))

    # ---- S1: loại quan hệ ----
    # Mâu thuẫn RÕ (error): hàm SQL loại trừ đúng điều câu hỏi nói.
    #   within/contain  vs touches, overlaps   (touches: không chung phần trong; overlaps: không hình nào chứa hình kia)
    #   overlap         vs touches, contains, within
    #   touch           vs contains, within, overlaps
    #   intersect       vs touches, overlaps, contains, within   (đa giác–đa giác: hàm hẹp hơn nghĩa câu hỏi)
    # Mâu thuẫn LỎNG (warn): cách nói tự nhiên không chặt, vd "within floodplain regions" + intersects.
    # Điểm–đa giác: intersects ≈ contains ≈ within (chỉ khác khi điểm nằm đúng trên biên) -> coi như nhau.
    HARD = {"within": {"st_touches", "st_overlaps"}, "contain": {"st_touches", "st_overlaps"},
            "overlap": {"st_touches", "st_contains", "st_within", "st_covers", "st_coveredby"},
            "touch": {"st_contains", "st_within", "st_overlaps", "st_covers", "st_coveredby"},
            "intersect": {"st_touches", "st_overlaps", "st_contains", "st_within", "st_covers", "st_coveredby"}}
    POINT_EQ = {"st_intersects", "st_contains", "st_within", "st_covers", "st_coveredby"}
    if it.relations and preds:
        allowed = set().union(*(REL[r] for r in it.relations))
        for p in preds:
            f = _fname(p)
            kinds = [geom_kind(a, ctx) for a in _args(p)[:2]]
            if f in allowed:
                continue
            if "POINT" in kinds:
                if f in POINT_EQ and it.relations & {"within", "contain", "intersect"}:
                    continue
                if f == "st_touches" and it.relations & {"within", "contain", "intersect"}:
                    out.append(Finding("S1", "error", "điểm 'chạm biên' (ST_TOUCHES) không khớp 'nằm trong/giao' trong câu hỏi",
                                       p.sql(dialect="duckdb"), {"source": "intent"}))
                continue
            # đa giác–đa giác: hàm SQL có bị MỌI quan hệ trong câu hỏi loại trừ không?
            hard = all(f in HARD.get(r, set()) for r in it.relations)
            out.append(Finding("S1", "error" if hard else "warn",
                               f"{f.upper()} không khớp quan hệ trong câu hỏi {sorted(it.relations)}",
                               p.sql(dialect="duckdb"), {"source": "intent", "allowed": sorted(allowed)}))

    # ---- S2: chiều chứa giữa 2 đa giác ----
    if it.containment:
        cont, inner = it.containment
        for p in preds:
            f = _fname(p)
            if f not in ("st_contains", "st_within", "st_covers", "st_coveredby"):
                continue
            a = _args(p)[:2]
            tabs = []
            for x in a:
                ts = {ctx.table_of(c)[0] for c in x.find_all(exp.Column)} - {None}
                tabs.append(next(iter(ts)) if len(ts) == 1 else None)
            if None in tabs or set(tabs) != {cont, inner}:
                continue
            container = tabs[0] if f in ("st_contains", "st_covers") else tabs[1]
            if container != cont:
                out.append(Finding("S2", "error", f"câu hỏi: {cont} chứa {inner}, nhưng SQL viết ngược chiều",
                                   p.sql(dialect="duckdb"), {"source": "intent"}))

    # ---- S5: diện tích phần giao / cả hình ----
    areas = [n for n in tree.find_all(exp.Anonymous) if n.name.lower() == "st_area"]
    if areas:
        has_inter = any(_fname(_args(a)[0]) == "st_intersection" for a in areas if _args(a))
        if it.area_intersection and not it.area_whole and not has_inter and preds:
            out.append(Finding("S5", "error", "câu hỏi hỏi diện tích phần giao nhưng SQL tính ST_Area của cả hình",
                               areas[0].sql(dialect="duckdb"), {"source": "intent"}))
        if it.area_whole and has_inter and not it.area_intersection:
            out.append(Finding("S5", "error", "câu hỏi hỏi diện tích cả hình nhưng SQL tính diện tích phần giao",
                               areas[0].sql(dialect="duckdb"), {"source": "intent"}))

    # ---- S9: đếm trùng ----
    if it.count_entity and not it.count_pairs and preds:
        sel = tree if isinstance(tree, exp.Select) else None
        top_counts = [c for c in (sel.expressions if sel else []) for c in c.find_all(exp.Count)]
        for c in top_counts:
            if isinstance(c.this, exp.Star):
                out.append(Finding("S9", "error", f"câu hỏi đếm {it.count_entity} nhưng SQL COUNT(*) sau nối không gian (có thể đếm trùng)",
                                   c.sql(dialect="duckdb"), {"source": "intent"}))
    return out


def detect(sql, question, kg, use_intent=True):
    """Bộ phát hiện đầy đủ = luật KG (checks.py) + đối chiếu câu hỏi."""
    from spatialrepair.detection.checks import check_sql
    fs = check_sql(sql, kg, question)
    if use_intent and kg.is_spatial:
        fs += check_intent(sql, question, kg)
    return fs
