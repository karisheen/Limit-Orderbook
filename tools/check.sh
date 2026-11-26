#!/usr/bin/env bash
# Run from any directory after synchronizing the locked development environment.
# Default: full offline gate, forwarding all arguments to pytest.
# --fast: fixed pre-push smoke; full offline and browser merge gates still apply.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE=full
if [[ "${1:-}" == --fast ]]; then
    MODE=fast
    shift
    if (( $# != 0 )); then
        printf '%s\n' '--fast takes no arguments; omit it to pass arguments to the full pytest gate.' >&2
        exit 2
    fi
fi

if [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]]; then
    PYTHON=("${UV_PROJECT_ENVIRONMENT}/bin/python")
elif [[ -x .venv-upgrade/bin/python ]]; then
    PYTHON=("$ROOT/.venv-upgrade/bin/python")
else
    PYTHON=(uv run --locked --offline --no-sync python)
fi

"${PYTHON[@]}" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else "Python 3.13 is required")'

# Both observation clients use the workstation core; keep the thin legacy
# presentation and its protocol-boundary regressions inside the quality gate.
RUFF_PATHS=(src/workstation src/ui crypto_lob.py tests/conftest.py tests/test_legacy_api_client.py)
shopt -s nullglob
for path in tests/test_workstation*.py tests/test_recording*.py tests/test_replay*.py tools/*.py; do
    if [[ "$path" != tools/connector_spike.py ]]; then
        RUFF_PATHS+=("$path")
    fi
done
"${PYTHON[@]}" -m ruff check "${RUFF_PATHS[@]}"
"${PYTHON[@]}" -m ruff format --check "${RUFF_PATHS[@]}"
if [[ "$MODE" == fast ]]; then
    "${PYTHON[@]}" -m pytest -q \
        tests/test_workstation_cli.py::test_demo_is_offline_real_capture_and_replay \
        -m 'not network'
else
    "${PYTHON[@]}" -m pytest "$@" -m 'not network'
    "${PYTHON[@]}" -m mypy src/workstation
fi

if [[ -f frontend/package.json ]]; then
    if [[ -x "$ROOT/.tools/node/bin/node" ]]; then
        export PATH="$ROOT/.tools/node/bin:$PATH"
    fi
    for script in tools/*.mjs; do
        node --check "$script"
    done
    if [[ "$MODE" == fast ]]; then
        (cd frontend && node --import tsx --test src/format.test.ts)
    else
        npm --prefix frontend run lint
        npm --prefix frontend test
        npm --prefix frontend run build
    fi
else
    printf '%s\n' 'Frontend checks not applicable: frontend/package.json does not exist.'
fi
