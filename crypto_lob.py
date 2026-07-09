"""Crypto Limit Order Book — Streamlit UI entrypoint.

All data ingestion lives in src/ (adapters + service). This file only wires
sidebar controls to the OrderBookService singleton and renders components.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from src.domain.orderbook_service import (
    AVAILABLE_EXCHANGES,
    DEFAULT_EXCHANGES,
    OrderBookService,
)
from src.domain.symbols import CANONICAL_ASSETS
from src.ui import components

st.set_page_config(
    page_title="Crypto Limit Order Book",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_service() -> OrderBookService:
    return OrderBookService()


service = get_service()

st.title("Crypto Limit Order Book")
st.caption("Live multi-exchange depth — websocket-first with REST fallback")

# --- sidebar controls ---

with st.sidebar:
    st.header("Settings")
    asset = st.selectbox("Asset", CANONICAL_ASSETS, index=0)
    exchanges = st.multiselect(
        "Exchanges",
        options=list(AVAILABLE_EXCHANGES),
        default=list(DEFAULT_EXCHANGES),
    )
    primary_options = exchanges or list(AVAILABLE_EXCHANGES)
    primary = st.selectbox("Primary venue (ladder & heatmap)", primary_options, index=0)
    ladder_depth = st.select_slider("Ladder depth", options=[10, 15, 20, 25], value=15)
    refresh_seconds = st.slider("UI refresh (seconds)", min_value=1, max_value=10, value=2)
    auto_refresh = st.toggle("Auto-refresh", value=True)

    st.subheader("Panels")
    show_depth_chart = st.toggle("Depth chart", value=True)
    show_spread_history = st.toggle("Spread history", value=True)
    show_heatmap = st.toggle("Depth heatmap", value=False)
    show_comparison = st.toggle("Exchange comparison", value=True)

if not exchanges:
    st.warning("Select at least one exchange in the sidebar.")
    st.stop()

service.set_subscription(asset, exchanges)


def render_dashboard() -> None:
    statuses = service.get_statuses()
    components.render_status_row(statuses, exchanges)
    st.divider()

    snapshot = service.get_snapshot(primary)

    ladder_col, charts_col = st.columns([2, 3])
    with ladder_col:
        st.subheader(f"{primary} · {asset} book")
        if snapshot is not None:
            st.markdown(components.ladder_html(snapshot, ladder_depth), unsafe_allow_html=True)
        else:
            st.info(f"Connecting to {primary}…")

    with charts_col:
        if snapshot is not None:
            components.render_topline(snapshot)
            if show_depth_chart:
                components.render_depth_chart(snapshot)
        else:
            st.info("Waiting for first snapshot…")
        if show_spread_history:
            st.subheader("Spread history")
            components.render_spread_history(service.get_spread_history(primary))

    if show_heatmap:
        st.subheader(f"Depth heatmap · {primary}")
        components.render_heatmap(service.get_depth_history(primary))

    if show_comparison:
        st.subheader("Exchange comparison")
        components.render_comparison(service.get_all_snapshots(), statuses)

    st.caption(f"Rendered at {datetime.now().strftime('%H:%M:%S')}")


if auto_refresh:

    @st.fragment(run_every=refresh_seconds)
    def live_dashboard() -> None:
        render_dashboard()

    live_dashboard()
else:
    if st.button("Refresh now"):
        pass  # the button click itself triggers a rerun
    render_dashboard()
