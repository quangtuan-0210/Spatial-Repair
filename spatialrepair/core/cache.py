"""Cache kết quả chạy SQL (sqlite, thư viện chuẩn của Python).
Mỗi câu SQL (đã chuẩn hóa khoảng trắng) chỉ chạy trên DuckDB đúng 1 lần; lần sau lấy từ cache.
Dùng chung cho: sinh đột biến, chạy các phương pháp sửa, chấm điểm.
"""
import hashlib, json, os, re, sqlite3, time

from spatialrepair import ROOT
DEFAULT_PATH = os.path.join(ROOT, "results", "sql_cache.sqlite")
MAX_ROWS = 20000  # kết quả lớn hơn thì chỉ lưu trạng thái + số dòng


def sql_key(sql):
    norm = re.sub(r"\s+", " ", (sql or "").strip().rstrip(";")).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


class SQLCache:
    def __init__(self, path=DEFAULT_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs(
            key TEXT PRIMARY KEY, sql TEXT, status TEXT, error TEXT, elapsed REAL,
            row_count INTEGER, rows TEXT, timeout INTEGER, created REAL)""")
        self.db.commit()
        self.hits = self.misses = 0

    def get(self, sql):
        r = self.db.execute("SELECT status,error,elapsed,row_count,rows,timeout FROM runs WHERE key=?", (sql_key(sql),)).fetchone()
        if not r:
            return None
        status, error, elapsed, n, rows, timeout = r
        return {"status": status, "error": error, "elapsed": elapsed, "row_count": n,
                "rows": json.loads(rows) if rows is not None else None, "timeout": timeout, "cached": True}

    def put(self, sql, res, timeout):
        rows = res.get("rows")
        n = None if rows is None else len(rows)
        payload = json.dumps(rows, ensure_ascii=False, default=str) if rows is not None and n <= MAX_ROWS else None
        self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                        (sql_key(sql), sql, res["status"], res.get("error"), res.get("elapsed"), n, payload, timeout, time.time()))
        self.db.commit()

    def run(self, con, sql, timeout=120):
        """Chạy có cache. Nếu lần trước timeout với giới hạn nhỏ hơn thì chạy lại với giới hạn mới."""
        from spatialrepair.core.db import run_sql
        hit = self.get(sql)
        if hit and not (hit["status"] == "timeout" and (hit["timeout"] or 0) < timeout):
            if hit["status"] != "ok" or hit["rows"] is not None:
                self.hits += 1
                return hit
        self.misses += 1
        res = con.run(sql, timeout) if hasattr(con, "run") else run_sql(con, sql, timeout)   # SafeRunner hoặc con thường
        if res["status"] == "ok":
            res["rows"] = json.loads(json.dumps(res["rows"], default=str))
        self.put(sql, res, timeout)
        res["cached"] = False
        res["row_count"] = None if res["rows"] is None else len(res["rows"])
        return res
