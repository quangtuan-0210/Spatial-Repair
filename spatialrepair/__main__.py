"""Dòng lệnh của SpatialRepair:  python -m spatialrepair <lệnh> ...

  detect   : phát hiện lỗi trong 1 câu SQL (không cần dữ liệu)
  repair   : sửa 1 câu SQL (thêm --run để chạy thử ứng viên trên DuckDB, cần file parquet FloodSQL-Bench)
  show     : xem kết quả thực nghiệm đã chạy (results/exp/<kho>/summary.md)

Có thể lấy câu hỏi + SQL ban đầu theo mã câu (--qid) từ một kho SQL trong bench/, vd:
  python -m spatialrepair repair --qid L4_0006
  python -m spatialrepair repair --question "How many schools ..." --sql "SELECT ..."
"""
import argparse, json, os, sys

from spatialrepair import ROOT


def _load_kg(variant):
    from spatialrepair.knowledge.kg import KG
    return KG(os.path.join(ROOT, "kg", f"kg_{variant}.json"))


def _resolve(a):
    """Lấy (câu hỏi, SQL) từ tham số hoặc từ kho SQL theo --qid."""
    if a.qid:
        gold = {}
        for line in open(os.path.join(ROOT, "data_source", "benchmark", "floodsql_443.jsonl"), encoding="utf-8"):
            r = json.loads(line); gold[r["id"]] = r
        if a.qid not in gold:
            sys.exit(f"Không có câu {a.qid} trong FloodSQL-Bench")
        question = a.question or gold[a.qid]["question"]
        sql = a.sql
        if not sql:
            store = os.path.join(ROOT, "bench", f"initial_{a.store}.jsonl")
            for line in open(store, encoding="utf-8"):
                r = json.loads(line)
                if r["qid"] == a.qid:
                    sql = r["sql"]; break
        if not sql:
            sys.exit(f"Không tìm thấy SQL của {a.qid} trong bench/initial_{a.store}.jsonl")
        return question, sql, gold[a.qid]["sql"]
    if not (a.sql and a.question):
        sys.exit("Cần --qid, hoặc cả --sql và --question")
    return a.question, a.sql, None


def cmd_detect(a):
    from spatialrepair.detection.intent import detect
    question, sql, _ = _resolve(a)
    fs = detect(sql, question, _load_kg(a.kg), use_intent=not a.no_question)
    print(f"Câu hỏi: {question}\nSQL:\n{sql}\n")
    if not fs:
        print("Không phát hiện lỗi.")
    for f in fs:
        print(f"[{f.level:7s}] {f.code:3s} {f.msg}\n          tại: {f.node[:120]}")


def cmd_repair(a):
    from spatialrepair.repair import repair
    question, sql, gold = _resolve(a)
    executor = None
    if a.run:
        from spatialrepair.core.db import connect, run_sql
        con, missing = connect()
        if missing:
            print(f"! Thiếu {len(missing)} file parquet (xem data_source/paths.json hoặc PARQUET_DIR trong .env) -> sửa không chạy thử.\n")
        else:
            executor = lambda s: run_sql(con, s, a.timeout)   # noqa: E731
    r = repair(sql, question, _load_kg(a.kg), use_intent=not a.no_question, executor=executor)
    print(f"Câu hỏi: {question}\n\nSQL ban đầu:\n{sql}\n")
    if r.findings:
        print("Lỗi phát hiện:")
        for f in r.findings:
            print(f"  {f['code']:3s} {f['msg']}")
        print()
    if r.changed:
        print("Các phép sửa:\n  " + "\n  ".join(r.edits))
        print(f"\nSQL sau sửa:\n{r.sql_out}")
    else:
        print(f"Không đổi: {r.reason}")
        if r.remaining:
            print("Lỗi còn lại: " + "; ".join(r.remaining))
    if gold and a.show_gold:
        print(f"\nSQL chuẩn (để so):\n{gold}")


def cmd_show(a):
    p = os.path.join(ROOT, "results", "exp", a.store, "summary.md")
    if not os.path.exists(p):
        sys.exit(f"Chưa có {p}")
    print(open(p, encoding="utf-8").read())


def main():
    ap = argparse.ArgumentParser(prog="python -m spatialrepair", description="SpatialRepair: sửa lỗi Text-to-Spatial SQL bằng KG + AST")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("detect", "repair"):
        p = sub.add_parser(name)
        p.add_argument("--qid", help="mã câu FloodSQL-Bench, vd L4_0006")
        p.add_argument("--store", default="qwen35", help="kho SQL ban đầu trong bench/ (qwen35, mut, sim)")
        p.add_argument("--sql"); p.add_argument("--question")
        p.add_argument("--kg", default="spatial", choices=["spatial", "relational"])
        p.add_argument("--no-question", action="store_true", help="không đối chiếu câu hỏi (chỉ luật KG)")
        if name == "repair":
            p.add_argument("--run", action="store_true", help="chạy thử ứng viên trên DuckDB (cần parquet)")
            p.add_argument("--timeout", type=int, default=60)
            p.add_argument("--show-gold", action="store_true", help="in kèm SQL chuẩn khi dùng --qid")
    p = sub.add_parser("show"); p.add_argument("--store", default="qwen35")
    a = ap.parse_args()
    {"detect": cmd_detect, "repair": cmd_repair, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
