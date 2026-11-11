"""6 phương pháp sửa so sánh trong thực nghiệm (theo đề cương mục 5) + 1 biến thể tùy chọn (kg_spatial_nointent).

Mỗi phương pháp: run(question, sql) -> dict(sql_out, changed, abstained, rounds, calls, tokens_in, tokens_out, latency, note)
  rounds = số vòng sửa thực sự đã làm (LLM: số lần gọi; luật/KG: số phép sửa áp dụng)
  none          : Không sửa                                  (đo cải thiện tổng thể)
  self_refine   : LLM tự kiểm tra và sinh lại                 (so với tự sửa)
  exec_feedback : LLM sửa bằng thông báo lỗi DuckDB           (so với sửa dựa trên lỗi database)
  rules_direct  : AST + luật không gian viết tay (không KG)   (giá trị bổ sung của KG)
  kg_relational : AST + KG chỉ lược đồ quan hệ                (giá trị của tri thức không gian)
  spatialrepair : AST + KG không gian đầy đủ + câu hỏi        (phương pháp đề xuất)
Ngân sách LLM: self_refine `rounds` lần gọi; exec_feedback tối đa `rounds` lần gọi (chỉ gọi khi SQL lỗi).
"""
import time
from spatialrepair.baselines.llm import extract_sql


class Method:
    name = "base"
    uses_llm = False

    def run(self, question, sql):
        raise NotImplementedError


def _res(sql_in, sql_out, t0, calls=0, tin=0, tout=0, abstained=False, note="", rounds=None):
    norm = lambda x: " ".join((x or "").strip().rstrip(";").split())   # noqa: E731
    return {"sql_out": sql_out, "changed": norm(sql_out) != norm(sql_in), "abstained": abstained,
            "rounds": calls if rounds is None else rounds, "calls": calls, "tokens_in": tin, "tokens_out": tout, "latency": round(time.time() - t0, 3), "note": note}


class NoRepair(Method):
    name = "none"

    def run(self, question, sql):
        return _res(sql, sql, time.time())


class RulesDirect(Method):
    name = "rules_direct"

    def run(self, question, sql):
        from spatialrepair.baselines.rules_direct import rules_direct
        t0 = time.time()
        out, applied = rules_direct(sql)
        return _res(sql, out, t0, note=",".join(applied), rounds=len(applied))


class KGRepair(Method):
    def __init__(self, kg, use_intent, name, executor=None):
        self.kg, self.use_intent, self.name, self.executor = kg, use_intent, name, executor

    def run(self, question, sql):
        from spatialrepair.repair import repair
        t0 = time.time()
        r = repair(sql, question, self.kg, use_intent=self.use_intent, executor=self.executor)
        return _res(sql, r.sql_out, t0, abstained=r.abstained, note="; ".join(r.edits) or r.reason,
                    rounds=len(r.edits) if r.changed else 0)


class SelfRefine(Method):
    name = "self_refine"
    uses_llm = True

    def __init__(self, llm, prompts, rounds=1):
        self.llm, self.pb, self.rounds = llm, prompts, rounds

    def run(self, question, sql):
        t0 = time.time(); cur = sql; calls = tin = tout = 0; note = ""
        for _ in range(self.rounds):
            r = self.llm.chat(self.pb.self_refine_messages(question, cur))
            calls += 1; tin += r["tokens_in"]; tout += r["tokens_out"]
            new = extract_sql(r["text"])
            if r["error"] or not new:
                note = "llm_error" if r["error"] else "no_sql"; break
            if new.strip().rstrip(";") == cur.strip().rstrip(";"):
                break
            cur = new
        return _res(sql, cur, t0, calls, tin, tout, note=note)


class ExecFeedback(Method):
    name = "exec_feedback"
    uses_llm = True

    def __init__(self, llm, prompts, executor, rounds=2):
        self.llm, self.pb, self.exe, self.rounds = llm, prompts, executor, rounds

    def run(self, question, sql):
        t0 = time.time(); cur = sql; calls = tin = tout = 0; note = ""
        for _ in range(self.rounds):
            ex = self.exe(cur)
            if ex["status"] == "ok":
                break
            r = self.llm.chat(self.pb.exec_fix_messages(question, cur, (ex.get("error") or ex["status"])[:800]))
            calls += 1; tin += r["tokens_in"]; tout += r["tokens_out"]
            new = extract_sql(r["text"])
            if r["error"] or not new:
                note = "llm_error" if r["error"] else "no_sql"; break
            cur = new
        return _res(sql, cur, t0, calls, tin, tout, note=note)


def build_methods(names, kg_spatial=None, kg_relational=None, llm=None, prompts=None, executor=None, rounds=None):
    rounds = rounds or {}
    out = []
    for n in names:
        if n == "none": out.append(NoRepair())
        elif n == "rules_direct": out.append(RulesDirect())
        # bộ sửa KG được chạy thử ứng viên bằng DuckDB (executor): chọn ứng viên chạy được, và nhận biết lỗi chỉ lộ ra khi
        # chạy (TopologyException -> S7). Không gọi LLM.
        elif n == "kg_relational": out.append(KGRepair(kg_relational, False, "kg_relational", executor))
        elif n == "kg_spatial_nointent": out.append(KGRepair(kg_spatial, False, "kg_spatial_nointent", executor))
        elif n == "spatialrepair": out.append(KGRepair(kg_spatial, True, "spatialrepair", executor))
        elif n == "self_refine": out.append(SelfRefine(llm, prompts, rounds.get("self_refine", 1)))
        elif n == "exec_feedback": out.append(ExecFeedback(llm, prompts, executor, rounds.get("exec_feedback", 2)))
        else: raise ValueError(f"không biết phương pháp: {n}")
    return out
