export type DecimalText = string;
export type Mode = 'demo' | 'replay' | 'live';
export interface Asset { asset_id: string; ticker: string; native_token_id: string | null }
export interface Instrument {
  instrument_id: string; venue: string; api_family: string; native_symbol: string;
  base: Asset; quote: Asset; settlement: Asset | null; market_type: string;
  tick_size: DecimalText | null; lot_size: DecimalText; quantity_unit: string;
  status: string; data_access: string; execution_access: string; metadata_version: string;
}
export interface Source {
  id: string; mode: Mode; label: string; status: string; synthetic: boolean;
  recording: boolean; duration_ns: string | null; streams: number; reasons: string[];
}
export interface Eligibility { eligible: boolean; reasons: string[] }
export interface Reference {
  session_id: string; ingest_ordinal: string; event_index: string; clock_id: string;
  receive_wall_ns: string; receive_mono_ns: string; exchange_event_wall_ns: string | null;
}
export interface Quality {
  lifecycle: string; integrity: string; source_mode: string; recorded_source_mode: string | null;
  reason_codes: string[]; capture_complete: boolean;
  coverage: { subscribed_depth: string | null; retained_bid_levels: string; retained_ask_levels: string; full_depth: boolean };
}
export interface Level { price: DecimalText; quantity: DecimalText }
export interface Book {
  instrument_id: string; bids: Level[]; asks: Level[]; quality: Quality;
  asof: Reference; revision: string;
  presentation: { depth_limit: number; bids_omitted: number; asks_omitted: number };
}
export interface Metric {
  name: string; value: DecimalText | null; unit: string; instrument_id: string; venue: string;
  asof: Reference | null; eligibility: Eligibility; formula_version: string; config_hash: string;
  window: Record<string, unknown>; quality: Record<string, unknown> | null; references: Reference[];
  trade_evidence?: Record<string, unknown> | null; trade_eligibility?: Eligibility | null; trade_references?: Reference[];
}
export interface TapePrint {
  trade: { trade_id: string | null; dedup_key: string; price: string; quantity: string; quantity_unit: string; taker_side: string; side_provenance: string; is_subscription_history: boolean };
  asof: Reference; stream_id: string; base_quantity: string | null; quote_notional: string | null;
}
export interface ProfileLevel { price: string; buy_base: string; sell_base: string; unknown_base: string; quote_notional: string }
export interface DisplayEvidence { side: string; price: string; kind: string; quantity: string; start_mono_ns: string; end_mono_ns: string; references: Reference[] }
export interface Analytics {
  instrument_id: string; metrics: Metric[]; tape: TapePrint[];
  profile: ProfileLevel[]; display_evidence: DisplayEvidence[]; reasons: string[];
  presentation: { profile_limit: number; profile_omitted: number; evidence_limit: number; evidence_omitted: number };
}
export interface ProcessingObservation {
  clock_id: string; ingest_ordinal: string; receive_mono_ns: string; queue_exit_mono_ns: string;
  decode_start_mono_ns: string; decode_end_mono_ns: string; validation_end_mono_ns: string;
  normalized_mono_ns: string; publish_mono_ns: string;
}
export interface SubscriptionStatus {
  key: { venue: string; api_family: string; instrument_id: string; channel: string; depth: string | null; aggregation: string[][] };
  stream_id: string; reference_count: string; lifecycle: string; connection_epoch: string;
  queue_depth: string; queue_bytes: string; reason: string; capture_complete: boolean;
  last_processing: ProcessingObservation | null; processed_frames: string;
  queue_highwater_count: string; queue_highwater_bytes: string;
}
export interface Frame {
  version: '1'; type: 'frame'; frame_id: string; session_id: string; generation: string;
  status: 'loading' | 'paused' | 'playing' | 'seeking' | 'ended' | 'live' | 'unavailable' | 'error';
  selection: { instrument_id: string | null }; source: Source; book: Book | null; analytics: Analytics | null;
  replay: { cursor: { delivery_index: string; ordinal: string; event_index: string }; speed: string; status: string; total_deliveries: string } | null;
  health: { ready: boolean; reasons: string[] };
  telemetry: {
    published_mono_ns: string; queue_depth: number; queue_highwater: number; coalesced: number; overflows: number;
    ack_expired: number; ingestion: SubscriptionStatus[];
    observer_duration_ms: number | null; queue_wait_ms: number | null;
    publish_to_ack_upper_bound_ms: number | null; browser_receive_to_paint_ms: number | null;
  };
  error: string | null;
}
export interface Estimate {
  instrument_id: string; venue: string; side: 'buy' | 'sell';
  requested_base_quantity: string | null; quote_budget: string | null; filled_base_quantity: string;
  unfilled_base_quantity: string | null; filled_native_quantity: string; unfilled_quote_budget: string | null;
  notional_quote: string; quote_asset_id: string; vwap: string | null; best_price: string | null;
  slippage_quote: string | null; slippage_bps: string | null; fee_amount: string | null;
  fee_asset_id: string | null; effective_price: string | null; partial: boolean;
  eligibility: Eligibility; reasons: string[]; assumptions: string[]; fees: Record<string, unknown>;
  consumed_levels: Record<string, unknown>[]; coverage: Record<string, unknown>; asof: Reference;
}
export interface Pair {
  buy_instrument_id: string; sell_instrument_id: string; feasible_base_quantity: string;
  unfilled_base_quantity: string; numeraire: string; gross_reference: string | null;
  depth_slippage: string | null; buy_fee: string | null; sell_fee: string | null;
  fx_cost: string | null; rebalance_cost: string | null; latency_stress: string | null;
  net: string | null; net_bps: string | null; eligible: boolean; reasons: string[]; assumptions: string[];
}
export interface Costs { version: '1'; generation: string; estimate: Estimate | null; matrix: Pair[]; reasons: string[] }
export interface CostInput {
  side: 'buy' | 'sell'; base_quantity?: string; quote_budget?: string;
  fee_bps?: string; fee_provenance?: string; fee_date?: string;
  latency_bps?: string; rebalance_quote?: string;
}
export interface Catalog { version: '1'; instruments: Instrument[]; sources: Source[]; preferred_source_id?: string }
export type ReplayAction = 'play' | 'pause' | 'step' | 'speed' | 'seek' | 'reset';

export interface Candle {
  open: string; high: string; low: string; close: string; volume: string;
  eligible: boolean; reasons: string[];
}
export interface VisualColumn {
  id: string; time_ns: string; end_ns: string; segment_id: string; asof: Reference | null;
  eligible: boolean; reasons: string[]; liquidity_hash: string; cells: [string | null, string | null][];
  best_bid: string | null; best_ask: string | null; cvd: Metric | null; ofi: Metric | null; candle: Candle | null;
}
export interface PlotTrade {
  id: string; time_ns: string; price: string; base_quantity: string | null; taker_side: string;
  asof: Reference; segment_id: string; is_subscription_history: boolean;
}
export interface PlotProfile { price: string; buy_base: string; sell_base: string; unknown_base: string }
export interface FootprintCell extends PlotProfile { time_ns: string }
export interface VisualGrid {
  version: '1'; generation: string; grid_id: string; instrument_id: string; clock_id: string;
  view_id: string; published_mono_ns: string;
  quantity_unit: string; sampling: string; price_step: string; price_min: string; prices: string[]; rows: number;
  bucket_ns: string; start_ns: string; end_ns: string; now_ns: string; retained_start_ns: string | null;
  step_options: string[]; columns: VisualColumn[]; trades: PlotTrade[]; profile: PlotProfile[]; footprints: FootprintCell[];
  reasons: string[]; truncated: boolean; complete_window: boolean; replace: boolean; next_after: string | null;
}
export interface VisualizationInput {
  start_ns?: string; end_ns?: string; price_step?: string; price_offset_rows?: number; recenter?: boolean;
  bucket_ns?: string; rows?: number; max_columns?: number; after_column?: string;
}
export interface VisualizationAck {
  publish_to_ack_upper_bound_ms: number; browser_receive_to_paint_ms: number;
}
export interface GroupedLevel { side: 'bid' | 'ask'; price: string; base_quantity: string; cumulative_base: string }
export interface GroupedLadder {
  version: '1'; generation: string; instrument_id: string; asof: Reference | null; step: string; unit: string;
  eligible: boolean; reasons: string[]; rows: GroupedLevel[];
}
export interface LadderInput { price_step?: string; rows?: number }
export interface ProposalRiskInput {
  side: 'buy' | 'sell'; entry_price: string; stop_price: string; base_quantity?: string; risk_budget?: string;
  fee_bps?: string; fee_provenance?: string; fee_date?: string;
}
export interface ProposalRisk {
  version: '1'; generation: string; instrument_id: string; quote_asset_id: string;
  base_quantity: string | null; suggested_base_quantity: string | null; gross_loss: string | null;
  fee_cost: string | null; estimated_loss: string | null; eligible: boolean; reasons: string[]; assumptions: string[];
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = 'ApiError'; }
}

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal, method?: string, onReceive?: (receivedAtMs: number) => void): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'), signal,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  onReceive?.(performance.now());
  const text = await response.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) as unknown : null; }
  catch { throw new ApiError(`HTTP ${response.status}: API returned non-JSON content. Check the backend and proxy.`, response.status); }
  if (!response.ok) {
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : text;
    throw new ApiError(`HTTP ${response.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`, response.status);
  }
  return data as T;
}

export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
export function streamUrl(sessionId: string): string {
  const url = new URL('/api/v1/stream', window.location.href);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('session_id', sessionId);
  return url.href;
}
