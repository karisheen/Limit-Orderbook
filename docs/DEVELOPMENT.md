# Development

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


## Unified frontend and native session

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

The `/` entry is one crypto terminal backed by Python. The shell, studies,
native charts and workflow drawers share one native session per browser
document. There is no separate browser exchange client or alternate workspace
route. `?source=<catalog ID>` requests a source in that same terminal.

For development, start the API and Vite in separate terminals:

```sh
uv run --locked --offline --no-sync python -m src.workstation.cli serve
# In another terminal:
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173/`. Vite proxies `/api`, including WebSocket upgrades,
and `/health` to `127.0.0.1:8000`; port 5173 is strict. Add `--network` to
`serve` for public crypto discovery/collection. Without it, the server offers
the explicitly synthetic offline demo and configured recordings. The Python
server serves `frontend/dist` at `http://127.0.0.1:8000/` after a production
build. Vite alone or `vite preview` does not provide the session API.

`frontend/src/integration/` connects the shell to the native session and
reuses the chart/workflow components. `frontend/src/orderflow/` owns the shell
and display-only studies; `@/` resolves there. Its market-data bridge consumes
native session events, not exchange WebSockets. Plotting may use floating-point
coordinates; exact costs, analytics, paper accounting and replay remain Python
responsibilities. Never feed display aggregates back into financial inputs.
Session/generation/source/instrument boundaries invalidate obsolete evidence.
Changing display studies must not create a second native session.

The bridge polls `POST /api/v1/market-data` with the existing session/generation,
about every 150 ms when caught up, draining `has_more` sequentially without
overlapping reads. Carry both exclusive cursor components: `after_delivery`
(string) and `after_event` (number), initially `'0'` and `-1`. Advance from the
returned cursor even for empty trade pages. Merge additive `boundaries` and
`trades` by cursor (boundaries first on ties); compare each trade's
`source_identity:{session_id,segment_id,clock_id,connection_epoch}` per stream
across pages. A boundary or identity change removes that stream's obsolete
trade inputs and rebuilds trade studies, preserving unrelated streams and
independent depth/OBI. Ordinary heartbeats and book-only controls are not trade
continuity resets. Cursor `gap` clears obsolete retained trade history; native
ownership changes reset all caches. Mixed pagination counts trades and
boundaries, with one event's fan-out group kept atomic.
Replay is fenced at the native delivery cursor and source clock. Books are
current snapshots, not snapshots at the page cursor. Use native level/trade
`base_quantity` and `quote_notional`, preserving nulls, quality and eligibility
instead of inferring derivative units in JavaScript.

Display recomputation retains the latest 100,000 exact cohort trades and
discloses truncation. This browser bound is distinct from native live history
(4096 batches / 8 MiB) and per-request scan/trade bounds. Unknown-side prints
contribute total volume but zero delta; subscription-history prints retain tape
provenance but are excluded from ordered studies. See
[API](API.md#native-market-data-delivery) for cursor, valuation and size limits.

Timeframe, tick/indicator and venue changes recompute retained trade inputs
without creating sessions or erasing independent depth/OBI history. A changed
depth cohort resets depth history separately. Busy/unusable polling state pauses
requests without clearing same-owner caches or cursor; retained evidence is
not a claim of freshness. Scope changes still fence all old evidence.

Lag subtracts exact native exchange-event wall nanoseconds from receive-wall
nanoseconds before projecting milliseconds. Missing timestamps, subscription
history, negative/skewed differences and nonfinite projections are unavailable,
not zero. Positive values still include clock skew. Replay labels capture-time
receipt **Recorded lag**, not playback latency; demo labels **Synthetic lag**.

No frontend environment settings or exchange API keys are required. Public
crypto collection is enabled on the server, not through browser credentials.
USD, USDT and USDC remain separate assets; removing fiat currency products does
not remove the need for explicit quote-conversion evidence in exact comparisons.
Runtime attribution and licenses remain in
`frontend/public/THIRD_PARTY_NOTICES.txt` and `frontend/public/licenses`.
Node tests use `tsx` with the same aliases. Keep protocol fixtures offline;
actual provider availability, symbols and sustained collection require separate
live evidence, not a successful build or fixture run.
Public crypto discovery excludes Bybit linear TradFi stock/ETF/commodity/forex
classifications and Bitget RWA/non-perpetual futures rows without rewriting
crypto contract metadata. Provider/market coverage is listed in
[README](../README.md#public-crypto-coverage).

Binance spot/USDⓈ-M and Bybit spot/linear metadata requests have independent family
deadlines. Preserve complete successful families and their exact specifications;
do not turn a sibling's HTTP restriction, timeout or incomplete pagination into
empty successful metadata or whole-venue success. Catalog evidence must retain
`metadata:<api_family>` and distinguish aggregate `metadata="partial"` from
exact-product probe readiness. Bybit's configured `api.bytick.com` is an
[official equivalent mainnet endpoint](https://bybit-exchange.github.io/docs/v5/guide),
not a regional bypass. Do not add synthetic products or hidden exchange fallback.
OKX public WebSocket uses TLS port 443; provider `notice` envelopes are neutral
informational controls, not errors, subscription acknowledgments, heartbeats or
market observations. See [adapter availability](ADAPTERS_ASIAN.md#metadata-and-identity)
for publication and failure semantics.

Managed recording controls use `GET /api/v1/recordings` and session/generation-
fenced `POST /api/v1/recordings/start` and `/stop` (stop also takes `id`).
Mutations return the list plus `recording_id`; each row's nullable `session_id`
identifies its active terminal owner, not the recording manifest session.
Only that owner may stop it, including after a feed failure. A row's
incomplete/error status alone does not mean its recorder has stopped.

Start accepts exactly two scope values: `instrument` (default), all currently
owned primary-instrument keys including reference/context channels; and
`cohort`, all currently owned native session keys, not only visible chart venues.
The default does not force unrelated cohort channels. Scope and keys freeze at
start; `RecorderBinding` owns independent leases, so later selection or cohort
changes do not retarget capture. Rows persist `scope`, `instruments` (native IDs)
and `channels` (`{venue,instrument_id,stream}`, where `stream` is a channel name)
for restart discovery. Limits are 24 instruments, 48 channels/keys and 64 KiB
managed metadata, one active capture per session, two active per server,
64 retained entries and 128 MiB per capture. Capacity returns 429; no
deletion/eviction API is exposed. Session close and server shutdown finalize
captures. Cancellation-shielded mutations can commit despite a disconnected
caller; inspect before retrying. Metadata is not evidence of receipt: missing
channel evidence and incomplete captures do not become complete through either
scope, successful stop or restart.

The server chooses opaque paths under `--state-dir/recordings`; browsers supply
no paths. Only complete verified captures become catalog replay sources,
immediately after stop and again through restart discovery. Incomplete/error
reasons remain visible. Preserve the state directory for captures, paper,
journal and research; embedded temporary runtime state is not persistent.
See [managed recording API](API.md#managed-recordings).

An isolated native browser run may set
`PLAYWRIGHT_BASE_URL=http://127.0.0.1:8011` after starting
`serve --port 8011 --state-dir /tmp/orderbook-smoke`.

`frontend/e2e/recording.spec.ts` includes offline synthetic-source rejection and
an explicitly opted-in real-live capture/replay scenario. The live case is
skipped by default. To opt in, start a `serve --network` backend and set
`PLAYWRIGHT_RECORDING_SOURCE_ID` to its exact ready native live source ID:

```sh
PLAYWRIGHT_RECORDING_SOURCE_ID=live npm --prefix frontend run test:e2e -- recording.spec.ts
```

Use the actual catalog ID if it differs from `live`. Optionally set
`PLAYWRIGHT_RECORDING_INSTRUMENT_ID` to an exact active native instrument ID
to exercise a particular public market rather than automatic startup selection.
The source must expose at least two instruments and a working real collector;
the test does not mock market data or waive fresh-book/capture-integrity checks.
It exercises durable recording, so use a dedicated state directory.
This command is an opt-in procedure, not a claim that live verification passed.

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
