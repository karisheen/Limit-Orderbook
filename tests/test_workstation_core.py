"""Offline consumer contracts; synthetic fixtures do not claim live venue validation."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from hashlib import sha256
import json

import pytest

from src.workstation.book import (
    BookReducer,
    Level,
    contiguous_sequence,
    monotonic_sequence,
)
from src.workstation.events import (
    AssetContext,
    BookUpdate,
    ChecksumInfo,
    Control,
    Gap,
    LevelChange,
    MonoNs,
    Ordinal,
    ReferencePrice,
    SequenceInfo,
    SubscriptionKey,
    Trade,
    WallNs,
    synthetic_envelope,
    synthetic_frame,
)
from src.workstation.instruments import (
    AssetIdentity,
    InstrumentCatalog,
    InstrumentQuery,
    MetadataSnapshot,
    OrderConstraints,
    UnknownInstrument,
    synthetic_instrument,
)
from src.workstation.precision import (
    canonical_decimal,
    decimal,
    exact_json_loads,
    scaled_decimal,
    validate_increment,
)
from src.workstation.quality import (
    Capabilities,
    ClockReading,
    EligibilityPolicy,
    evaluate,
    evaluate_cohort,
)
from src.workstation.wire import dumps, loads, to_wire


D = Decimal


def fixture_core(**options):
    instrument = synthetic_instrument()
    key = SubscriptionKey.for_instrument(instrument, depth=options.pop("depth", None))
    reducer = BookReducer(instrument, key, segment_id="synthetic-segment", **options)
    return instrument, key, reducer


def envelope(instrument, key, kind="snapshot", changes=None, **options):
    if changes is None:
        changes = (
            LevelChange("bid", D("99.00"), D("1.000")),
            LevelChange("ask", D("101.00"), D("2.000")),
        )
    return synthetic_envelope(
        instrument, BookUpdate(kind, changes, key.depth), key=key, **options
    )


def healthy(**options):
    instrument, key, reducer = fixture_core(**options)
    result = reducer.apply((envelope(instrument, key),))
    assert result.book is not None
    return instrument, key, reducer, result.book


@pytest.mark.parametrize(
    "value",
    [
        1.2,
        True,
        "NaN",
        "Infinity",
        "-Infinity",
        D("NaN"),
        D("Infinity"),
        "1e99999",
        "1e-39",
        "1" * 129,
        "0." + "1" * 39,
        " 1",
        "1_000",
        "1e31",
    ],
)
def test_authoritative_precision_rejects_unusable_values(value):
    with pytest.raises(ValueError):
        decimal(value)


def test_exact_precision_is_independent_of_ambient_decimal_context():
    with localcontext() as context:
        context.prec = 2
        value = decimal("123456789.123456789000")
        assert str(value) == "123456789.123456789000"
        validate_increment(value, D("0.000000001"))
        assert scaled_decimal(123456789123456789, 9) == value
    assert canonical_decimal(D("-0.000")) == "0"
    assert canonical_decimal(D("12.34000")) == "12.34"
    with pytest.raises(ValueError):
        validate_increment(D("1.005"), D("0.01"))
    with pytest.raises(ValueError):
        scaled_decimal(2**129, 1)


@pytest.mark.parametrize(
    "payload",
    [b'{"p":NaN}', b'{"p":Infinity}', b'{"p":1,"p":2}', b'{"p":1e999}', b"\xff", b"{"],
)
def test_exact_json_rejects_corruption(payload):
    with pytest.raises(ValueError):
        exact_json_loads(payload)


def test_json_preserves_original_decimal_scale_and_large_integer():
    value = exact_json_loads(b'{"px":1.2300,"id":9007199254740993}')
    assert str(value["px"]) == "1.2300"
    assert value["id"] == 9007199254740993
    with pytest.raises(ValueError):
        exact_json_loads(b"{}", max_bytes=1)


def test_metadata_versions_are_retained_and_collision_is_transactional():
    catalog = InstrumentCatalog()
    old = synthetic_instrument()
    new = replace(
        old, metadata_version="v2", metadata_asof_wall_ns=20, status="suspended"
    )
    catalog.apply_metadata(MetadataSnapshot("synthetic", 1, "fixture", (old,)))
    catalog.apply_metadata(MetadataSnapshot("synthetic", 20, "fixture2", (new,)))
    assert catalog.get(old.instrument_id) == new
    assert catalog.get(old.instrument_id, old.metadata_version) == old
    assert catalog.resolve(old.venue, old.api_family, old.native_symbol) == new
    assert catalog.list(InstrumentQuery(status="suspended")) == (new,)
    with pytest.raises(UnknownInstrument):
        catalog.resolve("unknown", "unknown", "BTC")
    with pytest.raises(ValueError):
        catalog.apply_metadata(
            MetadataSnapshot("synthetic", 21, "bad", (replace(new, status="active"),))
        )
    with pytest.raises(ValueError):
        catalog.apply_metadata(
            MetadataSnapshot(
                "synthetic",
                22,
                "bad",
                (
                    replace(
                        new, metadata_version="v3", quote=AssetIdentity("USDT", "USDT")
                    ),
                ),
            )
        )
    assert catalog.get(old.instrument_id) == new


def test_instrument_constraints_are_exact_and_unknown_is_not_zero():
    constraints = OrderConstraints(
        min_base=D("0.01"), min_notional=D("5"), min_notional_currency="USD"
    )
    instrument = synthetic_instrument(constraints=constraints)
    assert constraints.max_notional is None
    instrument.validate_order(D("100"), D("0.05"), order_type="limit")
    for price, quantity in (
        (D("100.005"), D("1")),
        (D("100"), D("0.0001")),
        (D("100"), D("0.02")),
        (D("100"), D("-1")),
    ):
        with pytest.raises(ValueError):
            instrument.validate_order(price, quantity, order_type="limit")
    with pytest.raises(ValueError):
        replace(instrument, tick_size=D("NaN"))
    with pytest.raises(FrozenInstanceError):
        instrument.status = "delisted"


def test_order_type_constraints_are_explicit_and_independent():
    instrument = synthetic_instrument(
        constraints=OrderConstraints(max_limit_base=D("10"), max_market_base=D("2"))
    )
    instrument.validate_order(D("100"), D("10"), order_type="limit")
    instrument.validate_order(D("100"), D("2"), order_type="market")
    for order_type, quantity in (("limit", "10.001"), ("market", "2.001")):
        with pytest.raises(ValueError, match="outside instrument constraints"):
            instrument.validate_order(D("100"), D(quantity), order_type=order_type)
    with pytest.raises(TypeError):
        instrument.validate_order(D("100"), D("1"))
    with pytest.raises(ValueError, match="order type"):
        instrument.validate_order(D("100"), D("1"), order_type="post_only")
    generic = replace(instrument, constraints=OrderConstraints(max_base=D("1")))
    for order_type in ("limit", "market"):
        with pytest.raises(ValueError):
            generic.validate_order(D("100"), D("2"), order_type=order_type)
    unknown = replace(instrument, constraints=OrderConstraints(max_market_base=D("2")))
    unknown.validate_order(D("100"), D("10"), order_type="limit")


@pytest.mark.parametrize("field", ["max_limit_base", "max_market_base"])
def test_order_type_constraints_reject_invalid_or_inverted_bounds(field):
    for value in (D("-1"), D("NaN"), D("Infinity")):
        with pytest.raises(ValueError):
            OrderConstraints(**{field: value})
    with pytest.raises(ValueError, match="inverted"):
        OrderConstraints(min_base=D("2"), **{field: D("1")})


def test_instrument_reference_and_settlement_metadata_wire_roundtrip():
    instrument = synthetic_instrument(
        reference_multiplier=D("0.001"),
        constraints=OrderConstraints(max_limit_base=D("10"), max_market_base=D("2")),
        market_type="perpetual",
        settlement=AssetIdentity("native:USDC", "USDC"),
        settlement_conversion="quanto_no_fx",
        contract_specification_source="https://example.com/contract-specification",
    )
    encoded = dumps(instrument)
    assert loads(encoded) == instrument
    snapshot = MetadataSnapshot("fixture", 0, "fixture-hash", (instrument,))
    assert loads(dumps(snapshot)) == snapshot
    default = synthetic_instrument()
    assert loads(dumps(default)) == default
    fields = json.loads(encoded)["value"]
    assert fields["reference_multiplier"] == "0.001"
    assert fields["constraints"]["max_limit_base"] == "10"
    assert fields["constraints"]["max_market_base"] == "2"
    fields["reference_multiplier"] = 0.001
    with pytest.raises(ValueError):
        loads(json.dumps({"schema_version": 1, "value": fields}))
    fields = json.loads(encoded)["value"]
    del fields["reference_multiplier"]
    with pytest.raises(ValueError):
        loads(json.dumps({"schema_version": 1, "value": fields}))
    fields = json.loads(encoded)["value"]
    fields["constraints"]["max_market_base"] = 2
    with pytest.raises(ValueError):
        loads(json.dumps({"schema_version": 1, "value": fields}))
    for value in (D("0"), D("-1"), D("NaN"), D("Infinity")):
        with pytest.raises(ValueError):
            replace(instrument, reference_multiplier=value)
    for changes in (
        {"settlement_conversion": "fx"},
        {"settlement": None},
        {"market_type": "spot"},
        {"contract_specification_source": ""},
    ):
        with pytest.raises(ValueError):
            replace(instrument, **changes)
    assert default.reference_multiplier is None
    assert default.settlement_conversion == "unknown"
    assert (
        replace(instrument, settlement_conversion="none").settlement.asset_id
        == "native:USDC"
    )


def test_frames_hash_exact_bytes_and_validate_time_and_ordinal():
    frame = synthetic_frame(b'{"p":1.000}', ordinal=2**60)
    assert frame.payload_sha256 == sha256(frame.payload).hexdigest()
    assert loads(dumps(frame)) == frame
    with pytest.raises(ValueError):
        replace(frame, payload=b"other")
    for cls in (WallNs, MonoNs, Ordinal):
        assert cls(2**60) == 2**60
        with pytest.raises(ValueError):
            cls(-1)
        with pytest.raises(ValueError):
            cls(True)


def test_envelope_timing_and_identity_are_explicit_and_immutable():
    instrument, key, _ = fixture_core()
    event = envelope(instrument, key, ordinal=2**60)
    assert event.exchange_event_wall_ns is None
    assert event.process_start_mono_ns is None
    assert event.order_key == (2**60, 0)
    with pytest.raises(ValueError):
        replace(event, process_start_mono_ns=0)
    with pytest.raises(ValueError):
        replace(event, exchange_time_kind="invented")
    with pytest.raises(ValueError):
        SequenceInfo("scope", last=123)
    assert SubscriptionKey.for_instrument(
        instrument, aggregation=(("z", "2"), ("a", "1"))
    ).aggregation == (("a", "1"), ("z", "2"))
    with pytest.raises(ValueError):
        SubscriptionKey.for_instrument(instrument, aggregation=(("a", "2"), ("a", "1")))


def test_no_publication_before_snapshot_or_protocol_bridge():
    instrument, key, reducer = fixture_core()
    result = reducer.apply((envelope(instrument, key, "delta"),))
    assert result.book is None
    assert result.recovery.reason == "snapshot_required"
    staged = reducer.apply((envelope(instrument, key, ordinal=2),), synchronized=False)
    assert staged.book is None
    assert reducer.quality.lifecycle == "synchronizing"
    restored = BookReducer(instrument, key, segment_id="synthetic-segment")
    restored.restore(loads(dumps(reducer.checkpoint())))
    assert restored.initialized and not restored.synchronized
    result = restored.apply((envelope(instrument, key, "delta", ordinal=3),))
    assert result.book.quality.integrity == "valid"


def test_delta_order_is_preserved_and_zero_deletes():
    instrument, key, reducer, _ = healthy()
    changes = (
        LevelChange("bid", D("98"), D("2")),
        LevelChange("bid", D("98"), D("3")),
        LevelChange("bid", D("98"), D("-1"), "incremental"),
        LevelChange("bid", D("99"), D("0")),
    )
    result = reducer.apply((envelope(instrument, key, "delta", changes, ordinal=2),))
    assert result.book.bids == (Level(D("98"), D("2")),)
    assert result.changed


def test_tiny_delta_reuses_immutable_levels_and_untouched_side():
    instrument, key, reducer = fixture_core(depth=1)
    changes = tuple(
        LevelChange(side, D(price), D("1.000"))
        for side, prices in (
            ("bid", ("99", "98", "97")),
            ("ask", ("101", "102", "103")),
        )
        for price in prices
    )
    old = reducer.apply((envelope(instrument, key, changes=changes),)).book
    old_wire = dumps(old)
    result = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                (LevelChange("bid", D("98.00"), D("4.000")),),
                ordinal=2,
            ),
        )
    )
    new = result.book
    assert result.changed
    assert new.asks is old.asks
    assert new.bids[0] is old.bids[0]
    assert new.bids[2] is old.bids[2]
    assert len(new.bids) == len(new.asks) == 3  # depth is not a display truncation
    assert sum(level.price * level.quantity for level in new.asks) == D("306")
    assert str(new.bids[1].price) == "98.00"
    assert str(new.bids[1].quantity) == "4.000"
    assert dumps(old) == old_wire
    assert old.bids[1].quantity == 1
    with pytest.raises(FrozenInstanceError):
        new.bids[0].quantity = D("9")
    with pytest.raises(FrozenInstanceError):
        new.asks = ()
    heartbeat = synthetic_envelope(instrument, Control("heartbeat"), key=key, ordinal=3)
    observed = reducer.apply((heartbeat,)).book
    assert observed.bids is new.bids
    assert observed.asks is new.asks
    reducer.reset(1, "new-segment")
    assert dumps(old) == old_wire
    assert new.bids[1].quantity == 4


def test_reducer_trusted_path_keeps_public_constructor_validation(monkeypatch):
    from src.workstation.book import Book

    instrument, key, reducer, old = healthy()
    delta = envelope(
        instrument,
        key,
        "delta",
        (LevelChange("bid", D("99"), D("3")),),
        ordinal=2,
    )

    def unexpected_validation(self):
        raise AssertionError("revalidating an already checked immutable value")

    # Both the new Level and candidate Book have already established all public
    # constructor invariants in apply; publication must not scan them again.
    with monkeypatch.context() as patch:
        patch.setattr(Level, "__post_init__", unexpected_validation)
        patch.setattr(Book, "__post_init__", unexpected_validation)
        new = reducer.apply((delta,)).book
    assert new.bids[0].quantity == 3
    assert old.bids[0].quantity == 1
    for price, quantity in (("NaN", "1"), ("0", "1"), ("99", "-1"), ("99", "1e31")):
        with pytest.raises(ValueError):
            Level(D(price), D(quantity))
    with pytest.raises(ValueError, match="ordered"):
        replace(new, bids=(Level(D("98"), D("1")), new.bids[0]))
    with pytest.raises(ValueError, match="crossed"):
        replace(new, asks=(Level(D("98"), D("1")),))
    with pytest.raises(ValueError, match="identity"):
        replace(new, instrument_id="another-instrument")
    for side in ("bids", "asks"):
        with pytest.raises(ValueError):
            replace(new, **{side: ()})


@pytest.mark.parametrize(
    "bad_change,reason",
    [
        (LevelChange("ask", D("101.005"), D("1")), "price is off increment"),
        (LevelChange("ask", D("101"), D("0.0001")), "quantity is off increment"),
        (LevelChange("ask", D("101"), D("-3"), "incremental"), "invalid decimal sign"),
        (
            LevelChange("ask", D("101"), D("1e30"), "incremental"),
            "decimal magnitude exceeded",
        ),
        (LevelChange("ask", D("98"), D("1")), "crossed_or_locked"),
    ],
)
def test_copied_candidate_rolls_back_both_sides_on_corruption(bad_change, reason):
    instrument, key, reducer, old = healthy()
    old_wire = dumps(old)
    first = envelope(
        instrument,
        key,
        "delta",
        (LevelChange("bid", D("98"), D("5")),),
        ordinal=2,
    )
    second = envelope(instrument, key, "delta", (bad_change,), ordinal=2, event_index=1)
    result = reducer.apply((first, second))
    assert result.recovery.reason == reason
    assert result.book.bids is old.bids
    assert result.book.asks is old.asks
    assert result.book.revision == old.revision
    assert result.book.quality.integrity == "invalid"
    assert dumps(old) == old_wire
    restored = BookReducer(instrument, key, segment_id="synthetic-segment")
    restored.restore(loads(dumps(reducer.checkpoint())))
    assert restored.book == result.book
    assert restored.checkpoint() == reducer.checkpoint()


def test_shared_candidates_preserve_checksum_rollback_and_lexical_updates():
    candidates = []

    def checksum(bids, asks, info):
        candidates.append((bids, asks))
        return str(bids[0].price) + ":" + str(bids[0].quantity)

    instrument, key, reducer, old = healthy(checksum_calculator=checksum)
    old_wire = dumps(old)
    result = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                (LevelChange("bid", D("99.000"), D("1.0000")),),
                ordinal=2,
                checksum=ChecksumInfo("synthetic", "99.000:1.0000"),
            ),
        )
    )
    assert not result.changed  # economics unchanged, lexical checksum data updated
    assert result.book.bids is candidates[-1][0]
    assert result.book.asks is candidates[-1][1] is old.asks
    committed = result.book
    failed = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                (LevelChange("bid", D("99"), D("8")),),
                ordinal=3,
                checksum=ChecksumInfo("synthetic", "wrong"),
            ),
        )
    )
    assert failed.recovery.reason == "checksum_mismatch"
    assert failed.book.bids is committed.bids
    assert failed.book.asks is committed.asks
    assert candidates[-1][0][0].quantity == 8
    assert committed.bids[0].quantity == 1
    assert dumps(old) == old_wire


@pytest.mark.parametrize("truncate", [False, True])
def test_full_snapshot_delta_and_checkpoint_reconstruction_agree(truncate):
    instrument, key, reducer = fixture_core(depth=2, truncate_depth=truncate)
    initial = tuple(
        LevelChange(side, D(price), D("1"))
        for side, prices in (
            ("bid", ("99", "98", "97")),
            ("ask", ("101", "102", "103")),
        )
        for price in prices
    )
    old = reducer.apply((envelope(instrument, key, changes=initial),)).book
    restored = BookReducer(
        instrument, key, segment_id="synthetic-segment", truncate_depth=truncate
    )
    restored.restore(loads(dumps(reducer.checkpoint())))
    changes = (
        LevelChange("bid", D("99"), D("0")),
        LevelChange("bid", D("98"), D("2"), "incremental"),
        LevelChange("bid", D("100"), D("4")),
        LevelChange("ask", D("102.00"), D("7.000")),
    )
    delta = envelope(instrument, key, "delta", changes, ordinal=2)
    result = reducer.apply((delta,))
    replayed = restored.apply((delta,))
    assert replayed == result
    assert restored.checkpoint() == reducer.checkpoint()
    assert result.book.bids[0].quantity == 4
    assert result.book.bids[1].quantity == 3
    full = BookReducer(
        instrument, key, segment_id="synthetic-segment", truncate_depth=truncate
    )
    expected_changes = (
        LevelChange("bid", D("100"), D("4")),
        LevelChange("bid", D("98"), D("3")),
        LevelChange("bid", D("97"), D("1")),
        LevelChange("ask", D("101"), D("1")),
        LevelChange("ask", D("102.00"), D("7.000")),
        LevelChange("ask", D("103"), D("1")),
    )
    rebuilt = full.apply((envelope(instrument, key, changes=expected_changes),)).book
    assert rebuilt.bids == result.book.bids
    assert rebuilt.asks == result.book.asks
    assert rebuilt.quality.coverage == result.book.quality.coverage
    assert old.bids[0].price == 99
    assert old.asks[1].quantity == 1


@pytest.mark.parametrize(
    "changes,reason",
    [
        (
            (
                LevelChange("bid", D("99"), D("1")),
                LevelChange("bid", D("99.0"), D("2")),
                LevelChange("ask", D("101"), D("1")),
            ),
            "duplicate_snapshot_price",
        ),
        ((), "empty_side"),
        (
            (
                LevelChange("bid", D("101"), D("1")),
                LevelChange("ask", D("101"), D("1")),
            ),
            "crossed_or_locked",
        ),
        (
            (
                LevelChange("bid", D("102"), D("1")),
                LevelChange("ask", D("101"), D("1")),
            ),
            "crossed_or_locked",
        ),
    ],
)
def test_invalid_snapshot_is_never_usable(changes, reason):
    instrument, key, reducer = fixture_core()
    result = reducer.apply((envelope(instrument, key, changes=changes),))
    assert result.book is None
    assert result.recovery.reason == reason
    assert reducer.quality.integrity == "invalid"


def test_entire_message_is_validated_not_intermediate_cross():
    instrument, key, reducer, _ = healthy()
    first = envelope(
        instrument, key, "delta", (LevelChange("bid", D("102"), D("1")),), ordinal=2
    )
    second = envelope(
        instrument,
        key,
        "delta",
        (LevelChange("ask", D("101"), D("0")), LevelChange("ask", D("103"), D("1"))),
        ordinal=2,
        event_index=1,
    )
    result = reducer.apply((first, second))
    assert result.recovery is None
    assert result.book.bids[0].price == D("102")
    assert result.book.asks[0].price == D("103")


def test_transaction_failure_does_not_leak_partial_changes():
    instrument, key, reducer, old = healthy()
    changes = (
        LevelChange("bid", D("98"), D("4")),
        LevelChange("ask", D("100.005"), D("1")),
    )
    result = reducer.apply((envelope(instrument, key, "delta", changes, ordinal=2),))
    assert result.recovery is not None
    assert result.book.bids == old.bids
    assert result.book.asks == old.asks
    assert not evaluate(
        result.book, instrument, "paper_fill", ClockReading("synthetic-clock", 2)
    ).eligible
    heartbeat = synthetic_envelope(instrument, Control("heartbeat"), key=key, ordinal=3)
    reducer.apply((heartbeat,))
    assert reducer.quality.integrity == "invalid"
    assert reducer.quality.last_healthy_observation_mono_ns == 1
    assert (
        reducer.apply((envelope(instrument, key, "delta", ordinal=4),)).recovery.reason
        == "snapshot_required"
    )


def test_depth_is_protocol_opt_in_not_a_generic_display_slice():
    for truncate, expected in ((False, 3), (True, 2)):
        instrument, key, reducer = fixture_core(depth=2, truncate_depth=truncate)
        changes = tuple(
            LevelChange("bid", D(price), D("1")) for price in ("99", "98")
        ) + tuple(LevelChange("ask", D(price), D("1")) for price in ("101", "102"))
        reducer.apply((envelope(instrument, key, changes=changes),))
        result = reducer.apply(
            (
                envelope(
                    instrument,
                    key,
                    "delta",
                    (LevelChange("bid", D("100"), D("1")),),
                    ordinal=2,
                ),
            )
        )
        assert len(result.book.bids) == expected
        assert result.book.quality.coverage.retained_bid_levels == expected


@pytest.mark.parametrize("side", ["bid", "ask"])
@pytest.mark.parametrize("kind", ["snapshot", "delta"])
@pytest.mark.parametrize("invalid_field", ["price", "quantity"])
def test_invalid_levels_beyond_retained_depth_reject_entire_message(
    side, kind, invalid_field
):
    instrument, key, reducer, old = healthy(depth=1, truncate_depth=True)
    old_wire = dumps(old)
    price = D("98") if side == "bid" else D("102")
    quantity = D("1")
    if invalid_field == "price":
        price += D("0.005")
    else:
        quantity = D("0.0001")
    changes = (
        LevelChange("bid", D("99"), D("7")),
        LevelChange("ask", D("101"), D("8")),
        LevelChange(side, price, quantity),
    )

    result = reducer.apply((envelope(instrument, key, kind, changes, ordinal=2),))

    assert result.recovery is not None
    assert result.book is not None
    assert result.book.bids == old.bids
    assert result.book.asks == old.asks
    assert result.book.revision == old.revision
    assert result.book.quality.integrity == "invalid"
    assert not reducer.synchronized
    assert dumps(old) == old_wire


@pytest.mark.parametrize("side", ["bid", "ask"])
def test_duplicate_snapshot_levels_beyond_retained_depth_are_rejected(side):
    instrument, key, reducer = fixture_core(depth=1, truncate_depth=True)
    price = D("98") if side == "bid" else D("102")
    changes = (
        LevelChange("bid", D("99"), D("1")),
        LevelChange("ask", D("101"), D("1")),
        LevelChange(side, price, D("1")),
        LevelChange(side, price, D("2")),
    )

    result = reducer.apply((envelope(instrument, key, changes=changes),))

    assert result.recovery is not None
    assert result.book is None
    assert reducer.quality.integrity == "invalid"
    assert not reducer.synchronized


def test_old_epoch_wrong_identity_and_duplicate_ordinals_do_not_mutate_state():
    instrument, key, reducer, old = healthy()
    for event, reason in (
        (envelope(instrument, key), "obsolete_ordinal"),
        (envelope(instrument, key, ordinal=2, epoch=1), "unannounced_epoch"),
        (envelope(instrument, key, ordinal=2, segment_id="other"), "wrong_segment"),
        (
            replace(
                envelope(instrument, key, ordinal=2),
                instrument_metadata_version="other",
            ),
            "wrong_subscription",
        ),
    ):
        checkpoint = reducer.checkpoint()
        assert reducer.apply((event,)).recovery.reason == reason
        assert reducer.checkpoint() == checkpoint
    reducer.reset(1, "next")
    assert (
        reducer.apply((envelope(instrument, key, ordinal=2),)).recovery.reason
        == "obsolete_epoch"
    )
    assert reducer.book is None
    with pytest.raises(ValueError):
        reducer.reset(0, "past")
    assert old.bids[0].price == D("99")


def test_sequence_strategy_counts_controls_without_inventing_bybit_contiguity():
    instrument, key, reducer = fixture_core(
        sequence_validator=contiguous_sequence,
        capabilities=Capabilities(deltas=True, sequence_scope="connection"),
    )
    reducer.apply(
        (envelope(instrument, key, sequence=SequenceInfo("connection", last="0")),)
    )
    ack = synthetic_envelope(
        instrument,
        Control("ack"),
        key=key,
        ordinal=2,
        sequence=SequenceInfo("connection", last="1"),
    )
    assert reducer.apply((ack,)).recovery is None
    result = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                ordinal=3,
                sequence=SequenceInfo("connection", last="2"),
            ),
        )
    )
    assert result.recovery is None
    duplicate = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                ordinal=4,
                sequence=SequenceInfo("connection", last="2"),
            ),
        )
    )
    assert not duplicate.changed and duplicate.events == ()
    gap = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                ordinal=5,
                sequence=SequenceInfo("connection", last="4"),
            ),
        )
    )
    assert gap.recovery.reason == "sequence_gap"
    assert (
        monotonic_sequence(
            SequenceInfo("u", last="2"), SequenceInfo("u", last="500"), False
        )
        == "accept"
    )
    assert (
        contiguous_sequence(
            SequenceInfo("u", last="2"), SequenceInfo("u", last="500"), False
        )
        == "gap"
    )


def test_checksum_receives_whole_truncated_lexical_candidate_before_commit():
    seen = []

    def checksum(bids, asks, info):
        seen.append((bids, asks))
        return str(bids[0].price) + ":" + str(bids[0].quantity)

    instrument, key, reducer = fixture_core(
        depth=1,
        truncate_depth=True,
        checksum_calculator=checksum,
        capabilities=Capabilities(checksum_scope="top"),
    )
    result = reducer.apply(
        (envelope(instrument, key, checksum=ChecksumInfo("synthetic", "99.00:1.000")),)
    )
    assert result.events[0].checksum.result == "match"
    old = result.book
    failed = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                (LevelChange("bid", D("100.00"), D("2.000")),),
                ordinal=2,
                checksum=ChecksumInfo("synthetic", "wrong"),
            ),
        )
    )
    assert len(seen[-1][0]) == 1
    assert failed.recovery.reason == "checksum_mismatch"
    assert failed.book.bids == old.bids


def test_protocol_price_and_candidate_hooks_run_before_commit():
    def price_rule(instrument, price):
        if price.as_tuple().exponent < -3:
            raise ValueError("venue_price_rule")

    def candidate(bids, asks, events):
        if bids[0].quantity > 10:
            raise ValueError("venue_candidate_rule")

    instrument = synthetic_instrument(
        tick_size="0.01", constraints=OrderConstraints(price_rule="significant_figures")
    )
    instrument = replace(instrument, tick_size=None)
    key = SubscriptionKey.for_instrument(instrument)
    reducer = BookReducer(
        instrument,
        key,
        segment_id="synthetic-segment",
        price_validator=price_rule,
        candidate_validator=candidate,
    )
    result = reducer.apply(
        (
            envelope(
                instrument,
                key,
                changes=(
                    LevelChange("bid", D("99.123"), D("1")),
                    LevelChange("ask", D("101"), D("1")),
                ),
            ),
        )
    )
    assert result.book.bids[0].price == D("99.123")
    result = reducer.apply(
        (
            envelope(
                instrument,
                key,
                "delta",
                (LevelChange("bid", D("99.123"), D("11")),),
                ordinal=2,
            ),
        )
    )
    assert result.recovery.reason == "venue_candidate_rule"
    assert result.book.bids[0].quantity == 1


@pytest.mark.parametrize("depth", [0, -1, 100_001, True])
def test_effective_reconstruction_depth_rejects_invalid_bounds(depth):
    with pytest.raises(ValueError):
        fixture_core(effective_depth=depth)


def test_ignored_sequence_heartbeat_does_not_refresh_book_health():
    instrument, key, reducer = fixture_core(
        capabilities=Capabilities(
            deltas=True, exact_event_flow=True, sequence_scope="connection"
        ),
        sequence_validator=monotonic_sequence,
    )
    sequence = SequenceInfo("connection", last="10")
    reducer.apply(
        (envelope(instrument, key, source_mode="ws_delta", sequence=sequence),)
    )
    now = 10_000_000_001
    result = reducer.apply(
        (
            synthetic_envelope(
                instrument,
                Control("heartbeat"),
                key=key,
                ordinal=2,
                mono_ns=now,
                source_mode="ws_delta",
                sequence=sequence,
            ),
        )
    )
    assert result.recovery is None and not result.events
    assert reducer.quality.last_healthy_observation_mono_ns == 1
    assert (
        "stale"
        in evaluate(
            reducer.book, instrument, "comparison", ClockReading("synthetic-clock", now)
        ).reasons
    )


def test_continuous_book_heartbeat_refreshes_health_without_inventing_book_change():
    instrument, key, reducer = fixture_core(
        capabilities=Capabilities(deltas=True, exact_event_flow=True)
    )
    original = reducer.apply((envelope(instrument, key, source_mode="ws_delta"),)).book
    now = 10_000_000_001
    heartbeat = synthetic_envelope(
        instrument,
        Control("heartbeat"),
        key=key,
        ordinal=2,
        mono_ns=now,
        source_mode="ws_delta",
    )
    result = reducer.apply((heartbeat,))
    book = result.book
    assert result.recovery is None and not result.changed
    assert book is not original
    assert book.bids is original.bids and book.asks is original.asks
    assert book.asof == original.asof and book.revision == original.revision
    assert book.quality.last_book_observation_mono_ns == 1
    assert book.quality.last_book_change_mono_ns == 1
    assert book.quality.last_transport_mono_ns == now
    assert book.quality.last_healthy_observation_mono_ns == now
    assert original.quality.last_healthy_observation_mono_ns == 1
    assert evaluate(
        book, instrument, "comparison", ClockReading("synthetic-clock", now)
    ).eligible
    assert evaluate(
        book, instrument, "event_ofi", ClockReading("synthetic-clock", now)
    ).eligible


@pytest.mark.parametrize("state", ["bookless", "invalid", "unsynchronized"])
def test_heartbeat_never_repairs_missing_invalid_or_unsynchronized_book(state):
    instrument, key, reducer = fixture_core(
        capabilities=Capabilities(deltas=True, exact_event_flow=True)
    )
    if state != "bookless":
        reducer.apply(
            (envelope(instrument, key, source_mode="ws_delta"),),
            synchronized=state != "unsynchronized",
        )
    if state == "invalid":
        reducer.apply(
            (
                synthetic_envelope(
                    instrument,
                    Gap("lost_continuity"),
                    key=key,
                    ordinal=2,
                    mono_ns=2,
                    source_mode="ws_delta",
                ),
            )
        )
    healthy_before = reducer.quality.last_healthy_observation_mono_ns
    now = 10_000_000_001
    result = reducer.apply(
        (
            synthetic_envelope(
                instrument,
                Control("heartbeat"),
                key=key,
                ordinal=3,
                mono_ns=now,
                source_mode="ws_delta",
            ),
        )
    )
    assert result.book is None and not reducer.synchronized
    assert reducer.quality.last_transport_mono_ns == now
    assert reducer.quality.last_healthy_observation_mono_ns == healthy_before
    if reducer.book is not None:
        assert not evaluate(
            reducer.book, instrument, "comparison", ClockReading("synthetic-clock", now)
        ).eligible


@pytest.mark.parametrize(
    "source_mode,capabilities,control",
    [
        ("ws_snapshot", Capabilities(deltas=True, exact_event_flow=True), "heartbeat"),
        (
            "rest_snapshot",
            Capabilities(deltas=True, exact_event_flow=True),
            "heartbeat",
        ),
        ("ws_delta", Capabilities(deltas=True), "heartbeat"),
        ("ws_delta", Capabilities(), "heartbeat"),
        ("ws_delta", Capabilities(deltas=True, exact_event_flow=True), "ack"),
        ("ws_delta", Capabilities(deltas=True, exact_event_flow=True), "clock_tick"),
    ],
)
def test_noncontinuous_observation_or_nonheartbeat_cannot_refresh_book_health(
    source_mode, capabilities, control
):
    instrument, key, reducer = fixture_core(capabilities=capabilities)
    reducer.apply((envelope(instrument, key, source_mode=source_mode),))
    now = 10_000_000_001
    reducer.apply(
        (
            synthetic_envelope(
                instrument,
                Control(control),
                key=key,
                ordinal=2,
                mono_ns=now,
                source_mode=source_mode,
            ),
        )
    )
    assert reducer.quality.last_healthy_observation_mono_ns == 1
    assert (
        "stale"
        in evaluate(
            reducer.book, instrument, "comparison", ClockReading("synthetic-clock", now)
        ).reasons
    )


def test_quiet_valid_observation_refreshes_health_but_not_change_time():
    instrument, key, reducer, _ = healthy()
    result = reducer.apply((envelope(instrument, key, "delta", ordinal=2, mono_ns=10),))
    assert not result.changed
    assert result.book.quality.last_book_change_mono_ns == 1
    assert result.book.quality.last_healthy_observation_mono_ns == 10
    reducer.apply(
        (
            synthetic_envelope(
                instrument, Control("heartbeat"), key=key, ordinal=3, mono_ns=20
            ),
        )
    )
    assert reducer.quality.last_transport_mono_ns == 20
    assert reducer.quality.last_healthy_observation_mono_ns == 10
    reducer.apply(
        (
            synthetic_envelope(
                instrument, Control("clock_tick"), key=key, ordinal=4, mono_ns=30
            ),
        )
    )
    assert reducer.quality.last_transport_mono_ns == 20
    policy = EligibilityPolicy(max_age_ns=10)
    assert evaluate(
        reducer.book,
        instrument,
        "comparison",
        ClockReading("synthetic-clock", 20),
        policy,
    ).eligible
    assert (
        "stale"
        in evaluate(
            reducer.book,
            instrument,
            "comparison",
            ClockReading("synthetic-clock", 21),
            policy,
        ).reasons
    )
    assert (
        "clock_mismatch"
        in evaluate(
            reducer.book, instrument, "display", ClockReading("different", 20), policy
        ).reasons
    )
    assert (
        "clock_regression"
        in evaluate(
            reducer.book, instrument, "comparison", ClockReading("synthetic-clock", 0)
        ).reasons
    )


def test_snapshot_event_flow_and_execution_access_are_independent_gates():
    instrument, _, _, book = healthy()
    now = ClockReading("synthetic-clock", 1)
    assert evaluate(book, instrument, "comparison", now).eligible
    assert (
        "missing_exact_event_flow"
        in evaluate(book, instrument, "event_ofi", now).reasons
    )
    assert (
        "execution_access_unknown"
        in evaluate(
            book,
            instrument,
            "paper_fill",
            now,
            EligibilityPolicy(require_execution_access=True),
        ).reasons
    )
    snapshot = replace(book, quality=replace(book.quality, source_mode="ws_snapshot"))
    assert not evaluate(
        snapshot,
        instrument,
        "comparison",
        now,
        EligibilityPolicy(allow_snapshot_quotes=False),
    ).eligible


def test_native_identity_not_ticker_drives_cohorts_and_skew():
    first, _, _, book = healthy()
    second = synthetic_instrument(venue="other")
    key = SubscriptionKey.for_instrument(second)
    reducer = BookReducer(second, key, segment_id="synthetic-segment")
    other = reducer.apply((envelope(second, key, mono_ns=10),)).book
    now = ClockReading("synthetic-clock", 10)
    assert evaluate_cohort((book, other), (first, second), now).eligible
    assert (
        "asof_skew"
        in evaluate_cohort(
            (book, other), (first, second), now, EligibilityPolicy(max_asof_skew_ns=1)
        ).reasons
    )
    for changed in (
        replace(second, quote=AssetIdentity("USDT", "USDT")),
        replace(
            second, base=AssetIdentity("wrapped-btc", "BTC", reference_asset_id="BTC")
        ),
        replace(second, market_type="perpetual"),
    ):
        assert (
            "incompatible_instruments"
            in evaluate_cohort((book, other), (first, changed), now).reasons
        )
    assert (
        "duplicate_instrument"
        in evaluate_cohort((book, book), (first, first), now).reasons
    )


def test_gap_invalidates_continuity_and_capture_without_repairing_prices():
    instrument, key, reducer, old = healthy()
    gap = synthetic_envelope(
        instrument, Gap("queue_overflow", capture_lost=True), key=key, ordinal=2
    )
    result = reducer.invalidate(gap)
    assert result.book.bids == old.bids
    assert result.book.quality.integrity == "invalid"
    assert not result.book.quality.capture_complete
    assert result.book.quality.continuous_since_ordinal is None
    assert not reducer.synchronized


@pytest.mark.parametrize(
    "payload",
    [
        Trade("9007199254740993", "venue:trade", D("99.0000000001"), D("0.001")),
        ReferencePrice("mark", D("123.456"), "USD"),
        AssetContext(
            mark=D("100"),
            funding_rate=D("-0.001"),
            funding_interval_ns=3600000000000,
            open_interest=D("1000"),
            open_interest_unit="base",
        ),
        Control("heartbeat"),
        Gap("missing", first_missing_ordinal=2**60),
    ],
)
def test_wire_roundtrips_every_payload_with_no_unsafe_numbers(payload):
    instrument = synthetic_instrument()
    event = synthetic_envelope(instrument, payload, ordinal=2**60)
    encoded = dumps(event)
    assert loads(encoded) == event
    value = json.loads(encoded)
    assert value["value"]["ingest_ordinal"] == str(2**60)
    assert b"NaN" not in encoded
    assert to_wire(D("1.2300"), canonical=True) == "1.23"
    with pytest.raises(ValueError):
        loads(encoded.replace(b'"schema_version":1', b'"schema_version":2'))
    with pytest.raises(ValueError):
        dumps(1.25)


def test_wire_rejects_unknown_fields_numeric_decimal_and_fake_types():
    value = json.loads(dumps(Level(D("1"), D("2"))))
    value["value"]["price"] = 1
    with pytest.raises(ValueError):
        loads(json.dumps(value))
    value["value"]["price"] = "1"
    value["value"]["unexpected"] = "x"
    with pytest.raises(ValueError):
        loads(json.dumps(value))
    with pytest.raises(ValueError):
        loads('{"schema_version":1,"value":{"$type":"os.system"}}')


def test_checkpoint_restores_exact_lexical_maps_protocol_buffer_and_next_result():
    instrument, key, reducer = fixture_core(sequence_validator=monotonic_sequence)
    reducer.apply((envelope(instrument, key, sequence=SequenceInfo("u", last="10")),))
    pending = envelope(
        instrument, key, "delta", ordinal=2, sequence=SequenceInfo("u", last="20")
    )
    reducer.buffered_events = (pending,)
    reducer.protocol_state = b'{"bridge":"10","lexical":"1.000"}'
    checkpoint = loads(dumps(reducer.checkpoint()))
    restored = BookReducer(
        instrument,
        key,
        segment_id="synthetic-segment",
        sequence_validator=monotonic_sequence,
    )
    restored.restore(checkpoint)
    assert str(restored.book.bids[0].price) == "99.00"
    assert str(restored.book.bids[0].quantity) == "1.000"
    assert restored.buffered_events == (pending,)
    assert restored.protocol_state == reducer.protocol_state
    assert restored.apply((pending,)) == reducer.apply((pending,))
    assert restored.checkpoint() == reducer.checkpoint()
    before = restored.checkpoint()
    with pytest.raises(ValueError):
        restored.restore(replace(checkpoint, config_hash="other"))
    assert restored.checkpoint() == before
    with pytest.raises(ValueError):
        replace(checkpoint, reducer_state=b"corrupted")


def test_native_candidate_transfer_matches_checkpoint_restore_and_future_results():
    instrument, key, candidate = fixture_core(sequence_validator=monotonic_sequence)
    old_book = candidate.apply(
        (envelope(instrument, key, sequence=SequenceInfo("u", last="10")),)
    ).book
    assert old_book is not None
    pending = envelope(
        instrument,
        key,
        "delta",
        changes=(LevelChange("bid", D("99.000"), D("3.0000")),),
        ordinal=2,
        sequence=SequenceInfo("u", last="20"),
    )
    candidate.buffered_events = (pending,)
    candidate.protocol_state = b'{"bridge":"10","lexical":"1.000"}'
    checkpoint = candidate.checkpoint()
    old_bytes = dumps(old_book, canonical=False)
    _, _, adopted = fixture_core(sequence_validator=monotonic_sequence)
    _, _, restored = fixture_core(sequence_validator=monotonic_sequence)
    adopted._adopt_candidate(candidate)
    restored.restore(loads(dumps(checkpoint)))
    assert adopted.checkpoint() == restored.checkpoint() == checkpoint
    assert str(adopted.book.bids[0].price) == "99.00"
    assert str(adopted.book.bids[0].quantity) == "1.000"
    candidate_result = candidate.apply((pending,))
    assert adopted.checkpoint() == checkpoint
    assert adopted.apply((pending,)) == restored.apply((pending,)) == candidate_result
    assert adopted.checkpoint() == restored.checkpoint() == candidate.checkpoint()
    assert str(adopted.book.bids[0].price) == "99.000"
    assert str(adopted.book.bids[0].quantity) == "3.0000"
    candidate.reset(1, "next-segment")
    assert adopted.checkpoint() == restored.checkpoint()
    assert dumps(old_book, canonical=False) == old_bytes


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("config_hash", "different"),
        ("adapter_revision", "different"),
        ("max_levels", 1),
        ("_last_order", (999, 0)),
    ],
)
def test_native_candidate_transfer_rejects_incompatible_state_atomically(
    attribute, value
):
    _, _, candidate, _ = healthy()
    _, _, target, _ = healthy()
    before = target.checkpoint()
    setattr(candidate, attribute, value)
    with pytest.raises(ValueError):
        target._adopt_candidate(candidate)
    assert target.checkpoint() == before


def test_checkpoint_before_initialization_and_after_invalidation_roundtrip():
    instrument, key, reducer = fixture_core()
    empty = reducer.checkpoint()
    restored = BookReducer(instrument, key, segment_id="synthetic-segment")
    restored.restore(loads(dumps(empty)))
    assert restored.checkpoint() == empty
    reducer.apply((envelope(instrument, key),))
    reducer.invalidate(synthetic_envelope(instrument, Gap("lost"), key=key, ordinal=2))
    restored.restore(loads(dumps(reducer.checkpoint())))
    assert restored.quality.integrity == "invalid"
    assert restored.checkpoint() == reducer.checkpoint()


def test_market_data_bound_accepts_realistic_full_depth_size_and_rejects_overflow():
    from src.workstation.precision import MAX_MARKET_DATA_BYTES

    payload = b'{"padding":"' + b"x" * (5 * 1024 * 1024) + b'"}'
    assert len(exact_json_loads(payload)["padding"]) == 5 * 1024 * 1024
    frame = synthetic_frame(payload)
    assert frame.payload == payload
    oversized = b" " * (MAX_MARKET_DATA_BYTES + 1)
    with pytest.raises(ValueError, match="size"):
        exact_json_loads(oversized)
    with pytest.raises(ValueError, match="size"):
        synthetic_frame(oversized)


def test_adapter_wrapping_preserves_control_sequence_and_rejects_partial_decode():
    from src.workstation.adapters.base import (
        DecodedEvent,
        DecodeError,
        DecodeResult,
        TransportRequest,
        wrap_decoded,
    )

    instrument, key, reducer = fixture_core(sequence_validator=contiguous_sequence)
    frame = synthetic_frame(ordinal=9007199254740993)
    decoded = DecodeResult(
        events=(
            DecodedEvent(Control("ack"), sequence=SequenceInfo("connection", last="0")),
            DecodedEvent(
                envelope(instrument, key).payload,
                sequence=SequenceInfo("connection", last="1"),
            ),
        )
    )
    events = wrap_decoded(
        frame, instrument, key, decoded, "synthetic-segment", "ws_delta"
    )
    assert tuple(event.event_index for event in events) == (0, 1)
    assert all(event.ingest_ordinal == frame.ingest_ordinal for event in events)
    assert events[0].sequence.last == "0"
    assert events[0].exchange_event_wall_ns is None
    assert reducer.apply(events).book.quality.integrity == "valid"
    error = DecodeError("schema_error", frame.payload_sha256, "bad payload")
    with pytest.raises(ValueError, match="partial"):
        DecodeResult(events=decoded.events, error=error)
    with pytest.raises(ValueError, match="decode failed"):
        wrap_decoded(
            frame,
            instrument,
            key,
            DecodeResult(error=error),
            "synthetic-segment",
            "ws_delta",
        )
    assert TransportRequest("WS", "wss://public.example", b"{}").method == "WS"
    with pytest.raises(ValueError):
        TransportRequest(
            "POST", "https://public.example", headers=(("Authorization", "secret"),)
        )


def test_validated_observation_resolves_readability_not_execution_permission():
    instrument, key, reducer, book = healthy()
    instrument = replace(instrument, data_access="unknown")
    now = ClockReading("synthetic-clock", 1)
    assert evaluate(book, instrument, "comparison", now).eligible
    assert instrument.data_access == "unknown"
    execution = evaluate(
        book,
        instrument,
        "paper_fill",
        now,
        EligibilityPolicy(require_execution_access=True),
    )
    assert not execution.eligible
    assert "execution_access_unknown" in execution.reasons
    stale = evaluate(
        book, instrument, "comparison", ClockReading("synthetic-clock", 10_000_000_001)
    )
    assert not stale.eligible and "stale" in stale.reasons


@pytest.mark.parametrize("access", ["blocked", "unsupported"])
def test_explicit_access_restrictions_survive_observation(access):
    instrument, key, reducer, book = healthy()
    result = evaluate(
        book,
        replace(instrument, data_access=access),
        "comparison",
        ClockReading("synthetic-clock", 1),
    )
    assert not result.eligible
    assert "data_access_" + access in result.reasons


def test_transport_evidence_without_book_observation_does_not_resolve_access():
    instrument, key, reducer, book = healthy()
    result = evaluate(
        replace(
            book,
            quality=replace(book.quality, last_book_observation_mono_ns=None),
        ),
        replace(instrument, data_access="unknown"),
        "comparison",
        ClockReading("synthetic-clock", 1),
    )
    assert not result.eligible and "data_access_unknown" in result.reasons
