import type { Eligibility, Frame, Instrument, Metric, Reference, SubscriptionStatus } from './api';

export type WorkflowQuery = <T extends object>(path: string, input: object, signal: AbortSignal) => Promise<T>;
export interface WorkflowPanelProps {
  frame: Frame | null; instruments: Instrument[]; connected: boolean; busy: boolean; active: boolean;
  request: WorkflowQuery;
}
export interface ConsumerEnvelope {
  version: '1'; session_id: string; generation: string; source_identity: string; delivery_index: string;
}
export interface SourceClock { clock_id: string; mono_ns: string; wall_ns: string | null }
export interface FeeInput { fee_bps?: string; fee_provenance?: string; fee_date?: string; fee_asset_id?: string }
export interface NativeFee { rate: string | null; asset_id: string | null; provenance: string; asof_wall_ns: string | null; assumed: boolean }
export interface BalanceInput { venue: string; asset_id: string; available: string; provenance?: string; asof?: SourceClock }
export interface FXInput {
  base_asset_id: string; quote_asset_id: string; bid: string; ask: string; asof: SourceClock;
  source: string; conversion_fee_rate?: string; assumed: boolean;
}
export type AlertKind = 'spread_shock' | 'depth_shock' | 'large_executed_print' | 'event_ofi' | 'best_quote_imbalance' | 'persistent_displayed_liquidity' | 'net_cost';
export interface AlertRuleInput {
  rule_id?: string; instrument_id?: string; kind: AlertKind; direction: 'above' | 'below';
  threshold: string; rearm_threshold: string; window_ns?: string; cooldown_ns?: string;
  depth_side?: 'bid' | 'ask'; depth_bps?: string; sell_instrument_id?: string; base_quantity?: string;
  buy_fees?: FeeInput; sell_fees?: FeeInput; latency_bps?: string; rebalance_quote?: string;
}
export interface AlertRule {
  rule_id: string; key: SubscriptionStatus['key']; kind: AlertKind; direction: 'above' | 'below';
  threshold: string; rearm_threshold: string; unit: string; window_ns: string; cooldown_ns: string;
  depth_side: 'bid' | 'ask'; depth_bps: string; sell_key: SubscriptionStatus['key'] | null;
  base_quantity: string | null; buy_fees: NativeFee; sell_fees: NativeFee;
  stress: { latency_bps: string; rebalance_cost: string; provenance: string };
}
export interface AlertEvaluation {
  rule: AlertRule; delivery_index: string; now: SourceClock | null; value: string | null;
  eligibility: Eligibility; window: Record<string, unknown> | null; armed: boolean;
  cooldown_remaining_ns: string; condition_met: boolean; reasons: string[]; persistence_ns: string;
  evidence_available: boolean;
}
export interface AlertEvent { event_id: string; evaluation: AlertEvaluation }
export interface AlertsResponse extends ConsumerEnvelope {
  snapshot: { rules: AlertRule[]; evaluations: AlertEvaluation[]; events: AlertEvent[];
    delivery_index: string; now: SourceClock | null; dropped_events: string; reasons: string[] };
  kinds: { kind: AlertKind; unit: string }[]; evaluation_scope: 'last_delivered_batch';
  presentation: { event_limit: number; events_omitted: number };
}
export interface AlertEvidenceResponse extends ConsumerEnvelope {
  evaluation: Omit<AlertEvaluation, 'evidence_available'> & {
    references: Reference[]; metric: Metric | null; prints: unknown[]; displayed: unknown[];
    pair: Record<string, unknown> | null; trade_evidence: Record<string, unknown> | null;
    book_evidence: Record<string, unknown> | null;
  };
}
export interface ReferenceObservation {
  instrument: Instrument; stream_id: string; value: { kind: string; price: string; currency: string; interval_ns: string | null } | null;
  asof: Reference | null; eligibility: Eligibility; label: string;
}
export interface AssetContextObservation {
  instrument: Instrument; stream_id: string; value: {
    mark: string | null; oracle: string | null; funding_rate: string | null; funding_interval_ns: string | null;
    open_interest: string | null; open_interest_unit: string | null;
  } | null;
  asof: Reference | null; eligibility: Eligibility; field_reasons: [string, string[]][];
  funding_rate_unit: string; open_interest_unit_provenance: string; label: string;
}
export interface ReferenceBasis {
  spot_instrument_id: string; perpetual_instrument_id: string; reference_kind: string;
  quote_asset_id: string; spot_mid: string | null; perpetual_reference: string | null;
  perpetual_quote_asset_id: string; proceeds_in_spot_quote: string | null; funding_cost_in_spot_quote: string | null;
  proceeds_basis_bps: string | null; funding_basis_bps: string | null;
  proceeds_conversion: Record<string, unknown> | null; funding_conversion: Record<string, unknown> | null;
  spot_asof: Reference | null; perpetual_asof: Reference | null; reasons: string[]; label: string; cost_scope: string;
}
export interface ContextResponse extends ConsumerEnvelope {
  snapshot: { instrument_id: string; now: SourceClock | null; watchlist: ReferenceObservation[];
    contexts: AssetContextObservation[]; basis: ReferenceBasis[]; reasons: string[] };
}
export interface PaperConfigurationInput {
  balances: BalanceInput[]; fees: (FeeInput & { instrument_id: string })[];
  feed_latency_ns?: string; decision_latency_ns?: string; order_latency_ns?: string;
}
export interface PaperConfiguration {
  balances: { venue: string; asset_id: string; available: string; provenance: string; asof: SourceClock | null }[];
  fees: { instrument_id: string; schedule: NativeFee }[];
  feed_latency_ns: string; decision_latency_ns: string; order_latency_ns: string; policy: Record<string, unknown>;
}
export interface PaperRunSummary {
  run_id: string; source_id: string; created_at: SourceClock; delivery_index: string;
  revision: string; parent_run_id: string | null;
}
export interface PaperRun extends PaperRunSummary {
  config: PaperConfiguration; instrument_metadata: { instrument_id: string; metadata_version: string }[];
}
export interface PaperOrderInput {
  instrument_id?: string; side: 'buy' | 'sell'; quantity: string; order_type?: 'market' | 'limit'; limit_price?: string;
}
export interface PaperOrder {
  order_id: string; run_id: string;
  request: { instrument_id: string; side: 'buy' | 'sell'; quantity: string; order_type: 'market' | 'limit'; limit_price: string | null };
  submitted_at: SourceClock; delivery_index: string; arrival_at: SourceClock; status: string;
  filled_quantity: string; remaining_quantity: string; reasons: string[];
}
export interface PaperFill {
  fill_id: string; run_id: string; order_id: string; instrument_id: string; venue: string; side: 'buy' | 'sell';
  at: SourceClock; delivery_index: string; quantity: string; notional_quote: string; quote_asset_id: string;
  fee_amount: string; fee_asset_id: string; fees: NativeFee; consumed_levels: Record<string, unknown>[];
  consumed_levels_omitted: number; book_asof: Reference; book_key: SubscriptionStatus['key']; book_revision: string;
}
export interface PaperPosition {
  venue: string; asset_id: string; quote_asset_id: string; instrument_id: string; quantity: string;
  cost_basis_quote: string | null; realized_pnl_quote: string | null; unrealized_pnl_quote: string | null;
  mark_price: string | null; reasons: string[];
}
export interface PaperSnapshot {
  run: PaperRun | null; delivery_index: string; at: SourceClock | null;
  balances: { venue: string; asset_id: string; available: string }[];
  orders: PaperOrder[]; fills: PaperFill[]; positions: PaperPosition[]; reasons: string[]; assumptions: string[];
  presentation: { orders_limit: number; orders_omitted: number; fills_limit: number; fills_omitted: number; fill_levels_limit: number };
}
export interface PaperResponse extends ConsumerEnvelope { snapshot: PaperSnapshot }
export interface PaperRunsResponse extends ConsumerEnvelope { runs: PaperRunSummary[] }
export interface PaperArchiveResponse extends PaperResponse { archived: true; archive_scope: 'historical_accounting_not_current_replay_state' }
export interface JournalEntry {
  entry_id: string; source_id: string; session_id: string; delivery_index: string; at: SourceClock;
  instrument_id: string; venue: string; rationale: string; entry: string | null; stop: string | null;
  context: string; run_id: string | null; order_id: string | null;
}
export interface JournalInput {
  entry_id?: string; rationale: string; entry?: string; stop?: string; context?: string; run_id?: string; order_id?: string;
}
export interface JournalResponse extends ConsumerEnvelope { entries: JournalEntry[]; scope: string; limit: number }
export interface JournalPutResponse extends ConsumerEnvelope { entry: JournalEntry }
export interface ResearchAttempt {
  attempt_id: string; started_wall_ns: string; finished_wall_ns: string | null; status: string;
  source: string; config_hash: string; report_hash: string | null; failure: string | null;
}
export interface ResearchStatistics {
  eligible_samples: string; selected_samples: string; labeled_samples: string; net_samples: string; nonoverlapping_samples: string;
  mean_gross_bps: string | null; expected_net_payoff_bps: string | null; hit_rate: string | null;
  average_win_bps: string | null; average_loss_bps: string | null; no_trade_bps: string;
  matched_long_bps: string | null; matched_short_bps: string | null; exclusions: [string, string][];
}
export interface ResearchTrial {
  hypothesis: string; threshold: string; horizon_ns: string; scenario: string; split: string; group: string;
  statistics: ResearchStatistics;
}
export interface ResearchReport {
  attempt_id: string; version: string; source_id: string; source_hash: string; provenance: string; synthetic: boolean;
  config: Record<string, unknown>; config_hash: string; observation_hash: string; label_hash: string;
  duration_ns: string; train_end_ns: string; validation_end_ns: string; observation_count: string; label_count: string;
  split_counts: [string, string][]; exclusions: [string, string][]; trials: ResearchTrial[];
  selections: { hypothesis: string; horizon_ns: string; threshold: string | null; reason: string }[];
  outcome: string; limitations: string[]; turnover: string | null; drawdown: string | null;
  portfolio_unavailable_reasons: string[]; report_hash: string;
  presentation: { trial_offset: number; trial_limit: number; trial_total: number };
}
export interface ResearchResponse {
  version: '1'; session_id: string; generation: string; attempts: ResearchAttempt[];
  attempt: ResearchAttempt | null; report: ResearchReport | null;
  config: Record<string, unknown> | null; source: [string, string][] | null;
}

export interface ManagedRecording {
  id: string;
  session_id: string | null;
  label: string;
  status: 'recording' | 'complete' | 'incomplete' | 'error';
  source_id: string | null;
  reasons: string[];
  scope: 'instrument' | 'cohort';
  instruments: string[];
  channels: { venue: string; instrument_id: string; stream: string }[];
}

export interface RecordingsResponse {
  version: '1';
  recordings: ManagedRecording[];
}

export interface RecordingMutationResponse extends RecordingsResponse {
  recording_id: string;
}
