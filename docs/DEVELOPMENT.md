# Native development

## Runtime and dependency policy

The workstation requires **Python 3.13** (minimum 3.13.0; Python 3.14 and later
are not yet supported). The reproducible CI interpreter is **CPython 3.13.5**.
Use a modern OpenSSL-backed interpreter, not the macOS system Python. CI pins
**uv 0.8.4**; use that version for matching dependency-management behavior.
The frontend uses **Node.js 22** and npm when `frontend/package.json` exists.
No Docker, database service, exchange credentials, or wallet is needed for the
offline development checks.

`pyproject.toml` owns exact direct dependency pins and the development group.
`uv.lock` is the generated, committed resolution of transitive dependencies;
do not hand-author hashes or silently refresh it during checks. Direct versions
were selected from the existing Python 3.13 upgrade environment, not from an
unbounded latest-version install. `requirements.txt` retains a pip-compatible
list of the same direct runtime and development pins for the Streamlit path;
it is **not** a substitute for the full transitive lock.

From the repository root, install uv and CPython through your preferred native
package manager, then initialize the default environment:

```sh
uv python install 3.13.5
uv sync --locked --python 3.13.5 --group dev
./tools/check.sh
```

To use the existing upgrade environment instead:

```sh
UV_PROJECT_ENVIRONMENT=.venv-upgrade uv sync --locked --python 3.13.5 --group dev
./tools/check.sh
```

The check script honors an explicit `UV_PROJECT_ENVIRONMENT` first, then uses
`.venv-upgrade/bin/python` if present, otherwise uses uv's default `.venv` via
`uv run --locked --offline --no-sync`. To choose the default environment when
both directories exist, run `UV_PROJECT_ENVIRONMENT=.venv ./tools/check.sh`.
Synchronize the selected environment before checking: the script deliberately
does not install dependencies or rewrite the lock. Native setup may download
packages; the tests themselves do not require live exchange access.

When intentionally changing dependencies, update the direct pins in
`pyproject.toml` and `requirements.txt` together, then run:

```sh
uv lock --python 3.13.5
uv sync --locked --python 3.13.5 --group dev
./tools/check.sh
```

Review and commit the generated lock alongside the dependency changes. A
missing or out-of-date lock is an error for normal setup/CI, not permission to
resolve a different environment silently.

## Offline checks and quality scope

`./tools/check.sh` runs these gates in order:

1. Python 3.13 runtime guard and the complete pytest suite, excluding the
   `network` marker. Additional script arguments are forwarded to pytest.
2. Ruff's conservative `E4`, `E7`, `E9`, and `F` rules plus `ruff format --check`
   on `src/workstation`, `tests/test_workstation*.py`,
   `tests/test_recording*.py`, `tests/test_replay*.py`, and Python tools other
   than the pre-existing `tools/connector_spike.py`.
3. mypy on `src/workstation`, including bodies of untyped functions. Untyped
   third-party dependencies may lack stubs (`ignore_missing_imports = true`);
   project errors are not globally suppressed.
4. The frontend production build if `frontend/package.json` exists; otherwise
   an explicit not-applicable message, not a claimed frontend pass.

Legacy sources and tests remain pytest-covered; this scope avoids an unrelated
whole-repository formatting migration. Add any new test naming family to
`tools/check.sh` when introducing it. Format changed in-scope files before
running the check script. Equivalent native commands for the current core are:

```sh
uv run --locked --offline --no-sync python -m pytest
uv run --locked --offline --no-sync python -m ruff check src/workstation tests/test_workstation_core.py
uv run --locked --offline --no-sync python -m ruff format --check src/workstation tests/test_workstation_core.py
uv run --locked --offline --no-sync python -m mypy src/workstation
```

For `.venv-upgrade`, replace `uv run --locked --offline --no-sync python` with
`.venv-upgrade/bin/python`. Pytest uses strict marker registration, strict
asyncio mode, and function-scoped async fixture loops. Async tests must use
`@pytest.mark.asyncio`; async fixtures must use `@pytest_asyncio.fixture`.

Live-service tests must be explicitly marked `network`. They are excluded by
default and from CI; running them is a deliberate separate action:

```sh
uv run --locked --offline --no-sync python -m pytest -m network
```

The pre-existing `tests/smoke_live.py` is a manual script, not a pytest test;
it contacts real public services and is not part of offline checks. Do not
interpret offline fixture success as live-feed validation.

## Frontend and legacy UI

Once the frontend package exists, install its committed npm lock before
running the shared checks:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
```

The check script also recognizes the local Node installation at
`.tools/node/bin`. CI installs Node 22 and uses `npm ci` only when the package
exists; a package without its lock must fail rather than perform an unpinned
install. Python and Node dependency installation may access package registries;
no live-market probe belongs in the offline CI job.

The current compatibility UI remains runnable natively, bound to loopback:

```sh
uv run --locked --offline --no-sync python -m streamlit run crypto_lob.py --server.address 127.0.0.1
```

This starts the legacy live-data UI, **not an offline demo**, and can contact
public exchange endpoints. Later backend/demo/replay launch commands must be
documented with their implementation; installing FastAPI and recording
libraries alone does not establish those runtime capabilities.
