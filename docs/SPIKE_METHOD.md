# M0 connector and chart spikes

These are bounded decision experiments, not replacement ingestion or a completed
terminal. Neither tool imports the legacy application. No credentials, exchange
orders, competing ingestion stacks, external chart packages, or CDN are involved.
The implementation author did not run tests or benchmarks; the integration owner
records actual execution evidence in the M0 handoff. Expected outcomes below are
acceptance criteria, **not reported measurements**.

## 1. Headless precision/raw/checksum contract

From the repository root, with Python 3.9 or newer (standard library only):

```sh
.venv/bin/python tools/connector_spike.py --output /tmp/lob-connector-offline.json
```

This is offline by default. It executes an independent direct-contract fixture:

- Decodes a published Kraken top-10 book vector using `parse_float=Decimal`.
  The embedded numerical values and expected CRC32 `3310070434` come from
  [Kraken's checksum specification](https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2).
  Its example decimal strings are represented as unquoted JSON decimal tokens to
  exercise the WebSocket-v2 precision boundary. This is documentation-derived
  fixture data, not a captured live session.
- Computes asks ascending then bids descending, ten levels each, removing decimal
  points and leading zeros while retaining trailing zeros, then unsigned CRC32.
- Serializes authoritative decimals and a synthetic ID above JavaScript's exact
  integer range as strings; stores original payload bytes in a base64 envelope.
  Decodes the envelope and compares raw bytes, normalized checksum, replayed raw
  checksum, decimal scale, precision, and large-ID representation.
- Changes one quantity and requires the checksum to change. Rejects floats and
  nonfinite/nonpositive authoritative fixture values rather than repairing them.
- Includes a clearly synthetic `100.000000000000000001` precision stress and a
  default-JSON/float-first negative control. The latter must lose information;
  it is **not an executed CCXT benchmark or library test**.

The JSON output includes individual booleans, actual/expected/corrupted CRCs,
fixture and normalized-envelope SHA-256 hashes, interpreter, OS, architecture,
and the exact precision counterexample. Exit code zero requires all fixture
checks to succeed. A positive result proves this narrow serialization/checksum
contract, not live synchronization, update semantics, capture completeness,
production domain validation, or performance.

## 2. Concrete direct/library source/API comparison

Opt in to fetching a small fixed set of upstream source and license files:

```sh
.venv/bin/python tools/connector_spike.py --inspect-sources --output /tmp/lob-connector-sources.json
```

This still runs the same offline fixture, then performs HTTPS reads only from
immutable revisions. It never imports, evaluates, installs, or executes fetched
code. Each fetched file gets its URL, revision, SHA-256, and line numbers for
contract-relevant markers. Missing markers require review; fetch errors remain
`unavailable` and make the requested source inspection incomplete/nonzero. No
network failure is silently relabeled as success. Marker matches are navigation
evidence, **not proof of semantic correctness**. Keep the JSON report alongside
the handoff so inspected source identities can be reproduced.

Sources inspected while preparing this spike (2026-10-31):

| Candidate | Exact inspected revision | License at that revision |
| --- | --- | --- |
| Cryptofeed | `61438078e330bc5277f30a4ef241de34b28fab76` | [AGPL-3.0-or-later plus section 7(b) attribution term](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/LICENSE); do not assume its historical license |
| CCXT / CCXT Pro | `a1bd1dd550c67db735107447b698a34a34c2efa6` | [MIT](https://github.com/ccxt/ccxt/blob/a1bd1dd550c67db735107447b698a34a34c2efa6/LICENSE.txt); the [Pro manual](https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual) describes Pro as a free part of CCXT |
| Independent direct fixture | Repository revision containing this file | Independently implements published exchange algorithm; no library implementation copied |

### What the inspected contract actually preserves

| Contract | Direct fixture | Cryptofeed inspected source | CCXT Pro inspected source |
| --- | --- | --- | --- |
| Decimal ingress | `json.loads(..., parse_float=Decimal)`; decimal text survives storage | [Kraken `message_handler`](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/cryptofeed/exchanges/kraken.py) explicitly uses `parse_float=Decimal`; book uses Decimal keys/values | [Kraken `custom_handle_deltas`](https://github.com/ccxt/ccxt/blob/a1bd1dd550c67db735107447b698a34a34c2efa6/python/ccxt/pro/kraken.py) uses `safe_number`; [WS client](https://github.com/ccxt/ccxt/blob/a1bd1dd550c67db735107447b698a34a34c2efa6/python/ccxt/async_support/base/ws/client.py) uses orjson or stdlib JSON. An exact original-decimal ingress contract is not established by the unified API |
| Checksum | Published expected CRC, roundtrip, and corruption control executed when invoked | Kraken source constructs `checksum_format='KRAKEN'`, conditionally validates, raises `BadChecksum`, and forwards checksum into book callback | Kraken source has optional CRC validation, but `watchOrderBook.checksum` defaults to **false**; unified result cannot be assumed validated |
| Raw event | Exact payload bytes retained separately from normalized string values | Kraken callback receives parsed `raw=msg`, which is not identical to original bytes; separate [PcapRecorder](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/cryptofeed/capture/recorder.py) exists for capture | Kraken resolves the maintained order book; this is not a byte-exact public-message journal. A separately validated predecode capture hook would be required |
| Replay/provenance | Deterministic envelope and checksum roundtrip only | Capture implementation has connection ordinals and metadata; not exercised here and not proven equivalent to our eventual recording manifest | Unified watch results alone do not establish every event, original numeric lexemes, or our replay ordering/epoch contract |
| Build/runtime burden | Python stdlib; works with existing Python 3.9 baseline by design | [Pinned pyproject](https://github.com/bmoscon/cryptofeed/blob/61438078e330bc5277f30a4ef241de34b28fab76/pyproject.toml) requires Python >=3.13, Cython >=3.1 for build, `order_book`, msgspec and async/network dependencies | Async CCXT family and generated Pro adapters; larger API surface includes private trading methods that this application must never use |

The precise uncertainty is important: this experiment does **not** claim CCXT
can never be configured or extended for exact data, nor that Cryptofeed is
incorrect. It demonstrates the direct storage/checksum contract and identifies
concrete adapter/API boundaries to validate before adoption. Current Cryptofeed
has materially useful precision, validation, and capture functionality; its
license and Python/runtime migration cost must be considered, not ignored.
Actual initial-venue coverage and the final direct-adapter decision are covered
by the architecture/connector ADRs. No one-venue source inspection proves every
Binance/global-US, Coinbase API-family, Kraken, Bybit, or Hyperliquid contract.
Both library integration tests are explicitly `not_run` in every report.

## 3. Dense heatmap primitive comparison

Serve only the tools directory on loopback using the standard library:

```sh
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory tools
```

Open `http://127.0.0.1:8765/chart_spike.html` in a visible foreground browser tab
and select **Run comparison**, or append `?autorun=1`. The file also works when
opened directly with `file://`. Select **Download JSON** to retain the actual
report. Automation can call `await window.runChartSpike()` and read
`window.chartSpikeResults`; the same JSON is visible in the page. Failed runs
publish an explicit error rather than fabricated measurements.

Default workload: a fixed 960 x 480 logical-pixel surface, 240 time columns x
120 price rows (28,800 cells), xorshift32 seed `0x5eed1234`, and a fixed 256-entry
color palette. Both renderers consume the same `Uint8Array`, same coordinates,
and same per-frame palette shift. The report contains seed, dimensions, generator,
and FNV-1a checksum of the data. There is no live or recorded market data.

Each primitive gets two excluded warmups and ten measured samples. Order
alternates to reduce consistent first/second bias. Canvas uses one rectangle per
cell and SVG one DOM rectangle per cell. Both rebuild the whole surface; previous
surface teardown and a frame settling interval occur outside the measured work.
This tests dense **full redraws**, not optimized incremental SVG reuse versus
incremental Canvas tiles. Initialization/allocation is deliberately included.

- `submit_and_layout_ms`: `performance.now()` elapsed through primitive creation,
  drawing/DOM insertion, and a layout read. Canvas command submission may defer
  raster/GPU work; SVG may defer painting. These are not GPU completion timings.
- `two_frame_opportunities_ms`: elapsed through two subsequent animation-frame
  callbacks, giving an approximate scheduling/presentation opportunity, **not**
  verified pixel presentation or publish-to-render latency. Refresh rate, browser
  throttling, GC, machine load, and rendering implementation affect this value.
- Raw samples and nearest-rank min/p50/p95/p99/max are retained for both metrics;
  with ten samples, p95 and p99 are the maximum, not stable tail estimates.
- The JSON records browser user agent, platform, device-pixel ratio, viewport,
  screen, and available hardware hints. Record hardware model/OS externally;
  browsers cannot reliably provide them. Memory/CPU/GPU measurements are absent,
  not zero. Visibility changes invalidate the run.

Run on the target workstation at both 1440x900 and 1280x800 browser viewports,
retain raw JSON and a screenshot, and note foreground/background load and browser
version. The grid's CSS size is also reported; compare like-for-like dimensions
and device-pixel ratios. A smoke headless browser result is useful for harness
verification but does not establish interactive workstation performance.

### Chart dependency decision

Proposed dense-chart primitive: **browser-native Canvas2D, zero package
dependencies**, compared against browser-native SVG in this spike. There is no
third-party chart package to version-pin or redistribute; the browser version is
the runtime pin captured in measurements. The architecture ADR records this
choice. No upstream chart implementation or licensed visual asset was copied.
The decision avoids pretending that a candlestick package implements a dense
heatmap. It remains subject to actual measurements and eventual incremental
heatmap/trade-overlay, interaction, accessibility, and UI responsiveness gates.
HTML controls and tables remain the accessible interaction path. This spike
neither delivers production chart interactions nor asserts Canvas is faster
before the integration owner runs and records the comparison.
