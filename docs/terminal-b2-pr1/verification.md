# Production verification · PR 1

## Evidence status and boundaries

This is a compact **historical report**, not a fresh full verification or an untouched log. The original publication is `6d83e67a5bf9a473a5a727488b3f8d8c90428dc7`; its reviewed implementation is `80b80214deb90a483c5d1826123202ea104a7eb4`, and the unmodified main comparison is `7b3c0768ed4bff30b4ef89c8030da458e48a6e47`. [Accepted receipt](evidence/gates/accepted.json) extracts the original gate results, including failed runs. [Retention](retention.md) and [equivalence](equivalence.md) distinguish retained bytes from new summaries and publication-only changes. The later PR #27 failure and remediation are recorded separately below.

Original full logs, trace extracts, collector manifests, excluded captures, one-off inspection commands and private design inputs remain **local-only**. The private review object `1933ec89c2` and design object `ce9e5a91f2` are provenance, not public dependencies or links. No private design renderer, authored market population or demonstration heat-band scale enters production.

Three separate claims must not be conflated:

- **Historical full results:** 2,221 Python, 279 frontend and final 116 Chromium passes, with three declared live-only browser skips; failures and repairs below remain part of that history.
- **Fresh publication smoke:** the configured `.githooks/pre-push` was invoked locally with no push. It calls `./tools/check.sh --fast`; it exited 0 (Ruff clean, 99 files already formatted, one native smoke and three frontend tests passed). See the [timestamped output](evidence/gates/publication-fast.txt). The smoke tests the unchanged implementation; it is not a repeat of the full offline or browser gates.
- **Remote protected `offline` CI at the local publication milestone:** not yet run; owner approval was still required before pushing. This historical boundary is not the current PR status: the subsequent actual GitHub check failed, as recorded below. No new live, performance or release proof is claimed.

## Named-grid CI remediation

The required [PR #27](https://github.com/karisheen/Limit-Orderbook/pull/27) `offline` [Ubuntu job](https://github.com/karisheen/Limit-Orderbook/actions/runs/46039219099/job/140642353068) failed on published `6d83e67a`: **2,221 Python and 279 frontend tests plus production build passed; Chromium finished 115 passed / 1 failed / 3 live-only skips**. The failed assertion measured the chart after rejecting an overlapping move. That same named test passed in the earlier Ubuntu main run `45766318882` and in **5/5 fresh Darwin runs** of the original 1440×900 case. Those passes did not explain or invalidate the CI failure.

The CI assertion recorded the chart DOMRects. The Ubuntu trace recorded both panels' inline assignments and the surface's `--grid-row-height`; the screencast shows `Cumulative delta added.` on one line and `Panels cannot overlap. Move into empty space first.` on two. It did **not** record a toolbar DOMRect, line-fragment rectangles, localStorage snapshots, observer callback timing or the resolved Linux glyph font.

| Published bundle: before → after rejected ArrowDown | Observation |
|---|---|
| Ubuntu 1440×900 chart | `(x, y, width, height)` = `(9, 254.578125, 1303, 576.375)` → `(9, 260.953125, 1303, 570)` |
| Ubuntu inline row height | `42.53515625` → `42.00390625` px; chart/CVD logical cells unchanged |
| Darwin 1440×900, Graphite and Paper | One → one notice line; chart box unchanged |
| Darwin 1536×900, Graphite and Paper | One → two lines; chart y `+6.375`, height `−6.375` px |
| Strengthened test, old real bundle, 1536×900 Paper | Measured toolbar `42` → `48.375` px; exact-height assertion fails |

The native Darwin reproduction explains the exact number. At 1536×900, the notice had 240.515625 CSS px of width. A 30px control set the flex row height for the one-line acknowledgement. Two 18.1875px line boxes needed **36.375px**: the toolbar grew by **36.375 − 30 = 6.375px**. Its top did not move. The grid surface below moved down by that amount and its content height fell from `576.421875` to `570.046875`. The six-pixel row gaps did not change. Twelve rows each lost `0.53125px`, leaving the chart bottom at `830.953125` in both measurements. This independently reproduces the Linux chart and track deltas; the exact Linux toolbar height remains an inference.

The notice mutation preceded the row-size update in the local observer record. Chart `(0,0,11,12)` and CVD `(0,12,3,3)` retained their `(x,y,w,h)` in the rendered grid **and** in the committed v2 preferences. Rejecting immediately or waiting for the successful Add's debounced persistence produced the same shift. Focus stayed on **Move Chart**; ancestor scroll positions and source/header/status geometry were stable. The same native owner, Coinbase BTC-USD spot, generation 2 and paused demo cursor `5000/6970` were retained across the operation. Ordinary analytical POST reads are not mutations. Three diagnostic contexts recorded another create only after their `complete` checkpoint, outside the measured transition. At Darwin 1440 the notice occupied its own 1408px-wide flex line; at 1536 it shared a line with the controls. Stock `.SF NS` was observed locally, not assumed to be the Ubuntu font.

The **production correction** reserves two line-heights for the existing live status only during desktop grid editing. A minimum, not a fixed height, preserves full wrapping and permits longer messages to grow. On the corrected 1536 surface the notice still wrapped, but the toolbar remained `48.375px` and **neither panel moved or resized**. At Darwin 1440 the reservation costs `18.1875px` of available space while editing; it does not apply to the non-editing toolbar or narrow stacked layout.

The **test correction** first observes the persisted successful Add, then checks the rejected command against stored cells, inline spans, actual tile rectangles relative to the grid content, the measured gap to CVD and exact toolbar height. It verifies the full visible polite error, focused handle, native identity and cursor, then continues through valid resize, save/load/rename/reload/delete. The original 1440 Graphite case is retained; 1536 Paper reaches the observed wrap boundary on Darwin. Waiting for committed storage distinguishes the operations; it does not mask the production defect. No pixel tolerance, sleep, automatic retry or timeout extension was added.

For focused reproduction, use the locked setup and native server below; keep diagnostic outputs private:

```sh
# From the repository root, with the candidate server ready:
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/lob-named-grid.XXXXXX")"
export PLAYWRIGHT_BASE_URL=http://127.0.0.1:8031
npm --prefix frontend run test:e2e -- e2e/chart-redesign.spec.ts \
  -g 'named grid editing' --repeat-each=5 --output="$RUN_ROOT/results"
```

The [comparison](evidence/regressions/named-grid-feedback.png) uses the same unscaled crop of four native Graphite screenshots at 1536×900, in separate camera runs so taking a screenshot did not separate the measured actions. Captions are outside application pixels. Full CI artifacts, raw instrumented timelines, native IDs and logs stay local. No local Linux VM, container runtime or configured Linux browser was available; the corrected Ubuntu run remains a required remote gate after separate publication authorization.

### Fresh local results

All runs below used the locked Darwin x64 checkout, production bundle, actual offline Python backend, one Chromium worker and zero automatic retries. The main diagnostic server listened on loopback port 8035. For manual reproduction: apply Price focus, enter grid editing, resize the chart left by one column, add Cumulative delta, focus **Move Chart**, then press **ArrowDown**. The complete automated case also exercises named layouts and restoration. Set `PLAYWRIGHT_BASE_URL` to your own local server; keep Playwright `--output` directories outside the public packet.

| Check | Observed result |
|---|---|
| Original named-grid test, original bundle; `-g 'named grid editing' --repeat-each=5` | 5/5 at 1440×900. The strengthened 1536 Paper case on that same bundle failed at the exact toolbar assertion: expected `42`, received `48.375`. |
| Corrected focused command above | **10/10**: five 1440 Graphite and five 1536 Paper. |
| Read-only width sweep, 15 widths from 1280 to 1640 at height 900 | Seven widths on the published bundle exhibited `+6.375/−6.375` chart displacement. On the corrected bundle all 15 retained exactly the same chart rect before and after rejection. |
| Related browser command below | **6/6**, covering touch and keyboard editing, historical preferences, Graphite/Paper contrast, boundaries and focus, and native session preservation. |
| `node tools/capture_shell_frame.mjs after "$CAPTURE_DIR"` | **19 captures, seven native contexts, all six themes**. All 19 had meaningful paints, matching prior non-edit grid and canvas geometry and no horizontal overflow. Fourteen `net::ERR_ABORTED` requests were recorded; zero page, HTTP or response-read errors or unexpected network requests. |
| `.githooks/pre-push` invoked locally, without pushing | Passed. Scoped lint/format, one Python smoke, JS syntax and three frontend format tests. |
| `./tools/check.sh --durations=20` | Passed: **2,221 Python / 279 frontend**. Scoped lint, format, mypy, frontend lint, types and production build passed. One pytest warning and a bundle-size advisory were not failures. |
| `npm --prefix frontend run test:e2e` against the actual backend | **116 passed / 1 failed / 3 live-only skips** out of 120. Both named-grid cases passed. **The complete browser gate is not green.** |

```sh
npm --prefix frontend run test:e2e -- \
  e2e/chart-redesign.spec.ts e2e/shell-frame.spec.ts e2e/workspace.spec.ts \
  -g 'grid touch gestures|narrow grid editing|historical v2 preferences|shell controls have readable text|unified chart navigation and grid sizing' \
  --output="$RUN_ROOT/related"
```

The full-run failure was the **390px narrow-grid parity** scenario in the unchanged `openDemo()` setup, *before* preset selection or editing. The `POST /api/v1/replay` seek did not deliver an HTTP acknowledgement (`status -1`, `net::ERR_ABORTED`). The UI reported an ambiguous 15s mutation timeout and displayed delivery `5000/6970`; the test reached its unchanged 45s limit. This is **not** evidence that the server rejected or did not commit the seek. The narrow scenario passed in the earlier six-case group. As a separate control on a newly started backend with zero active sessions, `npm --prefix frontend run test:e2e -- e2e/chart-redesign.spec.ts -g 'narrow grid editing'` passed **1/1** without any code, fixture, retry or timeout change. The control does not replace the full-run result or establish why the acknowledgement was late.

**Independent technical review: no finding on the grid correction.** The reviewer challenged causation against the Ubuntu trace and measured wrap boundary, checked the published-bundle red control, persistence, focus, exact placement, accessibility and session fences, and inspected the unscaled comparison and corrected receipts. They independently confirmed that the 390px failure occurred before grid editing, outside the changed media query; indirect run-order effects are unproven. No replay behavior was changed and no test was suppressed. PR #27 remains on the previously published head with its failed protected `offline` check; no remote rerun was requested.

## Reproducing native offline captures

These are optional reproduction instructions, **not commands executed for this publication extraction**. Use CPython **3.13.5**, uv **0.8.4**, Node **22.22.0**, npm **10.9.4**, locked Playwright **1.58.1** and its cached Chromium (**145.0.7632.6** in the historical run). Historical host: Darwin x64. Make those tools available on `PATH`; an existing repository-local `.tools/node/bin` may supply Node. The Python/npm/browser caches must already contain the locked dependencies and browser. If missing, stop rather than silently changing pins or enabling network access.

Build and serve the **public candidate in the existing production checkout**. This also works after a squash with equivalent implementation trees; it does not require the historical implementation commit to remain available. The optional main comparison uses one disposable export and the candidate's locked Python environment. Both servers use distinct empty temporary state directories, never a personal ledger. Build outputs stay in their respective trees; receipts and logs go only to a **new system temporary root**, not public evidence. Keep this shell open; its trap stops servers but preserves logs and captures.

```sh
set -eu
CANDIDATE="$PWD"
MAIN=7b3c0768ed4bff30b4ef89c8030da458e48a6e47
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/lob-pr1-reproduce.XXXXXX")"
export RUN_ROOT
export UV_PROJECT_ENVIRONMENT="$CANDIDATE/.venv-b2"
uv sync --locked --offline --python 3.13.5 --group dev
npm --prefix frontend ci --offline --no-audit --no-fund
npm --prefix frontend run build
mkdir "$RUN_ROOT/state-candidate"
"$UV_PROJECT_ENVIRONMENT/bin/python" -m src.workstation.cli serve \
  --port 8031 --state-dir "$RUN_ROOT/state-candidate" \
  >"$RUN_ROOT/candidate-server.txt" 2>&1 &
CANDIDATE_PID=$!
MAIN_PID=
trap 'kill "$CANDIDATE_PID" ${MAIN_PID:+"$MAIN_PID"} 2>/dev/null || true' EXIT
printf 'Fresh reproduction root: %s\n' "$RUN_ROOT"
```

For the optional unmodified-main comparison, continue in the same shell. Export the fixture with the source and frontend; do not create a second Python environment:

```sh
mkdir "$RUN_ROOT/main" "$RUN_ROOT/state-main"
git archive "$MAIN" frontend src tests/fixtures/workstation \
  | tar -x -C "$RUN_ROOT/main"
npm --prefix "$RUN_ROOT/main/frontend" ci --offline --no-audit --no-fund
npm --prefix "$RUN_ROOT/main/frontend" run build
PYTHONPATH="$RUN_ROOT/main" "$UV_PROJECT_ENVIRONMENT/bin/python" -P \
  -m src.workstation.cli serve --port 8032 --state-dir "$RUN_ROOT/state-main" \
  >"$RUN_ROOT/main-server.txt" 2>&1 &
MAIN_PID=$!
printf 'Fresh reproduction root: %s\n' "$RUN_ROOT"
```

Wait for each requested native server to report readiness in its temporary log; if one fails, stop and inspect it. Do not substitute another server on the same port. No `--network`, recording, user state directory or ledger is used. In that same shell, run the **existing candidate collector** against the candidate and, optionally, main:

```sh
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8031 \
  node "$CANDIDATE/tools/capture_shell_frame.mjs" after "$RUN_ROOT/after"
# Optional: only after starting the main reference above.
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8032 \
  node "$CANDIDATE/tools/capture_shell_frame.mjs" before "$RUN_ROOT/before"
```

The interface is `before|after OUTPUT_DIRECTORY`. Never point it at `docs/terminal-b2-pr1/evidence`, an existing receipt, another checkout or a user ledger. It refuses to overwrite receipts; every rerun needs a fresh temporary root. Historical collector SHA-256: `2306cd9c236b633cf21ac1da8d958fedc90b033b87e8eff49beabc0d1dd76a67`. A rerun creates new observations, not replacements for the retained historical files.

### Exact workload and ownership

For manual inspection, use a disposable browser context and open `http://127.0.0.1:8031/?source=synthetic-demo-v3-180s` (main uses port 8032). Stay in **one document** while changing themes, views and viewport; do not seed preferences, inject application state, intercept prices/responses or substitute a renderer. Native source: synthetic 180 seconds / 38 streams, in memory, **not a recording or market history**.

Use the exact primary `coinbase:advanced-trade:spot:BTC-USD`, Order flow/balanced six-panel layout, **Price action → Session trades**, **1m**, observed `session_trades`. In the visible **Delivery** control enter **3485**, press **Seek**, and wait for admitted **D3485/6970**, paused at 1×, generation 2, populated book/tape and native candles. Price is USD per BTC; native volume is BTC. Both observed buckets have O/H/L/C `60000`; first bucket `[1793545260000000000, 1793545320000000000)` has `0.41` BTC and is closed, second `[1793545320000000000, 1793545380000000000)` has `0.48` BTC and is open. These are native nanosecond boundaries, not a fabricated continuous price history.

Each final matrix used seven isolated contexts/documents and **one native session creation per document**; session identifiers differ between documents and reruns. Theme/view changes do not create additional owners. The matrix covered six DPR1 viewports (1280×800, 1440×900, 1920×1080, 2560×1440, 3840×2160, 390×844) plus 1440×900 DPR2. All six production theme IDs were captured at 1440×900; Graphite (`dark-dull`) also supplied Footprint and Liquidity. A clean default entry is distinct from this explicit pin: main opened Midnight, 1h, Footprint; implementation opens Graphite without changing the existing view/layout defaults.

## Immutable gate history

Commands below describe **completed historical runs**, not instructions to rerun full gates during publication. No hook, test, timeout policy, lockfile or required CI job was bypassed.

| Historical command | Recorded result |
|---|---|
| `./tools/check.sh --fast` | Ruff clean; 99 formatting checks; 1 native offline capture/replay smoke passed, 0 failed/skipped; browser-tool syntax; 3 exact numeric/clock frontend tests passed, 0 failed/skipped. |
| `./tools/check.sh --durations=20` | 2,221 Python passed, 0 failed/skipped under `-m 'not network'`; one Starlette TestClient deprecation warning; mypy clean on 45 source files; tool syntax, ESLint `--max-warnings 0`, 279 frontend passed with 0 failed/skipped, TypeScript and Vite production build passed. |
| `PLAYWRIGHT_BASE_URL=http://127.0.0.1:8031 npm --prefix frontend run test:e2e` — first full run | **115 passed / 1 failed / 3 skipped** of 119. |
| Same command — second full run | **113 passed / 3 failed / 3 skipped** of 119. |
| Same command — final full run, after the last test edit | **116 passed / 0 failed / 3 skipped** of 119; one worker, zero retries, 13.1 minutes. Includes all six PR 1 cases and repaired lifecycle/canvas/stale cases. |

The three skips require public live sources: generation-fenced live recording/replay, automatic-demo-to-live network-launch migration, and streaming-only native candidates. Networking was not enabled to convert skips into passes. Existing Vite large-chunk and Lucide ignored-`use client` advisories, npm ESLint deprecation and browser `NO_COLOR`/`FORCE_COLOR` warnings are not hidden. The second failed browser run also emitted `PromiseRejectionHandledWarning` during obsolete-body setup failure.

### Failed full runs and test-only repairs

1. **Held mutation (115/1/3):** failed before its timeout assertion. Setup accepted already-nonzero bootstrap paint `169/gen1` as the post-Step cursor; the real Step delivered `170/gen1`, while Reset committed `0/gen2` in the same session with its HTTP response held. This did not prove Reset leaked through the busy fence. The test now waits for the real Step response and exact displayed cursor before holding Reset. The 15s application deadline, ambiguity notice, late-response behavior, generation evidence, one-owner and no-retry assertions remain. The focused repair passed **1/0/0**.
2. **Generation cutover (two of the 113/3/3 failures):** canvas and laptop setup decoded an obsolete generation-1 `/market-data` body after seek committed generation 2. Chromium returned `Network.getResponseBody: No data found for resource with given identifier`; canvas setup had not reached a repaint assertion. Both helpers now reject foreign session/generation requests **before body decoding**, then still require matching decoded identity, completed drain and applicable nonempty native data. No error swallowing, retry or weaker paint assertion.
3. **Stale observation (third failure):** separate label/readout assertions crossed a real session replacement. The label assertion completed 5.92s after stream suppression; old values correctly disappeared at replacement. A read-only observer installed **before** websocket suppression captures the first actual stale DOM commit, including empty/wrong values, and checks label, generation and all five exact readouts together, followed by independent recovery. It neither waits for a convenient later sample nor alters the DOM.

All three transition probes subsequently passed **3/0/0** without retries, and final frontend lint/typecheck passed. These were test-only corrections; the reviewed production bundle, final capture matrix and full offline gate did not change. Historical fault tests deliberately hold/block/fault transport or drawing; exact-readout stress pads native values with trailing zeroes and paging uses controlled transport fixtures. They are not naturally occurring live failures or precision samples. The matched collector intercepts neither responses nor prices.

## Capture failures and presentation corrections

- The first baseline build stopped before building (exit 127: absent isolated Python environment). Locked offline setup repaired that prerequisite. A partial main export also omitted `tests/fixtures/workstation/demo-v3.json`; the server refused startup until the fixture was restored from the **same main object**, not replaced with invented data.
- The first baseline collector saved **zero images**: it treated **Choose preset** as persistent selection, although the action returns to its prompt. It was corrected to check all six mounted panel IDs and logical geometry; the application preset was not changed.
- The first candidate collector saved **eight Price images**, then timed out expecting a `session_id` field on `VisualGrid`; that identity belongs to the request. No loading/unrelated canvas was accepted as liquidity evidence.
- Four initial shell tests and then a 19-image candidate matrix passed, but that did **not** establish laptop usability. A 62px book header left only 2px of a 22px ask visible in the initial 1280px probe. The later regression exposed a clipped best bid despite no horizontal overflow and passing chart-paint samples: [unchanged failed PNG](evidence/regressions/clipped-bid-1280.png), [sanitized failure excerpt](evidence/regressions/clipped-touch.txt). That shell run was **4 passed / 1 failed / 0 skipped**.
- A bounded-deficit attempt still failed **0/1/0**: a 100px body could not fit a 19px coverage header plus hardcoded 86px ladder minimum. Final short-screen sizing derives from the two touch rows, spread and column header; asks scroll from the touch. Price toolbar wrapping had also hidden the sparse 60,000 price; horizontal toolbar scrolling and adaptive plot minimum repaired this without changing grid, readout or optional positioning.
- Both touches, latest print and study interiors then passed at 1280/1440, but the shell run still ended **5/1/0**: keyboard focus reached Volume Z's `100` lookback while its right edge lay about 1.8px outside the header. Scroll padding/margins and compact group spacing fixed it; controls/assertions were retained. The final focused laptop regression passed **1/0/0**.

A review preview appeared to lose shell glyphs in Footprint. The **initial source JPEGs**, compared with Liquidity at 1440×900, had identical RGB pixels in wordmark `(0,0,125,40)`, source/replay `(0,49,1439,177)`, delivery `(1230,120,1439,153)` and source/coverage footer `(0,860,450,899)` rectangles: empty Pillow difference bounds, maximum RGB difference **0**. A separate comparison of final source JPEGs also found zero difference in sampled shell regions, using slightly different rectangles; the coordinates above describe only the initial comparison. The source-scale PNG crop showed intact glyphs. The discrepancy was a preview artifact, not an established JPEG/runtime defect; the crop and original comparison commands are local-only. The retained [final Footprint](evidence/after/after-dark-dull-1440x900-dpr1-footprint.jpg) and [Liquidity](evidence/after/after-dark-dull-1440x900-dpr1-liquidity.jpg) are original source rasters, not repaired previews.

## Retained observations and limits

The complete historical final matrices contained **13 before / 19 after**, seven contexts each, with 13 exact `(theme, CSS viewport, DPR, view)` pairs. The reviewed matrices established matching first/last native candle records in those **13 historical pairs**, together with recorded image dimensions/digests. Only **four matched pairs are retained publicly**. The public [selection manifest](evidence/captures.json) is a **new compact summary**, retaining those four before images, 16 after images, two ancillary native observations and one failed regression PNG (23 original-byte images); it is not the full collector manifest.

Request aborts are not silently zeroed: pre-final centered baseline **15 aborts / 2 response-read errors**; final baseline **12 aborts / 0 response-read errors**; final candidate **16 aborts / 0 response-read errors**. Both final matrices recorded zero HTTP errors, page exceptions and horizontal document overflow. Canceled discovery traffic is recorded, not by itself proof of a new product incident. The final collector avoids centering an already visible canvas because doing so can scroll its toolbar out of the tile.

At 1920×1080 Graphite, the measured candle field moved from y=469 on main to y=385 CSS px, growing from 1340×193 to 1340×236. At 1280×800 it remains a compact **862×76** compromise with price line, volume and exact readout visible together; existing Price focus/expansion remain available. These are geometry measurements, not frame-rate improvements. A 390px analytical capture cannot show every stacked pane at once.

The two ancillary images precede the final focus/contrast refinements; they are **not final matrix substitutes**. In one disposable document, visible controls selected `binance-global:spot-v3:spot:BTCUSDT`, 1m, Seek 3485: [60,001 ask and 59,999 bid](evidence/inspection/binance-touch-1280.jpg) were visible together; a real wheel reached 60,002. This checks two demo levels per side, not arbitrary depth. In that same document, selecting `hyperliquid:info:perpetual:BTC`, Seek 5000, Price action → OI & funding produced [1260 base Δ 55 and 0.00002 fraction per 1h](evidence/inspection/hyperliquid-positioning-1920.jpg): BTC base, USDT quote, USDC settlement, matching the native positioning DTO, not a forecast or Paper assumption. The local-only narrow/Seek-0 observations used that same owner; explicit selection/seek legitimately changed generation, and unavailable statistics displayed `—`, not fabricated zeroes.

[Final ink samples](evidence/inspection/contrast-final.json) compare computed text ink to decoded **lossless screenshot** background pixels, not lossy JPEG estimates. One 1920×1080 DPR1 price/size scene, six roles in each of six themes: bid/ask touch, buy/sell tape, bounded-depth warning and identity. Initial Paper samples were 4.36/4.31:1; Daylight bid 2.44:1 and warning 3.73:1. Reduced side-fill opacity retained bar width/magnitude; darker Daylight financial inks retained its theme ID. Final **36/36 ≥4.5:1**, minimum **5.243:1**. This does not cover every state/pixel; control boundaries and actual Tab focus were checked separately. Historical token mapping was **27/27 exact** for Graphite and **27/27 exact** for Paper; see [tokens](tokens.md).

The [visual](review-visual.md) and [technical](review-technical.md) reviews accepted the corrected local scope, not a new independent publication run. Browser screenshots, synthetic observations and geometry do not establish physical 4K readability, cross-browser/assistive-technology support, sustained live capacity, execution quality or performance improvement.
