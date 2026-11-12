# M1 headless feed validation and core benchmark

These tools execute the `src.workstation` adapters, immutable core, and real
`FeedSupervisor`; they do not scrape the legacy browser or manufacture exchange
events. Live access and throughput remain measurements, not promises. This file
describes runnable procedures, **not a claim that the probes or soaks passed**.
Historical M0/M1 evidence in `docs/evidence/` retains its original date, command,
dataset and scope; it does not certify current head. This documentation audit
performed no external live revalidation.

## Public-feed probe

Use the project's installed Python environment from the repository root. The
probe uses the existing runtime dependencies (including `websockets`), requires
Python 3.11 or newer for bounded async operations, and needs no account, API key,
wallet, authentication, signing, or order permissions.

```sh
# No network: explicitly writes unrun dependency evidence.
python tools/probe_feeds.py --output docs/evidence/m1-feed-unrun.json

# All seven distinct initial venue identities; BTC, ETH and SOL.
python tools/probe_feeds.py --live --duration 60 \
  --output docs/evidence/m1-feed-60s.json

# A smaller explicit selection (other initial venue/base rows stay unrun).
python tools/probe_feeds.py --live --venues coinbase kraken hyperliquid \
  --bases BTC ETH SOL --include-hype --duration 120 \
  --output docs/evidence/m1-feed-selected.json

# Approximately 30 minutes of supervised streams after bounded preflight.
python tools/probe_feeds.py --live --duration 1800 --metrics-interval 10 \
  --output docs/evidence/m1-feed-30m.json

# Runnable 24-hour soak; this invocation is not evidence it was executed.
python tools/probe_feeds.py --live --duration 86400 --metrics-interval 60 \
  --output docs/evidence/m1-feed-24h.json
```

`--timeout` bounds each metadata discovery and REST operation (default 30s);
`--concurrency` bounds discovery/REST product concurrency (default three). Total
runtime includes discovery, REST preflight, the requested stream interval, and
joined shutdown. A product's streams start when its preflight completes, so
individual streams can run longer than the common requested interval. Independent
venue failures do not prevent successful venues from being observed. SIGINT and
SIGTERM request clean shutdown; an in-progress preflight can take its remaining
operation timeout before cancellation and join. All started supervisor tasks
are cancelled and awaited. No browser or rendering lease is required.

The tool atomically updates the evidence JSON periodically and on shutdown.
Periodic cumulative stream metrics are written to a JSONL file next to it
(`*.metrics.jsonl`), or to `--metrics PATH`. Choose distinct output paths for
concurrent/separate runs. A new run replaces the named files. Metrics append only
within that run; memory is bounded by counters, latest quality, and the
supervisor's bounded queues/dedup state, not soak duration. Disk use grows with
the number of samples. A hard kill can leave the last periodic report marked
`running`; it is not a completed soak.

### Product identity and coverage

This **probe CLI's** default venues remain Coinbase, Kraken, Binance global,
**Binance.US**, Bybit, KuCoin, and Hyperliquid. Binance.US is never renamed to or
merged with global. This is not the whole application catalog: `ADAPTER_CLASSES`
also registers **OKX, Bitget and Deribit**, while `AdapterCatalog` excludes
Binance.US unless explicitly enabled. This CLI cannot select those three newer
venues, nor Binance/Bybit perpetual families. Selection is explicit and conservative:

| Venue | Selected cohort | Reconstruction depth |
| --- | --- | --- |
| Coinbase | BTC/ETH/SOL spot USD | Adapter-native full known snapshot/deltas |
| Kraken | BTC/ETH/SOL spot USD | 25 levels, venue CRC validation |
| Binance global | BTC/ETH/SOL spot USDT | Adapter REST bootstrap and diff stream |
| Binance.US | BTC/ETH/SOL spot USDT | Independent partial top-20 snapshots |
| Bybit | BTC/ETH/SOL spot USDT | 50 levels |
| KuCoin | BTC/ETH/SOL spot USDT | Public REST top-20 snapshot/degraded mode |
| Hyperliquid | BTC/ETH/SOL default perpetual, USDT quote with independently resolved native USDC settlement | Default slow top-20 snapshots |

`--include-hype` adds Hyperliquid HYPE perpetual where metadata lists it. The
selected complete instrument record includes native identity, namespace,
metadata version, quote, settlement, market type, and access fields. Metadata
also records the discovered instrument count and raw metadata hash. This is not
a probe of every discovered spot token or DEX namespace. Multiple exact
candidates are listed and deterministic default-namespace/identity ordering is
used; a missing exact cohort is `unsupported`, not a silent alternate quote,
wrapped asset, or different market type.

Hyperliquid selection deliberately reads the chosen descriptor's quote rather
than hardcoding USDC. `--include-hype` adds the default HYPE perpetual, whose
quote is USDC; that does not change BTC/ETH/SOL denomination. USDT-to-native-USDC
`quanto_no_fx` is a contract settlement convention, not stablecoin parity.
This probe does not exercise fast5, references/context, OI/funding history,
optional venue candle history, paper execution or financial risk assumptions.
For current product/channel inventories, see [Asian adapters](ADAPTERS_ASIAN.md),
[USD adapters](ADAPTERS_USD.md) and [Hyperliquid](ADAPTER_HYPERLIQUID.md).

### Dependency evidence and interpretation

Each exact venue/base row separately records:

- **Metadata**: discovery success and whether an active exact product exists.
- **REST snapshot**: actual endpoint, byte count/hash, decoded book-event count,
  and the adapter reducer's integrity, coverage, and recovery result. An HTTP 200
  alone is not a pass.
- **REST trades**: actual decoded trade count, separate from the book result.
- **WS book**: at least one actual valid non-REST supervisor book publication.
  Acknowledgements, heartbeats, reference/context events, or successful REST
  fallback never satisfy this dependency.
- **WS trades**: at least one actual non-history non-REST trade. Subscription
  history and REST trade observations are counted separately. An otherwise
  healthy quiet stream can fail this finite observation criterion; zero events
  is not proof that a venue can never provide trades.

`passed`, `failed`, `unsupported`, `unrun`, and (in periodic evidence) `running`
are distinct. Missing metadata leaves dependent probes `unrun`. Unsupported
REST or WS capability is not invented to make a matrix green. KuCoin's honest
REST service can be useful while its WS cells remain unsupported. Each stream
records actual raw bytes/frames and endpoint counts, normalized event and level
change counts, trade history/live counts, book integrity publication counts,
last quality/coverage, diagnostic counters, reconnects, epochs, duplicate-trade
counts, observed gap-to-valid-book recoveries, and elapsed time. The core and
adapter determine integrity; missing venue sequence/checksum guarantees are
visible in capabilities and are not reported as verified checks.

The CLI's metadata-first dependency flow is narrower than the live catalog's
streaming bootstrap. HTTP metadata denial does not itself prove a distinct public
WS channel unreadable: Binance/Bybit's bundled four-family candidates can be
attempted by the live workstation without invented listing/precision metadata.
Candidates and acknowledgments prove nothing about actual data; only validated
book/trade messages establish their respective observations. Their pinned
`streaming_only` descriptors still fail metadata-dependent comparison, depth-walk
and paper-fill gates. The probe does not exercise or certify that candidate path.

A book count is a **publication count**, not necessarily a unique venue update:
lifecycle changes may republish last-known levels. Raw/event counts are separate.
Queue high-water marks and lifecycle transitions are sampled and may miss brief
peaks/transitions. Recovery counts cover only naturally observed faults, not
unexecuted fault-injection scenarios. There is no deliberate slow-consumer,
network disconnect, or process-restart injection in this CLI.

Only raw hashes/counters and normalized summaries are persisted, **not a
replayable raw capture**. `continuity_capture_complete` reports the supervisor's
loss indicator; it does not claim that raw capture files were written. Bounded
diagnostic overflow is combined into `other_diagnostics` rather than permitting
unbounded memory growth. The default supervisor owns retry, rate-limit,
ack/snapshot/silence deadlines, fallback, and recovery policies; this probe does
not replace them with a fake transport or a second recovery implementation.

`full_dependencies_passed` is deliberately strict: metadata, REST snapshot,
REST trades, WS valid book, and WS non-history trade must all pass for that
exact product in the run. `binance_preference_by_base` chooses global only if
that complete set passes and both supervised channels are currently live in
WS mode, without a recorded continuity loss or terminated owner. It then tries
independently named US under the same criteria, otherwise returns `null`. It
never moves an existing stream or combines cross-venue history. This
classification is not a claim of continued health past the observed interval;
inspect final lifecycle/integrity/reason fields.

Public readability is **not execution eligibility**. Metadata's data/execution
access fields are preserved, while `execution_eligibility` remains
`unknown_not_tested`: this tool never tests trading permission, legal eligibility,
accounts, balances, or executable orders. Successful market-data observations
cannot change that verdict.

`TransportRequest`/`validate_public_endpoint` require credential-free HTTPS/WSS,
allowlisted public query/body/header fields and approved subscription operations.
Shared HTTP transport rejects injected authentication/cookies/private headers and
does not follow redirects or use environment proxy settings in its owned client.
HTTP 401/403/451 is blocked; 404/410 is unsupported, neither retried as a successful
alternative. Retryable 429/5xx responses honor origin-wide `Retry-After` pacing;
an excessive delay opens the circuit instead of retrying early. No denial
authorizes proxy use, authentication, alternate product identity or restriction
bypass. An allowed public endpoint and a permitted trade are separate questions.

The default global profile uses official data-only hosts
`data-api.binance.vision` and `data-stream.binance.vision:443`. If an explicitly
separate standard-host access check is appropriate, use:

```sh
python tools/probe_feeds.py --live --venues binance-global \
  --binance-host-profile standard --duration 60 \
  --output docs/evidence/m1-binance-standard.json
```

That profile uses `api.binance.com` and `stream.binance.com:9443`. Keep evidence
from both profiles; no successful dependency on one profile implies success
for another. There is **no automatic host rotation after restriction errors**,
proxy/restriction bypass, testnet substitution, or mixing of US/global endpoints.
Endpoint/error details remain in the report. A tool exit status of zero means
report generation completed (or intentionally remained unrun), not that all
venues passed; failed dependencies are in the matrix. Interrupted/fatal runs
exit nonzero.

## Deterministic synthetic core benchmark

```sh
# Default: 40,000 initial levels, 300 delta messages per load, 100/s then 200/s.
python tools/benchmark_core.py --output docs/evidence/m1-core-benchmark.json

# Explicit finite dataset/load for comparison; retain the exact arguments/hash.
python tools/benchmark_core.py --depth 20000 --events 1000 \
  --normal-rate 100 --burst-multiplier 2 \
  --output docs/evidence/m1-core-benchmark-1000.json
```

The benchmark constructs one declared synthetic BTC/USD spot instrument, a
strictly ordered noncrossed snapshot with `--depth` levels **per side**, and a
deterministic four-message cycle: quantity replacement, zero deletion,
reinsertion, and two ordered updates at the same price in one message. It moves
through non-best price levels and alternates sides. The dataset contains no
third-party data and no trades. No PRNG or wall clock affects economic inputs.

Dataset SHA-256 covers the exact canonical workstation-wire instrument and
all envelopes, in order, each prefixed by its encoded byte length as an
unsigned 64-bit big-endian integer. Synthetic provenance timestamps are fixed
ordinal values, **not exchange timestamps or measured latency**. The report
records generator version, SHA, initial depth, exact message/change mix,
venue/symbol count, runtime/platform/processor, and reducer source hash. Normal
and burst use the same preconstructed dataset and fresh reducers; both are
finite, and every event must produce a valid book without recovery.

Measured values include snapshot processing time, delta processing
p50/p95/p99/max (nearest-rank percentiles), offered and achieved message rates,
processing-only throughput, serial schedule lateness, CPU time/one-core
utilization, and process peak RSS. Loads use absolute offered deadlines; when
the reducer falls behind, messages are not dropped, and lateness grows instead
of pretending to meet the offered rate. RSS is the process lifetime high-water
mark including dataset construction and both loads, not per-event allocation.
The benchmark does not disable garbage collection or hide snapshot time inside
delta percentiles.

Timing wraps **only `BookReducer.apply`**. Dataset construction, canonical
hashing, parsing, adapter decoding, network/receive time, real queue residence,
recording, publication transport, and UI rendering are outside it. Synthetic
schedule lateness is not measured queue residence. Generic core checks are
exercised, not venue-specific checksum/bootstrap cost. Processing-only threshold
booleans (p95 <=25ms, p99 <=100ms) must **not** be presented as the end-to-end
receive-to-valid or UI budget being met. Finite achieved throughput does not
establish a production peak sustainable rate. The prior
`docs/evidence/m1-core-initial-benchmark.json` is a separate measured baseline;
keep it rather than replacing earlier slower evidence with an optimized run.

The [M0 access snapshot](evidence/m0-access.json) at
2026-11-01T02:15:52Z and [Hyperliquid HTTP snapshot](evidence/m0-hyperliquid-access.json)
at 2026-11-01T02:16:14Z measured endpoint/channel access, explicitly not synchronized
adapter integrity. Their HTTP successes, observed frames and older benchmark
figures remain historical evidence only. In particular, the Hyperliquid snapshot's
recent-trade HTTP observations do not add a current adapter REST trade fallback.
Keep the initial, optimized and full M1 benchmark records and their original
commands/figures; do not present those reducer-only results as current end-to-end
queue, response-serialization, UI or sustained-feed performance.

## Integration-owner checks

After all adapter/core/supervisor edits land, the integration owner should run
an offline benchmark and no-network probe, then opt-in short actual feed probes
before a longer soak. Inspect exact per-dependency failures, selected products,
final integrity/lifecycle, natural recovery/gap counts, and joined-owner counts.
Compare benchmark hashes when comparing processing timings. Do not mark a
30-minute or 24-hour soak, a host/channel, or a fault scenario passed unless that
specific run was executed and its evidence retained.
