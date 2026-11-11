"""Prompt cho LLM: sinh SQL ban đầu và 2 phương pháp đối chứng (tự sửa, sửa theo lỗi DuckDB).

Cấu trúc giống FloodSQL-Bench (system prompt + metadata của các bảng/cột liên quan + luật chung + câu hỏi).
Khác bài gốc: chọn bảng/cột bằng KHỚP TỪ KHÓA (không cần API embedding) -> lặp lại được, không tốn thêm chi phí.
Prompt KHÔNG chứa thông tin từ SQL chuẩn.
"""
import json, os, re

from spatialrepair import ROOT
META_PATH = os.path.join(ROOT, "data_source", "metadata", "metadata_parquet.json")

SYSTEM_PROMPT = (
    "You are an expert DuckDB Spatial SQL generator for the FloodSQL-Bench dataset (Texas, Florida, Louisiana). "
    "Tables are DuckDB views over Parquet files; spatial functions come from the DuckDB 'spatial' extension. "
    "Use only the tables and columns provided. Return exactly ONE DuckDB SQL query inside a ```sql``` code block, no explanation."
)

TABLE_HINTS = {
    "claims": "claim claims nfip payout payouts paid loss losses insurance building contents dateofloss",
    "census_tracts": "tract tracts census_tract census",
    "county": "county counties parish parishes",
    "zcta": "zcta zctas zip code tabulation",
    "floodplain": "floodplain floodplains fema flood hazard zone zones fld_zone",
    "hospitals": "hospital hospitals healthcare",
    "schools": "school schools college colleges",
    "nri": "nri risk index expected annual loss coastal riverine flood risk score rating cfld rfld",
    "svi": "svi social vulnerability percentile poverty minority uninsured elderly aged vehicle housing theme",
    "cre": "cre community resilience population popuni pred0 pred3 pred12 components water_tract",
}
FORCED_COLS = {"geoid", "statefp", "countyfp", "countyfips", "geometry", "lat", "lon", "zip", "unique_id", "name", "city", "fld_zone"}
STOP = set("the a an of in on for to and or by with what which how many is are was were that this these those each all "
           "return its their it as at from be have has than more most less least top identified".split())


def _toks(s):
    return [w for w in re.findall(r"[a-z0-9_]+", (s or "").lower()) if w not in STOP and len(w) > 1]


class PromptBuilder:
    def __init__(self, meta_path=META_PATH, k_tables=4, k_cols=12, desc_chars=160):
        self.meta = json.load(open(meta_path, encoding="utf-8"))
        self.k_tables, self.k_cols, self.desc_chars = k_tables, k_cols, desc_chars
        g = self.meta["_global"]
        self.global_text = "\n".join([
            "## Join rules", json.dumps(g["join_rules"], ensure_ascii=False),
            "## Rules", *[f"- {k}: {v}" for k, v in g["rules"].items()],
            "## Notes", *[f"- {n}" for n in g["notes"]],
            "## Multi-table notes", *[f"- {n}" for n in g["triple_table_notes"]],
            "## Spatial function notes", *[f"- {n}" for n in g["spatial_function_notes"]],
        ])

    def select(self, question):
        q = set(_toks(question))
        scores = {}
        for t, v in self.meta.items():
            if t == "_global":
                continue
            words = set(_toks(TABLE_HINTS.get(t, "") + " " + t))
            s = 3 * len(q & words)
            s += 0.2 * len(q & set(_toks(" ".join((c["column_name"] + " " + (c.get("description") or "")) for c in v["schema"]))))
            scores[t] = s
        chosen = [t for t, s in sorted(scores.items(), key=lambda x: -x[1]) if s > 0][: self.k_tables]
        return chosen or ["census_tracts"]

    def table_text(self, t, question):
        v = self.meta[t]
        q = set(_toks(question))
        cols = v["schema"]
        if len(cols) > 20:
            scored = sorted(cols, key=lambda c: -len(q & set(_toks(c["column_name"] + " " + (c.get("description") or "")))))
            keep = [c for c in cols if c["column_name"].lower() in FORCED_COLS] + scored[: self.k_cols]
            seen, cols = set(), []
            for c in keep:
                if c["column_name"] not in seen:
                    seen.add(c["column_name"]); cols.append(c)
        lines = [f"### Table `{t}` ({v.get('row_count')} rows) — {(v.get('_meta') or '')[:300]}"]
        for c in cols:
            lines.append(f"- {c['column_name']} {c['column_type']}: {(c.get('description') or '')[: self.desc_chars]}")
        return "\n".join(lines)

    def context(self, question):
        tabs = self.select(question)
        return "# Candidate tables and columns\n" + "\n\n".join(self.table_text(t, question) for t in tabs) + \
               "\n\n# Global rules\n" + self.global_text

    # ---------------- các loại tin nhắn ----------------
    def gen_messages(self, question):
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": self.context(question)},
                {"role": "user", "content": f"Question:\n{question}\n\nReturn only a single valid DuckDB SQL query."}]

    def self_refine_messages(self, question, sql):
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": self.context(question)},
                {"role": "user", "content":
                    f"Question:\n{question}\n\nCandidate SQL:\n```sql\n{sql}\n```\n\n"
                    "Check whether the candidate SQL correctly answers the question given the schema and rules "
                    "(tables, joins, spatial predicates and argument order, coordinate order, filters, codes, aggregation). "
                    "If it is correct, return it unchanged. Otherwise return the corrected query. "
                    "Return only one SQL query in a ```sql``` block."}]

    def exec_fix_messages(self, question, sql, error):
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": self.context(question)},
                {"role": "user", "content":
                    f"Question:\n{question}\n\nThe following SQL failed in DuckDB:\n```sql\n{sql}\n```\n"
                    f"Error message:\n{error}\n\nFix the query. Return only one SQL query in a ```sql``` block."}]
