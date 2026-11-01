#!/usr/bin/env bash
# Run from any directory after synchronizing the locked development environment.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
    PYTHON=("${UV_PROJECT_ENVIRONMENT}/bin/python")
elif [[ -x .venv-upgrade/bin/python ]]; then
    PYTHON=("$ROOT/.venv-upgrade/bin/python")
else
    PYTHON=(uv run --locked --offline --no-sync python)
fi

"${PYTHON[@]}" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else "Python 3.13 is required")'
"${PYTHON[@]}" -m pytest "$@" -m 'not network'

# Both observation clients use the workstation core; keep the thin legacy
# presentation and its protocol-boundary regressions inside the quality gate.
RUFF_PATHS=(src/workstation src/ui crypto_lob.py tests/test_legacy_api_client.py)
shopt -s nullglob
for path in tests/test_workstation*.py tests/test_recording*.py tests/test_replay*.py tools/*.py; do
    if [[ "$path" != tools/connector_spike.py ]]; then
        RUFF_PATHS+=("$path")
    fi
done
"${PYTHON[@]}" -m ruff check "${RUFF_PATHS[@]}"
"${PYTHON[@]}" -m ruff format --check "${RUFF_PATHS[@]}"
"${PYTHON[@]}" -m mypy src/workstation

if [[ -f frontend/package.json ]]; then
    if [[ -x "$ROOT/.tools/node/bin/node" ]]; then
        export PATH="$ROOT/.tools/node/bin:$PATH"
    fi
    for script in tools/*.mjs; do
        node --check "$script"
    done
    npm --prefix frontend run lint
    npm --prefix frontend test
    npm --prefix frontend run build
else
    printf '%s\n' 'Frontend build not applicable: frontend/package.json does not exist.'
fi
