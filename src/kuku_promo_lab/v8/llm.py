"""Provider-agnostic chat client for any OpenAI-compatible endpoint.

Swapping models is a config change: Gemini, OpenAI, OpenRouter, Groq, a local
llama.cpp server, or an API Kuku provides all speak the same
`/chat/completions` protocol. Every request and raw response is written to
disk unedited, and identical requests are served from a content-addressed
cache so a rerun is free and an interrupted run resumes where it stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


class LLMError(RuntimeError):
    pass


def load_dotenv(path=".env"):
    """Read KEY=VALUE lines into os.environ without overriding real env vars."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def extract_json(text):
    """Parse a JSON object from a model reply, tolerating ``` fences or prose."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


class RateLimiter:
    def __init__(self, per_minute):
        self.interval = 60.0 / per_minute if per_minute else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if delay:
            time.sleep(delay)


class ChatClient:
    """Minimal JSON-mode chat client with caching, model fallback, retries and full logging."""

    def __init__(self, cfg, run_dir):
        self.cfg = cfg
        self.base_url = os.environ.get("KUKU_BASE_URL", cfg["base_url"]).rstrip("/")
        self.model = os.environ.get("KUKU_MODEL", cfg["model"])
        self.models = [self.model] + [m for m in cfg.get("fallback_models", []) if m != self.model]
        key_env = cfg.get("api_key_env")
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not self.api_key:
            raise LLMError(f"Set {key_env} (environment or .env) for {self.base_url}")
        self.calls_dir = Path(run_dir) / "model_calls"
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = Path(cfg.get("cache_dir", "private/cache-v8"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter(cfg.get("requests_per_minute", 0))
        self.lock = threading.Lock()
        self._model_idx = 0
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "total_tokens": 0, "seconds": 0.0, "models_used": {}}

    def _keys(self, role, system, user, temperature):
        primary = hashlib.sha256(json.dumps([self.model, role, system, user, temperature], ensure_ascii=False).encode()).hexdigest()[:24]
        generic = hashlib.sha256(json.dumps(["v8", role, system, user, temperature], ensure_ascii=False).encode()).hexdigest()[:24]
        return primary, generic

    def has_cached(self, role, system, user, temperature=None):
        temperature = self.cfg.get("temperature", 0.4) if temperature is None else temperature
        for k in self._keys(role, system, user, temperature):
            if (self.cache_dir / f"{k}.json").exists():
                return True
        return False

    def _pick_model(self, rotate=False):
        with self.lock:
            if rotate and len(self.models) > 1:
                self._model_idx = (self._model_idx + 1) % len(self.models)
            return self.models[self._model_idx]

    def chat_json(self, role, system, user, *, validate=None, call_id=None, temperature=None):
        """Return (parsed_json, record). `validate(parsed)` may raise ValueError to trigger a retry."""
        temperature = self.cfg.get("temperature", 0.4) if temperature is None else temperature
        primary_key, generic_key = self._keys(role, system, user, temperature)
        name = f"{call_id or role}-{primary_key[:10]}"
        for ck in (primary_key, generic_key):
            cached = self.cache_dir / f"{ck}.json"
            if cached.exists():
                record = json.loads(cached.read_text())
                parsed = extract_json(record["raw_text"])
                if validate:
                    validate(parsed)
                with self.lock:
                    self.stats["cache_hits"] += 1
                    m_used = record.get("model", self.model)
                    self.stats["models_used"][m_used] = self.stats["models_used"].get(m_used, 0) + 1
                (self.calls_dir / f"{name}.json").write_text(json.dumps({**record, "origin": "cache"}, indent=2, ensure_ascii=False))
                return parsed, record

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        errors = []
        active_model = self._pick_model(rotate=False)
        max_attempts = max(self.cfg.get("max_attempts", 6), len(self.models) + 2)
        for attempt in range(1, max_attempts + 1):
            if errors:
                messages = messages[:2] + [{"role": "user", "content": user + "\n\nYour previous reply was rejected: "
                                            + errors[-1][:800] + "\nReturn corrected JSON only."}]
            body = {"model": active_model, "messages": messages, "temperature": temperature,
                    "response_format": {"type": "json_object"}}
            if self.cfg.get("max_tokens"):
                body["max_tokens"] = self.cfg["max_tokens"]
            if self.cfg.get("reasoning_effort"):
                body["reasoning_effort"] = self.cfg["reasoning_effort"]
            body.update(self.cfg.get("extra_body", {}))
            started = time.perf_counter()
            record = {"role": role, "call_id": call_id, "model": active_model, "base_url": self.base_url,
                      "attempt": attempt, "request": body, "origin": "fresh"}
            try:
                self.limiter.wait()
                raw, usage, finish = self._post(body)
                record.update(raw_text=raw, usage=usage, finish_reason=finish)
                parsed = extract_json(raw)
                if validate:
                    validate(parsed)
                record["elapsed_s"] = round(time.perf_counter() - started, 3)
                self._account(record, usage)
                (self.calls_dir / f"{name}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
                (self.cache_dir / f"{primary_key}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
                (self.cache_dir / f"{generic_key}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
                return parsed, record
            except _Retryable as exc:
                record.update(error=str(exc), elapsed_s=round(time.perf_counter() - started, 3))
                (self.calls_dir / f"{name}-fail{attempt}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
                with self.lock:
                    self.stats["errors"] += 1
                prev_model = active_model
                active_model = self._pick_model(rotate=True)
                if active_model != prev_model:
                    time.sleep(1.5)
                else:
                    time.sleep(min(45, exc.wait or 6 * attempt))
            except (ValueError, KeyError, TypeError) as exc:
                record.update(error=f"invalid reply: {exc}", elapsed_s=round(time.perf_counter() - started, 3))
                self._account(record, record.get("usage"))
                (self.calls_dir / f"{name}-fail{attempt}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
                with self.lock:
                    self.stats["errors"] += 1
                errors.append(str(exc))
        raise LLMError(f"{name}: no valid reply after {max_attempts} attempts: {errors[-1:] or 'transport/quota errors'}")

    def _account(self, record, usage):
        with self.lock:
            self.stats["requests"] += 1
            self.stats["seconds"] += record.get("elapsed_s", 0)
            m_used = record.get("model", self.model)
            self.stats["models_used"][m_used] = self.stats["models_used"].get(m_used, 0) + 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.stats[k] += int((usage or {}).get(k) or 0)

    def _post(self, body):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/chat/completions",
                                     json.dumps(body, ensure_ascii=False).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.get("timeout_s", 180)) as resp:
                envelope = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(2500).decode("utf-8", errors="replace")
            if exc.code in (429, 500, 502, 503, 504):
                wait = 20 if exc.code == 429 else 6
                m = re.search(r'retry(?:Delay| in)[^0-9]*([0-9.]+)s', detail)
                if m:
                    wait = float(m.group(1)) + 1
                raise _Retryable(f"HTTP {exc.code}: {detail[:800]}", wait) from exc
            raise LLMError(f"HTTP {exc.code}: {detail[:800]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _Retryable(f"transport: {exc}", 8) from exc
        choice = envelope["choices"][0]
        return choice["message"].get("content") or "", envelope.get("usage"), choice.get("finish_reason")


class _Retryable(Exception):
    def __init__(self, message, wait=None):
        super().__init__(message)
        self.wait = wait
