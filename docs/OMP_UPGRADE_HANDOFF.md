# OMP upgrade handoff

## Delivery ledger

| Milestone | Branch | Commit / push | PR / merge | State |
| --- | --- | --- | --- | --- |
| M0 | `upgrade/m0-contracts` | Pending final M0 gate | Pending | Audit, contracts and concrete spikes |
| M1–M6 | Not started | Not performed | Not performed | Required; not complete |

## M0 verified evidence

- Starting checkout: clean `main`, `e7dceaf9492e4b5413ea3a972b66db7474b0232f`; origin `https://github.com/karisheen/Limit-Orderbook.git`; remote default `main`, unprotected. No applicable AGENTS.md found.
- Existing authenticated GitHub account: `karisheen`, repository ADMIN. Existing configured author `karisheen <dakariantoine@gmail.com>` matches the GitHub-associated author of the baseline commit. Email-list endpoint lacks user scope; no authentication/global identity settings changed.
- Baseline `.venv/bin/python -m pytest tests/ -q`: **35 passed**, one existing Python 3.9 LibreSSL/urllib3 warning.
- Modern local verification runtime: Python **3.13.5**, Node **22.22.0**, npm **10.9.4**. Ignored `.venv-upgrade` and `.tools/node`; original environment preserved. No language servers configured; CLI checks are the available diagnostic gate.
- `.venv-upgrade/bin/python -m pytest tests/ -q`: **35 passed, 24 strict xfailed**. These xfails capture unfixed baseline defects, not waived M1 acceptance. M1 must remove them as defects are corrected.
- `.venv-upgrade/bin/ruff format --isolated tests/test_upgrade_regressions.py tools/connector_spike.py`; `.venv-upgrade/bin/ruff check --isolated --select E4,E7,E9,F tests/test_upgrade_regressions.py tools/connector_spike.py`: passed after formatting. Broad inherited workstation lint policy was not used as this project's policy.
- `.venv-upgrade/bin/python tools/connector_spike.py --inspect-sources --output docs/evidence/m0-connector-spike.json`: all ten contract controls passed; published Kraken CRC **3310070434** matched; pinned library source/license inspection succeeded. Competing library runtimes were **not** installed/executed.
- Actual Chromium dense-chart runs at **1440×900** and **1280×800**: native Canvas2D and SVG rendered identical 28,800-cell synthetic grids. Raw samples in `docs/evidence/m0-chart-spike*.json`; screenshot `docs/evidence/m0-chart-1280.png`. These headless submission/layout/frame-opportunity timings are not production publish-to-render measurements. Canvas selected provisionally; production incremental tiles still require M4 measurement.
- Actual public access: Binance global market-data-only metadata and BTC/ETH/SOL depth/trades HTTP 200; BTC diff100ms and trade stream frames received. Coinbase Advanced Trade public BTC metadata and unauthenticated level2/trades/heartbeats received. Kraken metadata and BTC book/trade frames received. Hyperliquid metadata, spot metadata, contexts, mids, BTC/ETH/SOL/HYPE depth and trades HTTP 200; BTC l2Book/trades/activeAssetCtx frames received. See `docs/evidence/m0-access.json` and `m0-hyperliquid-access.json`. These are access probes, **not synchronized adapter validation**, nor permission to trade.

## Decisions and next action

Use one local asyncio backend worker, exact immutable domain contracts, explicit quality gates, direct venue adapters, a React/TypeScript terminal, native Canvas heatmap, Parquet/raw compressed recording and SQLite metadata. Preserve the legacy entrypoint during cutover; do not duplicate analytics. Detailed decisions/contracts/backlog are in the accompanying M0 documents.

Next: finish M0 publication/merge; implement M1 shared exact contracts and legacy regression fixes in independent slices, verify those contracts, then integrate venue adapters and supervised subscriptions against them. All later milestone requirements remain open. No live exchange execution or external deployment is authorized or implemented.
