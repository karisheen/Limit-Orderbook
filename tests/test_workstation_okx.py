"""Deterministic OKX public protocol fixtures; no live availability claim."""

from dataclasses import replace
from decimal import Decimal, localcontext
from hashlib import sha256
import json

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.okx import CONTRACT_SPECIFICATION_SOURCE, OKXAdapter
from src.workstation.events import (
    BookUpdate,
    Control,
    Gap,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
    synthetic_frame,
)
from src.workstation.ingestion import make_snapshot_reducer
from src.workstation.instruments import Instrument
from src.workstation.wire import dumps, loads


D = Decimal


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def metadata_row(swap=False, **changes):
    row = {
        "instType": "SWAP" if swap else "SPOT",
        "instId": "BTC-USDT-SWAP" if swap else "BTC-USDT",
        "baseCcy": "" if swap else "BTC",
        "quoteCcy": "" if swap else "USDT",
        "tickSz": "0.1",
        "lotSz": "0.01" if swap else "0.000001",
        "minSz": "0.01" if swap else "0.00001",
        "maxLmtSz": "100000",
        "maxMktSz": "10000",
        "state": "live",
        "instCategory": "1",
    }
    if swap:
        row.update(
            {
                "ctType": "linear",
                "ctVal": "0.01",
                "ctMult": "1",
                "ctValCcy": "BTC",
                "settleCcy": "USDT",
                "instFamily": "BTC-USDT",
            }
        )
    return row | changes


def metadata_bytes(*rows):
    return encoded({"code": "0", "msg": "", "data": list(rows)})


def setup(*, swap=False, channel="book"):
    adapter = OKXAdapter()
    instrument = adapter.parse_metadata(
        metadata_bytes(metadata_row(swap)),
        inst_type="SWAP" if swap else "SPOT",
        fetch_wall_ns=123,
    ).instruments[0]
    key = SubscriptionKey.for_instrument(
        instrument, channel=channel, depth=5 if channel == "book" else None
    )
    return adapter, instrument, key


def book_message(instrument, *, seq=100, bids=None, asks=None):
    return {
        "arg": {"channel": "books5", "instId": instrument.native_symbol},
        "data": [
            {
                "bids": bids if bids is not None else [["100.0", "2.00", "0", "2"]],
                "asks": asks if asks is not None else [["100.1", "3.00", "0", "1"]],
                "ts": "1700000000123",
                "seqId": seq,
            }
        ],
    }


def trade_row(instrument, **changes):
    return {
        "instId": instrument.native_symbol,
        "tradeId": "9007199254740993",
        "px": "100.1",
        "sz": "1.25",
        "side": "buy",
        "ts": "1700000000123",
        "count": "2",
        "seqId": 123,
    } | changes


def trade_message(instrument, *rows):
    return {
        "arg": {"channel": "trades", "instId": instrument.native_symbol},
        "data": list(rows) or [trade_row(instrument)],
    }


def raw_frame(adapter, instrument, key, message, *, rest=False, ordinal=1, **options):
    return synthetic_frame(
        message if isinstance(message, bytes) else encoded(message),
        ordinal=ordinal,
        mono_ns=ordinal,
        transport="rest" if rest else "ws",
        endpoint_id=adapter.snapshot_request(instrument, key).endpoint_id
        if rest
        else adapter.stream,
        **options,
    )


def apply_message(adapter, instrument, key, reducer, message, *, rest=False, ordinal=1):
    raw = raw_frame(
        adapter,
        instrument,
        key,
        message,
        rest=rest,
        ordinal=ordinal,
        epoch=reducer.epoch,
    )
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    return reducer.apply(
        wrap_decoded(
            raw,
            instrument,
            key,
            decoded,
            reducer.segment_id,
            "rest_snapshot" if rest else "ws_snapshot",
        )
    )


@pytest.mark.parametrize("swap", (False, True))
def test_metadata_preserves_native_units_and_explicit_contract_valuation(swap):
    _, instrument, _ = setup(swap=swap)
    assert instrument.market_type == ("perpetual" if swap else "spot")
    assert instrument.api_family == ("v5-swap" if swap else "v5-spot")
    assert instrument.base.asset_id == "BTC" and instrument.quote.asset_id == "USDT"
    assert instrument.quantity_unit == ("contracts" if swap else "base")
    assert instrument.lot_size == D("0.01" if swap else "0.000001")
    assert instrument.contract_multiplier == D("0.01" if swap else "1")
    assert instrument.constraints.min_base == D("0.0001" if swap else "0.00001")
    assert instrument.constraints.max_limit_base == D("1000" if swap else "100000")
    assert instrument.constraints.max_market_base == (D("100") if swap else None)
    assert not instrument.inverse
    assert instrument.metadata_asof_wall_ns == 123
    assert instrument.data_access == instrument.execution_access == "unknown"
    if swap:
        assert instrument.settlement == instrument.quote
        assert instrument.settlement_conversion == "none"
        assert instrument.contract_value_currency == instrument.base.asset_id
        assert instrument.contract_specification_source == CONTRACT_SPECIFICATION_SOURCE
    assert loads(dumps(instrument), Instrument) == instrument


def test_metadata_constraints_conversion_is_independent_of_ambient_precision():
    row = metadata_row(True, ctVal="0.000123456789", minSz="123.45")
    with localcontext() as context:
        context.prec = 4
        instrument = (
            OKXAdapter()
            .parse_metadata(metadata_bytes(row), inst_type="SWAP")
            .instruments[0]
        )
    assert instrument.constraints.min_base == D("0.01524074060205")


def test_discovery_excludes_inverse_and_noncrypto_without_aliasing_symbols():
    rows = (
        metadata_row(True),
        metadata_row(True, instId="BTC-USD-SWAP", ctType="inverse"),
        metadata_row(True, instId="XAU-USDT-SWAP", instCategory="4"),
        metadata_row(True, instId="EUR-USDT-SWAP", instCategory="5"),
        metadata_row(
            True,
            instId="ETH-USDT-SWAP",
            instFamily="ETH-USDT",
            ctValCcy="ETH",
            state="suspend",
        ),
    )
    snapshot = OKXAdapter().parse_metadata(metadata_bytes(*rows), inst_type="SWAP")
    assert {item.native_symbol for item in snapshot.instruments} == {
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    }
    assert (
        next(item for item in snapshot.instruments if item.base.ticker == "ETH").status
        == "suspended"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ctValCcy": "USDT"},
        {"settleCcy": "USDC"},
        {"minSz": "0"},
        {"ctVal": "NaN"},
        {"tickSz": "0"},
        {"lotSz": "-1"},
        {"instFamily": "BTC-USD"},
        {"ctType": "quanto"},
        {"instType": "FUTURES"},
    ],
)
def test_bad_contract_metadata_is_explicit(changes):
    with pytest.raises(ValueError):
        OKXAdapter().parse_metadata(
            metadata_bytes(metadata_row(True, **changes)), inst_type="SWAP"
        )


def test_metadata_endpoint_errors_and_duplicates_are_not_empty_success():
    adapter = OKXAdapter()
    with pytest.raises(ValueError, match="metadata endpoint error"):
        adapter.parse_metadata(
            encoded({"code": "50011", "msg": "rate limited", "data": []})
        )
    with pytest.raises(ValueError, match="duplicate metadata"):
        adapter.parse_metadata(metadata_bytes(metadata_row(), metadata_row()))


@pytest.mark.asyncio
async def test_discovery_fetches_both_public_metadata_markets(monkeypatch):
    from src.workstation import transport

    requests = []

    async def fetch(request):
        requests.append(request)
        return metadata_bytes(metadata_row(request.endpoint_id.endswith("SWAP")))

    monkeypatch.setattr(transport, "fetch_public", fetch)
    snapshot = await OKXAdapter().discover()
    assert {item.market_type for item in snapshot.instruments} == {"spot", "perpetual"}
    assert [request.endpoint_id.rsplit("=", 1)[1] for request in requests] == [
        "SPOT",
        "SWAP",
    ]
    assert all(
        request.method == "GET" and request.body is None and not request.headers
        for request in requests
    )
    assert (
        snapshot.payload_hash
        == sha256(
            b"\x00".join(
                [metadata_bytes(metadata_row()), metadata_bytes(metadata_row(True))]
            )
        ).hexdigest()
    )


@pytest.mark.parametrize("swap", (False, True))
@pytest.mark.parametrize("channel", ("book", "trades"))
def test_public_requests_route_market_identity_without_private_operations(
    swap, channel
):
    adapter, instrument, key = setup(swap=swap, channel=channel)
    (request,) = adapter.subscription_requests(instrument, key)
    assert request.endpoint_id == adapter.ws_endpoint(instrument, key)
    assert request.endpoint_id == "wss://ws.okx.com:443/ws/v5/public"
    assert json.loads(request.body) == {
        "op": "subscribe",
        "args": [
            {
                "channel": "books5" if channel == "book" else "trades",
                "instId": instrument.native_symbol,
            }
        ],
    }
    snapshot = adapter.snapshot_request(instrument, key)
    assert f"instId={instrument.native_symbol}" in snapshot.endpoint_id
    capabilities = adapter.capabilities(instrument, key)
    assert not capabilities.deltas and not capabilities.exact_event_flow
    assert capabilities.checksum_scope is None
    assert capabilities.snapshot == (channel == "book")
    assert capabilities.trades == (channel == "trades")


@pytest.mark.parametrize("depth", (None, 1, 20, 400))
def test_only_documented_five_level_snapshot_depth_is_supported(depth):
    adapter, instrument, key = setup()
    with pytest.raises(ValueError, match="depth 5"):
        adapter.subscription_requests(instrument, replace(key, depth=depth))


@pytest.mark.parametrize("swap", (False, True))
def test_snapshots_replace_window_preserve_exact_values_and_accept_sequence_reset(swap):
    adapter, instrument, key = setup(swap=swap)
    reducer = adapter.new_reducer(instrument, key)
    first = apply_message(
        adapter,
        instrument,
        key,
        reducer,
        book_message(
            instrument, bids=[["100.0", "2.00", "0", "1"], ["99.9", "1.00", "0", "1"]]
        ),
    )
    assert str(first.book.bids[0].price) == "100.0"
    assert str(first.book.bids[0].quantity) == "2.00"
    assert first.book.quality.coverage.subscribed_depth == 5
    assert not first.book.quality.coverage.full_depth
    assert first.events[0].exchange_event_wall_ns == 1700000000123000000
    assert first.events[0].sequence.last == "100"
    replacement = apply_message(
        adapter, instrument, key, reducer, book_message(instrument, seq=2), ordinal=2
    )
    assert len(replacement.book.bids) == 1
    assert replacement.book.quality.integrity == "valid"
    assert reducer.sequence.last == "2"
    repeated = apply_message(
        adapter, instrument, key, reducer, book_message(instrument, seq=2), ordinal=3
    )
    assert not repeated.changed
    assert repeated.book.quality.last_book_observation_mono_ns == 3
    assert repeated.book.quality.last_book_change_mono_ns == 2


@pytest.mark.parametrize("bad_kind", ("crossed", "duplicate", "empty"))
def test_invalid_snapshot_is_atomic_and_next_snapshot_recovers(bad_kind):
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    first = apply_message(adapter, instrument, key, reducer, book_message(instrument))
    message = book_message(instrument, seq=101)
    if bad_kind == "crossed":
        message["data"][0]["asks"][0][0] = "99.9"
    elif bad_kind == "duplicate":
        message["data"][0]["bids"] *= 2
    else:
        message["data"][0]["asks"] = []
    rejected = apply_message(adapter, instrument, key, reducer, message, ordinal=2)
    assert rejected.recovery is not None
    assert reducer.book.bids == first.book.bids
    assert reducer.book.quality.integrity == "invalid"
    recovered = apply_message(
        adapter, instrument, key, reducer, book_message(instrument, seq=1), ordinal=3
    )
    assert recovered.recovery is None and recovered.book.quality.integrity == "valid"


def test_gap_then_rest_snapshot_recovers_bounded_book_and_checkpoint_replays():
    adapter, instrument, key = setup(swap=True)
    reducer = adapter.new_reducer(instrument, key)
    apply_message(adapter, instrument, key, reducer, book_message(instrument))
    gap = synthetic_envelope(
        instrument,
        Gap("transport_gap"),
        ordinal=2,
        key=key,
        segment_id=reducer.segment_id,
        source_mode="ws_snapshot",
    )
    assert reducer.apply((gap,)).recovery.reason == "transport_gap"
    # Live REST fallback starts a separate epoch with snapshot-only evidence;
    # changing source_mode on the old WS reducer is correctly rejected.
    reducer = make_snapshot_reducer(
        adapter, instrument, key, epoch=1, segment_id="rest-recovery"
    )
    rest = {"code": "0", "data": book_message(instrument, seq=3)["data"]}
    recovered = apply_message(
        adapter, instrument, key, reducer, rest, rest=True, ordinal=3
    )
    assert recovered.recovery is None and reducer.synchronized
    assert recovered.book.quality.integrity == "valid"
    assert recovered.book.quality.coverage.subscribed_depth == 5
    assert not recovered.book.quality.coverage.full_depth
    assert recovered.book.quality.source_mode == "rest_snapshot"
    assert recovered.book.quality.lifecycle == "degraded"
    restored = make_snapshot_reducer(
        adapter, instrument, key, epoch=1, segment_id="rest-recovery"
    )
    restored.restore(loads(dumps(reducer.checkpoint())))
    assert restored.book == recovered.book
    message = {"code": "0", "data": book_message(instrument, seq=4)["data"]}
    assert apply_message(
        adapter, instrument, key, restored, message, rest=True, ordinal=4
    ) == apply_message(adapter, instrument, key, reducer, message, rest=True, ordinal=4)
    assert restored.checkpoint() == reducer.checkpoint()


@pytest.mark.parametrize(
    "fault",
    (
        "delta",
        "overdepth",
        "offtick",
        "offlot",
        "badlevel",
        "badtime",
        "badsequence",
        "manysnapshots",
    ),
)
def test_book_decoder_rejects_malformed_and_out_of_bound_frames_atomically(fault):
    adapter, instrument, key = setup(swap=True)
    message = book_message(instrument)
    row = message["data"][0]
    if fault == "delta":
        message["action"] = "update"
    elif fault == "overdepth":
        row["bids"] *= 6
    elif fault == "offtick":
        row["bids"][0][0] = "100.01"
    elif fault == "offlot":
        row["bids"][0][1] = "0.001"
    elif fault == "badlevel":
        row["bids"][0] = ["100.0", "1"]
    elif fault == "badtime":
        row["ts"] = "1.5"
    elif fault == "badsequence":
        row["seqId"] = -1
    else:
        message["data"] *= 2
    raw = raw_frame(adapter, instrument, key, message)
    result = adapter.decode(raw, instrument, key)
    assert result.error is not None and result.error.raw_hash == raw.payload_sha256
    assert not result.events and not result.controls


@pytest.mark.parametrize("swap", (False, True))
@pytest.mark.parametrize("side", ("buy", "sell", "unrecognized", None))
def test_trade_side_time_native_quantity_and_aggregate_volume_are_preserved(swap, side):
    adapter, instrument, key = setup(swap=swap, channel="trades")
    message = trade_message(instrument, trade_row(instrument, side=side))
    result = adapter.decode(
        raw_frame(adapter, instrument, key, message), instrument, key
    )
    assert result.error is None
    (event,) = result.events
    assert isinstance(event.payload, Trade)
    assert event.payload.trade_id == "9007199254740993"
    assert event.payload.quantity == D(
        "1.25"
    )  # Already aggregated; never multiply by count.
    assert event.payload.quantity_unit == instrument.quantity_unit
    assert event.payload.quantity * instrument.contract_multiplier == D(
        "0.0125" if swap else "1.25"
    )
    assert event.payload.taker_side == (side if side in ("buy", "sell") else "unknown")
    assert event.payload.side_provenance == (
        "documented_taker" if side in ("buy", "sell") else "unknown"
    )
    assert event.exchange_event_wall_ns == 1700000000123000000
    assert event.sequence.last == "123"
    rest = {"code": "0", "data": message["data"]}
    historical = adapter.decode(
        raw_frame(adapter, instrument, key, rest, rest=True), instrument, key
    )
    assert historical.error is None
    assert historical.events[0].payload.is_subscription_history
    assert historical.events[0].payload.dedup_key == event.payload.dedup_key


def test_bad_trade_late_in_batch_does_not_publish_partial_trades():
    adapter, instrument, key = setup(channel="trades")
    message = trade_message(
        instrument, trade_row(instrument), trade_row(instrument, instId="ETH-USDT")
    )
    decoded = adapter.decode(
        raw_frame(adapter, instrument, key, message), instrument, key
    )
    assert decoded.error is not None and not decoded.events


@pytest.mark.parametrize(
    "payload,kind",
    [
        (b"pong", "heartbeat"),
        (
            {"event": "error", "code": "60012", "msg": "unsupported subscription"},
            "rejection",
        ),
    ],
)
def test_public_heartbeat_and_errors_remain_controls_not_valid_books(payload, kind):
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    raw = raw_frame(adapter, instrument, key, payload)
    result = adapter.decode(raw, instrument, key)
    assert result.error is None and result.controls[0].kind == kind
    reduced = reducer.apply(
        wrap_decoded(raw, instrument, key, result, reducer.segment_id, "ws_snapshot")
    )
    assert reduced.book is None and not reducer.synchronized


@pytest.mark.parametrize("swap", (False, True))
@pytest.mark.parametrize("channel", ("book", "trades"))
@pytest.mark.parametrize(
    "code,detail",
    [
        (
            "64010",
            "WebSocket port 8443 will be retired soon. Please migrate to port 443.",
        ),
        (
            "64008",
            "The connection will soon be closed for a service upgrade. Please reconnect.",
        ),
    ],
)
def test_notice_then_ack_then_market_data_preserves_stream_and_provenance(
    swap, channel, code, detail
):
    adapter, instrument, key = setup(swap=swap, channel=channel)
    reducer = adapter.new_reducer(instrument, key)
    notice = {
        "event": "notice",
        "code": code,
        "msg": detail,
        "connId": "d7d6f1f5",
    }
    raw = raw_frame(adapter, instrument, key, notice)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None and not decoded.events
    assert decoded.controls == (Control("clock_tick", f"venue_notice:{code}", detail),)
    (envelope,) = wrap_decoded(
        raw, instrument, key, decoded, reducer.segment_id, "ws_snapshot"
    )
    assert envelope.raw_ref == raw.ref()
    assert raw.payload == encoded(notice)
    assert envelope.connection_id == raw.connection_id
    assert envelope.connection_epoch == raw.connection_epoch
    assert envelope.ingest_ordinal == raw.ingest_ordinal
    assert envelope.receive_mono_ns == raw.receive_mono_ns
    assert envelope.exchange_event_wall_ns is None
    assert envelope.sequence is None
    noticed = reducer.apply((envelope,))
    assert noticed.recovery is None and noticed.book is None
    assert not reducer.synchronized
    assert reducer.quality.last_transport_mono_ns is None
    assert reducer.quality.last_book_observation_mono_ns is None
    assert reducer.quality.last_healthy_observation_mono_ns is None
    ack = {
        "event": "subscribe",
        "arg": {
            "channel": "books5" if channel == "book" else "trades",
            "instId": instrument.native_symbol,
        },
        "connId": notice["connId"],
    }
    acknowledged = apply_message(adapter, instrument, key, reducer, ack, ordinal=2)
    assert acknowledged.recovery is None and acknowledged.book is None
    assert acknowledged.events[0].payload == Control("ack")
    assert not reducer.synchronized
    message = (
        book_message(instrument) if channel == "book" else trade_message(instrument)
    )
    observed = apply_message(adapter, instrument, key, reducer, message, ordinal=3)
    assert observed.recovery is None
    assert isinstance(
        observed.events[0].payload, BookUpdate if channel == "book" else Trade
    )
    if channel == "book":
        assert reducer.synchronized and observed.book.quality.integrity == "valid"
        assert observed.book.quality.last_book_observation_mono_ns == 3
        after_notice = apply_message(
            adapter, instrument, key, reducer, notice, ordinal=4
        )
        assert after_notice.recovery is None and not after_notice.changed
        assert after_notice.book.quality.integrity == "valid"
        assert after_notice.book.quality.last_book_observation_mono_ns == 3


@pytest.mark.parametrize("code", ("60012", "64010", "64008"))
def test_error_envelopes_remain_rejections_even_with_notice_codes(code):
    adapter, instrument, key = setup()
    reducer = adapter.new_reducer(instrument, key)
    apply_message(adapter, instrument, key, reducer, book_message(instrument))
    rejected = apply_message(
        adapter,
        instrument,
        key,
        reducer,
        {"event": "error", "code": code, "msg": "subscription rejected"},
        ordinal=2,
    )
    assert rejected.events[0].payload == Control(
        "rejection", f"subscription_rejected:{code}", "subscription rejected"
    )
    assert rejected.recovery is not None
    assert rejected.book.quality.integrity == "invalid"


@pytest.mark.parametrize(
    "message",
    [
        {"event": "notice", "msg": "missing code"},
        {"event": "notice", "code": "64010"},
        {"event": "notice", "code": 64010, "msg": "invalid code type"},
        {"event": "notice", "code": "64010", "msg": {}},
        {"event": "unknown", "code": "64010", "msg": "not a notice"},
        {
            "event": "subscribe",
            "code": "60012",
            "arg": {"channel": "books5", "instId": "BTC-USDT"},
        },
    ],
)
def test_malformed_notices_and_failed_acknowledgements_are_not_swallowed(message):
    adapter, instrument, key = setup()
    raw = raw_frame(adapter, instrument, key, message)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is not None
    assert decoded.error.raw_hash == raw.payload_sha256
    assert not decoded.events and not decoded.controls


def test_notice_detail_is_bounded_without_losing_raw_payload():
    adapter, instrument, key = setup()
    message = {"event": "notice", "code": "64010", "msg": "x" * 5000}
    raw = raw_frame(adapter, instrument, key, message)
    decoded = adapter.decode(raw, instrument, key)
    assert decoded.error is None
    assert decoded.controls[0].detail == "x" * 4096
    assert raw.payload == encoded(message)


def test_wrong_channel_endpoint_transport_and_malformed_json_are_rejected():
    adapter, instrument, key = setup()
    good = raw_frame(adapter, instrument, key, book_message(instrument))
    wrong_topic = book_message(instrument)
    wrong_topic["arg"]["channel"] = "books"
    frames = [
        replace(good, endpoint_id="wss://ws.okx.com:443/ws/v5/private"),
        replace(good, transport="rest"),
        raw_frame(adapter, instrument, key, wrong_topic),
        raw_frame(adapter, instrument, key, b'{"data":[],"data":[]}'),
        raw_frame(adapter, instrument, key, {"code": "50011", "data": []}, rest=True),
    ]
    for raw in frames:
        result = adapter.decode(raw, instrument, key)
        assert result.error is not None and not result.events and not result.controls


def test_scoped_acknowledgement_and_replay_delta_cannot_fabricate_book():
    adapter, instrument, key = setup()
    ack = {
        "event": "subscribe",
        "arg": {"channel": "books5", "instId": instrument.native_symbol},
    }
    decoded = adapter.decode(raw_frame(adapter, instrument, key, ack), instrument, key)
    assert decoded.error is None and decoded.controls[0].kind == "ack"
    reducer = adapter.new_reducer(instrument, key)
    first = apply_message(adapter, instrument, key, reducer, book_message(instrument))
    event = replace(
        first.events[0],
        ingest_ordinal=2,
        event_index=0,
        payload=BookUpdate("delta", (), 5),
        raw_ref=None,
    )
    # A normalized replay cannot turn a snapshot-only adapter into a delta feed.
    rejected = reducer.apply((event,))
    assert rejected.recovery is not None
    assert reducer.book.bids == first.book.bids
