# Crypto Limit Order Book

A **Streamlit** dashboard that shows live limit order books for major cryptocurrencies
across multiple exchanges — websocket-first with automatic REST fallback, styled after
pro trading UIs (ladder, depth chart, spread history, depth heatmap, venue comparison).

## Features

- **Real order book data** from free public APIs on every supported venue (no synthetic books).
- **Websocket-first ingestion** with reconnect supervision and automatic REST-polling fallback.
- **Hyperliquid-style ladder**: cumulative depth bars, mid-price/spread row, bid/ask coloring.
- **Depth chart**: classic cumulative bid/ask area chart around the mid.
- **Spread history**: rolling spread (bps) and mid-price drift.
- **Depth heatmap**: liquidity at price levels over time for the primary venue.
- **Multi-exchange comparison**: per-venue best bid/ask, spread, top-of-book size, and a synthetic NBBO.
- **Feed health banners**: connected / degraded / disconnected per venue, with data age and last error.
- **No-crash guarantee**: every network call has timeouts, retries with exponential backoff + jitter, and typed failure paths.

## Exchange Support Matrix

| Venue      | Pair quoted | Live source            | Fallback     | Notes |
|------------|-------------|------------------------|--------------|-------|
| Binance.US | `BTCUSDT`   | Websocket (depth20)    | REST polling | Partial-book stream, 1s cadence |
| Kraken     | `BTC/USD`   | Websocket v2 (book)    | REST polling | Snapshot + delta maintenance |
| Coinbase   | `BTC-USD`   | REST polling (level 2) | —            | Level-2 WS now requires auth |
| KuCoin     | `BTC-USDT`  | REST polling           | —            | WS needs token bootstrap; REST is simpler |
| Bybit      | `BTCUSDT`   | Websocket (orderbook.50)| REST polling| Geo-blocked in the US (shows disconnected) |

Venues quote different pairs (USD vs USDT), so the cross-venue NBBO is approximate.

## Getting Started

1. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   # or: .venv\Scripts\activate  # Windows
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app**:
   ```bash
   streamlit run crypto_lob.py
   ```

4. **In the browser sidebar**:
   - Pick the **asset** (BTC, ETH, SOL, …) and the **exchanges** to watch.
   - Choose the **primary venue** for the ladder and heatmap.
   - Toggle panels (depth chart, spread history, heatmap, comparison).
   - Adjust the UI refresh rate; data ingestion runs continuously in the background.

## Architecture

```
crypto_lob.py                     # Streamlit UI entrypoint (controls + layout only)
src/
├── data/
│   ├── clients.py                # HttpClient (retry/backoff/timeouts) + WsConnection
│   └── adapters/                 # One connector per exchange, normalized output
│       ├── base.py               # Adapter contract + LiveBook (diff-stream maintenance)
│       ├── binance.py  kraken.py  coinbase.py  kucoin.py  bybit.py
├── domain/
│   ├── models.py                 # OrderBookSnapshot, FeedStatus, normalization
│   ├── symbols.py                # Canonical asset list + per-venue pair mapping
│   └── orderbook_service.py      # Feed workers, failover, thread-safe state + history
└── ui/
    └── components.py             # Ladder, depth chart, spread history, heatmap, comparison
tests/
├── test_models.py                # Normalizer + symbol registry
├── test_adapters.py              # Adapter contract tests with canned payloads
├── test_resilience.py            # Timeout/429/proxy failures, degraded states
└── smoke_live.py                 # Manual live REST check across all venues
```

### Reliability behavior

- Each REST call: 3 attempts, exponential backoff with jitter, 10s timeout.
  Retryable: proxy/connection errors, timeouts, HTTP 429/5xx, malformed JSON.
  Other 4xx fail fast.
- Websocket feeds: reconnect with capped backoff; after 3 failed sessions the
  worker permanently falls back to REST polling for that subscription.
- A feed with data older than 15s is reported **degraded** (stale); a feed with
  no data is **disconnected**. The UI keeps rendering whatever venues are healthy.
- History buffers are bounded (spread: 1200 points, depth: 300 snapshots) so
  long sessions don't grow memory unboundedly.

## Tests

```bash
python -m pytest tests/          # unit + contract + resilience tests (no network)
python tests/smoke_live.py       # optional: live REST smoke check for every venue
```

## Known free-tier limitations

- **Bybit** blocks US IPs on its public API (CloudFront 403) — the venue will simply show as disconnected.
- **Coinbase/KuCoin** run on REST polling (2–3s cadence), so their books update slower than websocket venues.
- Public endpoints are rate limited; if you see 429 errors, reduce the number of subscribed venues.
- **Disclaimer**: educational project — not trading advice; data quality/availability can change.
