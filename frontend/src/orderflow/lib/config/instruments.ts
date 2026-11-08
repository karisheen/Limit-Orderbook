// Native catalog identity is authoritative; these helpers only label display controls.
import type { Instrument } from "../../../api";
import type { MarketType, Venue } from "@/lib/types/market";

export function displayVenue(instrument: Instrument): Venue { return instrument.venue; }
export function displaySymbol(instrument: Instrument): string {
  return `${instrument.base.ticker}/${instrument.quote.ticker}`;
}
export function displayMarketType(instrument: Instrument): MarketType {
  return instrument.market_type === "spot" ? "spot" : "perpetual";
}
export function sameNativeMarket(a: Instrument, b: Instrument): boolean {
  return a.base.asset_id === b.base.asset_id && a.market_type === b.market_type;
}

// Display eligibility only: no quote conversion or financial equivalence.
const INDICATIVE_USD_QUOTES = ["USDT", "USDC", "USD"];
export function displayQuoteEligible(a: Instrument, b: Instrument): boolean {
  return a.quote.asset_id === b.quote.asset_id ||
    (INDICATIVE_USD_QUOTES.includes(a.quote.asset_id) && INDICATIVE_USD_QUOTES.includes(b.quote.asset_id));
}

/** One native instrument per venue/market, with the exact primary always winning its group. */
export function displayCohort(instruments: Iterable<Instrument>, primary: Instrument): Instrument[] {
  const chosen = new Map<string, Instrument>();
  const rank = (instrument: Instrument): number => instrument.instrument_id === primary.instrument_id ? -2 :
    instrument.quote.asset_id === primary.quote.asset_id ? -1 : INDICATIVE_USD_QUOTES.indexOf(instrument.quote.asset_id);
  for (const instrument of [primary, ...instruments]) {
    if (!sameNativeMarket(instrument, primary) || !displayQuoteEligible(instrument, primary)) continue;
    const key = JSON.stringify([instrument.venue, instrument.market_type]);
    const previous = chosen.get(key);
    if (!previous || rank(instrument) < rank(previous) ||
        (rank(instrument) === rank(previous) && instrument.instrument_id < previous.instrument_id)) {
      chosen.set(key, instrument);
    }
  }
  return [...chosen.values()];
}

/** Bundled artwork metadata only: never a list of selectable instruments. */
const ASSET_NAMES: Record<string, string> = {
  AAVE: "Aave", ADA: "Cardano", APT: "Aptos", ARB: "Arbitrum", ATOM: "Cosmos",
  AVAX: "Avalanche", BCH: "Bitcoin Cash", BNB: "BNB", BTC: "Bitcoin", DOGE: "Dogecoin",
  DOT: "Polkadot", ETC: "Ethereum Classic", ETH: "Ethereum", FIL: "Filecoin",
  LINK: "Chainlink", LTC: "Litecoin", NEAR: "NEAR", OP: "Optimism", SEI: "Sei",
  SOL: "Solana", SUI: "Sui", TIA: "Celestia", TRX: "TRON", UNI: "Uniswap", XRP: "XRP",
};

export function baseOf(symbol: string): string { return symbol.split("/")[0] ?? ""; }
export function assetFor(symbol: string): { name: string; logo: boolean; color?: string } {
  const base = baseOf(symbol);
  return { name: ASSET_NAMES[base] ?? base, logo: Object.hasOwn(ASSET_NAMES, base) };
}
export const VENUE_LOGOS: Record<string, string> = {
  binance: "/venues/binance.svg", "binance-global": "/venues/binance.svg", "binance-us": "/venues/binance.svg",
  bybit: "/venues/bybit.svg", bitget: "/venues/bitget.svg",
  coinbase: "/venues/coinbase.svg", okx: "/venues/okx.svg",
};
