"""Presentation only: native API evidence, never a second analytics engine."""

from __future__ import annotations

import json
import math
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from src.ui.api_client import exact_text, instrument_label


def reasons(values: list[str], *, label: str = "Limitations") -> None:
    if values:
        st.warning(f"{label}: " + "; ".join(values))


def exact_table(rows: list[dict[str, Any]]) -> None:
    """Keep Decimal and ordinal strings out of numeric dataframe inference."""
    if not rows:
        st.info("No evidence available in this window.")
        return
    st.dataframe(
        [
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else exact_text(value)
                for key, value in row.items()
            }
            for row in rows
        ],
        hide_index=True,
        width="stretch",
    )


def render_identity(frame: dict[str, Any], instrument: dict[str, Any]) -> None:
    source = frame["source"]
    st.subheader(instrument_label(instrument))
    st.caption(
        f"{source['label']} · mode={source['mode']} · synthetic={source['synthetic']} · "
        f"recorded source={source['recording']} · status={frame['status']}"
    )
    st.caption(
        f"Session {frame['session_id']} · generation {frame['generation']} · Frame {frame['frame_id']}"
    )
    reasons(source.get("reasons", []), label="Source")
    if not frame["health"]["ready"]:
        st.warning(
            "Market evidence is not ready; diagnostic data below is not execution-eligible."
        )
    reasons(frame["health"]["reasons"], label="Health")
    if frame.get("error"):
        st.error(frame["error"])
    presentation = (frame.get("book") or {}).get("presentation")
    if presentation and (presentation["bids_omitted"] or presentation["asks_omitted"]):
        st.caption(
            f"Frame presentation: first {presentation['depth_limit']} levels per side; "
            f"{presentation['bids_omitted']} bids / {presentation['asks_omitted']} asks omitted. "
            "Native reconstruction, grouped ladder and costs still use full retained depth."
        )
    with st.expander("Exact native instrument, source and quality"):
        st.json(
            {
                "instrument": instrument,
                "source": source,
                "quality": (frame.get("book") or {}).get("quality"),
                "asof": (frame.get("book") or {}).get("asof"),
                "presentation": presentation,
            },
            expanded=False,
        )
    with st.expander("Server processing telemetry (not browser paint latency)"):
        st.json(frame["telemetry"], expanded=False)
        st.caption(
            "Python HTTP receipt does not measure browser paint. This client sends no paint acknowledgements."
        )


def render_analytics(analytics: dict[str, Any] | None) -> None:
    st.subheader("Native metrics and public tape")
    if analytics is None:
        st.info("Analytics unavailable for this selection / cursor.")
        return
    reasons(analytics.get("reasons", []), label="Analytics")
    presentation = analytics["presentation"]
    if presentation["profile_omitted"] or presentation["evidence_omitted"]:
        st.caption(
            f"Bounded presentation: {presentation['profile_omitted']} profile rows and "
            f"{presentation['evidence_omitted']} evidence records omitted. "
            "Metrics still use the full eligible native window."
        )
    exact_table(
        [
            {
                "Metric": metric["name"],
                "Value": metric["value"],
                "Unit": metric["unit"],
                "Eligible": metric["eligibility"]["eligible"],
                "Reasons": metric["eligibility"]["reasons"],
                "Formula": metric["formula_version"],
                "Window": metric["window"],
            }
            for metric in analytics["metrics"]
        ]
    )
    tape = analytics["tape"]
    st.caption(
        "Native public prints; subscription history is labeled, not counted as observed live flow."
    )
    exact_table(
        [
            {
                "Price": item["trade"]["price"],
                "Native quantity": item["trade"]["quantity"],
                "Unit": item["trade"]["quantity_unit"],
                "Base quantity": item["base_quantity"],
                "Quote notional": item["quote_notional"],
                "Aggressor": item["trade"]["taker_side"],
                "Side provenance": item["trade"]["side_provenance"],
                "Subscription history": item["trade"]["is_subscription_history"],
                "Trade ID": item["trade"]["trade_id"],
                "Stream": item["stream_id"],
                "Receive monotonic ns": item["asof"]["receive_mono_ns"],
            }
            for item in tape
        ]
    )
    with st.expander("Exact analytics evidence, references and provenance"):
        st.json(analytics, expanded=False)


def render_ladder(ladder: dict[str, Any]) -> None:
    st.subheader("Grouped native ladder and cumulative depth")
    st.caption(
        f"Server quantum {ladder['step']} · unit {ladder['unit']} · eligible={ladder['eligible']}"
    )
    reasons(ladder["reasons"], label="Depth")
    exact_table(ladder["rows"])
    figure = go.Figure()
    for side, color in (("bid", "#0ecb81"), ("ask", "#f6465d")):
        levels = [row for row in ladder["rows"] if row["side"] == side]
        if levels:
            figure.add_trace(
                go.Scatter(
                    x=[row["price"] for row in levels],
                    y=[
                        None
                        if row["cumulative_base"] is None
                        else float(row["cumulative_base"])
                        for row in levels
                    ],
                    customdata=[
                        [row["price"], exact_text(row["cumulative_base"])]
                        for row in levels
                    ],
                    name=side,
                    mode="lines",
                    line={"color": color, "shape": "hv"},
                    hovertemplate="Price %{customdata[0]}<br>Cumulative base %{customdata[1]}<extra>%{fullData.name}</extra>",
                )
            )
    figure.update_layout(
        height=290,
        margin={"l": 20, "r": 20, "t": 20, "b": 30},
        xaxis={
            "type": "category",
            "title": "Native price (displayed levels)",
            "categoryorder": "array",
            "categoryarray": [
                row["price"] for row in reversed(ladder["rows"]) if row["side"] == "bid"
            ]
            + [row["price"] for row in ladder["rows"] if row["side"] == "ask"],
        },
        yaxis_title="Server cumulative base",
    )
    st.plotly_chart(figure, width="stretch", key="legacy_depth")


def render_heatmap(grid: dict[str, Any], side: str, color_ceiling: str) -> None:
    try:
        ceiling = float(color_ceiling)
        if not math.isfinite(ceiling) or ceiling <= 0:
            raise ValueError("nonpositive or nonfinite")
    except ValueError:
        st.error("Heatmap color ceiling must be a positive finite plotting value.")
        return
    st.subheader("Server historical liquidity")
    st.caption(
        f"Source clock {grid['clock_id']} · quantum {grid['price_step']} · sampling {grid['bucket_ns']} ns · "
        f"unit {grid['quantity_unit']} · complete window={grid['complete_window']} · truncated={grid['truncated']}"
    )
    reasons(grid["reasons"], label="History")
    columns = grid["columns"]
    if not columns:
        st.info(
            "History is warming or unavailable. Advance replay to observe closed source-time boundaries."
        )
    else:
        side_index = 0 if side == "bid" else 1
        # Transpose server cells only. Nulls remain gaps; no local bins, sums,
        # normalization, interpolation, or replacement historical observations.
        exact = [
            [column["cells"][row][side_index] for column in columns]
            for row in range(len(grid["prices"]))
        ]
        figure = go.Figure(
            go.Heatmap(
                x=[column["time_ns"] for column in columns],
                y=grid["prices"],
                z=[
                    [None if value is None else float(value) for value in row]
                    for row in exact
                ],
                customdata=[
                    [[price, exact_text(value)] for value in row]
                    for price, row in zip(grid["prices"], exact)
                ],
                colorscale="Greens" if side == "bid" else "Reds",
                zmin=0,
                zmax=ceiling,
                hoverongaps=False,
                hovertemplate="Source ns %{x}<br>Price %{customdata[0]}<br>Base %{customdata[1]}<extra></extra>",
            )
        )
        figure.update_layout(
            height=400,
            margin={"l": 20, "r": 20, "t": 20, "b": 30},
            xaxis={"type": "category", "title": "Source receive-monotonic ns"},
            yaxis={
                "type": "category",
                "title": "Native price (exact grid rows)",
                "categoryorder": "array",
                "categoryarray": grid["prices"],
            },
        )
        st.plotly_chart(figure, width="stretch", key="legacy_heatmap")
        limited = [
            {
                "Source time ns": column["time_ns"],
                "Eligible": column["eligible"],
                "Reasons": column["reasons"],
                "Segment": column["segment_id"],
                "Liquidity hash": column["liquidity_hash"],
            }
            for column in columns
            if column["reasons"] or not column["eligible"]
        ]
        if limited:
            exact_table(limited)
    with st.expander("Exact server grid, flow evidence and retention"):
        st.json(grid, expanded=False)


def render_costs(result: dict[str, Any]) -> None:
    st.subheader("Native size-cost estimate and directed comparison")
    st.caption(
        "Hypothetical depth walk, not execution or guaranteed opportunity. Unknown fees/FX/inventory remain unknown; quotes and market types are not merged."
    )
    reasons(result["reasons"], label="Comparison")
    estimate = result["estimate"]
    if estimate is None:
        st.info("Selected native estimate unavailable.")
    else:
        reasons(estimate["reasons"], label="Estimate")
        exact_table(
            [
                {
                    key: estimate.get(key)
                    for key in (
                        "instrument_id",
                        "venue",
                        "side",
                        "requested_base_quantity",
                        "quote_budget",
                        "filled_base_quantity",
                        "unfilled_base_quantity",
                        "unfilled_quote_budget",
                        "notional_quote",
                        "quote_asset_id",
                        "vwap",
                        "slippage_bps",
                        "fee_amount",
                        "effective_price",
                        "partial",
                        "eligibility",
                        "assumptions",
                    )
                }
            ]
        )
    exact_table(result["matrix"])
    with st.expander("Exact cost assumptions, consumed levels and comparison evidence"):
        st.json(result, expanded=False)
