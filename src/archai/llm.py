"""Robust Apertus client for any OpenAI-compatible endpoint (local llama.cpp or hosted).

Retries with backoff, JSON repair, schema checks, and a circuit breaker: once the endpoint keeps failing,
callers get None and the pipeline falls back to deterministic-only output instead of crashing."""
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

_ENV = Path(os.environ.get("ARCHAI_ENV", Path.home() / ".config/archai/apertus.env"))
if _ENV.exists():  # secrets live outside the repo; real env vars win
    for _l in _ENV.read_text().splitlines():
        if "=" in _l and not _l.startswith("#"):
            _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip())

BASE = os.environ.get("APERTUS_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
KEY = os.environ.get("APERTUS_API_KEY", "none")
MODEL = os.environ.get("APERTUS_MODEL", "apertus-v1.5-8b-text-q4_k_m")
TIMEOUT = float(os.environ.get("APERTUS_TIMEOUT", "600"))
SYSTEM = "You are a careful Swiss archivist. Use only facts present in the input. Reply with JSON only."
_fails = {"n": 0}
MAX_CONSECUTIVE_FAILS = 3


def available(timeout=5):
    try:
        req = urllib.request.Request(BASE + "/models", headers={"Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def extract_json(s):
    """Parse the first balanced JSON object in s (handles ```json fences and trailing chatter)."""
    if not s:
        return None
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M)
    start = s.find("{")
    while start != -1:
        depth, instr, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if instr:
                esc = (ch == "\\" and not esc)
                if ch == '"' and not esc:
                    instr = False
                continue
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def check_schema(obj, schema):
    """schema: {key: type or tuple of types}. Coerces str->[str] for list fields, drops wrong types. Returns problems."""
    problems = []
    for k, t in schema.items():
        v = obj.get(k)
        if v is None:
            problems.append(f"missing:{k}"); continue
        if t is list and isinstance(v, str):
            obj[k] = [v]
        elif not isinstance(v, t):
            problems.append(f"type:{k}"); obj.pop(k)
    return problems


def _post(body):
    req = urllib.request.Request(BASE + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def chat_json(prompt, schema=None, max_tokens=400, retries=3, temperature=0.0):
    """Returns (obj or None, meta). meta records attempts, seconds, usage, errors — written into traces."""
    meta = {"model": MODEL, "attempts": 0, "errors": [], "seconds": 0.0}
    if _fails["n"] >= MAX_CONSECUTIVE_FAILS:
        meta["errors"].append("circuit_open"); return None, meta
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    t0 = time.time()
    for a in range(retries):
        meta["attempts"] = a + 1
        body = {"model": MODEL, "temperature": temperature, "max_tokens": max_tokens, "messages": msgs,
                "response_format": {"type": "json_object"}}
        try:
            d = _post(body)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            meta["errors"].append(f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(min(2 ** a, 8)); continue
        content = d["choices"][0]["message"].get("content") or ""
        meta["usage"] = d.get("usage", {})
        obj = extract_json(content)
        if obj is None:
            meta["errors"].append("invalid_json")
            msgs = msgs[:2] + [{"role": "assistant", "content": content[:2000]},
                               {"role": "user", "content": "That was not valid JSON. Reply again with one JSON object only."}]
            continue
        if schema:
            meta["schema_problems"] = check_schema(obj, schema)
        _fails["n"] = 0; meta["seconds"] = round(time.time() - t0, 1)
        return obj, meta
    _fails["n"] += 1; meta["seconds"] = round(time.time() - t0, 1)
    return None, meta


def chat_text(prompt, max_tokens=300, retries=3):
    meta = {"model": MODEL, "attempts": 0, "errors": []}
    if _fails["n"] >= MAX_CONSECUTIVE_FAILS:
        meta["errors"].append("circuit_open"); return None, meta
    for a in range(retries):
        meta["attempts"] = a + 1
        try:
            d = _post({"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]})
            _fails["n"] = 0
            return d["choices"][0]["message"].get("content") or "", meta
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            meta["errors"].append(f"{type(e).__name__}: {str(e)[:120]}"); time.sleep(min(2 ** a, 8))
    _fails["n"] += 1
    return None, meta
