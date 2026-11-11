"""Kết nối DuckDB + spatial và đăng ký 10 bảng FloodSQL-Bench thành VIEW trên file parquet.
Cách nạp giống hệt script gốc của tác giả (benchmark/*/run_*.py) để kết quả khớp gold_result.
"""
import json, os, threading, time

from spatialrepair import ROOT
PATHS_FILE = os.path.join(ROOT, "data_source", "paths.json")


def load_paths(parquet_dir=None):
    """Đường dẫn parquet: tham số > biến PARQUET_DIR trong .env > data_source/paths.json."""
    cfg = json.load(open(PATHS_FILE, encoding="utf-8"))
    pd = parquet_dir or os.environ.get("PARQUET_DIR")
    if pd:
        cfg["parquet_dir"] = pd
    return cfg


def connect(parquet_dir=None, threads=None, memory_limit=None):
    import duckdb
    cfg = load_paths(parquet_dir)
    threads = threads or os.environ.get("DUCKDB_THREADS") or None
    memory_limit = memory_limit or os.environ.get("DUCKDB_MEMORY_LIMIT") or None
    con = duckdb.connect()
    con.execute("INSTALL spatial;")
    con.execute("LOAD spatial;")
    if threads:
        con.execute(f"SET threads={int(threads)};")
    if memory_limit:
        con.execute(f"SET memory_limit='{memory_limit}';")
    missing = []
    for view, fname in cfg["tables"].items():
        fpath = os.path.join(cfg["parquet_dir"], fname).replace("\\", "/")
        if os.path.exists(fpath):
            con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM '{fpath}'")
        else:
            missing.append(fpath)
    return con, missing


def run_sql(con, sql, timeout_s=60):
    """Chạy 1 câu SQL có giới hạn thời gian (chạy được cả trên Windows).
    Trả về dict: status = ok | error | timeout, rows, error, elapsed."""
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    t0 = time.time()
    try:
        rows = con.execute(sql).fetchall()
        return {"status": "ok", "rows": rows, "error": None, "elapsed": round(time.time() - t0, 3)}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        status = "timeout" if "interrupt" in msg.lower() else "error"
        return {"status": status, "rows": None, "error": msg, "elapsed": round(time.time() - t0, 3)}
    finally:
        timer.cancel()


# ---------------------------------------------------------------------------------------------
# Chạy SQL trong TIẾN TRÌNH RIÊNG — dừng chắc chắn khi quá thời gian.
# Lý do: con.interrupt() chỉ là "nhắc" DuckDB dừng; một số hàm hình học nặng (vd ST_MakeValid trên
# floodplain) không kiểm tra lời nhắc -> câu SQL chạy hàng giờ. SafeRunner tắt hẳn tiến trình và mở lại.
# ---------------------------------------------------------------------------------------------
def _worker(conn, parquet_dir, threads, memory_limit):
    con, missing = connect(parquet_dir, threads, memory_limit)
    conn.send(("ready", missing))
    while True:
        try:
            sql = conn.recv()
        except EOFError:
            break
        if sql is None:
            break
        t0 = time.time()
        try:
            rows = con.execute(sql).fetchall()
            rows = json.loads(json.dumps(rows, default=str))
            conn.send({"status": "ok", "rows": rows, "error": None, "elapsed": round(time.time() - t0, 3)})
        except Exception as e:  # noqa: BLE001
            conn.send({"status": "error", "rows": None, "error": str(e), "elapsed": round(time.time() - t0, 3)})


class SafeRunner:
    """Giống connect() + run_sql() nhưng mỗi câu SQL chạy trong 1 tiến trình con.
    Quá `timeout` giây -> tắt hẳn tiến trình con, mở tiến trình mới, trả status='timeout'.
    Dùng: r = SafeRunner(); r.run(sql, timeout=120); r.close()
    Script dùng SafeRunner phải có `if __name__ == "__main__":` (yêu cầu của Windows)."""

    def __init__(self, parquet_dir=None, threads=None, memory_limit=None):
        self.args = (parquet_dir, threads, memory_limit)
        self.proc = None
        self.missing = []
        self.restarts = 0
        self._start()

    def _start(self):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child, *self.args), daemon=True)
        self.proc.start()
        child.close()
        _, self.missing = self.conn.recv()

    def _restart(self):
        try:
            self.proc.kill()
            self.proc.join(10)
        except Exception:  # noqa: BLE001
            pass
        self.restarts += 1
        self._start()

    def run(self, sql, timeout=120):
        if self.proc is None or not self.proc.is_alive():
            self._restart()
        t0 = time.time()
        self.conn.send(sql)
        if self.conn.poll(timeout):
            try:
                return self.conn.recv()
            except (EOFError, OSError) as e:
                self._restart()
                return {"status": "error", "rows": None, "error": f"tiến trình DuckDB bị dừng đột ngột: {e}",
                        "elapsed": round(time.time() - t0, 3)}
        self._restart()
        return {"status": "timeout", "rows": None, "error": f"quá {timeout}s - đã tắt hẳn tiến trình DuckDB",
                "elapsed": round(time.time() - t0, 3)}

    def close(self):
        try:
            self.conn.send(None)
            self.proc.join(5)
        except Exception:  # noqa: BLE001
            pass
        if self.proc is not None and self.proc.is_alive():
            self.proc.kill()

    # để dùng được ở chỗ trước đây truyền `con`
    def interrupt(self):
        pass
