"""Lớp gọi LLM dùng chung. Chỉ dùng thư viện chuẩn (urllib), không cần cài thêm.

Hỗ trợ:
  backend "openai" : mọi API theo chuẩn OpenAI /v1/chat/completions
                      (OpenAI, DeepSeek, Qwen/DashScope, Groq, OpenRouter, vLLM, Ollama, llama.cpp server, LM Studio...)
  backend "mock"   : LLM GIẢ để chạy thử toàn bộ hệ thống khi chưa có LLM thật
                      mode "echo"  : trả lại nguyên câu SQL có trong tin nhắn (không sửa gì)
                      mode "empty" : trả lời rỗng (mô phỏng LLM lỗi)
Cấu hình: file .env ở thư mục gốc (xem .env.example). File .env đã nằm trong .gitignore -> khóa API không bị đưa lên git.
"""
import json, os, re, time, urllib.request, urllib.error

from spatialrepair import ROOT


def load_config(env_path=None):
    """Đọc cấu hình LLM từ file .env (mặc định: <thư mục gốc>/.env; có thể chỉ file khác, vd .env.qwen).
    Các biến: LLM_BACKEND, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_TEMPERATURE, LLM_MAX_TOKENS, LLM_TIMEOUT,
              LLM_RETRIES, LLM_MOCK_MODE, LLM_EXTRA_BODY (JSON gửi kèm mỗi request, vd tắt chế độ suy nghĩ của Qwen3). (Vẫn đọc được file .json kiểu cũ nếu đưa đường dẫn .json.)"""
    from spatialrepair.core.config import load_env, env
    if env_path and env_path.endswith(".json"):
        cfg = json.load(open(env_path, encoding="utf-8"))
        key = cfg.get("api_key", "")
        if key.startswith("env:"):
            cfg["api_key"] = os.environ.get(key[4:], "")
        return cfg
    if env_path:
        load_env(env_path, override=True)
    cfg = {"backend": env("LLM_BACKEND", "mock"), "base_url": env("LLM_BASE_URL", ""), "model": env("LLM_MODEL", "mock"),
           "api_key": env("LLM_API_KEY", ""), "temperature": env("LLM_TEMPERATURE", 0.0, float),
           "max_tokens": env("LLM_MAX_TOKENS", 1024, int), "timeout": env("LLM_TIMEOUT", 120, int),
           "retries": env("LLM_RETRIES", 3, int), "mock_mode": env("LLM_MOCK_MODE", "echo")}
    extra = env("LLM_EXTRA_BODY", "")
    if extra:
        try:
            cfg["extra_body"] = json.loads(extra)
        except json.JSONDecodeError:
            raise SystemExit("LLM_EXTRA_BODY trong .env không phải JSON hợp lệ")
    if cfg["backend"] == "openai" and not cfg["base_url"]:
        raise SystemExit("Thiếu LLM_BASE_URL trong file .env")
    return cfg


def _approx_tokens(text):
    return max(1, len(text or "") // 4)


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.backend = cfg.get("backend", "openai")
        self.model = cfg.get("model", "mock")
        self.calls = 0
        self.tokens_in = self.tokens_out = 0

    def chat(self, messages, temperature=None, max_tokens=None):
        """Trả về dict: text, tokens_in, tokens_out, latency, error."""
        t0 = time.time()
        if self.backend == "mock":
            text = self._mock(messages)
            tin = sum(_approx_tokens(m["content"]) for m in messages); tout = _approx_tokens(text)
            res = {"text": text, "tokens_in": tin, "tokens_out": tout, "latency": time.time() - t0, "error": None}
        else:
            res = self._openai(messages, temperature, max_tokens)
            res["latency"] = time.time() - t0
        self.calls += 1
        self.tokens_in += res["tokens_in"]; self.tokens_out += res["tokens_out"]
        return res

    def _mock(self, messages):
        mode = self.cfg.get("mock_mode", "echo")
        if mode == "empty":
            return ""
        last = messages[-1]["content"]
        m = [x for x in re.findall(r"```sql\s*(.*?)```", last, re.S | re.I) if x.strip()]
        return f"```sql\n{m[0].strip()}\n```" if m else "```sql\nSELECT 1\n```"

    @staticmethod
    def _merge_system(messages):
        sys_parts = [m["content"] for m in messages if m["role"] == "system"]
        rest = [m for m in messages if m["role"] != "system"]
        return ([{"role": "system", "content": "\n\n".join(sys_parts)}] if sys_parts else []) + rest

    def _openai(self, messages, temperature, max_tokens):
        messages = self._merge_system(messages)
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        body = {"model": self.model, "messages": messages,
                "temperature": self.cfg.get("temperature", 0.0) if temperature is None else temperature,
                "max_tokens": max_tokens or self.cfg.get("max_tokens", 1024)}
        body.update(self.cfg.get("extra_body", {}))
        headers = {"Content-Type": "application/json"}
        if self.cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {self.cfg['api_key']}"
        last_err = None
        for attempt in range(self.cfg.get("retries", 3)):
            try:
                req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.cfg.get("timeout", 120)) as r:
                    d = json.loads(r.read().decode())
                msg = d["choices"][0]["message"]
                text = msg.get("content") or ""
                if not text.strip() and d["choices"][0].get("finish_reason") == "stop":
                    text = msg.get("reasoning_content") or ""
                u = d.get("usage") or {}
                return {"text": text,
                        "tokens_in": u.get("prompt_tokens") or sum(_approx_tokens(m["content"]) for m in messages),
                        "tokens_out": u.get("completion_tokens") or _approx_tokens(text), "error": None}
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as e:
                last_err = str(e)
                time.sleep(2 * (attempt + 1))
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "error": last_err}


def extract_sql(text):
    """Lấy câu SQL từ câu trả lời của LLM (khối ```sql ...```, hoặc đoạn bắt đầu bằng SELECT/WITH)."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)       # bỏ phần "suy nghĩ" của model lý luận
    blocks = [b for b in re.findall(r"```(?:sql|duckdb)?\s*(.*?)```", text, re.S | re.I) if re.search(r"\b(select|with)\b", b, re.I)]
    if blocks:
        return blocks[-1].strip().rstrip(";") + ";"
    m = re.search(r"\b(WITH|SELECT)\b.*", text, re.S | re.I)
    return (m.group(0).strip().split("\n\n")[0].rstrip(";") + ";") if m else ""
