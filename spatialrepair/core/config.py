"""Đọc file .env (KEY=VALUE) — không cần thư viện ngoài.
Biến đã có sẵn trong môi trường KHÔNG bị ghi đè (để có thể `set LLM_API_KEY=...` trong cmd).
"""
import os

_loaded = {}


def load_env(path=None, override=False):
    from spatialrepair import ROOT
    path = path or os.path.join(ROOT, ".env")
    vals = {}
    if not os.path.exists(path):
        return vals
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if "#" in v and not v.startswith(("'", '"')):
            v = v.split(" #")[0].strip()
        v = v.strip('"').strip("'")
        vals[k] = v
        if override or k not in os.environ:
            os.environ[k] = v
    _loaded[path] = vals
    return vals


def env(key, default=None, cast=str):
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    try:
        return cast(v)
    except ValueError:
        return default
