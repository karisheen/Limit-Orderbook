# Supported legacy Streamlit observation client

Streamlit is a thin client of the same native workstation API as the React
terminal. It does not collect exchange data, maintain a second order book,
calculate financial metrics, rebin history, or execute orders. A **separately
running local backend is required**. No private credentials are requested.

## Run

From the repository root in the locked Python environment, start the backend:

```sh
python -m src.workstation.cli serve
```

Then, in another terminal using the same environment:

```sh
streamlit run crypto_lob.py --server.address 127.0.0.1
```

Open the Streamlit URL, normally `http://127.0.0.1:8501`. The API origin defaults
to `http://127.0.0.1:8000`; the sidebar permits only HTTP loopback origins
(`127.0.0.1`, `localhost`, or `[::1]`). Select the source and **exact native
instrument**, then select **Open / apply native selection**. The source catalog
can be refreshed while the backend prepares metadata. **Connect / reconnect**
probes the candidate's native catalog before releasing this document's previous
API session. An unavailable candidate leaves the working connection/session intact.
After a successful replacement, open the selected source/instrument.

The default backend is the offline, synthetic 180-second / 24-stream memory
scenario, not a recording or live exchange history. Existing operator-managed
recordings and public live data require explicit backend options:

```sh
python -m src.workstation.cli serve --capture data/workstation/CAPTURE_ID
python -m src.workstation.cli serve --network
```

Run only the intended backend, not all three commands together. The client uses
opaque catalog IDs; it never submits capture paths. Live source ownership,
recording verification, source readiness and instrument metadata remain the
backend's responsibility. Do not start a competing live collector beside a
recorder. See [API](API.md) for server options and constraints.

The sidebar links the full React terminal at the configured API origin. Its
static frontend must be built as documented in [API](API.md); a missing React
build does not prevent use of these API-backed Streamlit views.

## Views and controls

- **Identity and quality:** exact native venue, API family, symbol, market type,
  instrument ID and full source-specific metadata. Source classification,
  readiness, reasons, native quality/coverage and as-of references are visible.
  A not-ready frame is explicitly diagnostic, not execution-eligible.
  Frame depth omissions are disclosed separately from native reconstruction
  coverage; grouped ladder and financial inputs retain full native depth.
- **Ladder/depth:** server-grouped native rows and server cumulative base
  quantities. Exact quantum text defaults to the source's native tick when
  known. Unknown/variable rules are disclosed by the server; display quanta
  are not executable price rules.
- **Historical liquidity:** bounded `/visualization` grids, source-monotonic
  sampling, server-persisted price anchor, explicit recenter/pan and exact
  price/time controls. The client fetches complete bounded grids rather than
  merging or rebinning history. Null cells stay gaps and covered zero stays
  zero. Bid and ask cells are separate. The color ceiling is explicit and fixed
  until the operator changes it; it is a plotting-only control, not adaptive
  normalization. Server step options, completeness, truncation, quality
  reasons and full exact grid/hash evidence remain inspectable. The initial
  paused demo may have no closed historical columns: advance replay.
- **Metrics/tape:** shared analytics values, units, eligibility, formula/window
  provenance, native public prints, aggressor provenance, unknown valuation,
  subscription-history labels and exact evidence. Missing values say
  `Unavailable`; they are never replaced by zero.
  Bounded profile/evidence prefixes show omitted counts; metrics still use the
  full eligible native window.
- **Size-cost comparison:** exact base quantity or quote budget, selected side,
  optional explicit assumed fees with provenance/date, and explicit latency /
  rebalance scenarios. The API supplies the native depth walk and directed
  matrix, including incompatible/excluded pairs, partial fills and unknown
  fees/FX/inventory. After submission, polling recomputes the same request at
  the current cursor; selection, source or generation changes discard it.
  This is hypothetical observation, not executable profit or an account view.
- **Replay:** actual API play, pause, single/multi-delivery step, exact speed,
  delivery-index seek and reset. Live sources do not offer replay controls.
  Seek/reset/selection generations fence presentation and costs. Playback
  advances only in the backend; the UI never invents history or pacing.

Economic metadata, inputs, timestamps, generations and evidence stay exact
JSON strings through controls, requests, tables and expandable evidence.
Raw JSON trees start collapsed, including inside expanders: opening a panel does
not eagerly mount every historical cell/trade as a browser DOM node. Full exact
evidence remains available for deliberate drill-down.
Price and source-time coordinates remain exact categorical strings, so adjacent
sub-float prices cannot collapse into one row/level. Ladder price categories
follow server bid/ask ordering and represent displayed levels, not proportional
gap widths; heatmap categories are the server's uniform exact grid rows.
Only cumulative-quantity plot heights and color intensities use floating point.
Hover data retains exact price/quantity text. Recenter resets the keyed viewport
offset to zero before requesting a new server anchor, including later polls.
Python response receipt is **not browser paint latency**; this client does not
issue frame or visualization paint ACKs.

## Ownership, bounds and unavailable behavior

Each Streamlit document stores its own HTTP client, backend session ID,
generation and latest frame in document-local session state; no global cached
client or shared session is used. Operator forms/actions run outside the timed
observation fragment. Replay/cost callbacks consume submitted intent before
polling can request another run. Replay parameters are keyed by session,
generation and instrument, so seek/reset/reconnect replace prior form state.
Generation-bound cost output is replaced before slower historical plotting.
Recenter is consumed once by the matching session/generation, not on every poll.
Polling defaults to every two seconds, with five/ten-second options or manual
refresh. Requests are sequential, use one connection, disable environment
proxies and redirects, and have two-second connect/pool and ordinary ten-second
read/write timeouts. Session creation alone allows 30-second read/write
initialization while the backend verifies its managed capture; verification is
not skipped. Streaming responses check a 30-second elapsed budget between
chunks (a blocked read can add at most that operation's read timeout), cap
decoded output at 12 MiB, and cap requests at 64 KiB. Captures exceeding these
bounds fail explicitly rather than enabling unbounded requests.
No automatic retries, remote fallback or fabricated market evidence is used.

**Close this document's API session** explicitly deletes only that session.
Source replacement deletes the previous session before creating another;
reconnect also closes the previous HTTP pool. Browser disappearance cannot
reliably run Python cleanup: the backend reaps unattached HTTP sessions after
60 idle seconds. Turning off polling and leaving the page idle can therefore
expire its session. Reconnect and open the source again; the UI never adopts
another tab's session. Failed cleanup is disclosed, and the backend's reaper
remains the fallback. Server session-capacity limits still apply.

Backend startup, unavailable sources, expired sessions, rejected economic
inputs and failed history/ladder/cost requests are shown as actionable errors.
The previous presentation is not used as a hidden fallback for a failed frame
request. Start/check the backend and use reconnect. Source loading and partial
market coverage are not transport success or financial health.

## Operator smoke checklist

1. Start Streamlit without the backend. Confirm the startup command and
   reconnect controls are visible, with no collector/network fallback.
2. Start the default backend; reconnect, refresh the source catalog as needed,
   and open an exact demo native instrument. Confirm synthetic/not-recorded
   labeling, native metadata, quality and exact metric/tape evidence.
3. Step multiple deliveries, play, pause, change exact speed, seek backward and
   reset. Confirm cursor changes and generation fencing. Step far enough to
   populate history. Change price step using a listed exact server option,
   pan/recenter, and switch bid/ask; inspect null cells, fixed color ceiling,
   hashes and retention/quality reasons without local rebinning.
4. Submit a base-size comparison, then a quote-budget comparison. Check partial
   fills and exclusions. Leave fees unknown; then explicitly supply a rate,
   provenance and ISO date. Confirm unknown net results are not financial zero.
5. Open another independent browser tab/document, select a different native
   instrument and cursor, and verify that one document's seek/close does not
   change the other. Switch instruments and verify metadata, quality and
   comparison inputs do not leak from the prior selection.
6. Close a session explicitly and reopen it. Disable polling for over 60
   seconds, refresh, and verify actionable expiration/reconnect behavior.
   Stop the backend and verify unavailable state rather than retained healthy
   charts. Restart and reconnect successfully.
7. With an operator-authorized capture or explicit public-live backend, select
   that catalog source and verify source-specific metadata/classification;
   live must omit replay controls. Inspect the layout at laptop width and use
   expandable evidence/controls and horizontally scrollable exact tables.

Regression entrypoint for the integration owner:
`python -m pytest tests/test_legacy_api_client.py`. Run it with the shared-core
API/visualization tests and the actual two-document Streamlit smoke above.
