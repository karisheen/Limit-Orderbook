"""Consumer-visible charts, DOM, risk and paint acknowledgments over real sources."""

import asyncio
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext

import pytest
from fastapi.testclient import TestClient

from src.workstation.api import create_app
from src.workstation.demo import create_demo
from src.workstation.precision import DECIMAL_CONTEXT
from src.workstation.runtime import TerminalRuntime


FEES = {
    "fee_bps": "10",
    "fee_provenance": "explicit synthetic scenario assumption",
    "fee_date": "2024-12-31",
}
PROPOSAL = {
    "side": "buy",
    "entry_price": "60000",
    "stop_price": "59000",
    "base_quantity": "0.1",
}


@pytest.fixture(scope="module", params=("demo", "replay"))
def visual_client(request, tmp_path_factory):
    captures = ()
    if request.param == "replay":
        root = tmp_path_factory.mktemp("visual-api-recording")

        async def capture():
            with await create_demo(root, duration_seconds=10) as result:
                return result.reader.path

        captures = (asyncio.run(capture()),)
    runtime = TerminalRuntime(captures=captures, max_sessions=2)
    with TestClient(create_app(runtime), base_url="http://127.0.0.1") as client:
        # The actual bootstrap task verifies managed captures after demo startup.
        bootstrap = successful(client.post("/api/v1/sessions", json={}), 201)
        client.delete("/api/v1/sessions/" + bootstrap["session_id"])

        async def catalog_ready():
            await runtime.demo_task

        client.portal.call(catalog_ready)
        sources = client.get("/api/v1/instruments").json()["sources"]
        source = next(item for item in sources if item["mode"] == request.param)
        assert source["status"] == "ready"
        yield client, source


@pytest.fixture
def market(visual_client):
    client, source = visual_client
    frame = create_session(client, source, "BTC")
    frame = replay(client, frame, "step", count=120)
    yield client, source, frame
    for session in client.get("/api/v1/sessions").json()["sessions"]:
        client.delete("/api/v1/sessions/" + session["id"])


def successful(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def identity(frame):
    return {key: frame[key] for key in ("session_id", "generation")}


def post(client, frame, endpoint, **options):
    return client.post("/api/v1/" + endpoint, json={**identity(frame), **options})


def instrument(client, source, ticker):
    catalog = successful(
        client.get("/api/v1/instruments", params={"source_id": source["id"]})
    )
    return next(
        item
        for item in catalog["instruments"]
        if item["venue"] == "coinbase" and item["base"]["ticker"] == ticker
    )


def create_session(client, source, ticker):
    frame = successful(
        client.post(
            "/api/v1/sessions",
            json={"mode": source["mode"], "source_id": source["id"]},
        ),
        201,
    )
    return select(client, frame, instrument(client, source, ticker)["instrument_id"])


def select(client, frame, instrument_id):
    return successful(post(client, frame, "subscriptions", instrument_id=instrument_id))


def replay(client, frame, action, **options):
    return successful(post(client, frame, "replay", action=action, **options))


def visual(client, frame, **options):
    return successful(post(client, frame, "visualization", **options))


def current(client, frame):
    return successful(client.get("/api/v1/sessions/" + frame["session_id"]))


def ack(client, frame, view_id, duration=1.25):
    return post(
        client,
        frame,
        "visualization/ack",
        view_id=view_id,
        browser_receive_to_paint_ms=duration,
    )


def economic_view(view):
    # Publications/generations are intentionally new; reconstructed source evidence is not.
    return {
        key: view[key]
        for key in (
            "instrument_id",
            "clock_id",
            "prices",
            "price_step",
            "bucket_ns",
            "start_ns",
            "end_ns",
            "now_ns",
            "retained_start_ns",
            "columns",
            "trades",
            "profile",
            "footprints",
            "reasons",
            "complete_window",
        )
    }


def test_chart_links_real_liquidity_trades_candles_profile_and_footprints(market):
    client, source, frame = market
    before = current(client, frame)["replay"]["cursor"]
    view = visual(client, frame, price_step="1", rows=32)
    assert frame["source"]["recording"] is (source["mode"] == "replay")
    assert view["instrument_id"] == frame["selection"]["instrument_id"]
    assert view["quantity_unit"] == "BTC"
    assert len(view["columns"]) >= 3
    assert len(view["prices"]) == 32
    assert all(
        Decimal(right) - Decimal(left) == 1
        for left, right in zip(view["prices"], view["prices"][1:])
    )
    assert any(
        value is not None and Decimal(value) > 0
        for column in view["columns"]
        for cell in column["cells"]
        for value in cell
    )
    assert all(len(column["cells"]) == 32 for column in view["columns"])
    assert any(column["best_bid"] == "59999" for column in view["columns"])
    assert any(column["best_ask"] == "60001" for column in view["columns"])
    prints = [trade for trade in view["trades"] if not trade["is_subscription_history"]]
    assert prints
    assert len({trade["id"] for trade in prints}) == len(prints)
    assert all(trade["price"] == "60000" for trade in prints)
    assert all(int(trade["time_ns"]) <= int(view["now_ns"]) for trade in prints)
    assert all(isinstance(trade["asof"]["ingest_ordinal"], str) for trade in prints)
    expected_profile = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    expected_footprints = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    bucket = int(view["bucket_ns"])
    for trade in prints:
        price = Decimal(trade["price"])
        side = {"buy": 0, "sell": 1}.get(trade["taker_side"], 2)
        quantity = Decimal(trade["base_quantity"])
        expected_profile[price][side] += quantity
        stamp = int(trade["time_ns"]) // bucket * bucket
        expected_footprints[stamp, price][side] += quantity
    amounts = ("buy_base", "sell_base", "unknown_base")
    assert {
        Decimal(row["price"]): [Decimal(row[key]) for key in amounts]
        for row in view["profile"]
    } == dict(expected_profile)
    assert {
        (int(row["time_ns"]), Decimal(row["price"])): [
            Decimal(row[key]) for key in amounts
        ]
        for row in view["footprints"]
    } == dict(expected_footprints)
    candles = 0
    for column in view["columns"]:
        trades = [
            trade
            for trade in prints
            if int(column["time_ns"]) <= int(trade["time_ns"]) < int(column["end_ns"])
            and trade["segment_id"] == column["segment_id"]
        ]
        if not trades:
            assert column["candle"] is None
            continue
        candles += 1
        candle = column["candle"]
        prices = [Decimal(trade["price"]) for trade in trades]
        assert Decimal(candle["open"]) == prices[0]
        assert Decimal(candle["close"]) == prices[-1]
        assert Decimal(candle["high"]) == max(prices)
        assert Decimal(candle["low"]) == min(prices)
        assert Decimal(candle["volume"]) == sum(
            Decimal(trade["base_quantity"]) for trade in trades
        )
    assert candles >= 2
    assert current(client, frame)["replay"]["cursor"] == before


def test_grouped_ladder_conserves_native_depth_and_side_cumulative_totals(market):
    client, _, frame = market
    before = current(client, frame)
    ladder = successful(post(client, frame, "ladder", price_step="1", rows=100))
    assert ladder["eligible"] is True
    assert ladder["asof"] == before["book"]["asof"]
    assert ladder["instrument_id"] == before["book"]["instrument_id"]
    assert ladder["unit"] == "BTC"
    assert ladder["quote_unit"] == "USD"
    assert ladder["step"] in ladder["step_options"]
    assert ladder["source_mode"] is not None
    assert ladder["coverage"]["retained_bid_levels"] == str(len(before["book"]["bids"]))
    assert ladder["coverage"]["retained_ask_levels"] == str(len(before["book"]["asks"]))
    summary = ladder["summary"]
    assert summary["best_bid"] == before["book"]["bids"][0]["price"]
    assert summary["best_ask"] == before["book"]["asks"][0]["price"]
    with localcontext(DECIMAL_CONTEXT):
        best_bid, best_ask = Decimal(summary["best_bid"]), Decimal(summary["best_ask"])
        midpoint = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        assert Decimal(summary["midpoint"]) == midpoint
        assert Decimal(summary["spread"]) == spread
        assert Decimal(summary["spread_ticks"]) == spread / Decimal(ladder["tick_size"])
        assert Decimal(summary["spread_bps"]) == spread / midpoint * 10000
    for side, native_side, rounding in (
        ("bid", "bids", ROUND_FLOOR),
        ("ask", "asks", ROUND_CEILING),
    ):
        expected = defaultdict(Decimal)
        expected_quote = defaultdict(Decimal)
        for level in before["book"][native_side]:
            price = Decimal(level["price"]).to_integral_value(rounding=rounding)
            expected[price] += Decimal(level["quantity"])
            expected_quote[price] += Decimal(level["price"]) * Decimal(
                level["quantity"]
            )
        rows = [row for row in ladder["rows"] if row["side"] == side]
        assert [Decimal(row["price"]) for row in rows] == sorted(
            expected, reverse=side == "bid"
        )
        cumulative = Decimal(0)
        cumulative_quote = Decimal(0)
        for row in rows:
            quantity = expected[Decimal(row["price"])]
            cumulative += quantity
            quote = expected_quote[Decimal(row["price"])]
            cumulative_quote += quote
            assert Decimal(row["base_quantity"]) == quantity
            assert Decimal(row["cumulative_base"]) == cumulative
            assert Decimal(row["quote_notional"]) == quote
            assert Decimal(row["cumulative_quote"]) == cumulative_quote
    bids = [Decimal(row["price"]) for row in ladder["rows"] if row["side"] == "bid"]
    asks = [Decimal(row["price"]) for row in ladder["rows"] if row["side"] == "ask"]
    assert max(bids) < min(asks)
    assert current(client, frame)["replay"]["cursor"] == before["replay"]["cursor"]
    assert current(client, frame)["book"] == before["book"]


def test_proposed_trade_risk_uses_exact_two_leg_fees_and_conservative_lots(market):
    client, _, frame = market
    before = current(client, frame)["replay"]["cursor"]
    risk = successful(post(client, frame, "proposal-risk", **PROPOSAL, **FEES))
    assert risk["eligible"] is True
    assert risk["quote_asset_id"] == "USD"
    assert risk["gross_loss"] == "100"
    assert risk["fee_cost"] == "11.9"
    assert risk["estimated_loss"] == "111.9"
    assert "stop_liquidity_unknown" in risk["assumptions"]
    assert "not_a_guaranteed_loss_bound" in risk["assumptions"]
    assert "gaps_and_slippage_can_exceed_estimate" in risk["assumptions"]
    sized = successful(
        post(
            client,
            frame,
            "proposal-risk",
            side="buy",
            entry_price="60000",
            stop_price="59000",
            risk_budget="100",
            **FEES,
        )
    )
    assert sized["eligible"] is True
    assert sized["suggested_base_quantity"] == "0.089"
    assert sized["base_quantity"] == "0.089"
    assert sized["estimated_loss"] == "99.591"
    assert Decimal(sized["estimated_loss"]) <= 100
    assert (Decimal(sized["base_quantity"]) + Decimal("0.001")) * 1119 > 100
    unknown = successful(post(client, frame, "proposal-risk", **PROPOSAL))
    assert unknown["eligible"] is False
    assert "unknown_fees" in unknown["reasons"]
    assert unknown["gross_loss"] == "100"
    assert unknown["fee_cost"] is None and unknown["estimated_loss"] is None
    unsized = successful(
        post(
            client,
            frame,
            "proposal-risk",
            side="buy",
            entry_price="60000",
            stop_price="59000",
            risk_budget="100",
        )
    )
    assert unsized["eligible"] is False
    assert unsized["suggested_base_quantity"] is None
    assert unsized["estimated_loss"] is None
    invalid = successful(
        post(
            client,
            frame,
            "proposal-risk",
            **{**PROPOSAL, "stop_price": "61000"},
            **FEES,
        )
    )
    assert invalid["eligible"] is False
    assert "stop_must_be_below_buy_or_above_sell_entry" in invalid["reasons"]
    assert invalid["estimated_loss"] is None
    off_lot = successful(
        post(
            client,
            frame,
            "proposal-risk",
            **{**PROPOSAL, "base_quantity": "0.0001"},
            **FEES,
        )
    )
    assert off_lot["eligible"] is False
    assert "quantity_off_native_lot" in off_lot["reasons"]
    assert current(client, frame)["replay"]["cursor"] == before


def test_chart_incremental_poll_bounds_and_closed_liquidity_stay_stable(market):
    client, _, frame = market
    before = current(client, frame)["replay"]["cursor"]
    view = visual(client, frame, price_step="1", rows=32)
    repeated = visual(client, frame, rows=32, after_column=view["next_after"])
    assert repeated["replace"] is False
    assert repeated["columns"] == []
    assert repeated["trades"] == view["trades"]
    bounded = visual(client, frame, rows=32, max_columns=2, end_ns="999999999999999999")
    assert len(bounded["columns"]) == 2
    assert bounded["end_ns"] == bounded["now_ns"]
    assert bounded["truncated"] is True
    assert bounded["complete_window"] is False
    assert "column_output_limit" in bounded["reasons"]
    panned = visual(client, frame, rows=32, price_offset_rows=3)
    assert panned["grid_id"] == view["grid_id"]
    assert Decimal(panned["price_min"]) == Decimal(view["price_min"]) + 3
    assert current(client, frame)["replay"]["cursor"] == before
    advanced = replay(client, frame, "step", count=48)
    later = visual(client, advanced, rows=32)
    previous = {column["id"]: column for column in view["columns"]}
    shared = [column for column in later["columns"] if column["id"] in previous]
    assert shared
    for column in shared:
        old = previous[column["id"]]
        assert column["cells"] == old["cells"]
        assert column["liquidity_hash"] == old["liquidity_hash"]


def test_independent_pinned_panes_keep_exact_projection_after_peer_recenter(market):
    client, _, frame = market
    before = current(client, frame)
    fine_options = {"price_min": "59990", "price_step": "1", "rows": 32}
    coarse_options = {"price_min": "59900", "price_step": "5", "rows": 64}
    fine = visual(client, frame, **fine_options)
    coarse = visual(client, frame, **coarse_options)
    assert fine["price_min"] == "59990"
    assert coarse["price_min"] == "59900"
    assert any(
        quantity not in (None, "0")
        for view in (fine, coarse)
        for column in view["columns"]
        for cell in column["cells"]
        for quantity in cell
    )
    visual(client, frame, recenter=True, price_step="10", rows=128)
    for options, expected in (
        (fine_options, fine),
        (coarse_options, coarse),
        (fine_options, fine),
        (coarse_options, coarse),
    ):
        actual = visual(client, frame, **options)
        assert economic_view(actual) == economic_view(expected)
        assert actual["grid_id"] == expected["grid_id"]
    panned = visual(client, frame, **fine_options, price_offset_rows=-3)
    assert panned["price_min"] == "59987"
    assert panned["grid_id"] == fine["grid_id"]
    assert economic_view(visual(client, frame, **fine_options)) == economic_view(fine)
    for offset, expected in ((3, "0.55"), (-3, "0.25"), (-8, "0"), (-9, "0")):
        small = visual(
            client,
            frame,
            price_min="0.4",
            price_step="0.05",
            price_offset_rows=offset,
            rows=32,
        )
        assert small["price_min"] == expected
        assert Decimal(small["prices"][-1]) == Decimal(expected) + Decimal("1.55")
    after = current(client, frame)
    assert after["replay"]["cursor"] == before["replay"]["cursor"]
    assert after["book"] == before["book"]


@pytest.mark.parametrize("action", ("reset", "seek"))
def test_pinned_origin_is_request_scoped_across_generation_reset(market, action):
    client, _, frame = market
    options = {"price_min": "59990", "price_step": "1", "rows": 32}
    original = visual(client, frame, **options)
    target = frame["replay"]["cursor"]["delivery_index"]
    changed = replay(
        client, frame, action, **({"delivery_index": "0"} if action == "seek" else {})
    )
    assert changed["generation"] != frame["generation"]
    stale = post(client, frame, "visualization", **options)
    assert stale.status_code == 409, stale.text
    fresh = visual(client, changed, **options, after_column=original["next_after"])
    assert fresh["generation"] == changed["generation"]
    assert fresh["replace"] is True
    assert fresh["prices"] == original["prices"]
    assert fresh["columns"] == fresh["trades"] == fresh["profile"] == []
    assert visual(client, changed, price_step="1", rows=32)["price_min"] == "0"
    restored = replay(client, changed, "seek", delivery_index=target)
    assert economic_view(visual(client, restored, **options)) == economic_view(original)


@pytest.mark.parametrize(
    "options",
    (
        {"price_min": 60000, "price_step": "1"},
        {"price_min": 60000.0, "price_step": "1"},
        {"price_min": True, "price_step": "1"},
        {"price_min": "", "price_step": "1"},
        {"price_min": "-1", "price_step": "1"},
        {"price_min": "NaN", "price_step": "1"},
        {"price_min": "Infinity", "price_step": "1"},
        {"price_min": " 60000", "price_step": "1"},
        {"price_min": "1e39", "price_step": "1"},
        {"price_min": "0" * 129, "price_step": "1"},
        {"price_min": "60000.005", "price_step": "0.01"},
        {"price_min": "60000", "price_step": "0.03"},
        {"price_min": "60000"},
        {"price_min": "60000", "price_step": None},
        {"price_min": "60000", "price_step": "1", "recenter": True},
    ),
)
def test_pinned_origin_validation_preserves_legacy_state(market, options):
    client, _, frame = market
    original = visual(client, frame, price_step="1", rows=32)
    before = current(client, frame)["replay"]["cursor"]
    response = post(client, frame, "visualization", **options)
    assert response.status_code == 422, response.text
    assert economic_view(visual(client, frame, rows=32)) == economic_view(original)
    assert current(client, frame)["replay"]["cursor"] == before


@pytest.mark.parametrize("transition", ("seek", "selection"))
def test_generation_fences_every_visual_request_and_obsoletes_publications(
    market, transition
):
    client, source, frame = market
    old = visual(client, frame, rows=32)
    if transition == "seek":
        changed = replay(client, frame, "seek", delivery_index="0")
    else:
        changed = select(
            client, frame, instrument(client, source, "ETH")["instrument_id"]
        )
    assert changed["generation"] != frame["generation"]
    for endpoint, payload in (
        ("visualization", {}),
        ("ladder", {}),
        ("proposal-risk", PROPOSAL),
        (
            "visualization/ack",
            {"view_id": old["view_id"], "browser_receive_to_paint_ms": 1},
        ),
    ):
        response = post(client, frame, endpoint, **payload)
        assert response.status_code == 409, response.text
    assert ack(client, changed, old["view_id"]).status_code == 409
    fresh = visual(client, changed, rows=32, after_column=old["next_after"])
    assert fresh["replace"] is True
    assert fresh["generation"] == changed["generation"]
    if transition == "seek":
        assert fresh["columns"] == []
        assert fresh["trades"] == []
        assert fresh["profile"] == []
        assert fresh["footprints"] == []
        assert fresh["complete_window"] is False
        empty_ladder = successful(post(client, changed, "ladder"))
        assert empty_ladder["eligible"] is False
        assert empty_ladder["rows"] == []
    else:
        assert fresh["instrument_id"] != old["instrument_id"]
        assert fresh["trades"]
        assert all(trade["price"] == "3000" for trade in fresh["trades"])


def test_backward_seek_reconstructs_identical_chart_without_future_prints(market):
    client, _, frame = market
    original = visual(client, frame, price_step="1", rows=32)
    target = frame["replay"]["cursor"]["delivery_index"]
    advanced = replay(client, frame, "step", count=48)
    future = visual(client, advanced, price_step="1", rows=32)
    assert int(future["now_ns"]) > int(original["now_ns"])
    restored = replay(client, advanced, "seek", delivery_index=target)
    rebuilt = visual(client, restored, price_step="1", rows=32)
    assert economic_view(rebuilt) == economic_view(original)
    assert all(
        int(trade["time_ns"]) <= int(original["now_ns"]) for trade in rebuilt["trades"]
    )
    assert ack(client, restored, future["view_id"]).status_code == 409


def test_replay_selection_rebuilds_prior_history_like_selected_from_start(market):
    client, source, frame = market
    early = create_session(client, source, "ETH")
    early = replay(
        client,
        early,
        "seek",
        delivery_index=frame["replay"]["cursor"]["delivery_index"],
    )
    expected = visual(client, early, price_step="1", rows=32)
    selected = select(client, frame, instrument(client, source, "ETH")["instrument_id"])
    assert selected["replay"]["cursor"] == frame["replay"]["cursor"]
    actual = visual(client, selected, price_step="1", rows=32)
    assert len(actual["columns"]) >= 3
    assert len({trade["time_ns"] for trade in actual["trades"]}) >= 3
    assert any(
        int(column["time_ns"]) < int(actual["now_ns"]) - 1_000_000_000
        for column in actual["columns"]
    )
    assert economic_view(actual) == economic_view(expected)


def test_publication_ack_is_single_use_session_scoped_and_bounded(market):
    client, source, frame = market
    before = current(client, frame)["replay"]["cursor"]
    assert ack(client, frame, "0" * 32).status_code == 409
    publications = [visual(client, frame, rows=32, max_columns=1) for _ in range(65)]
    assert len({view["view_id"] for view in publications}) == 65
    assert all(isinstance(view["published_mono_ns"], str) for view in publications)
    assert all(
        int(left["published_mono_ns"]) <= int(right["published_mono_ns"])
        for left, right in zip(publications, publications[1:])
    )
    assert ack(client, frame, publications[0]["view_id"]).status_code == 409
    oldest_retained = successful(ack(client, frame, publications[1]["view_id"]))
    assert oldest_retained["browser_receive_to_paint_ms"] == 1.25
    assert 0 <= oldest_retained["publish_to_ack_upper_bound_ms"] < 60000
    assert ack(client, frame, publications[1]["view_id"]).status_code == 409
    other = create_session(client, source, "BTC")
    newest = publications[-1]["view_id"]
    assert ack(client, other, newest).status_code == 409
    for duration in (-1, 60001):
        assert ack(client, frame, newest, duration).status_code == 422
    accepted = successful(ack(client, frame, newest, 0))
    assert accepted["browser_receive_to_paint_ms"] == 0
    assert accepted["publish_to_ack_upper_bound_ms"] >= 0
    assert ack(client, frame, newest).status_code == 409
    assert current(client, frame)["replay"]["cursor"] == before
    assert successful(client.delete("/api/v1/sessions/" + frame["session_id"])) == {
        "closed": True
    }
    assert ack(client, frame, publications[2]["view_id"]).status_code == 404


@pytest.mark.parametrize(
    "endpoint,payload",
    (
        ("visualization", {"price_step": "0"}),
        ("visualization", {"price_step": "0.03"}),
        ("visualization", {"bucket_ns": "2000000000"}),
        ("visualization", {"rows": 31}),
        ("visualization", {"max_columns": 601}),
        ("visualization", {"start_ns": "999999999999999999"}),
        ("ladder", {"rows": 101}),
        ("ladder", {"price_step": "NaN"}),
        ("proposal-risk", {**PROPOSAL, "risk_budget": "100"}),
        ("proposal-risk", {**PROPOSAL, "fee_bps": "0"}),
        ("proposal-risk", {**PROPOSAL, "entry_price": "Infinity"}),
    ),
)
def test_invalid_visual_inputs_are_rejected_without_cursor_movement(
    market, endpoint, payload
):
    client, _, frame = market
    before = current(client, frame)["replay"]["cursor"]
    response = post(client, frame, endpoint, **payload)
    assert response.status_code == 422, response.text
    assert current(client, frame)["replay"]["cursor"] == before
