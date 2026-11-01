# Native development

## Runtime and dependency policy

The workstation requires **Python 3.13** (minimum 3.13.0; Python 3.14 and later
are not yet supported). The reproducible CI interpreter is **CPython 3.13.5**.
Use a modern OpenSSL-backed interpreter, not the macOS system Python. CI pins
**uv 0.8.4**; use that version for matching dependency-management behavior.
The frontend uses **Node.js 22.22.0** and **npm 10.9.4**.
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
npm --prefix frontend ci
./tools/check.sh
```

To use the existing upgrade environment instead:

```sh
UV_PROJECT_ENVIRONMENT=.venv-upgrade uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
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
   on `src/workstation`, `src/ui`, `crypto_lob.py`,
   `tests/test_legacy_api_client.py`, `tests/test_workstation*.py`,
   `tests/test_recording*.py`, `tests/test_replay*.py`, and Python tools other
   than the pre-existing `tools/connector_spike.py`.
3. mypy on `src/workstation`, including bodies of untyped functions. Untyped
   third-party dependencies may lack stubs (`ignore_missing_imports = true`);
   project errors are not globally suppressed.
4. Node syntax checks for browser tooling, frontend lint/unit tests, and the
   TypeScript/Vite production build.

The thin Streamlit client shares the native API/core; the duplicate legacy
collectors, reducers and analytics have been removed. Its protocol and exact
presentation regressions run with the native suite. Add any new test naming
family to `tools/check.sh`. Format changed in-scope files before the check.
Equivalent native commands for the current core are:

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

Use `python tools/probe_feeds.py --live` and
`python -m src.workstation.cli record --network` for real public-service checks;
see the README for venue/asset selection and capture replay. The old
`tests/smoke_live.py` and its separate collectors were retired.
Offline fixture success is not live-feed validation.

## Frontend and legacy UI

Install the committed npm lock before running the shared checks:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
```

The check script also recognizes the local Node installation at
`.tools/node/bin`. CI installs Node 22 and uses `npm ci` only when the package
exists; a package without its lock must fail rather than perform an unpinned
install. Python and Node dependency installation may access package registries;
no live-market probe belongs in the offline CI job.

The supported thin Streamlit client requires a separately running loopback API:

```sh
uv run --locked --offline --no-sync python -m src.workstation.cli serve
# In another terminal:
uv run --locked --offline --no-sync python -m streamlit run crypto_lob.py --server.address 127.0.0.1
```

The default backend is an offline synthetic demo. Streamlit does not contact
exchanges or start collectors; public live data requires an explicitly started
`serve --network` backend. See [LEGACY](LEGACY.md) for exact selection, source
labels, document ownership, request bounds and unavailable-state recovery.
