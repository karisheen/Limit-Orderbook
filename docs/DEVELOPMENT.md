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

1. Python 3.13 runtime guard.
2. Ruff's conservative `E4`, `E7`, `E9`, and `F` rules plus `ruff format --check`
   on `src/workstation`, `src/ui`, `crypto_lob.py`,
   `tests/test_legacy_api_client.py`, `tests/test_workstation*.py`,
   `tests/test_recording*.py`, `tests/test_replay*.py`, and Python tools other
   than the pre-existing `tools/connector_spike.py`. These fast checks run before
   pytest so formatting failures do not wait for the full suite.
3. The complete pytest suite, excluding the `network` marker. Additional script
   arguments are forwarded to pytest.
4. mypy on `src/workstation`, including bodies of untyped functions. Untyped
   third-party dependencies may lack stubs (`ignore_missing_imports = true`);
   project errors are not globally suppressed.
5. Node syntax checks for browser tooling, frontend lint/unit tests, and the
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

### Pre-push protection

After synchronizing the locked Python and frontend dependencies, enable the
versioned hook once per clone:

```sh
git config --local core.hooksPath .githooks
```

The hook runs `tools/check.sh` before Git uploads updates and blocks the push on
any failure. It uses the same environment selection and quality scope as CI;
it does not install dependencies, format files, or submit orders. No additional
hook framework is required. Check an existing `core.hooksPath` before enabling
this in another clone so unrelated custom hooks are not silently replaced.

Commit the intended changes before pushing: this gate checks the current working
tree, not every historical commit or an independently selected ref. Hooks can be
bypassed and do not replace GitHub checks. CI additionally launches the real
offline server and runs Chromium workflows; wait for that run before treating a
push as verified.

The desktop redesign push `e3d1f36` failed after 1,302 passing Python tests because
one candle API test payload did not meet Ruff formatting. The payload formatting
was corrected, the shared gate was reordered to fail fast, and the hook's
rejection path was exercised with a temporary unformatted file (exit 1 before
pytest). The temporary probe was removed; the existing formatter gate remains
the regression protection rather than a test that inspects source formatting.

### GitHub CI and protected main

The **Offline checks** workflow runs for pull requests and pushes to `main`.
Feature-branch pushes do not start a duplicate run; open a pull request to get
the required remote check. A newer commit cancels obsolete work for the same
pull request or main branch, while different pull requests have separate
concurrency groups. Tests, quality gates, browser workflows, read-only token
permissions, and the 15-minute job timeout remain in place. There is no automatic
deployment.

The active **Protect main** repository ruleset requires:

- A pull request rather than a direct push.
- The `offline` status check from the **GitHub Actions** integration, with the
  branch up to date before merging. Keep this job name stable; renaming it also
  requires updating the ruleset.
- Resolution of review conversations.
- No branch deletion or non-fast-forward updates (force pushes).

There are no configured bypass actors, including administrators. Required
approvals are zero so a solo maintainer can merge their own pull request after
checks pass; this is not a mandatory independent-review policy. Administrators
can still explicitly change repository rules through GitHub settings.

Normal contribution flow:

```sh
git switch -c descriptive-branch-name
# Make and commit the intended changes.
git push -u origin HEAD
gh pr create --base main
gh pr checks --required --watch
gh pr merge --squash
```

The policy source is [`.github/main-ruleset.json`](../.github/main-ruleset.json).
Committing that file does not apply GitHub settings automatically. An authorized
administrator can update the existing ruleset explicitly:

```sh
gh api --method PUT repos/karisheen/Limit-Orderbook/rulesets/26781442 \
  --input .github/main-ruleset.json
gh api repos/karisheen/Limit-Orderbook/rules/branches/main
```

Use the effective-rules response to verify enforcement. The local pre-push hook
is supplementary; bypassing it does not bypass the server-side merge rules.


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

The default `/` entry is the browser-direct order-flow screener. Vite runs it
without Python; the production Python host serves the complete built bundle,
including fonts, logos and licenses. `/?workspace=native` and `?source=...` select
the retained native client. Native browser tests explicitly use those routes.
Styles and engines are lazy-loaded per document rather than co-mounted.

The Apache-2.0 adaptation lives under `frontend/src/orderflow`, with `@/` resolving
there. React remains pinned to this project's version; Zustand and Lucide are
additional runtime dependencies. Node tests use `tsx` to resolve the same aliases.
Provider regression fixtures are offline; actual live access is verified
separately. Do not add network-dependent assertions to the offline gate.

For FX only, copy `frontend/.env.example` to `frontend/.env.local`, supply
`VITE_FINNHUB_API_KEY`, then rebuild. Vite embeds this value in the browser bundle:
it is not a secret. No trading credentials, upstream telemetry or remote font
requests are used. Upstream revision and licenses are distributed in
`frontend/public/THIRD_PARTY_NOTICES.txt` and `frontend/public/licenses`.

An isolated native browser run may set
`PLAYWRIGHT_BASE_URL=http://127.0.0.1:8011` after starting
`serve --port 8011 --state-dir /tmp/orderbook-smoke`.

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
