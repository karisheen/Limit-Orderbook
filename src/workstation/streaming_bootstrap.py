"""Bundled crypto subscription candidates, never exchange instrument metadata."""

from hashlib import sha256

from .adapters.binance import asset_identity
from .instruments import Instrument, InstrumentId, MetadataSnapshot, OrderConstraints


STREAMING_SOURCE = "bundled:crypto-public-ws-subscription-candidates:v1"
# Crypto-only list mirrored from zadescoxp/order-flow-screener:
# lib/config/instruments.ts; lib/adapters/capabilities.ts has no exclusions
# for these four venue/family combinations. Listing status remains unknown.
STREAMING_CRYPTO_BASES = (
    "BTC",
    "ETH",
    "XRP",
    "BNB",
    "SOL",
    "DOGE",
    "TRX",
    "ADA",
    "HYPE",
    "LINK",
    "SUI",
    "AVAX",
    "BCH",
    "LTC",
    "DOT",
    "UNI",
    "NEAR",
    "APT",
    "AAVE",
    "ETC",
    "ENA",
    "ATOM",
    "ARB",
    "OP",
    "INJ",
    "FIL",
    "SEI",
    "TIA",
    "WIF",
)
# These identify protocol subscriptions, not a claim of current listing or access.
# USDT linear contracts and spot messages express quantities in base units.
STREAMING_FAMILIES = (
    ("binance-global", "spot-v3", "spot"),
    ("binance-global", "usdm-v1", "perpetual"),
    ("bybit", "v5-spot", "spot"),
    ("bybit", "v5-linear", "perpetual"),
)


def streaming_snapshot() -> MetadataSnapshot:
    """Return immutable, deterministic candidates with no invented increments."""
    identities = tuple(
        (venue, family, market_type, base, "USDT")
        for venue, family, market_type in STREAMING_FAMILIES
        for base in STREAMING_CRYPTO_BASES
    )
    payload_hash = sha256(repr(identities).encode()).hexdigest()
    version = f"{STREAMING_SOURCE}:{payload_hash}"
    constraints = OrderConstraints(
        price_rule="unconstrained", size_rule="unconstrained"
    )
    instruments = []
    for venue, family, market_type, base_symbol, quote_symbol in identities:
        base = asset_identity(venue, base_symbol)
        quote = asset_identity(venue, quote_symbol)
        symbol = f"{base_symbol}{quote_symbol}"
        perpetual = market_type == "perpetual"
        instruments.append(
            Instrument(
                instrument_id=InstrumentId(f"{venue}:{family}:{market_type}:{symbol}"),
                metadata_version=version,
                venue=venue,
                api_family=family,
                native_symbol=symbol,
                base=base,
                quote=quote,
                tick_size=None,
                lot_size=None,
                settlement=quote if perpetual else None,
                market_type=market_type,
                quantity_unit="base",
                contract_value_currency=base.asset_id if perpetual else None,
                settlement_conversion="none" if perpetual else "unknown",
                contract_specification_source=STREAMING_SOURCE if perpetual else None,
                constraints=constraints,
                status="unknown",
                metadata_status="streaming_only",
                metadata_asof_wall_ns=0,
                metadata_source=STREAMING_SOURCE,
            )
        )
    return MetadataSnapshot(STREAMING_SOURCE, 0, payload_hash, tuple(instruments))
