# OMP upgrade handoff

## Delivery ledger

| Milestone | Branch | Commit / push | PR / merge | State |
| --- | --- | --- | --- | --- |
| M0 | `upgrade/m0-contracts` | `a9f3259a22a358c4578f59f9b0d9762cad61e14f`, pushed successfully | [PR #1](https://github.com/karisheen/Limit-Orderbook/pull/1), merged `56b7c0dfbf9be5e146faffdf6afb77dc523257c0` | Complete; local gates attached to PR, no remote checks configured |
| M1 | `upgrade/m1-feed-integrity` | In progress | Not yet opened | Exact core and legacy safety slices underway |
| M2–M6 | Not started | Not performed | Not performed | Required; not complete |

## M0 verified evidence

- Starting checkout: clean `main`, `e7dceaf9492e4b5413ea3a972b66db7474b0232f`; origin `https://github.com/karisheen/Limit-Orderbook.git`; remote default `main`, unprotected. No applicable AGENTS.md found.
- Existing authenticated GitHub account: `karisheen`, repository ADMIN. Existing configured author `karisheen <dakariantoine@gmail.com>` matches the GitHub-associated author of the baseline commit. Email-list endpoint lacks user scope; no authentication/global identity settings changed.
- Baseline `.venv/bin/python -m pytest tests/ -q`: **35 passed**, one existing Python 3.9 LibreSSL/urllib3 warning.
- Modern local verification runtime: Python **3.13.5**, Node **22.22.0**, npm **10.9.4**. Ignored `.venv-upgrade` and `.tools/node`. M1 dependency synchronization also upgraded ignored `.venv` to Python 3.13.5; subsequent explicit `UV_PROJECT_ENVIRONMENT=.venv-upgrade` selects the intended verification environment. No language servers configured; CLI checks are the available diagnostic gate.
- `.venv-upgrade/bin/python -m pytest tests/ -q`: **35 passed, 24 strict xfailed**. These xfails capture unfixed baseline defects, not waived M1 acceptance. M1 must remove them as defects are corrected.
- `.venv-upgrade/bin/ruff format --isolated tests/test_upgrade_regressions.py tools/connector_spike.py`; `.venv-upgrade/bin/ruff check --isolated --select E4,E7,E9,F tests/test_upgrade_regressions.py tools/connector_spike.py`: passed after formatting. Broad inherited workstation lint policy was not used as this project's policy.
- `.venv-upgrade/bin/python tools/connector_spike.py --inspect-sources --output docs/evidence/m0-connector-spike.json`: all ten contract controls passed; published Kraken CRC **3310070434** matched; pinned library source/license inspection succeeded. Competing library runtimes were **not** installed/executed.
- Actual Chromium dense-chart runs at **1440×900** and **1280×800**: native Canvas2D and SVG rendered identical 28,800-cell synthetic grids. Raw samples in `docs/evidence/m0-chart-spike*.json`; screenshot `docs/evidence/m0-chart-1280.png`. These headless submission/layout/frame-opportunity timings are not production publish-to-render measurements. Canvas selected provisionally; production incremental tiles still require M4 measurement.
- Actual public access: Binance global market-data-only metadata and BTC/ETH/SOL depth/trades HTTP 200; BTC diff100ms and trade stream frames received. Coinbase Advanced Trade public BTC metadata and unauthenticated level2/trades/heartbeats received. Kraken metadata and BTC book/trade frames received. Hyperliquid metadata, spot metadata, contexts, mids, BTC/ETH/SOL/HYPE depth and trades HTTP 200; BTC l2Book/trades/activeAssetCtx frames received. See `docs/evidence/m0-access.json` and `m0-hyperliquid-access.json`. These are access probes, **not synchronized adapter validation**, nor permission to trade.

## M1 integration evidence (milestone not yet complete)

- Exact contracts and corrected legacy safety suite: `.venv-upgrade/bin/python -m pytest tests -q` **217 passed**, no xfails. `.venv-upgrade/bin/mypy src/workstation`: **9 source files clean**. Scoped Ruff passed after unused-import cleanup; parent formats the changed-file union.
- Actual Coinbase full snapshot initially exceeded a 4 MiB parser cap. Already observed payload contained 40,405 updates (~4.78 MB when reserialized). Shared market-data cap is now explicitly **32 MiB**, with bounded-input regression coverage. Direct live legacy adapter then accepted the initial snapshot and six subsequent updates; subscription acknowledgement did not produce a book.
- Browser smoke launched the real Streamlit application on loopback. Independent tabs held BTC and ETH without replacing each other's selections; Coinbase and Kraken live books rendered. Screenshots: `docs/evidence/m1-legacy-btc.png`, `m1-legacy-eth.png`. File watching was disabled for the stable smoke while agents edited source; an earlier reload/watcher run stalled and is not counted as successful stability evidence. New registry ownership/long-duration stability remain pending.
- Early full-depth reducer measurement: synthetic 20,000 levels per side, 30 single-level deltas; initial snapshot 2229.48 ms, delta p50 39.86 ms/p95 75.13 ms/p99 95.29 ms. `docs/evidence/m1-core-initial-benchmark.json` retains dataset hash/environment. This is a core-only diagnostic, not end-to-end ingest/render latency. Normal p95 target is not yet met; Python allocation/validation optimization is underway, not an unmeasured native rewrite.
- `uv.lock` generated and intended upgrade environment synchronized with `env UV_PROJECT_ENVIRONMENT=.venv-upgrade uv sync --locked`. Actual full modern feed adapter/supervisor validation, milestone publication and merge remain pending.
- Optimized the same declared core workload in Python: snapshot 937.89 ms, delta p50 3.51 ms/p95 8.49 ms/p99 10.55 ms; original evidence retained alongside `m1-core-optimized-benchmark.json`. Cold full-book initialization still exceeds normal-update budgets and must be reported separately. Post-change core suite **63 passed**, mypy six core modules clean. No native component introduced.

## Decisions and next action

Use one local asyncio backend worker, exact immutable domain contracts, explicit quality gates, direct venue adapters, a React/TypeScript terminal, native Canvas heatmap, Parquet/raw compressed recording and SQLite metadata. Preserve the legacy entrypoint during cutover; do not duplicate analytics. Detailed decisions/contracts/backlog are in the accompanying M0 documents.

Next: verify M1 shared exact contracts and legacy regression fixes, then integrate venue adapters and supervised subscriptions against those produced contracts. All later milestone requirements remain open. No live exchange execution or external deployment is authorized or implemented.
