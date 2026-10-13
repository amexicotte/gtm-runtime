#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

python_bin=${PYTHON_BIN:-python3}

for test_file in engine/tests/test_*.py; do
  "$python_bin" "$test_file"
done

"$python_bin" -m compileall -q engine reasoning migrations
PYTHONPATH=engine "$python_bin" -m runtime.field_policy

"$python_bin" - <<'PY'
from reasoning.build_prompt import build, build_email, build_manual, build_portfolio

prompts = {
    "meeting": build(),
    "manual_note": build_manual(),
    "email": build_email(),
    "portfolio": build_portfolio(),
}
for name, prompt in prompts.items():
    if not prompt.strip():
        raise SystemExit(f"{name} prompt assembled empty")
    unresolved = [
        marker for marker in ("{{SCHEMA_SUMMARY}}", "{{ENTITIES}}", "{{ROUTING}}")
        if marker in prompt
    ]
    if unresolved:
        raise SystemExit(
            f"{name} prompt contains unresolved template markers: {unresolved}"
        )
print("prompt assembly: meeting, manual_note, email, portfolio OK")
PY
