#!/usr/bin/env bash
# Compare the local LLM setup for one example query across several OpenAI-compatible
# endpoints, timing each model with hyperfine. The three endpoints run in parallel,
# so one wall-clock pass gives all three numbers.
#
#   scripts/model-bench.sh                       # the three default models, 3 runs each
#   scripts/model-bench.sh --runs 5 --warmup 1
# (hyperfine 1.20 has no --jobs flag; the parallelism here is one hyperfine
#  process per model, started together, so a pass takes about one model's time.)
#   scripts/model-bench.sh --query "Wie viele Schulen gab es 1799 in Bern?"
#   scripts/model-bench.sh --models "apertus-8b=https://gateway.keera.ch/api/v1|apertus-v1-5-8b|KEERA_API_KEY,qwen3.5-9b=https://api.melious.ai/v1|qwen3.5-9b|MELIOUS_API_KEY"
#   scripts/model-bench.sh --keys-from-sops secrets.enc.yaml   # keys decrypted with sops
#
# Reasoning models (deepseek-v4.1-flash, qwen3.5-9b on melious) spend the token
# budget on reasoning_content before they write content, so the budget default of
# 1024 is deliberate; the summary reports reasoning tokens per answer.
#
# Keys: taken from the environment ($KEERA_API_KEY, $MELIOUS_API_KEY, ...) or, with
# --keys-from-sops FILE, decrypted with `sops -d` and exported from that document.
# No key is ever printed: hyperfine only ever sees the variable name.
#
# Requires: hyperfine, curl, python3 (for the summary), and a key per endpoint.
# Note: hosted endpoints rate-limit (melious answers HTTP 429 under a burst of
# runs); those runs land in the summary as "run failed: 429 ..." rather than as a
# timing, so re-run with fewer --runs if a row is missing.
set -euo pipefail

RUNS=3
WARMUP=1
MAX_TOKENS=1024
TIMEOUT=180
OUT_DIR=${OUT_DIR:-bench-out}
QUERY=${QUERY:-"Wie viele Schülerinnen gab es total in Bern?"}
SYSTEM_PROMPT="Antworte kurz und nenne die Quelle."
# name=base_url|model_id|key_env_var
MODELS=${MODELS:-"apertus-8b=https://gateway.keera.ch/api/v1|apertus-v1-5-8b|KEERA_API_KEY,qwen3.5-9b=https://api.melious.ai/v1|qwen3.5-9b|MELIOUS_API_KEY,deepseek-v4.1-flash=https://api.melious.ai/v1|deepseek-v4.1-flash|MELIOUS_API_KEY"}

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --runs) RUNS=$2; shift 2 ;;
    --warmup) WARMUP=$2; shift 2 ;;
    --query) QUERY=$2; shift 2 ;;
    --models) MODELS=$2; shift 2 ;;
    --max-tokens) MAX_TOKENS=$2; shift 2 ;;
    --timeout) TIMEOUT=$2; shift 2 ;;
    --out) OUT_DIR=$2; shift 2 ;;
    --keys-from-sops)
      # decrypt the document and export every KEY=value it holds, without echoing it
      if ! command -v sops >/dev/null; then echo "sops not found" >&2; exit 1; fi
      while IFS='=' read -r key value; do
        [ -n "$key" ] && export "$key=$value"
      done < <(sops -d --output-type json "$2" | python3 -c '
import json, sys
def walk(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, k)
    elif isinstance(node, str):
        yield prefix, node
for k, v in walk(json.load(sys.stdin)):
    if k.upper().endswith("KEY") or "API" in k.upper():
        print(f"{k.upper()}={v}")
')
      shift 2
      ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

for tool in hyperfine curl python3; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

mkdir -p "$OUT_DIR"
echo "query:   $QUERY"
echo "runs:    $RUNS (+$WARMUP warmup), one hyperfine per model, all models in parallel"
echo "models:  $MODELS" | tr ',' '\n' | sed 's/^models:  /         /'
echo

pids=()
IFS=',' read -ra SPECS <<< "$MODELS"
for spec in "${SPECS[@]}"; do
  name=${spec%%=*}
  rest=${spec#*=}
  base=${rest%%|*}
  rest=${rest#*|}
  model=${rest%%|*}
  key_var=${rest##*|}

  if [ -z "${!key_var:-}" ]; then
    echo "! $name: \$$key_var is not set, skipping" >&2
    continue
  fi

  body=$(python3 - "$model" "$SYSTEM_PROMPT" "$QUERY" "$MAX_TOKENS" <<'PY'
import json, sys
model, system, query, max_tokens = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
print(json.dumps({
    "model": model,
    "messages": [{"role": "system", "content": system}, {"role": "user", "content": query}],
    "max_tokens": max_tokens,
    "temperature": 0,
}))
PY
)
  json_file="$OUT_DIR/$name.json"
  md_file="$OUT_DIR/$name.md"
  answer_file="$OUT_DIR/$name.answer.json"

  (
    hyperfine \
      --runs "$RUNS" \
      --warmup "$WARMUP" \
      --style basic \
      --export-json "$json_file" \
      --export-markdown "$md_file" \
      --command-name "$name ($model)" \
      "curl -sS --fail-with-body -m $TIMEOUT -X POST '$base/chat/completions' \
        -H 'Content-Type: application/json' \
        -H \"Authorization: Bearer \$$key_var\" \
        -d '$body' -o '$answer_file'"
    echo "$name done"
  ) &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done

echo
echo "=== summary ($OUT_DIR) ==="
python3 - "$OUT_DIR" "${SPECS[@]}" <<'PY'
import json, os, sys

out_dir = sys.argv[1]
specs = [s.split("=")[0] for s in sys.argv[2:]]
rows = []
for name in specs:
    json_path = os.path.join(out_dir, f"{name}.json")
    answer_path = os.path.join(out_dir, f"{name}.answer.json")
    if not os.path.exists(json_path) or os.path.getsize(json_path) == 0:
        # build a message from whatever the endpoint said (e.g. 429 rate limit)
        note = "run failed"
        if os.path.exists(answer_path):
            try:
                payload = json.load(open(answer_path))
                error = payload.get("error")
                if error:
                    note = f"run failed: {error.get('code') or ''} {error.get('message') or error}".strip()[:90]
            except Exception:
                pass
        rows.append((name, "-", "-", "-", note))
        continue
    try:
        data = json.load(open(json_path))
        if not data.get("results"):
            raise ValueError("no results in export")
    except Exception as exc:
        rows.append((name, "-", "-", "-", f"run failed ({exc})"))
        continue
    result = data["results"][0]
    times = result["times"]
    chars = "-"
    ok = "no answer file"
    if os.path.exists(answer_path):
        try:
            payload = json.load(open(answer_path))
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            usage = payload.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0
            finish = choice.get("finish_reason") or "?"
            if content.strip():
                ok = f"{len(content)} chars, {usage.get('total_tokens', '?')} tok"
            elif reasoning.strip():
                ok = f"reasoning only ({len(reasoning)} chars, {reasoning_tokens} reason tok, finish {finish})"
            else:
                ok = f"empty ({finish})"
            if reasoning_tokens and content.strip():
                ok += f", {reasoning_tokens} reason tok"
        except Exception as exc:
            ok = f"unreadable answer ({exc})"
    rows.append((name, f"{result['median']:.2f}", f"{result['mean']:.2f}", f"{min(times):.2f}", ok))

width = max(len(r[0]) for r in rows) + 2
print(f"{'model'.ljust(width)}{'median s':>10}{'mean s':>9}{'min s':>8}  answer")
for name, median, mean, minimum, ok in rows:
    print(f"{name.ljust(width)}{median:>10}{mean:>9}{minimum:>8}  {ok}")
PY
echo
echo "per-model details: $OUT_DIR/<model>.md and .json, raw responses in <model>.answer.json"

exit "$status"
