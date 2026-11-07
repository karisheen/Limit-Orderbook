"""Bounded native OHLCV and explicitly partial retained-session trade candles.

This is independent of VisualHistory's analytical receive-monotonic grid.
Native starts are preserved, never rebucketed or filled across missing periods.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
import time
from urllib.parse import quote, urlencode

from .adapters.base import TransportRequest
from .adapters.binance import BinanceGlobalAdapter, BinanceUSAdapter
from .instruments import Instrument
from .precision import (
    DECIMAL_CONTEXT,
    DERIVED_DECIMAL_POLICY,
    canonical_decimal,
    decimal,
    exact_json_loads,
)
from .transport import PublicTransportError, fetch_public

SECOND = 1_000_000_000
MAX_LIMIT = 500
MAX_CACHE = 32
MAX_CONCURRENT = 4
CACHE_SECONDS = 5
FIXED_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}
LOCAL_INTERVALS = tuple(key for key in FIXED_SECONDS if key not in ("3d", "1w"))
BINANCE_INTERVALS = tuple(FIXED_SECONDS) + ("1M",)
COINBASE_INTERVALS = dict(
    zip(
        ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"),
        (
            "ONE_MINUTE",
            "FIVE_MINUTE",
            "FIFTEEN_MINUTE",
            "THIRTY_MINUTE",
            "ONE_HOUR",
            "TWO_HOUR",
            "FOUR_HOUR",
            "SIX_HOUR",
            "ONE_DAY",
        ),
    )
)
BYBIT_INTERVALS = {
    **{
        key: str(value // 60)
        for key, value in FIXED_SECONDS.items()
        if value < 86400 and key != "8h"
    },
    "1d": "D",
    "1w": "W",
    "1M": "M",
}
KUCOIN_INTERVALS = {
    **{
        key: key.replace("m", "min").replace("h", "hour")
        for key, value in FIXED_SECONDS.items()
        if value < 86400
    },
    "1d": "1day",
    "1w": "1week",
    "1M": "1month",
}
FAMILIES = {
    "binance-global": "spot-v3",
    "binance-us": "spot-v3",
    "coinbase": "advanced-trade",
    "kraken": "spot-v2",
    "bybit": "v5-spot",
    "kucoin": "classic-spot",
    "hyperliquid": "info",
}


def supported_intervals(instrument: Instrument) -> tuple[str, ...]:
    if FAMILIES.get(instrument.venue) != instrument.api_family:
        return ()
    if instrument.venue != "hyperliquid" and instrument.market_type != "spot":
        return ()
    if instrument.venue.startswith("binance-"):
        return BINANCE_INTERVALS
    if instrument.venue == "coinbase":
        return tuple(COINBASE_INTERVALS)
    if instrument.venue == "kraken":
        # Kraken's 21600-minute bucket is fifteen days, NOT a calendar month.
        return ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")
    if instrument.venue == "bybit":
        return tuple(BYBIT_INTERVALS)
    if instrument.venue == "kucoin":
        return tuple(KUCOIN_INTERVALS)
    if instrument.venue == "hyperliquid":
        return tuple(key for key in BINANCE_INTERVALS if key != "6h")
    return ()


def integer(value) -> int:
    if (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and len(value) <= 20
    ):
        value = int(value)
    if type(value) is not int or not 0 <= value <= 10**20:
        raise ValueError("invalid candle timestamp")
    return value


def bucket_end(start_ns: int, interval: str) -> int:
    if interval == "1M":
        start = datetime.fromtimestamp(start_ns // SECOND, timezone.utc)
        if (start.day, start.hour, start.minute, start.second) != (
            1,
            0,
            0,
            0,
        ) or start_ns % SECOND:
            raise ValueError("invalid calendar month boundary")
        year, month = (
            (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        )
        return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp()) * SECOND
    seconds = FIXED_SECONDS[interval]
    # Week/multiday starts belong to the venue, not an assumed epoch/Monday grid.
    alignment = min(seconds, 86400)
    if start_ns % (alignment * SECOND):
        raise ValueError("unaligned native candle")
    return start_ns + seconds * SECOND


def request_start(now_ns: int, interval: str, count: int) -> int:
    if interval == "1M":
        now = datetime.fromtimestamp(now_ns // SECOND, timezone.utc)
        index = now.year * 12 + now.month - count
        year, month = divmod(index, 12)
        return (
            int(datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()) * SECOND
        )
    seconds = FIXED_SECONDS[interval]
    return max(0, (now_ns // (seconds * SECOND) - count + 1) * seconds * SECOND)


@dataclass(frozen=True)
class Candle:
    start_ns: int
    end_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def wire(self, now_ns: int) -> dict:
        return {
            "start_ns": str(self.start_ns),
            "end_ns": str(self.end_ns),
            **{
                name: canonical_decimal(
                    getattr(self, name), policy=DERIVED_DECIMAL_POLICY
                )
                for name in ("open", "high", "low", "close", "volume")
            },
            "closed": self.end_ns <= now_ns,
        }


def candle(start, values, interval: str, *, scale: int, native_end=None) -> Candle:
    start_ns = integer(start) * scale
    end_ns = bucket_end(start_ns, interval)
    if native_end is not None and (integer(native_end) + 1) * scale != end_ns:
        raise ValueError("native candle end disagrees with interval")
    opening, high, low, close = (decimal(value, positive=True) for value in values[:4])
    volume = decimal(values[4], nonnegative=True)
    if not low <= opening <= high or not low <= close <= high:
        raise ValueError("invalid candle OHLC envelope")
    return Candle(start_ns, end_ns, opening, high, low, close, volume)


def native_request(
    instrument: Instrument, interval: str, limit: int, now_ns: int
) -> TransportRequest:
    venue, symbol = instrument.venue, instrument.native_symbol
    if interval not in supported_intervals(instrument):
        raise ValueError("unsupported candle interval or instrument")
    if venue.startswith("binance-"):
        host = (
            BinanceGlobalAdapter.rest_host
            if venue == "binance-global"
            else BinanceUSAdapter.rest_host
        )
        endpoint = f"{host}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
    elif venue == "coinbase":
        count = min(limit, 350)
        endpoint = (
            "https://api.coinbase.com/api/v3/brokerage/market/products/"
            + quote(symbol, safe="")
            + "/candles"
        )
        params = {
            "granularity": COINBASE_INTERVALS[interval],
            "limit": count,
            "start": request_start(now_ns, interval, count) // SECOND,
            "end": now_ns // SECOND,
        }
    elif venue == "kraken":
        if instrument.native_asset_id is None:
            raise ValueError("Kraken candles require native pair identity")
        endpoint = "https://api.kraken.com/0/public/OHLC"
        params = {
            "pair": instrument.native_asset_id,
            "interval": FIXED_SECONDS[interval] // 60,
            "since": request_start(now_ns, interval, limit) // SECOND,
        }
    elif venue == "bybit":
        endpoint = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": BYBIT_INTERVALS[interval],
            "limit": limit,
        }
    elif venue == "kucoin":
        endpoint = "https://api.kucoin.com/api/v1/market/candles"
        params = {
            "symbol": symbol,
            "type": KUCOIN_INTERVALS[interval],
            "startAt": request_start(now_ns, interval, limit) // SECOND,
            "endAt": now_ns // SECOND,
        }
    else:
        return TransportRequest(
            "POST",
            "https://api.hyperliquid.xyz/info",
            json.dumps(
                {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": symbol,
                        "interval": interval,
                        "startTime": request_start(now_ns, interval, limit)
                        // 1_000_000,
                        "endTime": now_ns // 1_000_000,
                    },
                },
                separators=(",", ":"),
            ).encode(),
            (("Content-Type", "application/json"),),
        )
    return TransportRequest("GET", endpoint + "?" + urlencode(params))


def parse_native(
    payload: bytes, instrument: Instrument, interval: str, limit: int, now_ns: int
) -> tuple[Candle, ...]:
    root = exact_json_loads(payload, max_bytes=2 * 1024 * 1024)
    venue = instrument.venue
    if venue == "coinbase":
        rows = root["candles"]
    elif venue == "kraken":
        if root["error"]:
            raise ValueError("Kraken candle endpoint rejected request")
        result = root["result"]
        if set(result) != {instrument.native_asset_id, "last"}:
            raise ValueError("wrong Kraken candle identity")
        rows = result[instrument.native_asset_id]
    elif venue == "bybit":
        if root["retCode"] != 0:
            raise ValueError("Bybit candle endpoint rejected request")
        result = root["result"]
        if result["category"] != "spot" or result["symbol"] != instrument.native_symbol:
            raise ValueError("wrong Bybit candle identity")
        rows = result["list"]
    elif venue == "kucoin":
        if root["code"] != "200000":
            raise ValueError("KuCoin candle endpoint rejected request")
        rows = root["data"]
    else:
        rows = root
    if not isinstance(rows, list) or len(rows) > 1500:
        raise ValueError("candle response row bound exceeded")
    output = []
    for row in rows:
        if venue.startswith("binance-"):
            item = candle(
                row[0], row[1:6], interval, scale=1_000_000, native_end=row[6]
            )
        elif venue == "coinbase":
            item = candle(
                row["start"],
                [row[key] for key in ("open", "high", "low", "close", "volume")],
                interval,
                scale=SECOND,
            )
        elif venue == "kraken":
            item = candle(row[0], [*row[1:5], row[6]], interval, scale=SECOND)
        elif venue == "bybit":
            item = candle(row[0], row[1:6], interval, scale=1_000_000)
        elif venue == "kucoin":
            item = candle(
                row[0], [row[1], row[3], row[4], row[2], row[5]], interval, scale=SECOND
            )
        else:
            if row["s"] != instrument.native_symbol or row["i"] != interval:
                raise ValueError("wrong Hyperliquid candle identity")
            item = candle(
                row["t"],
                [row[key] for key in ("o", "h", "l", "c", "v")],
                interval,
                scale=1_000_000,
                native_end=row["T"],
            )
        if item.start_ns > now_ns:
            raise ValueError("future native candle")
        output.append(item)
    output.sort(key=lambda item: item.start_ns)
    if any(
        previous.end_ns > current.start_ns
        for previous, current in zip(output, output[1:])
    ):
        raise ValueError("duplicate or overlapping native candles")
    return tuple(output[-limit:])


def response(
    instrument,
    interval,
    candles,
    *,
    source,
    now_ns,
    supported,
    reasons=(),
    coverage=None,
    unavailable=False,
):
    if coverage is None:
        coverage = (
            (candles[0].start_ns, min(candles[-1].end_ns, now_ns))
            if candles
            else (None, None)
        )
    return {
        "version": "1",
        "instrument_id": str(instrument.instrument_id),
        "interval": interval,
        "status": "unavailable" if unavailable else "ready" if candles else "empty",
        "source": source,
        "venue": instrument.venue,
        "time_basis": "exchange_utc" if source == "venue_ohlcv" else "receive_utc",
        "fetched_at_ns": str(time.time_ns() if source == "session_trades" else now_ns),
        "supported_intervals": list(supported),
        "candles": [item.wire(now_ns) for item in candles],
        "reasons": list(reasons),
        "volume_unit": "base",
        "coverage_start_ns": None if coverage[0] is None else str(coverage[0]),
        "coverage_end_ns": None if coverage[1] is None else str(coverage[1]),
    }


class CandleService:
    """No wait queue or detached tasks: overload is explicit; cancellation propagates."""

    def __init__(self):
        self.cache: OrderedDict[tuple, tuple[float, tuple[Candle, ...], int]] = (
            OrderedDict()
        )
        self.active = 0

    async def query(
        self, instrument: Instrument, interval: str, limit: int = 300
    ) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError("invalid candle limit")
        supported = supported_intervals(instrument)
        now = time.time_ns()
        options = dict(source="venue_ohlcv", now_ns=now, supported=supported)
        if interval not in supported:
            return response(
                instrument,
                interval,
                (),
                **options,
                reasons=(
                    "unsupported_interval" if supported else "unsupported_instrument",
                ),
                unavailable=True,
            )
        key = (instrument, interval, limit)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            self.cache.move_to_end(key)
            return response(
                instrument,
                interval,
                cached[1],
                **{**options, "now_ns": cached[2]},
                reasons=self._reasons(instrument, limit),
            )
        if self.active >= MAX_CONCURRENT:
            return response(
                instrument,
                interval,
                (),
                **options,
                reasons=("candle_capacity",),
                unavailable=True,
            )
        self.active += 1
        try:
            payload = await fetch_public(
                native_request(instrument, interval, limit, now), attempts=1
            )
            fetched = time.time_ns()
            candles = parse_native(payload, instrument, interval, limit, fetched)
            # Only successful, uncancelled native results populate the neutral cache.
            self.cache[key] = (time.monotonic(), candles, fetched)
            self.cache.move_to_end(key)
            while len(self.cache) > MAX_CACHE:
                self.cache.popitem(last=False)
            return response(
                instrument,
                interval,
                candles,
                **{**options, "now_ns": fetched},
                reasons=self._reasons(instrument, limit),
            )
        except PublicTransportError as exc:
            return response(
                instrument,
                interval,
                (),
                **options,
                reasons=(exc.reason,),
                unavailable=True,
            )
        except (ValueError, KeyError, TypeError, IndexError, OverflowError):
            return response(
                instrument,
                interval,
                (),
                **options,
                reasons=("invalid_native_candles",),
                unavailable=True,
            )
        finally:
            self.active -= 1

    @staticmethod
    def _reasons(instrument, limit):
        reasons = [
            "native_history_may_have_gaps",
            "closed_is_time_boundary_not_completeness",
        ]
        if instrument.venue == "coinbase" and limit > 350:
            reasons.append("venue_limit_350")
        return tuple(reasons)


def receive_utc_frontier(history):
    """Project only the admitted source-monotonic frontier, never today's clock."""
    clock = history.clock
    if clock is None or history._clock_frontier is None:
        return None, None, ()
    frontier = min(clock.mono_ns, history._clock_frontier)
    segment = next(
        (
            item
            for item in reversed(history.clock_segments)
            if item.start_ns <= frontier
        ),
        None,
    )
    if segment is None or segment.reference.clock_id != clock.clock_id:
        return None, None, ()
    reference = segment.reference
    now = reference.receive_wall_ns + frontier - reference.receive_mono_ns
    if now < 0:
        return None, None, ()
    reasons = []
    if any(item.cause == "wall_offset_change" for item in history.clock_segments):
        reasons.append("receive_clock_discontinuity")
    if history.last_unapplied_pair is not None:
        reasons.append("nonadvancing_receive_pair")
    if history._clock_changed:
        reasons.append("source_clock_changed")
    if history._clock_truncated:
        reasons.append("receive_clock_history_truncated")
    return now, (reference.session_id, clock.clock_id, frontier), tuple(reasons)


def session_candles(history, instrument: Instrument, interval: str, limit: int) -> dict:
    """Read only already-admitted bounded trades on the session owner thread.

    Receive UTC is explicit: no invented exchange clock and no historical HTTP.
    Replay rebuilds this deque on seek, so future deliveries are never visible.
    """
    now, receive_scope, clock_reasons = receive_utc_frontier(history)
    options = dict(source="session_trades", now_ns=now or 0, supported=LOCAL_INTERVALS)
    if now is None:
        return response(
            instrument,
            interval,
            (),
            **options,
            reasons=("missing_receive_utc", "session_history_partial"),
            unavailable=True,
        )
    if interval not in LOCAL_INTERVALS:
        return response(
            instrument,
            interval,
            (),
            **options,
            reasons=("unsupported_interval", "retained_session_trades_only"),
            unavailable=True,
        )
    reasons = [
        "retained_session_trades_only",
        "session_history_partial",
        "receive_time_buckets",
        "closed_is_time_boundary_not_completeness",
    ]
    reasons.extend(clock_reasons)
    if history.truncated or history.trade_floor is not None:
        reasons.append("retained_history_truncated")
    if history.discontinuities:
        reasons.append("session_discontinuities")
    buckets = {}
    coverage = {}
    width = FIXED_SECONDS[interval] * SECOND
    previous = None
    with localcontext(DECIMAL_CONTEXT):
        for observed in history.trades:
            item = observed.print
            if receive_scope is not None and (
                item.asof.session_id != receive_scope[0]
                or item.asof.clock_id != receive_scope[1]
                or item.asof.receive_mono_ns > receive_scope[2]
            ):
                continue
            if item.trade.is_subscription_history:
                continue
            if item.base_quantity is None:
                if "unknown_trade_quantity" not in reasons:
                    reasons.append("unknown_trade_quantity")
                continue
            stamp = item.asof.receive_wall_ns
            if stamp > now:
                if "receive_clock_discontinuity" not in reasons:
                    reasons.append("receive_clock_discontinuity")
                continue
            if (
                previous is not None
                and stamp < previous
                and "receive_clock_discontinuity" not in reasons
            ):
                reasons.append("receive_clock_discontinuity")
            previous = stamp
            start = stamp // width * width
            price = item.trade.price
            if start not in buckets:
                buckets[start] = [price, price, price, price, item.base_quantity]
                coverage[start] = [stamp, stamp]
            else:
                values = buckets[start]
                values[1], values[2], values[3] = (
                    max(values[1], price),
                    min(values[2], price),
                    price,
                )
                values[4] += item.base_quantity
                coverage[start][0] = min(coverage[start][0], stamp)
                coverage[start][1] = max(coverage[start][1], stamp)
        starts = sorted(buckets)[-limit:]
        candles = tuple(
            Candle(start, start + width, *buckets[start]) for start in starts
        )
    actual = (
        (
            min(coverage[start][0] for start in starts),
            max(coverage[start][1] for start in starts),
        )
        if starts
        else (None, None)
    )
    return response(
        instrument, interval, candles, **options, reasons=reasons, coverage=actual
    )
