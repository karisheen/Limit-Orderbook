import type { Frame, Instrument } from './api';

/** Subscription candidates are not evidence that a feed is fresh or connected. */
export function streamableInstrument(instrument: Instrument): boolean {
  return instrument.status === 'active' || instrument.status === 'unknown' && instrument.metadata_status === 'streaming_only';
}

export function selectableInstrument(instrument: Instrument): boolean {
  return streamableInstrument(instrument) && instrument.data_access !== 'blocked';
}

export function initialLiveInstrument(instruments: readonly Instrument[]): Instrument | undefined {
  const available = instruments.filter(selectableInstrument);
  const accessible = available.filter(instrument => instrument.data_access === 'available');
  const candidates = accessible.length ? accessible : available;
  return candidates.find(instrument => instrument.venue === 'binance-global' && instrument.market_type === 'spot' && instrument.native_symbol === 'BTCUSDT')
    ?? candidates.find(instrument => instrument.venue === 'hyperliquid' && instrument.market_type === 'perpetual' && instrument.native_symbol === 'BTC')
    ?? candidates[0];
}

/** Only native session-pinned metadata can enable financial operations. */
export function metadataVerified(frame: Pick<Frame, 'selection'> | null): boolean {
  return !!frame?.selection.instrument_id && !!frame.selection.metadata_version && frame.selection.metadata_status === 'verified';
}

/** Retained native observations, not a freshness or execution-support guarantee. */
export function nativeReadinessEvidence(frame: Pick<Frame, 'selection' | 'book' | 'analytics'> | null) {
  const id = frame?.selection.instrument_id;
  return {
    bookObserved: !!id && frame?.book?.instrument_id === id,
    tradesObserved: !!id && frame?.analytics?.instrument_id === id &&
      frame.analytics.tape.some(print => !print.trade.is_subscription_history),
    metadataVerified: metadataVerified(frame),
  };
}

export function metadataNotice(frame: Pick<Frame, 'selection'> | null, instruments: readonly Instrument[]): string | null {
  if (!frame?.selection.instrument_id) return null;
  const pinned = frame.selection;
  const latest = instruments.find(instrument => instrument.instrument_id === pinned.instrument_id);
  const refreshed = latest?.metadata_status === 'verified' && latest.metadata_version !== pinned.metadata_version;
  const notice = pinned.metadata_status === 'streaming_only'
    ? 'Streaming-only metadata: exchange tick, lot and order limits are unverified. Cost estimates, comparison, proposed risk and paper orders are unavailable. Subscription candidacy does not establish data access or native book/trade receipt; read-only studies are not financial execution inputs.'
    : !metadataVerified(frame) ? 'Session metadata is unavailable; metadata-dependent financial operations are disabled.' : '';
  const refresh = refreshed ? 'Newer verified metadata is available. This session retains its pinned metadata while shared streams are in use. Close other sessions and stop recordings using this instrument, then reconnect or reselect to adopt the verified metadata.' : '';
  return [notice, refresh].filter(Boolean).join(' ') || null;
}
