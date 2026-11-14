"""Exact admitted native candles and explicitly separate optional venue history.

Session buckets use the source-owned receive projection, UTC epoch floors for
fixed intervals (including Thursday-origin weeks), and UTC calendar months.
Venue history preserves its own exchange-time boundaries and is never merged.
"""

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import asyncio
from hashlib import sha256
import json
import time
import sys
from urllib.parse import quote, urlencode

from .adapters.base import TransportRequest
from .adapters.binance import BinanceGlobalAdapter, BinanceUSAdapter
from .adapters.bybit import BybitAdapter
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
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_BARS = 10_000
MAX_WAITERS = 64
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
LOCAL_INTERVALS = tuple(FIXED_SECONDS) + ("1M",)
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
# Select UTC variants explicitly; default daily/monthly bars can be UTC+8.
OKX_INTERVALS = {
    **{
        key: key.replace("h", "H")
        for key in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h")
    },
    **{key: key.upper() + "utc" for key in ("6h", "12h", "1d", "3d", "1w", "1M")},
}
BITGET_INTERVALS = {
    **{key: key.replace("m", "min") for key in ("1m", "3m", "5m", "15m", "30m")},
    "1h": "1h",
    "4h": "4h",
    **{key: key.upper() + "utc" for key in ("6h", "12h", "1d", "3d", "1w", "1M")},
}
DERIBIT_INTERVALS = {
    **{
        key: str(FIXED_SECONDS[key] // 60)
        for key in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "6h", "12h")
    },
    "1d": "1D",
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
    if instrument.venue == "okx":
        if (instrument.market_type, instrument.api_family) == ("spot", "v5-spot"):
            return tuple(OKX_INTERVALS)
        if (
            (instrument.market_type, instrument.api_family) == ("perpetual", "v5-swap")
            and not instrument.inverse
            and instrument.settlement == instrument.quote
            and instrument.contract_value_currency == instrument.base.asset_id
        ):
            return tuple(OKX_INTERVALS)
        return ()
    if instrument.venue == "bitget":
        if (instrument.market_type, instrument.api_family) == ("spot", "v2-spot"):
            return tuple(BITGET_INTERVALS)
        if (
            (instrument.market_type, instrument.api_family)
            == ("perpetual", "v2-usdt-futures")
            and not instrument.inverse
            and instrument.quote.asset_id == "USDT"
            and instrument.settlement == instrument.quote
        ):
            return tuple(BITGET_INTERVALS)
        return ()
    if instrument.venue == "deribit":
        if (
            instrument.api_family == "jsonrpc-v2"
            and instrument.market_type == "perpetual"
            and instrument.inverse
            and instrument.native_symbol in ("BTC-PERPETUAL", "ETH-PERPETUAL")
            and instrument.native_symbol == f"{instrument.base.asset_id}-PERPETUAL"
            and instrument.contract_value_currency == instrument.quote.asset_id
            and instrument.settlement == instrument.base
            and instrument.quote.asset_id == "USD"
        ):
            return tuple(DERIBIT_INTERVALS)
        return ()
    if instrument.market_type == "perpetual":
        if (instrument.venue, instrument.api_family) == ("binance-global", "usdm-v1"):
            return BINANCE_INTERVALS
        if (instrument.venue, instrument.api_family) == ("bybit", "v5-linear"):
            return tuple(BYBIT_INTERVALS)
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
        index = max(1970 * 12, now.year * 12 + now.month - count)
        year, month = divmod(index, 12)
        return (
            int(datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()) * SECOND
        )
    seconds = FIXED_SECONDS[interval]
    # Include the native week/multiday bucket regardless of its epoch origin.
    alignment = min(seconds, 86400)
    periods = count if seconds > 86400 else count - 1
    return max(
        0, (now_ns // (alignment * SECOND) * alignment - periods * seconds) * SECOND
    )


@dataclass(frozen=True)
class Candle:
    start_ns: int
    end_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    confirmed: bool | None = None

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
            "closed": self.end_ns <= now_ns and self.confirmed is not False,
        }


def candle(
    start, values, interval: str, *, scale: int, native_end=None, confirmed=None
) -> Candle:
    start_ns = integer(start) * scale
    end_ns = bucket_end(start_ns, interval)
    if native_end is not None and (integer(native_end) + 1) * scale != end_ns:
        raise ValueError("native candle end disagrees with interval")
    opening, high, low, close = (decimal(value, positive=True) for value in values[:4])
    volume = decimal(values[4], nonnegative=True)
    if not low <= opening <= high or not low <= close <= high:
        raise ValueError("invalid candle OHLC envelope")
    return Candle(start_ns, end_ns, opening, high, low, close, volume, confirmed)


def native_request(
    instrument: Instrument,
    interval: str,
    limit: int,
    now_ns: int,
    before_ns: int | None = None,
) -> TransportRequest:
    venue, symbol = instrument.venue, instrument.native_symbol
    if interval not in supported_intervals(instrument):
        raise ValueError("unsupported candle interval or instrument")
    if venue == "kraken" and before_ns is not None:
        raise ValueError("Kraken candles are recent-only")
    limit = min(limit, page_limit(instrument, before_ns))
    end = min(now_ns, before_ns - 1) if before_ns is not None else now_ns
    start = request_start(end, interval, limit)
    if venue.startswith("binance-"):
        host = (
            BinanceGlobalAdapter.rest_host
            if venue == "binance-global"
            else BinanceUSAdapter.rest_host
        )
        endpoint = (
            "https://fapi.binance.com/fapi/v1/klines"
            if instrument.api_family == "usdm-v1"
            else f"{host}/api/v3/klines"
        )
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if before_ns is not None:
            params["endTime"] = end // 1_000_000
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
            "start": start // SECOND,
            "end": end // SECOND,
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
        endpoint = f"{BybitAdapter.rest_host}/v5/market/kline"
        params = {
            "category": "linear" if instrument.api_family == "v5-linear" else "spot",
            "symbol": symbol,
            "interval": BYBIT_INTERVALS[interval],
            "limit": limit,
        }
        if before_ns is not None:
            params["end"] = end // 1_000_000
    elif venue == "kucoin":
        endpoint = "https://api.kucoin.com/api/v1/market/candles"
        params = {
            "symbol": symbol,
            "type": KUCOIN_INTERVALS[interval],
            "startAt": start // SECOND,
            "endAt": end // SECOND,
        }
    elif venue == "okx":
        endpoint = "https://www.okx.com/api/v5/market/" + (
            "history-candles" if before_ns is not None else "candles"
        )
        params = {"instId": symbol, "bar": OKX_INTERVALS[interval], "limit": limit}
        if before_ns is not None:
            # OKX's `after` is exclusive, unlike inclusive endTime providers.
            params["after"] = (end // 1_000_000) + 1
    elif venue == "bitget":
        linear = instrument.api_family == "v2-usdt-futures"
        endpoint = (
            "https://api.bitget.com/api/v2/"
            + ("mix" if linear else "spot")
            + "/market/"
            + ("history-candles" if before_ns is not None else "candles")
        )
        granularity = BITGET_INTERVALS[interval]
        if linear and interval in ("1m", "3m", "5m", "15m", "30m", "1h", "4h"):
            granularity = interval.replace("h", "H")
        params = {"symbol": symbol, "granularity": granularity, "limit": limit}
        if linear:
            params["productType"] = "USDT-FUTURES"
        if before_ns is not None:
            params["endTime"] = (end // 1_000_000) + 1
    elif venue == "deribit":
        endpoint = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
        params = {
            "instrument_name": symbol,
            "resolution": DERIBIT_INTERVALS[interval],
            "start_timestamp": start // 1_000_000,
            "end_timestamp": end // 1_000_000,
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
                        "startTime": start // 1_000_000,
                        "endTime": end // 1_000_000,
                    },
                },
                separators=(",", ":"),
            ).encode(),
            (("Content-Type", "application/json"),),
        )
    return TransportRequest("GET", endpoint + "?" + urlencode(params))


def parse_native(
    payload: bytes,
    instrument: Instrument,
    interval: str,
    limit: int,
    now_ns: int,
    before_ns: int | None = None,
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
        category = "linear" if instrument.api_family == "v5-linear" else "spot"
        if (
            result["category"] != category
            or result["symbol"] != instrument.native_symbol
        ):
            raise ValueError("wrong Bybit candle identity")
        rows = result["list"]
    elif venue == "kucoin":
        if root["code"] != "200000":
            raise ValueError("KuCoin candle endpoint rejected request")
        rows = root["data"]
    elif venue in ("okx", "bitget"):
        if root["code"] != ("0" if venue == "okx" else "00000"):
            raise ValueError("native candle endpoint rejected request")
        rows = root["data"]
    elif venue == "deribit":
        if root.get("jsonrpc") != "2.0" or "error" in root:
            raise ValueError("Deribit candle endpoint rejected request")
        result = root["result"]
        if result["status"] not in ("ok", "no_data"):
            raise ValueError("invalid Deribit candle status")
        names = ("ticks", "open", "high", "low", "close", "volume", "cost")
        arrays = [
            result.get(name, []) if result["status"] == "no_data" else result[name]
            for name in names
        ]
        if any(not isinstance(values, list) for values in arrays) or any(
            len(values) != len(arrays[0]) for values in arrays
        ):
            raise ValueError("invalid Deribit candle arrays")
        if result["status"] == "no_data" and arrays[0]:
            raise ValueError("inconsistent Deribit no_data")
        rows = list(zip(*arrays))
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
        elif venue == "okx":
            if len(row) != 9 or row[8] not in ("0", "1"):
                raise ValueError("invalid OKX candle schema")
            # vol is contracts for swaps; volCcy is the native base volume.
            volume = row[6] if instrument.market_type == "perpetual" else row[5]
            item = candle(
                row[0],
                [*row[1:5], volume],
                interval,
                scale=1_000_000,
                confirmed=row[8] == "1",
            )
        elif venue in ("bitget", "deribit"):
            expected = (
                8 if venue == "bitget" and instrument.market_type == "spot" else 7
            )
            if len(row) != expected:
                raise ValueError("invalid native candle schema")
            # Both APIs publish base volume, including Deribit's inverse products.
            # Deribit cost is quote volume, NOT contracts or base quantity.
            decimal(row[6], nonnegative=True)
            item = candle(row[0], row[1:6], interval, scale=1_000_000)
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
    if before_ns is not None:
        output = [item for item in output if item.start_ns < before_ns]
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
        "instrument_ids": [str(instrument.instrument_id)],
        "scope": "primary",
        "venue_label": instrument.venue,
        "quote_asset_ids": [instrument.quote.asset_id],
        "interval": interval,
        "status": "unavailable" if unavailable else "ready" if candles else "empty",
        "source": source,
        "venue": instrument.venue,
        "time_basis": "exchange_utc" if source == "venue_ohlcv" else "receive_utc",
        "fetched_at_ns": str(now_ns),
        "supported_intervals": list(supported),
        "candles": [item.wire(now_ns) for item in candles],
        "reasons": list(reasons),
        "volume_unit": "base",
        "partial": source == "session_trades" or bool(reasons),
        "coverage_start_ns": None if coverage[0] is None else str(coverage[0]),
        "coverage_end_ns": None if coverage[1] is None else str(coverage[1]),
    }


def page_limit(instrument: Instrument, before_ns: int | None = None) -> int:
    if instrument.venue == "coinbase":
        return 350
    if instrument.venue == "okx":
        return 300
    if instrument.venue == "bitget" and before_ns is not None:
        return 200
    return MAX_LIMIT


def series_key(instrument: Instrument, interval: str) -> str:
    identity = {
        "instrument": asdict(instrument),
        "interval": interval,
        "alignment": "native-utc0"
        if instrument.venue in ("okx", "bitget")
        else "native-exchange",
        "time_basis": "exchange_utc",
        "schema": 1,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode()).hexdigest()


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


class CandleService:
    """Bounded page LRU and single-flight HTTP; the last waiter owns cancellation.

    Capacity counts native requests, not their coalesced consumers. A cancelled
    sole consumer drains its transport before releasing capacity; another
    consumer's cancellation cannot poison the shared request or neutral cache.
    """

    def __init__(self):
        # Store immutable wire bytes: callers cannot mutate another session's page.
        self.cache: OrderedDict[tuple, tuple[float, bytes, int, int]] = OrderedDict()
        self.cache_bytes = 0
        self.cache_bars = 0
        self.inflight: dict[tuple, _Flight] = {}
        self.active = 0

    async def query(
        self,
        instrument: Instrument,
        interval: str,
        limit: int = 300,
        *,
        before_ns: str | None = None,
    ) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError("invalid candle limit")
        if before_ns is not None and (
            not isinstance(before_ns, str)
            or not before_ns.isascii()
            or not before_ns.isdigit()
            or before_ns.startswith("0")
            or len(before_ns) > 19
            or int(before_ns) > 2**63 - 1
        ):
            raise ValueError("invalid candle boundary")
        before = int(before_ns) if before_ns is not None else None
        now = time.time_ns()
        supported = supported_intervals(instrument)
        identity = series_key(instrument, interval)
        if interval not in supported:
            return self._response(
                instrument,
                interval,
                (),
                limit,
                now,
                before,
                identity,
                state="unsupported",
                unavailable=True,
                reasons=(
                    "unsupported_interval" if supported else "unsupported_instrument",
                ),
            )
        if instrument.venue == "kraken" and before is not None:
            return self._response(
                instrument,
                interval,
                (),
                limit,
                now,
                before,
                identity,
                state="limited",
                reasons=("kraken_recent_only_720",),
            )
        key = (identity, interval, limit, before)
        cached = self.cache.get(key)
        if cached is not None:
            if time.monotonic() - cached[0] < CACHE_SECONDS:
                self.cache.move_to_end(key)
                return json.loads(cached[1])
            self._evict(key)
        flight = self.inflight.get(key)
        if flight is None:
            if self.active >= MAX_CONCURRENT:
                return self._response(
                    instrument,
                    interval,
                    (),
                    limit,
                    now,
                    before,
                    identity,
                    state="unavailable",
                    unavailable=True,
                    reasons=("candle_capacity",),
                )
            self.active += 1
            task = asyncio.create_task(
                self._fetch(instrument, interval, limit, now, before, identity, key)
            )
            flight = _Flight(task)
            self.inflight[key] = flight
            task.add_done_callback(lambda done: self._finished(key, done))
        if flight.waiters >= MAX_WAITERS:
            return self._response(
                instrument,
                interval,
                (),
                limit,
                now,
                before,
                identity,
                state="unavailable",
                unavailable=True,
                reasons=("candle_capacity",),
            )
        flight.waiters += 1
        try:
            return json.loads(await asyncio.shield(flight.task))
        finally:
            flight.waiters -= 1
            if not flight.waiters and not flight.task.done():
                # Stop new waiters joining a task whose cancellation has begun.
                if self.inflight.get(key) is flight:
                    del self.inflight[key]
                flight.task.cancel()
                await asyncio.gather(flight.task, return_exceptions=True)

    def _finished(self, key, task):
        self.active -= 1
        flight = self.inflight.get(key)
        if flight is not None and flight.task is task:
            del self.inflight[key]

    def _evict(self, key):
        _, _, size, count = self.cache.pop(key)
        self.cache_bytes -= size
        self.cache_bars -= count

    async def _fetch(self, instrument, interval, limit, now, before, identity, key):
        successful = False
        try:
            payload = await fetch_public(
                native_request(instrument, interval, limit, now, before), attempts=1
            )
            fetched = time.time_ns()
            candles = parse_native(
                payload,
                instrument,
                interval,
                min(limit, page_limit(instrument, before)),
                fetched,
                before,
            )
            result = self._response(
                instrument,
                interval,
                candles,
                limit,
                fetched,
                before,
                identity,
            )
            successful = True
        except PublicTransportError as exc:
            result = self._response(
                instrument,
                interval,
                (),
                limit,
                now,
                before,
                identity,
                state="unsupported" if exc.reason == "unsupported" else "unavailable",
                unavailable=True,
                reasons=(exc.reason,),
            )
        except (ValueError, KeyError, TypeError, IndexError, OverflowError):
            result = self._response(
                instrument,
                interval,
                (),
                limit,
                now,
                before,
                identity,
                state="unavailable",
                unavailable=True,
                reasons=("invalid_native_candles",),
            )
        encoded = json.dumps(result, separators=(",", ":")).encode()
        current = asyncio.current_task()
        if successful and current is not None and not current.cancelling():
            count = len(result["candles"])
            # Include per-entry/key overhead as well as immutable response bytes.
            size = (
                sys.getsizeof(encoded)
                + sys.getsizeof(key)
                + 256
                + sum(sys.getsizeof(part) for part in key)
            )
            if size <= MAX_CACHE_BYTES and count <= MAX_CACHE_BARS and MAX_CACHE > 0:
                while self.cache and (
                    len(self.cache) >= MAX_CACHE
                    or self.cache_bytes + size > MAX_CACHE_BYTES
                    or self.cache_bars + count > MAX_CACHE_BARS
                ):
                    self._evict(next(iter(self.cache)))
                self.cache[key] = (time.monotonic(), encoded, size, count)
                self.cache_bytes += size
                self.cache_bars += count
        return encoded

    @staticmethod
    def _response(
        instrument,
        interval,
        candles,
        limit,
        now,
        before,
        identity,
        *,
        state=None,
        unavailable=False,
        reasons=None,
    ):
        supported = supported_intervals(instrument)
        maximum = page_limit(instrument, before)
        if reasons is None:
            reasons = [
                "native_history_may_have_gaps",
                "closed_is_time_boundary_not_completeness",
            ]
            if limit > maximum:
                reasons.append(f"venue_limit_{maximum}")
            if instrument.venue == "deribit":
                reasons.append("deribit_excludes_block_trades")
            if instrument.venue == "hyperliquid":
                reasons.append("hyperliquid_recent_5000")
        else:
            reasons = list(reasons)
        next_before = None
        if state is None:
            if instrument.venue == "kraken":
                state = "limited"
                reasons.append("kraken_recent_only_720")
            else:
                end = min(now, before - 1) if before is not None else now
                start = request_start(end, interval, min(limit, maximum))
                next_before = candles[0].start_ns if candles else start
                if instrument.venue == "hyperliquid" and not candles:
                    # Retention is 5000 native candles, not 5000 elapsed intervals:
                    # sparse series must not be truncated by an invented time floor.
                    state = "limited"
                if state is None:
                    if next_before <= 0:
                        state = "exhausted"
                    elif not candles and instrument.venue not in (
                        "coinbase",
                        "kucoin",
                        "deribit",
                    ):
                        # End-only APIs do not prove there are no earlier trades.
                        state = "limited"
                        reasons.append("provider_history_boundary_unknown")
                    else:
                        state = "more"
        if state != "more" or (before is not None and next_before >= before):
            next_before = None
        result = response(
            instrument,
            interval,
            candles,
            source="venue_ohlcv",
            now_ns=now,
            supported=supported,
            reasons=reasons,
            unavailable=unavailable,
        )
        result["series_key"] = identity
        result["history"] = {
            "requested_before_ns": None if before is None else str(before),
            "next_before_ns": None if next_before is None else str(next_before),
            "state": state,
            "pagination": "recent_only" if instrument.venue == "kraken" else "time",
            "max_bars": maximum,
            "update_mode": "poll",
            "poll_interval_ms": CACHE_SECONDS * 1000,
        }
        return result


def session_bucket(stamp: int, interval: str) -> tuple[int, int]:
    if interval == "1M":
        utc = datetime.fromtimestamp(stamp // SECOND, timezone.utc)
        start = (
            int(datetime(utc.year, utc.month, 1, tzinfo=timezone.utc).timestamp())
            * SECOND
        )
        return start, bucket_end(start, interval)
    width = FIXED_SECONDS[interval] * SECOND
    start = stamp // width * width
    return start, start + width


def session_candles(
    history, instrument: Instrument, interval: str, limit: int, *, cohort=()
) -> dict:
    """Aggregate the same admitted native prints exposed by the market cursor.

    Delivery order determines open/close, not exchange timestamps or wall-clock
    corrections. No synthetic bars and no current-time advance during replay.
    """
    from .market_presentation import MAX_TRADE_BYTES, MAX_TRADE_ROWS

    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("invalid candle limit")
    instruments = cohort or (instrument,)
    identities = {str(item.instrument_id) for item in instruments}
    now = history.now_ns
    reasons = {
        "retained_session_trades_only",
        "session_history_partial",
        "receive_time_buckets",
        "closed_is_time_boundary_not_completeness",
        *history.reasons,
    }
    if history.truncated:
        reasons.add("retained_history_truncated")
    if now is None:
        reasons.add("missing_receive_utc")
    if interval not in LOCAL_INTERVALS:
        reasons.add("unsupported_interval")
    buckets, coverage = {}, {}
    seen = set()
    if now is not None and interval in LOCAL_INTERVALS:
        with localcontext(DECIMAL_CONTEXT):
            for item in history.trades:
                event = item.event
                identity = str(event.key.instrument_id)
                if identity not in identities:
                    continue
                if (event.session_id, event.clock_id) != history.scope:
                    reasons.add("unrelated_source_clock")
                    continue
                if item.delivery > history.highwater or item.display_time_ns > now:
                    reasons.add("future_trades_excluded")
                    continue
                if event.payload.is_subscription_history:
                    reasons.add("subscription_history_excluded")
                    continue
                if item.base_quantity is None:
                    reasons.add("unknown_trade_quantity")
                    continue
                stamp = item.display_time_ns
                start, end = session_bucket(stamp, interval)
                seen.add(identity)
                price = event.payload.price
                if start not in buckets:
                    buckets[start] = [price, price, price, price, item.base_quantity]
                    coverage[start] = [stamp, stamp, end]
                else:
                    values = buckets[start]
                    values[1] = max(values[1], price)
                    values[2] = min(values[2], price)
                    values[3] = price
                    values[4] += item.base_quantity
                    coverage[start][0] = min(coverage[start][0], stamp)
                    coverage[start][1] = max(coverage[start][1], stamp)
    starts = sorted(buckets)[-limit:]
    bars = tuple(Candle(start, coverage[start][2], *buckets[start]) for start in starts)
    actual = (
        (
            min(coverage[start][0] for start in starts),
            max(coverage[start][1] for start in starts),
        )
        if starts
        else (None, None)
    )
    missing = sorted(identities - seen)
    if missing:
        reasons.add("cohort_members_without_admitted_trades")
    result = response(
        instrument,
        interval,
        bars,
        source="session_trades",
        now_ns=now or 0,
        supported=LOCAL_INTERVALS,
        reasons=sorted(reasons),
        coverage=actual,
        unavailable=now is None or interval not in LOCAL_INTERVALS,
    )
    aggregate = len(instruments) > 1
    result["quote_asset_ids"] = sorted({item.quote.asset_id for item in instruments})
    result.update(
        instrument_ids=[str(item.instrument_id) for item in instruments],
        scope="aggregate" if aggregate else "primary",
        venue="aggregate" if aggregate else instrument.venue,
        venue_label=" + ".join(sorted({item.venue for item in instruments})),
        missing_instrument_ids=missing,
        bucket_alignment="utc_calendar_month"
        if interval == "1M"
        else "utc_epoch_floor",
        highwater=str(history.highwater),
        retention={
            "max_trades": MAX_TRADE_ROWS,
            "max_bytes": MAX_TRADE_BYTES,
            "retained_trades": len(history.trades),
            "retained_bytes": history.bytes,
            "truncated": history.truncated,
        },
    )
    if aggregate:
        result["reasons"].append("indicative_quote_family_no_conversion")
    return result
