#!/bin/bash
# Web sessions start from a fresh clone: sync the locked dev environment so
# pytest, ruff and mypy work immediately. Local sessions manage their own.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet uv
fi
uv sync --all-extras --frozen --quiet
