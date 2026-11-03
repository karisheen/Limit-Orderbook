#!/usr/bin/env node
/** Actual offline terminal/browser benchmark. Never injects market responses. */
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { cpus, platform, release, totalmem, tmpdir } from 'node:os';
import { readFile, writeFile, mkdir, readdir, stat, lstat, realpath, mkdtemp, rm } from 'node:fs/promises';
import { basename, dirname, resolve, join, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = await realpath(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
const require = createRequire(join(root, 'frontend/package.json'));
const options = { url: 'http://127.0.0.1:8000', source: null, seconds: 30, speeds: ['1', '2'], width: 1440, height: 900, output: null, manifest: null, instrument: null, start: null, python: join(root, '.venv-upgrade/bin/python') };
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index], value = process.argv[index + 1];
  if (!value) throw new Error(`Missing value for ${key}`);
  if (key === '--url') options.url = value;
  else if (key === '--source') options.source = value;
  else if (key === '--seconds') options.seconds = Number(value);
  else if (key === '--speeds') options.speeds = value.split(',');
  else if (key === '--viewport') [options.width, options.height] = value.split('x').map(Number);
  else if (key === '--output') options.output = resolve(value);
  else if (key === '--manifest') options.manifest = resolve(value);
  else if (key === '--instrument') options.instrument = value;
  else if (key === '--start-delivery') options.start = value;
  else if (key === '--python') options.python = resolve(value);
  else throw new Error(`Unknown option ${key}`);
}
const url = new URL(options.url);
if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('--url must be a loopback HTTP origin, without credentials, path, query or fragment');
const port = Number(url.port || 80), host = url.hostname === '[::1]' ? '::1' : url.hostname;
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Port must be 1..65535');
if (!options.output || !Number.isFinite(options.seconds) || options.seconds < 2 || options.seconds > 120) throw new Error('--output is required; seconds must be 2..120');
if (![options.width, options.height].every(value => Number.isInteger(value) && value >= 600 && value <= 2000)) throw new Error('Viewport dimensions must be 600..2000');
if (!options.speeds.length || options.speeds.length > 6 || new Set(options.speeds).size !== options.speeds.length || options.speeds.some(value => !['0.25','0.5','1','2','4','8'].includes(value))) throw new Error('Unsupported or duplicate replay speeds');
if (options.start !== null && !/^\d{1,12}$/.test(options.start)) throw new Error('Start delivery must be a nonnegative integer string');
options.source ??= options.manifest ? basename(dirname(options.manifest)) : 'demo';
if (options.source !== 'demo' && !options.manifest) throw new Error('Recorded benchmark requires --manifest');
if (options.manifest && (basename(options.manifest) !== 'manifest.json' || options.source !== basename(dirname(options.manifest)))) throw new Error('--manifest must be capture/manifest.json and --source, if supplied, must exactly equal the capture directory basename');
await mkdir(dirname(options.output), { recursive: true });
// Reserve before any child or browser starts; even setup failures leave evidence.
await writeFile(options.output, '{}\n', { flag: 'wx' });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const errorText = error => String(error?.stack ?? error).slice(0, 8000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitWithin(promise, milliseconds) {
  let timer;
  try { return await Promise.race([promise, new Promise(resolve => { timer=setTimeout(resolve,milliseconds); })]); }
  finally { clearTimeout(timer); }
}
const limits = { samples: 20000, errors: 100, responseTasks: 128, apiRequests: 256, logBytes: 65536, assetBytes: 16 * 1024 * 1024, runtimeReportBytes: 4 * 1024 * 1024, runtimeFiles: 4096, drainMs: 15000, startupMs: 120000 };
const minimumSamples = { ack: 20, draw: 20, frame_interval: 120, input_paint_opportunity: 20, telemetry: 20 };
const report = {
  schema: 'terminal-browser-benchmark-4', status: 'running', command: [process.execPath, ...process.execArgv, ...process.argv.slice(1)],
  runtime: process.version, platform: `${platform()} ${release()}`, cpus: cpus().map(cpu => cpu.model), logical_cpus: cpus().length, memory_bytes: totalmem(),
  viewport: { width: options.width, height: options.height }, limits, minimum_samples: minimumSamples,
  requested_seconds_per_scenario: options.seconds, tool_sha256: null, scenarios: [], served_assets: [], provenance_errors: [],
  server: { owned: true, root, python: options.python, host, port, pid: null, bind_confirmed: false, stdout: '', stderr: '', stdout_bytes: 0, stderr_bytes: 0, signals: [], exit: null, runtime_snapshots: [] },
  limitations: [
    'Publication-to-ACK is a server-monotonic round-trip upper bound, not synchronized one-way paint or GPU completion. Exceeding its budget does not establish actual one-way/GPU latency.',
    'ACK cohorts use request-issue time in the Node monotonic half-open measurement interval; pre-window requests are excluded, post-window responses are drained, and unresolved or failed requests invalidate certification.',
    'Browser receive-to-paint includes decode/render and two animation-frame opportunities; draw measures JavaScript Canvas submission only.',
    'Both visible chart panes contribute draw, visualization ACK and request samples. Interaction geometry and chart-progress checks target the primary Liquidity pane; these aggregate timings are not per-pane certification.',
    'Instrumentation, automation, screenshots and concurrent workstation load perturb timing; runs do not evict OS/browser caches.',
    'Checkout, dependency-lock and dataset hashes bracket the run. Before each cohort and at shutdown, the owned child hashes its resolved executable, shared Python library when applicable, and all loaded module files. Hashing occurs outside timing cohorts. This is not an OS/transitive shared-library attestation or protection against adversarial mutate-and-restore activity.',
    'Frontend source hashes are context only; built bytes and actual browser document/script/stylesheet responses identify the measured surface.',
    'Input paint opportunities are responsiveness evidence, not proof of a particular GPU pixel completion.',
    'Coalesced book revisions and frame ACK evictions are retained as distinct telemetry, not relabeled as dropped market events.',
    'Displayed data ages are observational 500ms DOM samples of the rendered Freshness badges, not response-arrival estimates. Browser performance.now and Node observation times have distinct origins and are never subtracted.',
    'Source-clock lag is same-clock receive-monotonic evidence-to-highwater distance, not exchange, transport or wall-clock latency. Evidence residence and optional response residence are separate browser elapsed ages; badge values update at the UI cadence.',
    'Unavailable, missing, offscreen, partial and transitioning displayed evidence is reported without assigning zero or inventing a freshness SLA. Rendering-budget success is not freshness or live-capacity certification.',
    'No live collector, private credentials, execution or external deployment is started.'
  ]
};
const provenanceError = message => {
  if (report.provenance_errors.length < limits.errors) report.provenance_errors.push(message);
  else report.provenance_errors_omitted = (report.provenance_errors_omitted ?? 0) + 1;
};
const persist = () => writeFile(options.output, JSON.stringify(report, null, 2) + '\n');
const quantiles = values => {
  const sorted = values.filter(value => typeof value === 'number' && Number.isFinite(value) && value >= 0).sort((a,b) => a-b);
  const invalid_count = values.length - sorted.length;
  if (!sorted.length) return { count: 0, invalid_count, p50_ms: null, p95_ms: null, p99_ms: null, max_ms: null };
  return { count: sorted.length, invalid_count, p50_ms: sorted[Math.ceil(sorted.length*.5)-1], p95_ms: sorted[Math.ceil(sorted.length*.95)-1], p99_ms: sorted[Math.ceil(sorted.length*.99)-1], max_ms: sorted.at(-1), method: 'nearest rank; all retained finite nonnegative observations' };
};
async function fileHash(path) {
  const digest = createHash('sha256');
  for await (const bytes of createReadStream(path)) digest.update(bytes);
  return digest.digest('hex');
}
async function treeHashes(directory, extensions, result = {}) {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a,b) => a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.') || entry.name === '__pycache__') continue;
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Provenance tree cannot contain symlinks: ${path}`);
    if (entry.isDirectory()) await treeHashes(path, extensions, result);
    else if (entry.isFile() && (!extensions || extensions.test(entry.name))) result[relative(root, path)] = await fileHash(path);
    if (Object.keys(result).length > 10000) throw new Error('Provenance file-count bound exceeded');
  }
  return result;
}
async function codeSnapshot() {
  const dependency_manifests = {};
  for (const path of ['pyproject.toml', 'uv.lock', 'requirements.txt', 'frontend/package.json', 'frontend/package-lock.json']) dependency_manifests[path] = await fileHash(join(root,path));
  return { python: await treeHashes(join(root, 'src'), /\.py$/), frontend_source: await treeHashes(join(root, 'frontend/src'), /\.(ts|tsx|css)$/), built_frontend: await treeHashes(join(root, 'frontend/dist')), dependency_manifests, runtime_helper: await fileHash(join(root,'tools/benchmark_runtime.py')), tool: await fileHash(fileURLToPath(import.meta.url)) };
}
let datasetPath, dataset, datasetFiles;
async function datasetSnapshot() {
  const result = { manifest_or_fixture_sha256: await fileHash(datasetPath), files: {} };
  for (const file of datasetFiles) {
    const path = resolve(dirname(datasetPath), file.path);
    const rel = relative(dirname(datasetPath), path);
    if (isAbsolute(rel) || rel === '..' || rel.startsWith('../') || (await realpath(path)) !== path) throw new Error(`Unsafe capture file ${file.path}`);
    const info = await stat(path);
    if (!info.isFile()) throw new Error(`Not a capture file: ${file.path}`);
    result.files[file.path] = { sha256: await fileHash(path), size_bytes: String(info.size) };
  }
  return result;
}
const assertRunning = () => {
  if (report.interrupted) throw new Error(`Interrupted by ${report.interrupted}`);
  if (report.server.exit) throw new Error('Owned server exited before benchmark cleanup');
};
async function unusedPort() {
  const probe = createServer();
  await new Promise((resolve, reject) => {
    probe.once('error', reject);
    probe.listen({ host, port, exclusive: true }, () => probe.close(error => error ? reject(error) : resolve()));
  });
}
// Redirect pycache reads to an empty owned directory so stale checkout bytecode
// cannot masquerade as the Python source hashed before this child was started.
let browser, child, childClosed, cacheDirectory, closing = false;
function sendSignal(signal) {
  if (child && !report.server.exit) { report.server.signals.push({ signal, at: new Date().toISOString() }); child.kill(signal); }
}
const onSignal = signal => {
  report.interrupted = signal;
  sendSignal('SIGINT');
  if (browser) void browser.close().catch(() => {});
};
const onInterrupt = () => onSignal('SIGINT'), onTerminate = () => onSignal('SIGTERM');
process.on('SIGINT', onInterrupt);
process.on('SIGTERM', onTerminate);
async function startServer() {
  await unusedPort();
  assertRunning();
  cacheDirectory = await mkdtemp(join(tmpdir(), 'lob-benchmark-pycache-'));
  const args = ['-B', '-X', `pycache_prefix=${cacheDirectory}`, '-u', join(root,'tools/benchmark_runtime.py'), root, cacheDirectory, 'serve', '--host', host, '--port', String(port)];
  if (options.manifest) args.push('--capture', dirname(options.manifest));
  report.server.args = args;
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1' };
  delete env.PYTHONPATH; delete env.PYTHONHOME;
  child = spawn(options.python, args, { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  report.server.pid = child.pid ?? null;
  let bindResolve, bindReject;
  const bound = new Promise((resolve, reject) => { bindResolve = resolve; bindReject = reject; });
  const timer = setTimeout(() => bindReject(new Error('Owned server bind timeout')), limits.startupMs);
  childClosed = new Promise(resolve => child.once('close', (code, signal) => {
    report.server.exit = { code, signal, at: new Date().toISOString(), during_cleanup: closing };
    if (!closing) { bindReject(new Error(`Owned server exited (${code}, ${signal})`)); if (browser) void browser.close().catch(() => {}); }
    resolve();
  }));
  child.once('error', error => { report.server.spawn_error = errorText(error); bindReject(error); });
  for (const stream of ['stdout', 'stderr']) {
    let partial = '';
    child[stream].setEncoding('utf8');
    child[stream].on('data', text => {
      report.server[`${stream}_bytes`] += Buffer.byteLength(text);
      report.server[stream] = Buffer.from(report.server[stream] + text).subarray(-limits.logBytes).toString('utf8');
      partial += text;
      if (partial.length > 256 * 1024) { provenanceError('Owned child emitted an overlong evidence line'); partial = partial.slice(-256 * 1024); }
      let newline;
      while ((newline = partial.indexOf('\n')) !== -1) {
        const line = partial.slice(0, newline); partial = partial.slice(newline + 1);
        if (line.includes(`Uvicorn running on http://${url.hostname}:${port} `)) { report.server.bind_confirmed = true; bindResolve(); }
        for (const [prefix, field] of [['LOB_BENCHMARK_PROCESS ', 'process']]) {
          if (line.startsWith(prefix)) {
            try { report.server[field] = JSON.parse(line.slice(prefix.length)); }
            catch (error) { provenanceError(`Invalid child provenance: ${errorText(error)}`); }
          }
        }
      }
    });
  }
  try { await bound; } finally { clearTimeout(timer); }
  assertRunning();
  const response = await fetch(new URL('/health/live', url), { signal: AbortSignal.timeout(10000), redirect: 'error' });
  if (!response.ok) throw new Error(`Owned server liveness returned ${response.status}`);
  await response.arrayBuffer();
  assertRunning();
}
async function stopServer() {
  closing = true;
  if (!child) return;
  if (!report.server.exit) {
    sendSignal('SIGINT');
    await waitWithin(childClosed,10000);
    if (!report.server.exit) { sendSignal('SIGKILL'); await waitWithin(childClosed,5000); }
  }
  report.server.reaped = Boolean(report.server.exit);
  if (!report.server.reaped) throw new Error('Owned child was not reaped');
}
async function readRuntimeSnapshot(name) {
  const path=join(cacheDirectory,name), info=await lstat(path);
  if (!info.isFile() || info.size>limits.runtimeReportBytes) throw new Error('Runtime fingerprint is not a bounded regular file');
  const bytes=await readFile(path);
  if (bytes.length>limits.runtimeReportBytes) throw new Error('Runtime fingerprint byte bound exceeded');
  const value=JSON.parse(bytes);
  if (value.error) throw new Error(`Owned runtime fingerprint failed: ${value.error}`);
  if (value.schema!=='1' || value.pid!==report.server.pid || !value.files || Object.keys(value.files).length>limits.runtimeFiles || !value.files[value.executable]) throw new Error('Invalid owned runtime fingerprint');
  for (const [path,file] of Object.entries(value.files)) if (!isAbsolute(path) || !/^[a-f0-9]{64}$/.test(file.sha256) || !Number.isSafeInteger(file.size_bytes) || file.size_bytes<0) throw new Error('Invalid runtime file evidence');
  return value;
}
async function pinRuntime() {
  const index=report.server.runtime_snapshots.length, name=`runtime-${index}.json`, started=performance.now();
  sendSignal('SIGUSR1');
  while (performance.now()-started<30000) {
    assertRunning();
    try {
      const fingerprint=await readRuntimeSnapshot(name);
      report.server.runtime_snapshots.push({index,fingerprinting_wall_ms:performance.now()-started,fingerprint});
      return index;
    } catch (error) { if (error.code!=='ENOENT') throw error; }
    await sleep(50);
  }
  throw new Error('Owned runtime fingerprint deadline exceeded');
}
async function verifyRuntime() {
  const final=await readRuntimeSnapshot('runtime-final.json');
  report.server.runtime_at_end=final;
  if (!report.server.runtime_snapshots.length) throw new Error('No pre-cohort runtime fingerprint');
  if (await realpath(options.python)!==final.executable) throw new Error('Owned executable differs from the selected interpreter');
  const helper=join(root,'tools/benchmark_runtime.py');
  if (final.module_paths.__main__!==helper || final.files[helper]?.sha256!==report.hashes_at_start.runtime_helper) throw new Error('Runtime helper was not pinned');
  for (const snapshot of report.server.runtime_snapshots) {
    const first=snapshot.fingerprint;
    snapshot.unchanged=first.executable===final.executable&&JSON.stringify(first.files)===JSON.stringify(final.files)&&JSON.stringify(first.distributions)===JSON.stringify(final.distributions);
    if (!snapshot.unchanged) provenanceError(`Loaded runtime files/dependencies changed or appeared after cohort fence ${snapshot.index}`);
  }
  let sourceFiles=0;
  for (const [name,path] of Object.entries(final.module_paths)) if (name==='src'||name.startsWith('src.')) {
    sourceFiles++;
    const local=relative(root,path);
    if (isAbsolute(local)||local==='..'||local.startsWith('../')||report.hashes_at_start.python[local]!==final.files[path]?.sha256) provenanceError(`Loaded project module was not pinned: ${name} ${path}`);
  }
  if (!sourceFiles) throw new Error('No loaded project module evidence');
}
const canonicalInteger = value => typeof value === 'string' && /^(0|[1-9]\d{0,31})$/.test(value);
function progressEvidence(points, field, start, end, speed) {
  const selected = points.filter(point => point.received_at_ms >= start && point.received_at_ms < end);
  if (selected.length < 2 || selected.some(point => !canonicalInteger(point[field]))) return { passed:false, count:selected.length, reason:'insufficient_or_invalid_progress' };
  let previous=BigInt(selected[0][field]), changed=start, advances=0, monotonic=true, stall=0;
  for (const point of selected.slice(1)) {
    const value=BigInt(point[field]);
    if (value<previous) monotonic=false;
    if (value>previous) { advances++; stall=Math.max(stall,point.received_at_ms-changed); changed=point.received_at_ms; }
    previous=value;
  }
  stall=Math.max(stall,end-changed);
  const first=selected[0], last=selected.at(-1);
  const maximumStall=Math.max(3000,3000/Number(speed));
  return { passed:monotonic&&advances>=3&&stall<=maximumStall, count:selected.length, advances, monotonic, maximum_stall_wall_ms:stall, maximum_allowed_stall_ms:maximumStall, first_value:first[field], last_value:last[field], span:(BigInt(last[field])-BigInt(first[field])).toString(), wall_span_ms:last.received_at_ms-first.received_at_ms };
}
const displayedPanes = ['heatmap', 'flow', 'dom', 'tape'];
function displayedAges() {
  return {
    sample_interval_ms: 500, raw_sample_limit_per_pane: limits.samples, dropped_samples: 0,
    units: { ages: 'milliseconds', native_timestamps: 'exact integer nanosecond strings', browser_sample_at_ms: 'browser performance.now', node_observed_at_ms: 'Node performance.now' },
    scope: 'Rendered badge values only; chart identity/crop is the retained painted view, never the requested geometry. Quantiles exclude invalid, identity-mismatched, missing, offscreen and mismatched-painted-evidence observations. A pending requested replacement does not invalidate the retained painted evidence. Partial visibility is included and counted. Unavailable source lag remains null; optional residence may still be observed. Counts of unavailability, invalidity, identity mismatch and coverage can overlap. No freshness SLA.',
    panes: Object.fromEntries(displayedPanes.map(pane => [pane, { samples: [], dropped_samples: 0 }]))
  };
}
function retainDisplayedAges(ages, snapshot, pin, observedAt) {
  for (const pane of displayedPanes) {
    const target = ages.panes[pane];
    if (target.samples.length >= limits.samples) { target.dropped_samples++; ages.dropped_samples++; continue; }
    const sample = { ...snapshot.panes[pane], browser_sample_at_ms: snapshot.browser_sample_at_ms, node_observed_at_ms: observedAt };
    const invalid = [], mismatches = [], reasons = [];
    const number = (field, integer = false) => {
      const value = sample[field];
      if (value === null) return;
      if (typeof value !== 'string' || value.trim() === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || (integer && !Number.isSafeInteger(Number(value)))) {
        invalid.push(`invalid_${field}`); sample[field] = null;
      } else sample[field] = Number(value);
    };
    if (!Number.isFinite(snapshot.browser_sample_at_ms) || snapshot.browser_sample_at_ms < 0 || !Number.isFinite(observedAt)) invalid.push('invalid_sample_clock');
    if (sample.badge_count > 1 || sample.chart_count > 1) invalid.push('ambiguous_rendered_element');
    for (const field of ['source_lag_ms', 'evidence_resident_ms', 'response_resident_ms']) number(field);
    for (const field of ['painted_row_first', 'painted_row_count']) number(field, true);
    for (const field of ['evidence_ns', 'highwater_ns', 'generation']) {
      if (sample[field] !== null && !canonicalInteger(sample[field])) invalid.push(`invalid_${field}`);
    }
    if (sample.present) {
      for (const field of ['source_id', 'session_id', 'generation', 'source_mode']) {
        if (sample[field] === null) invalid.push(`missing_${field}`);
        else if (sample[field] !== pin?.[field]) mismatches.push(field);
      }
      if (!['true', 'false'].includes(sample.frozen)) invalid.push('invalid_frozen');
      if (sample.unavailable_reason !== null) {
        reasons.push(sample.unavailable_reason);
        if (sample.source_lag_ms !== null) invalid.push('unavailable_with_source_lag');
      } else if (sample.source_lag_ms === null) invalid.push('missing_source_lag_without_reason');
      if (sample.source_lag_ms !== null) {
        if (!sample.clock_id || !canonicalInteger(sample.evidence_ns) || !canonicalInteger(sample.highwater_ns)) invalid.push('missing_source_clock_evidence');
        else if (BigInt(sample.evidence_ns) > BigInt(sample.highwater_ns)) invalid.push('evidence_after_highwater');
        if (sample.evidence_resident_ms === null) invalid.push('missing_evidence_residence');
      }
      if (pane === 'heatmap' || pane === 'flow') {
        if (sample.view_id !== null) {
          if (!['heatmap', 'footprint', 'candles', 'profile', 'cvd', 'ofi'].includes(sample.painted_mode)) invalid.push('invalid_painted_mode');
          if (sample.painted_row_first === null || sample.painted_row_count === null || sample.painted_row_count === 0) invalid.push('invalid_painted_crop');
          if (sample.view_id !== sample.painted_view_id) mismatches.push('painted_view_id');
        } else if (sample.source_lag_ms !== null) invalid.push('source_lag_without_painted_view');
      }
    } else reasons.push('missing_badge');
    sample.transition = (pane === 'heatmap' || pane === 'flow') && (sample.requested_view_id !== sample.painted_view_id || sample.view_id !== sample.painted_view_id);
    // A pending replacement alone is not a transition of the retained painted evidence.
    sample.evidence_transition = sample.present && (pane === 'heatmap' || pane === 'flow') && sample.view_id !== sample.painted_view_id;
    if (sample.transition) reasons.push('view_transition');
    if (sample.coverage !== 'visible') reasons.push(sample.coverage);
    if (mismatches.length) reasons.push('identity_mismatch');
    reasons.push(...invalid);
    sample.invalid_reasons = invalid;
    sample.identity_mismatches = mismatches;
    sample.reasons = reasons;
    sample.valid = sample.present && !invalid.length && !mismatches.length;
    sample.quantile_eligible = sample.valid && ['visible', 'partial'].includes(sample.coverage) && !sample.evidence_transition;
    sample.status = invalid.length || mismatches.length ? 'invalid' : !sample.present ? 'missing' : sample.unavailable_reason !== null ? 'unavailable' : 'available';
    target.samples.push(sample);
  }
}
function summarizeDisplayedAges(ages) {
  ages.invalid_samples = 0; ages.identity_mismatches = 0;
  for (const target of Object.values(ages.panes)) {
    const samples = target.samples, eligible = samples.filter(sample => sample.quantile_eligible);
    const counts = field => {
      const result = {};
      for (const sample of samples) for (const reason of Array.isArray(sample[field]) ? sample[field] : [sample[field]]) result[reason] = (result[reason] ?? 0) + 1;
      return result;
    };
    target.summary = {
      total: samples.length, available: samples.filter(sample => sample.status === 'available').length,
      unavailable: samples.filter(sample => sample.unavailable_reason !== null).length,
      missing: samples.filter(sample => !sample.present).length, invalid: samples.filter(sample => sample.status === 'invalid').length,
      identity_mismatch: samples.filter(sample => sample.identity_mismatches.length).length,
      transitions: samples.filter(sample => sample.transition).length, evidence_transitions: samples.filter(sample => sample.evidence_transition).length,
      quantile_eligible: eligible.length, reason_counts: counts('reasons'), invalid_reason_counts: counts('invalid_reasons'),
      identity_mismatch_counts: counts('identity_mismatches'), coverage_counts: counts('coverage'), pane_coverage_counts: counts('pane_coverage'),
      source_clock_lag: quantiles(eligible.map(sample => sample.source_lag_ms).filter(value => value !== null)),
      evidence_residence: quantiles(eligible.map(sample => sample.evidence_resident_ms).filter(value => value !== null)),
      response_residence: quantiles(eligible.map(sample => sample.response_resident_ms).filter(value => value !== null))
    };
    ages.invalid_samples += target.summary.invalid;
    ages.identity_mismatches += target.summary.identity_mismatch;
  }
}
function verdict(scenario) {
  const m = scenario.measurements;
  const failure = [];
  if (scenario.error) failure.push('scenario_exception');
  if (scenario.browser_errors.length) failure.push('browser_error');
  const pin=scenario.cohort_pin;
  const obsoletePreparation=error=>!error.cohort&&Number.isFinite(error.issued_at_ms)&&error.issued_at_ms<scenario.window_start_ms&&error.session_id===pin?.session_id&&canonicalInteger(error.generation)&&canonicalInteger(pin?.generation)&&BigInt(error.generation)<BigInt(pin.generation);
  scenario.expected_setup_rejections=scenario.response_errors.filter(error=>obsoletePreparation(error)&&error.status===409&&error.detail==='obsolete session generation');
  scenario.expected_setup_cancellations=scenario.response_errors.filter(error=>obsoletePreparation(error)&&error.path==='/api/v1/visualization/ack'&&error.error==='net::ERR_ABORTED');
  scenario.unexpected_api_error_count=scenario.api_error_count-scenario.expected_setup_rejections.length-scenario.expected_setup_cancellations.length;
  if (scenario.unexpected_api_error_count) failure.push('api_error');
  if (scenario.response_tasks_lost || scenario.request_records_lost || scenario.error_records_lost || scenario.telemetry_dropped) failure.push('evidence_buffer_overflow');
  if (scenario.unresolved_requests.length) failure.push('unresolved_measurement_requests');
  if (scenario.queue_overflows) failure.push('analytical_queue_overflow');
  if (scenario.frame_ack_evictions) failure.push('frame_ack_evidence_evicted');
  if (scenario.frame_failures) failure.push('unavailable_or_error_frame');
  if (scenario.telemetry_invalid) failure.push('invalid_frame_telemetry');
  if (scenario.source_mismatches) failure.push('source_identity_mismatch');
  if (scenario.lifecycle_errors.length) failure.push('replay_lifecycle_mismatch');
  if (scenario.horizontal_document_overflow) failure.push('horizontal_document_overflow');
  if (m?.dropped_samples) failure.push('dropped_measurement_samples');
  if (m?.invalid_browser_samples) failure.push('invalid_browser_timing');
  if (m && Object.values(m).some(value => value?.invalid_count > 0)) failure.push('invalid_timing');
  if (scenario.displayed_data_ages.dropped_samples) failure.push('displayed_age_buffer_overflow');
  if (scenario.displayed_data_ages.invalid_samples) failure.push('invalid_displayed_age_instrumentation');
  if (scenario.displayed_data_ages.identity_mismatches) failure.push('displayed_age_identity_mismatch');
  scenario.functional_correctness = { passed: failure.length === 0, failures: failure };
  const counts = { ack: m?.publish_to_ack_upper_bound.count ?? 0, draw: m?.draw.count ?? 0, frame_interval: m?.frame_interval.count ?? 0, input_paint_opportunity: m?.input_paint_opportunity.count ?? 0, telemetry: scenario.frame_telemetry.length };
  const completed_requested_window = !scenario.capture_ended_early && scenario.measured_wall_seconds >= options.seconds;
  const observed_liquidity_and_trades = scenario.positive_cells_received > 0 && scenario.visible_trade_points_received > 0;
  const cursor=progressEvidence(scenario.frame_telemetry,'delivery_index',scenario.window_start_ms,scenario.window_end_ms,scenario.speed);
  const sourceClock=progressEvidence(scenario.frame_telemetry,'source_mono_ns',scenario.window_start_ms,scenario.window_end_ms,scenario.speed);
  const chart=progressEvidence(scenario.chart_progress,'end_ns',scenario.window_start_ms,scenario.window_end_ms,scenario.speed);
  const achievedRate=sourceClock.wall_span_ms>0?Number(sourceClock.span)/1e6/sourceClock.wall_span_ms:null;
  const rateMatches=achievedRate!==null&&Math.abs(achievedRate/Number(scenario.speed)-1)<=.1;
  scenario.workload_progress={cursor,selected_book_source_clock:sourceClock,chart,achieved_source_seconds_per_wall_second:achievedRate,requested_speed:Number(scenario.speed),relative_rate_tolerance:.1,rate_matches:rateMatches,scope:'Selected-book receive-monotonic clock; observational sampling, not a one-way latency clock.'};
  scenario.sample_sufficiency = { counts, required: minimumSamples, completed_requested_window, observed_liquidity_and_trades, streaming_window_advanced: scenario.streaming_window_advanced === true, passed: completed_requested_window && observed_liquidity_and_trades && cursor.passed && sourceClock.passed && chart.passed && rateMatches && Object.entries(minimumSamples).every(([key, count]) => counts[key] >= count) };
  const p95 = m?.publish_to_ack_upper_bound.p95_ms ?? null;
  scenario.budget_observation = { metric: 'publication_to_ack_upper_bound', p95_ms: p95, threshold_ms: 250, met: p95 === null ? null : p95 <= 250, interpretation: 'Conservative round-trip upper bound only; a failure is not proof that one-way/GPU latency exceeded 250ms.' };
  scenario.certified = scenario.functional_correctness.passed && scenario.sample_sufficiency.passed && scenario.budget_observation.met === true;
  scenario.status = !scenario.functional_correctness.passed ? 'failed' : !scenario.sample_sufficiency.passed ? 'inconclusive' : !scenario.budget_observation.met ? 'budget_not_met' : 'completed';
}
async function runScenario(speed) {
  assertRunning();
  const context = await browser.newContext({ viewport: report.viewport, deviceScaleFactor: 1, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const primary = page.getByTestId('chart-pane-heatmap');
  const primaryChart = primary.getByTestId('market-charts');
  const primaryViewResponse = response => response.url().endsWith('/api/v1/visualization')
    && response.request().method() === 'POST' && response.ok()
    && response.request().postDataJSON().rows === 128
    && response.request().postDataJSON().bucket_ns === '1000000000';
  const scenario = { speed, status: 'starting', acks: [], requests: [], unresolved_requests: [], response_errors: [], browser_errors: [], visual_responses: 0, response_tasks_lost: 0, request_records_lost: 0, error_records_lost: 0, api_error_count: 0, telemetry_dropped: 0, telemetry_invalid: 0, queue_overflows: 0, frame_ack_baseline: null, frame_ack_evictions: 0, source_mismatches: 0, frame_failures: 0, frame_telemetry: [], source_frame: null, received_view: null, served_assets: [], catalog_sources: [] };
  Object.assign(scenario, { positive_cells_received: 0, visible_trade_points_received: 0, controls: [], non_cohort_cancellations: [], lifecycle_errors: [], chart_progress: [], cohort_pin:null, displayed_data_ages: displayedAges() });
  report.scenarios.push(scenario);
  let measuring = false, sequence = 0, expectedSourceId = options.source === 'demo' ? 'synthetic-demo-v3-180s' : null;
  const pending = new Set(), inflight = new Map(), records = new WeakMap(), ackIdentities = new Set();
  const addError = (target, value) => { if (target.length < limits.errors) target.push(value); else scenario.error_records_lost++; };
  const apiError = (record, detail) => {
    // Retain superseded setup queries and post-cohort cleanup cancellations.
    // All cohort failures remain fatal; obsolete setup generations are checked in verdict.
    if (record && !record.cohort && !measuring && (record.status === undefined || record.status < 400) && detail.error === 'net::ERR_ABORTED' && (scenario.window_end_ms !== undefined || ['/api/v1/visualization','/api/v1/ladder','/api/v1/instruments'].includes(record.path))) {
      addError(scenario.non_cohort_cancellations, { id:record.id,path:record.path,phase:scenario.window_end_ms===undefined?'preparation':'after_cohort',...detail });
      return;
    }
    scenario.api_error_count++; addError(scenario.response_errors, { id: record?.id, path: record?.path, cohort: record?.cohort, issued_at_ms:record?.issued_at_ms, session_id:record?.session_id, generation:record?.generation, ...detail });
  };
  const frameEvidence = (frame, origin, retain) => {
    if (frame?.type !== 'frame') return;
    scenario.source_frame = { frame_id: frame.frame_id, session_id: frame.session_id, generation: frame.generation, status: frame.status, source: frame.source, selection: frame.selection, replay: frame.replay, health: frame.health, telemetry: frame.telemetry, book: frame.book ? { bid_levels: frame.book.bids.length, ask_levels: frame.book.asks.length, presentation: frame.book.presentation, quality: frame.book.quality, asof: frame.book.asof } : null };
    const expired = frame.telemetry?.ack_expired;
    if (!retain) {
      if (scenario.window_start_ms === undefined && Number.isInteger(expired) && expired >= 0) scenario.frame_ack_baseline = expired;
      return;
    }
    const pin=scenario.cohort_pin;
    if (!pin || frame.session_id!==pin.session_id || frame.generation!==pin.generation || frame.selection?.instrument_id!==pin.instrument_id || frame.source?.id!==pin.source_id || frame.status!=='playing' || frame.replay?.status!=='playing' || frame.replay?.speed!==pin.speed) {
      addError(scenario.lifecycle_errors,{origin,session_id:frame.session_id,generation:frame.generation,instrument_id:frame.selection?.instrument_id,source_id:frame.source?.id,status:frame.status,replay_status:frame.replay?.status,speed:frame.replay?.speed});
    }
    if (!Number.isInteger(expired) || expired < 0 || scenario.frame_ack_baseline === null || expired < scenario.frame_ack_baseline) {
      scenario.telemetry_invalid++;
    } else {
      scenario.frame_ack_evictions = Math.max(scenario.frame_ack_evictions, expired - scenario.frame_ack_baseline);
    }
    if (!expectedSourceId || frame.source?.id !== expectedSourceId || (options.manifest && (!frame.source.recording || frame.source.label !== options.source))) scenario.source_mismatches++;
    if (['unavailable', 'error'].includes(frame.status)) scenario.frame_failures++;
    if (!frame.telemetry || !Number.isInteger(frame.telemetry.overflows) || frame.telemetry.overflows < 0) scenario.telemetry_invalid++;
    else scenario.queue_overflows = Math.max(scenario.queue_overflows, frame.telemetry.overflows);
    if (scenario.frame_telemetry.length < limits.samples) scenario.frame_telemetry.push({ received_at_ms: performance.now(), origin, frame_id: frame.frame_id, session_id: frame.session_id, generation: frame.generation, source_id: frame.source?.id, status: frame.status, speed:frame.replay?.speed, delivery_index:frame.replay?.cursor.delivery_index, source_mono_ns:frame.book?.asof?.receive_mono_ns, telemetry: frame.telemetry });
    else scenario.telemetry_dropped++;
  };
  const task = fn => {
    if (pending.size >= limits.responseTasks) { scenario.response_tasks_lost++; return; }
    const job = fn().catch(error => apiError(null, { error: errorText(error) }));
    pending.add(job); void job.finally(() => pending.delete(job));
  };
  const settle = (request, record, state) => {
    record.state = state; record.settled_at_ms = performance.now(); inflight.delete(request);
  };
  page.on('pageerror', error => addError(scenario.browser_errors, errorText(error)));
  page.on('websocket', socket => {
    socket.on('framereceived', ({ payload }) => {
      if (Buffer.byteLength(payload) > 16 * 1024 * 1024) { scenario.telemetry_dropped++; return; }
      try { frameEvidence(JSON.parse(String(payload)), 'websocket', measuring); }
      catch (error) { if (measuring) { scenario.telemetry_invalid++; addError(scenario.response_errors, { error: errorText(error), path: 'websocket' }); } }
    });
    socket.on('socketerror', error => { if (measuring) apiError(null, { path: 'websocket', error: String(error) }); });
    socket.on('close',()=>{if(measuring)addError(scenario.lifecycle_errors,{reason:'websocket_closed_during_cohort'});});
  });
  page.on('request', request => {
    const target = new URL(request.url());
    if (!target.pathname.startsWith('/api/v1/')) return;
    const record = { id: ++sequence, path: target.pathname, method: request.method(), issued_at_ms: performance.now(), cohort: measuring, state: 'issued' };
    if (request.method()==='POST') {
      try {
        const input=request.postDataJSON();
        if (typeof input?.session_id==='string'&&typeof input?.generation==='string') Object.assign(record,{session_id:input.session_id,generation:input.generation});
      } catch (error) { apiError(record,{error:errorText(error)}); }
    }
    if (inflight.size >= limits.apiRequests) { scenario.request_records_lost++; return; }
    if (record.cohort) {
      const pin=scenario.cohort_pin;
      if ((record.method==='POST'&&(record.session_id!==pin?.session_id||record.generation!==pin?.generation))||record.method==='DELETE') addError(scenario.lifecycle_errors,{reason:'request_identity_changed',...record});
      if (scenario.requests.length < limits.samples) scenario.requests.push(record); else scenario.request_records_lost++;
      if (record.path === '/api/v1/visualization/ack') {
        try {
          const value = request.postDataJSON();
          record.ack_identity = { session_id: value.session_id, generation: value.generation, view_id: value.view_id };
          record.browser_receive_to_paint_ms = value.browser_receive_to_paint_ms;
          if (!Object.values(record.ack_identity).every(value => typeof value === 'string' && value.length > 0) || !Number.isFinite(record.browser_receive_to_paint_ms) || record.browser_receive_to_paint_ms < 0 || record.browser_receive_to_paint_ms > 60000) throw new Error('Malformed ACK request evidence');
          const identity = JSON.stringify(record.ack_identity);
          if (ackIdentities.has(identity)) throw new Error('Duplicate ACK identity in measurement cohort');
          if (ackIdentities.size >= limits.samples) throw new Error('ACK identity evidence bound exceeded');
          ackIdentities.add(identity);
        }
        catch (error) { apiError(record, { error: errorText(error) }); }
      }
    }
    records.set(request, record); inflight.set(request, record);
  });
  page.on('requestfailed', request => {
    const record = records.get(request);
    if (record && record.state !== 'failed') { apiError(record, { error: request.failure()?.errorText ?? 'request failed' }); settle(request, record, 'failed'); }
  });
  page.on('response', response => {
    const request = response.request(), target = new URL(response.url()), path = target.pathname;
    if (['document', 'script', 'stylesheet'].includes(request.resourceType())) {
      task(async () => {
        if (target.origin !== url.origin || !response.ok()) throw new Error(`Unattested frontend response ${response.url()} ${response.status()}`);
        const asset = path === '/' ? 'frontend/dist/index.html' : `frontend/dist${decodeURIComponent(path)}`;
        const expected = report.hashes_at_start.built_frontend[asset];
        if (!expected) throw new Error(`Frontend response is not a pinned built asset: ${path}`);
        const declaredSize = Number(response.headers()['content-length']);
        if (declaredSize > limits.assetBytes) throw new Error(`Frontend asset exceeds byte bound: ${path}`);
        const bytes = await response.body();
        if (bytes.length > limits.assetBytes) throw new Error(`Frontend asset exceeds byte bound: ${path}`);
        const actual = hash(bytes), evidence = { url: response.url(), path: asset, resource_type: request.resourceType(), sha256: actual, expected_sha256: expected, bytes: bytes.length, matched: actual === expected };
        if (scenario.served_assets.length >= 256) throw new Error('Served asset evidence bound exceeded');
        scenario.served_assets.push(evidence);
        if (!evidence.matched) throw new Error(`Served frontend hash mismatch: ${path}`);
      });
      return;
    }
    if (!path.startsWith('/api/v1/')) return;
    const record = records.get(request);
    if (!record) { scenario.request_records_lost++; return; }
    record.status = response.status();
    task(async () => {
      if (record.state === 'failed') return;
      // Playwright's finished() resolves on requestfinished, not requestfailed.
      // Let the already-classified failure release this reader before teardown.
      let onFailure;
      const failed = new Promise(resolve => {
        onFailure = candidate => { if (candidate === request) resolve(null); };
        page.on('requestfailed', onFailure);
      });
      try {
        const finishedError = await Promise.race([response.finished(), failed]);
        if (record.state === 'failed') return; // requestfailed already classified this cancellation.
        if (finishedError) throw finishedError;
        if (!response.ok()) {
          const detail=response.status()===409?(await response.json()).detail:undefined;
          apiError(record,{status:response.status(),detail}); settle(request,record,'http_error'); return;
        }
        if (['/api/v1/sessions','/api/v1/subscriptions','/api/v1/replay'].includes(path) && request.method() === 'POST') frameEvidence(await response.json(), 'http', record.cohort);
        else if (path === '/api/v1/instruments') {
          const catalog = await response.json();
          if (!Array.isArray(catalog.sources) || catalog.sources.length > 1024) throw new Error('Invalid source catalog');
          scenario.catalog_sources = catalog.sources;
        }
        else if (path === '/api/v1/visualization/ack' && record.cohort) {
          const value = await response.json();
          if (value.browser_receive_to_paint_ms !== record.browser_receive_to_paint_ms) throw new Error('ACK timing does not match its request');
          if (scenario.acks.length < limits.samples) scenario.acks.push({ request_id: record.id, issued_at_ms: record.issued_at_ms, settled_at_ms: performance.now(), ...record.ack_identity, publish_to_ack_upper_bound_ms: value.publish_to_ack_upper_bound_ms, browser_receive_to_paint_ms: value.browser_receive_to_paint_ms });
          else scenario.response_tasks_lost++;
        } else if (path === '/api/v1/visualization' && record.cohort) {
          scenario.visual_responses++;
          const view = await response.json();
          if(view.generation!==scenario.cohort_pin?.generation||view.instrument_id!==scenario.cohort_pin?.instrument_id) addError(scenario.lifecycle_errors,{reason:'visualization_identity_changed',generation:view.generation,instrument_id:view.instrument_id});
          // Plotting-only coverage counts; never financial inputs or unique-print counts.
          scenario.positive_cells_received += view.columns.filter(column=>column.eligible).reduce((sum,column)=>sum+column.cells.reduce((n,pair)=>n+pair.filter(value=>value!==null&&Number(value)>0).length,0),0);
          const low=Number(view.prices[0]), high=Number(view.prices.at(-1))+Number(view.price_step);
          scenario.visible_trade_points_received += view.trades.filter(trade=>Number(trade.price)>=low&&Number(trade.price)<high&&BigInt(trade.time_ns)>=BigInt(view.start_ns)&&BigInt(trade.time_ns)<BigInt(view.end_ns)).length;
          if (!scenario.received_view) scenario.received_view = { grid_id:view.grid_id, instrument_id:view.instrument_id, clock_id:view.clock_id, rows:view.rows, columns:view.columns.length, eligible_columns:view.columns.filter(column=>column.eligible).length, sampling:view.sampling, quantity_unit:view.quantity_unit, price_step:view.price_step, bucket_ns:view.bucket_ns, reasons:view.reasons, truncated:view.truncated };
        }
        settle(request, record, 'completed');
      } catch (error) {
        if (record.state === 'failed') return; // Do not count the same failed request again when its reader closes.
        apiError(record, { error: errorText(error) }); settle(request, record, 'failed');
      } finally { page.off('requestfailed', onFailure); }
    });
  });
  // No external browser fetches, even if a mistakenly built surface tries one.
  await context.route('**/*', route => new URL(route.request().url()).origin === url.origin ? route.continue() : route.abort('blockedbyclient'));
  await page.addInitScript(() => {
    const state = { active:false, started_at_ms:null, ended_at_ms:null, draw:[], long_tasks:[], frame_intervals:[], input_paint:[], dropped:0, invalid:0, lastFrame:null };
    const put = (target,value) => { if (!state.active) return; if (!Number.isFinite(value) || value < 0) { state.invalid++; return; } if (target.length<20000) target.push(value); else state.dropped++; };
    new PerformanceObserver(list => { for (const entry of list.getEntries()) if (entry.name === 'lob:chart-draw' && state.started_at_ms !== null && entry.startTime >= state.started_at_ms) put(state.draw,entry.duration); }).observe({ type:'measure', buffered:false });
    try { new PerformanceObserver(list => { for (const entry of list.getEntries()) if (state.started_at_ms !== null && entry.startTime >= state.started_at_ms) put(state.long_tasks,entry.duration); }).observe({ type:'longtask', buffered:false }); } catch { /* Capability is retained separately. */ }
    const frame = stamp => { if (state.active && state.lastFrame !== null) put(state.frame_intervals,stamp-state.lastFrame); state.lastFrame=stamp; requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
    let inputPending=false;
    const input = () => { if (!state.active || inputPending) return; inputPending=true;const start=performance.now();requestAnimationFrame(()=>requestAnimationFrame(()=>{put(state.input_paint,performance.now()-start);inputPending=false;})); };
    for (const type of ['pointermove','pointerdown','wheel','keydown']) addEventListener(type,input,{capture:true,passive:true});
    window.__lobBenchmark = state;
  });
  const startup = performance.now();
  try {
    if (options.manifest) {
      // Resolve the verified opaque ID before opening the app. Starting Demo and
      // then replacing it can strand an intentionally cancelled response reader.
      const deadline = performance.now() + limits.startupMs;
      while (performance.now() < deadline) {
        assertRunning();
        const response = await fetch(new URL('/api/v1/instruments', url), { signal: AbortSignal.timeout(Math.max(1, Math.ceil(deadline - performance.now()))), redirect: 'error' });
        if (!response.ok) throw new Error(`Recorded source catalog returned ${response.status}`);
        const catalog = await response.json();
        if (!Array.isArray(catalog.sources) || catalog.sources.length > 1024) throw new Error('Invalid source catalog');
        scenario.catalog_sources = catalog.sources;
        const matches = catalog.sources.filter(source => source.label === options.source && source.recording && source.status === 'ready');
        if (matches.length > 1) throw new Error('Exact recorded source is not unique in the verified catalog');
        if (matches.length === 1) { expectedSourceId = matches[0].id; break; }
        await sleep(100);
      }
      if (typeof expectedSourceId !== 'string' || !expectedSourceId) throw new Error('Exact recorded source is unavailable in the verified catalog');
    }
    scenario.expected_source = { id: expectedSourceId, label: options.source, capture: options.manifest ? dirname(options.manifest) : null };
    const launch = new URL(url.origin);
    if (options.manifest) launch.searchParams.set('source', expectedSourceId);
    scenario.launch_url = launch.href;
    await page.goto(launch.href);
    await page.locator('.status').filter({ hasText:/^Transport: Connected$/ }).waitFor({ timeout:60000 });
    if (options.manifest) {
      await page.waitForFunction(id => document.querySelector('select[aria-label="Data source"]')?.value === id, expectedSourceId, { timeout:30000 });
      await page.locator('.source-banner > strong').filter({hasText:/^RECORDED$/}).waitFor({timeout:120000});
    }
    if (options.instrument) {
      await page.getByLabel('Search instruments').fill(options.instrument);
      await page.locator('[data-instrument-id='+JSON.stringify(options.instrument)+']').click();
    }
    const start = options.start ?? (options.manifest ? null : '2000');
    if (start !== null) {
      await page.getByLabel('Seek delivery').fill(start);
      await page.getByRole('button',{name:'Seek',exact:true}).click();
      await page.waitForFunction(value=>document.querySelector('.cursor')?.textContent.trim().startsWith(value+' /'),start,{timeout:120000});
    }
    const waitPaint = async response => {
      const view = await (await response).json();
      await page.waitForFunction(id=>document.querySelector('[data-testid="chart-pane-heatmap"] [data-testid="market-charts"]')?.dataset.paintedViewId===id,view.view_id);
      return view;
    };
    const nextView = () => page.waitForResponse(primaryViewResponse);
    await page.waitForFunction(()=>Boolean(document.querySelector('[data-testid="chart-pane-heatmap"] [data-testid="market-charts"]')?.dataset.paintedViewId));
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-testid="chart-pane-heatmap"] select[aria-label="Price aggregation"]')?.options.length>1);
    const quantum=await primary.getByLabel('Price aggregation').locator('option').last().evaluate(option=>option.value);
    if (!quantum) throw new Error('No declared display quantum');
    let fresh=nextView();
    await primary.getByLabel('Price aggregation').selectOption(quantum);
    await waitPaint(fresh);
    fresh=nextView();
    await primary.getByLabel('Chart range').selectOption('30');
    await waitPaint(fresh);
    fresh=nextView();
    await primary.getByRole('button',{name:'Recenter price',exact:true}).click();
    await waitPaint(fresh);
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    await page.getByLabel('Replay speed').selectOption(speed);
    const started=page.waitForResponse(response=>response.url().endsWith('/api/v1/replay')&&response.request().postDataJSON()?.action==='play'&&response.ok());
    await page.getByTestId('replay-play').click();
    await started;
    await waitPaint(nextView());
    const canvas=primary.getByTestId('liquidity-canvas');
    // Separate pan/zoom completion from steady streaming timing: superseded
    // queries during a control change are not failed market-data processing.
    for (const kind of ['pan','zoom']) {
      await waitPaint(nextView());
      const before=await primaryChart.evaluate(element=>({...element.dataset}));
      await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
      const box=await canvas.boundingBox();
      if (!box) throw new Error('Chart geometry unavailable');
      await page.mouse.move(box.x+box.width*.5,box.y+box.height*.45);
      const plot=primary.locator('.m4-chart-plot');
      await plot.focus();
      fresh=page.waitForResponse(response=>{
        if(!primaryViewResponse(response))return false;
        const input=response.request().postDataJSON();
        if(!canonicalInteger(input?.start_ns)||!canonicalInteger(input?.end_ns))return false;
        const width=BigInt(input.end_ns)-BigInt(input.start_ns), beforeWidth=BigInt(before.endNs)-BigInt(before.startNs);
        return kind==='pan'?width===beforeWidth&&BigInt(input.end_ns)<BigInt(before.endNs):width<beforeWidth;
      });
      const issued=performance.now();
      if(kind==='pan')await plot.press('ArrowLeft');else await page.mouse.wheel(0,-80);
      const changed=await waitPaint(fresh), painted=performance.now();
      await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
      const oldWidth=BigInt(before.endNs)-BigInt(before.startNs), newWidth=BigInt(changed.end_ns)-BigInt(changed.start_ns);
      if(await primary.getByLabel('Follow time',{exact:true}).isChecked()||(kind==='pan'?(newWidth!==oldWidth||changed.start_ns===before.startNs):newWidth===oldWidth))throw new Error(`The ${kind} control did not change its declared geometry`);
      const followIssued=performance.now();
      fresh=page.waitForResponse(response=>{
        if(!primaryViewResponse(response)||!(records.get(response.request())?.issued_at_ms>=followIssued))return false;
        const input=response.request().postDataJSON();
        return canonicalInteger(input?.end_ns)&&BigInt(input.end_ns)>BigInt(changed.end_ns);
      });
      await primary.getByRole('button',{name:'Follow latest',exact:true}).click();
      const following=await waitPaint(fresh), followingPainted=performance.now();
      await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
      if(!await primary.getByLabel('Follow time',{exact:true}).isChecked())throw new Error('Follow recovery did not remain enabled');
      scenario.controls.push({kind,method:kind==='pan'?'ArrowLeft':'wheel -80',before_window:{start_ns:before.startNs,end_ns:before.endNs},after_window:{start_ns:changed.start_ns,end_ns:changed.end_ns},requested_view_id:changed.view_id,following_view_id:following.view_id,control_to_paint_ms:painted-issued,follow_recovery_to_paint_ms:followingPainted-followIssued});
    }
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    await waitPaint(nextView());
    scenario.display_controls={pane:'heatmap',concurrent_pane:'flow',measurement_scope:'Both panes; primary interaction and progress geometry',price_step:quantum,range_seconds:30,rows:128,follow_time:true,interaction_policy:'Pan/zoom completion is measured during replay before the steady streaming cohort; cursor paint responsiveness is measured throughout that cohort. Superseded non-cohort query cancellations are retained separately.'};
    scenario.startup_and_history_preparation_ms=performance.now()-startup;
    scenario.runtime_snapshot_index=await pinRuntime();
    scenario.layout_before=await page.locator('.workspace').boundingBox();
    scenario.canvas_before=await primaryChart.evaluate(element=>({...element.dataset}));
    const cdp=await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    scenario.renderer_metrics_before=(await cdp.send('Performance.getMetrics')).metrics;
    const pinned=scenario.source_frame;
    if(!pinned||!canonicalInteger(pinned.generation)||pinned.status!=='playing'||pinned.replay?.status!=='playing'||pinned.replay?.speed!==speed)throw new Error('Replay is not playing at the requested speed at the cohort fence');
    scenario.cohort_pin={session_id:pinned.session_id,generation:pinned.generation,source_id:pinned.source.id,source_mode:pinned.source.mode,instrument_id:pinned.selection.instrument_id,speed};
    await page.evaluate(()=>{window.__lobBenchmark.started_at_ms=performance.now();window.__lobBenchmark.active=true;window.__lobBenchmark.lastFrame=null;});
    scenario.window_start_ms=performance.now();
    measuring=true;
    let iteration=0;
    while (performance.now()-scenario.window_start_ms < options.seconds*1000) {
      assertRunning();
      const box=await canvas.boundingBox();
      if (box) {
        await page.mouse.move(box.x+box.width*(.2+(iteration%6)*.1),box.y+box.height*.45);
      }
      await page.waitForTimeout(500);
      iteration++;
      const displayed = await page.evaluate(() => {
        const coverage = element => {
          if (!element) return 'missing';
          const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
          if (!rect.width || !rect.height || style.visibility === 'hidden' || style.display === 'none') return 'hidden';
          let left = Math.max(0, rect.left), top = Math.max(0, rect.top), right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
          for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            const clip = getComputedStyle(parent), bounds = parent.getBoundingClientRect();
            if (clip.visibility === 'hidden' || clip.display === 'none' || clip.opacity === '0') return 'hidden';
            if (/(auto|scroll|hidden|clip)/.test(clip.overflowX)) { left = Math.max(left, bounds.left); right = Math.min(right, bounds.right); }
            if (/(auto|scroll|hidden|clip)/.test(clip.overflowY)) { top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom); }
          }
          if (right <= left || bottom <= top) return 'offscreen';
          return left > rect.left || top > rect.top || right < rect.right || bottom < rect.bottom ? 'partial' : 'visible';
        };
        const panes = {};
        for (const pane of ['heatmap', 'flow', 'dom', 'tape']) {
          const chartPane = pane === 'heatmap' || pane === 'flow';
          const container = document.querySelector(chartPane ? `[data-testid="chart-pane-${pane}"]` : `[data-pane="${pane}"]`);
          const badges = container?.querySelectorAll(chartPane ? '[data-testid="chart-freshness"]' : '[data-testid="data-freshness"]');
          const charts = chartPane ? container?.querySelectorAll('[data-testid="market-charts"]') : null;
          const badge = badges?.[0], chart = charts?.[0];
          const sample = {
            present: Boolean(badge), badge_count: badges?.length ?? 0, chart_count: charts?.length ?? 0,
            coverage: coverage(badge), pane_coverage: coverage(container),
            painted_view_id: chart?.getAttribute('data-painted-view-id') ?? null,
            requested_view_id: chart?.getAttribute('data-view-id') ?? null,
            requested_mode: chart?.getAttribute('data-mode') ?? null
          };
          for (const field of ['frame_id', 'session_id', 'source_mode', 'source_id', 'generation', 'view_id', 'clock_id', 'evidence_ns', 'highwater_ns', 'source_lag_ms', 'evidence_resident_ms', 'response_resident_ms', 'unavailable_reason', 'frozen', 'painted_mode', 'painted_row_first', 'painted_row_count']) {
            sample[field] = badge?.getAttribute(`data-${field.replaceAll('_', '-')}`) ?? null;
          }
          panes[pane] = sample;
        }
        return { browser_sample_at_ms: performance.now(), end_ns: document.querySelector('[data-testid="chart-pane-heatmap"] [data-testid="market-charts"]')?.getAttribute('data-end-ns') ?? null, panes };
      });
      const observedAt = performance.now();
      if(scenario.chart_progress.length<limits.samples)scenario.chart_progress.push({received_at_ms:observedAt,browser_sample_at_ms:displayed.browser_sample_at_ms,end_ns:displayed.end_ns});
      else scenario.telemetry_dropped++;
      retainDisplayedAges(scenario.displayed_data_ages, displayed, scenario.cohort_pin, observedAt);
      if (/\bended\b/i.test(await page.locator('.source-banner .source-state').innerText())) { scenario.capture_ended_early=true; break; }
    }
    scenario.window_end_ms=performance.now();
    measuring=false;
    scenario.measured_wall_seconds=(scenario.window_end_ms-scenario.window_start_ms)/1000;
    const measured=await page.evaluate(()=>{window.__lobBenchmark.ended_at_ms=performance.now();window.__lobBenchmark.active=false;return{...window.__lobBenchmark,longtask_supported:PerformanceObserver.supportedEntryTypes.includes('longtask')};});
    // Keep the context alive: responses for requests issued before the fence may
    // arrive afterwards. Never replace the issue cohort with an arrival cohort.
    const deadline=performance.now()+limits.drainMs;
    while (scenario.requests.some(record=>record.state==='issued') && performance.now()<deadline) { assertRunning(); await sleep(50); }
    scenario.unresolved_requests=scenario.requests.filter(record=>record.state==='issued').map(record=>({...record}));
    scenario.layout_after=await page.locator('.workspace').boundingBox();
    scenario.canvas_after=await primaryChart.evaluate(element=>({...element.dataset}));
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    scenario.streaming_window_advanced=await primary.getByLabel('Follow time',{exact:true}).isChecked()&&BigInt(scenario.canvas_after.endNs)>BigInt(scenario.canvas_before.endNs);
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    scenario.renderer_metrics_after=(await cdp.send('Performance.getMetrics')).metrics;
    scenario.renderer_metrics_scope='Chromium renderer cumulative durations and JS heap snapshots; not whole-browser RSS or GPU memory.';
    await cdp.detach();
    scenario.horizontal_document_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    scenario.measurements={publish_to_ack_upper_bound:quantiles(scenario.acks.map(v=>v.publish_to_ack_upper_bound_ms)),receive_to_paint:quantiles(scenario.acks.map(v=>v.browser_receive_to_paint_ms)),draw:quantiles(measured.draw),frame_interval:quantiles(measured.frame_intervals),input_paint_opportunity:quantiles(measured.input_paint),long_tasks:quantiles(measured.long_tasks),longtask_supported:measured.longtask_supported,dropped_samples:measured.dropped,invalid_browser_samples:measured.invalid};
    scenario.raw_browser_measurements=measured;
    scenario.final_cursor=await page.locator('.cursor').innerText();
    await primary.getByRole('button',{name:'Liquidity settings',exact:true}).click();
    await primary.getByRole('button',{name:'Liquidity Inspector',exact:true}).click();
    await primary.locator('.chart-pane-diagnostics > summary').click();
    scenario.pane_paint_diagnostics=await primary.getByTestId('chart-telemetry').innerText();
    await primary.getByRole('button',{name:'Close Chart Inspector',exact:true}).click();
    const screenshot=options.output.replace(/\.json$/,'')+`-${speed}x.png`;
    // Never overwrite an earlier screenshot, including after a failed run.
    const image=await page.screenshot({fullPage:true});
    await writeFile(screenshot,image,{flag:'wx'});
    scenario.screenshot=screenshot;
  } catch (error) { scenario.error=errorText(error); }
  finally {
    measuring=false;
    if (scenario.window_start_ms && !scenario.window_end_ms) scenario.window_end_ms=performance.now();
    for (const record of scenario.requests) if (record.state==='issued' && !scenario.unresolved_requests.some(value=>value.id===record.id)) scenario.unresolved_requests.push({...record});
    scenario.cleanup = { method: 'navigate-away-and-drain', pending_before_navigation: pending.size };
    // Stop page polling and deliver real network cancellation events while the
    // context is still alive. Closing it first can destroy an unmeasured reader.
    await page.goto('about:blank', { waitUntil: 'load', timeout: limits.drainMs }).catch(error => { scenario.error ??= errorText(error); });
    const readerDeadline = performance.now() + limits.drainMs;
    while (pending.size && performance.now() < readerDeadline) await sleep(50);
    scenario.cleanup.pending_before_context_close = pending.size;
    if (pending.size) scenario.error ??= 'Response readers did not drain before context closure';
    await context.close().catch(error=>{scenario.error ??= errorText(error);});
    const deadline=performance.now()+limits.drainMs;
    while (pending.size && performance.now()<deadline) await sleep(50);
    if (pending.size) scenario.response_tasks_lost+=pending.size;
    for (const kind of ['document','script','stylesheet']) if (!scenario.served_assets.some(asset=>asset.resource_type===kind&&asset.matched)) apiError(null,{error:`Missing measured frontend ${kind} evidence`});
    summarizeDisplayedAges(scenario.displayed_data_ages);
    verdict(scenario);
    await persist();
  }
}
try {
  report.hashes_at_start = await codeSnapshot();
  report.tool_sha256 = report.hashes_at_start.tool;
  if (!report.hashes_at_start.built_frontend['frontend/dist/index.html']) throw new Error('Build frontend/dist before benchmarking');
  for (const path of Object.keys(report.hashes_at_start.built_frontend)) if ((await stat(join(root,path))).size > limits.assetBytes) throw new Error(`Built asset exceeds byte bound: ${path}`);
  datasetPath = options.manifest ?? join(root, 'tests/fixtures/workstation/demo-v3.json');
  if (options.manifest) {
    if ((await lstat(dirname(datasetPath))).isSymbolicLink()) throw new Error('Capture directory cannot be a symlink');
    datasetPath = join(await realpath(dirname(datasetPath)), basename(datasetPath));
    options.manifest = datasetPath;
  }
  const datasetInfo = await lstat(datasetPath);
  if (!datasetInfo.isFile() || datasetInfo.size > 16*1024*1024) throw new Error('Manifest/fixture must be a bounded regular file, not a symlink');
  const datasetBytes = await readFile(datasetPath);
  dataset = JSON.parse(datasetBytes);
  datasetFiles = options.manifest ? dataset.files : [];
  if (!Array.isArray(datasetFiles) || datasetFiles.length > 100000) throw new Error('Invalid or oversized manifest file list');
  if (options.manifest && (dataset.complete !== true || dataset.incomplete_reasons?.length || !datasetFiles.length)) throw new Error('Recorded benchmark requires a complete finalized manifest');
  report.dataset = { path:datasetPath, capture_directory:options.manifest?dirname(datasetPath):null, sha256:hash(datasetBytes), declaration:options.manifest?'Exact capture supplied to owned CLI; runtime RecordingReader performs full verification before source availability':'repository synthetic fixture, not recorded market activity', streams:dataset.streams??null, counts:options.manifest?{raw:dataset.raw_count,batches:dataset.batch_count,events:dataset.event_count}:null };
  report.dataset.hashes_at_start = await datasetSnapshot();
  if (report.dataset.hashes_at_start.manifest_or_fixture_sha256 !== report.dataset.sha256) throw new Error('Dataset declaration changed during hashing');
  for (const file of datasetFiles) {
    const actual=report.dataset.hashes_at_start.files[file.path];
    if (actual.sha256 !== file.sha256 || actual.size_bytes !== String(file.size_bytes)) throw new Error(`Manifest file hash/size mismatch: ${file.path}`);
  }
  await persist();
  await startServer();
  const { chromium } = require('@playwright/test');
  browser = await chromium.launch({ headless:true });
  report.browser = browser.version();
  for (const speed of options.speeds) await runScenario(speed);
} catch (error) { report.error=errorText(error); }
finally {
  if (browser) await browser.close().catch(error=>{report.cleanup_error=errorText(error);});
  await stopServer().catch(error=>{report.cleanup_error=errorText(error);});
  try {
    if (report.hashes_at_start) {
      report.hashes_at_end=await codeSnapshot();
      report.code_unchanged=JSON.stringify(report.hashes_at_start)===JSON.stringify(report.hashes_at_end);
      if (!report.code_unchanged) provenanceError('Checkout code or built assets changed during the run');
    }
    if (report.dataset?.hashes_at_start) {
      report.dataset.hashes_at_end=await datasetSnapshot();
      report.dataset.unchanged=JSON.stringify(report.dataset.hashes_at_start)===JSON.stringify(report.dataset.hashes_at_end);
      if (!report.dataset.unchanged) provenanceError('Dataset changed during the run');
    }
    if (cacheDirectory && report.server.reaped) await verifyRuntime();
    else provenanceError('Owned runtime finalization evidence is unavailable');
    if (!report.server.process || report.server.process.root!==root || !report.server.bind_confirmed) provenanceError('Owned process/bind identity was not established');
    if (report.server.exit && (!report.server.exit.during_cleanup || ![0,2].includes(report.server.exit.code) || report.server.exit.signal)) provenanceError('Owned server exited unexpectedly or required forced termination');
  } catch (error) { provenanceError(errorText(error)); }
  if (cacheDirectory) await rm(cacheDirectory,{recursive:true,force:true}).catch(error=>{report.cleanup_error=errorText(error);});
  report.served_assets=report.scenarios.flatMap(scenario=>scenario.served_assets);
  report.budgets_met=report.scenarios.length===options.speeds.length&&report.scenarios.every(s=>s.budget_observation?.met===true);
  report.certified=!report.error&&!report.cleanup_error&&!report.interrupted&&!report.provenance_errors.length&&report.scenarios.length===options.speeds.length&&report.scenarios.every(s=>s.certified);
  report.status=report.certified?'completed':report.error||report.cleanup_error||report.interrupted||report.provenance_errors.length||report.scenarios.some(s=>s.status==='failed')?'failed':report.scenarios.some(s=>s.status==='inconclusive')?'inconclusive':'budget_not_met';
  process.removeListener('SIGINT',onInterrupt); process.removeListener('SIGTERM',onTerminate);
  await persist();
}
console.log(JSON.stringify({status:report.status,certified:report.certified,budgets_met:report.budgets_met,output:options.output}));
process.exitCode=report.certified?0:1;
