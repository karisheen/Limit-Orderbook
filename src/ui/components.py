"""Streamlit rendering components for the order book dashboard."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.domain.models import FeedState, FeedStatus, OrderBookSnapshot
from src.domain.orderbook_service import DepthPoint, SpreadPoint

GREEN = "#0ecb81"
RED = "#f6465d"
YELLOW = "#f0b90b"

_STATE_COLORS = {
    FeedState.CONNECTED: GREEN,
    FeedState.DEGRADED: YELLOW,
    FeedState.DISCONNECTED: RED,
}


# --- formatting helpers ---

def fmt_price(price: float) -> str:
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    return f"{price:,.6f}"


def fmt_qty(qty: float) -> str:
    if qty >= 1000:
        return f"{qty:,.1f}"
    if qty >= 1:
        return f"{qty:,.3f}"
    return f"{qty:,.5f}"


def _age_label(status: FeedStatus) -> str:
    age = status.age_seconds()
    if age is None:
        return "never"
    if age < 1.5:
        return "just now"
    return f"{age:.0f}s ago"


# --- status banner ---

def render_status_row(statuses: Dict[str, FeedStatus], exchanges: Sequence[str]) -> None:
    columns = st.columns(max(len(exchanges), 1))
    for column, name in zip(columns, exchanges):
        status = statuses.get(name, FeedStatus())
        color = _STATE_COLORS[status.state]
        source = status.source if status.source != "none" else "—"
        label = (
            f"<span style='color:{color}; font-size:15px'>&#9679;</span> "
            f"<b>{name}</b><br>"
            f"<span style='font-size:12px; opacity:.75'>{status.state.value}"
            f" · {source} · {_age_label(status)}</span>"
        )
        column.markdown(label, unsafe_allow_html=True)
        if status.last_error and status.state != FeedState.CONNECTED:
            column.caption(f":warning: {status.last_error[:90]}")


# --- topline metrics ---

def render_topline(snapshot: OrderBookSnapshot) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Best Bid", f"${fmt_price(snapshot.best_bid)}")
    c2.metric("Best Ask", f"${fmt_price(snapshot.best_ask)}")
    c3.metric("Mid", f"${fmt_price(snapshot.mid_price)}")
    c4.metric("Spread", f"{snapshot.spread_bps:.2f} bps")
    imbalance = snapshot.imbalance(10)
    c5.metric("Bid Imbalance (10)", f"{imbalance * 100:.0f}%")


# --- ladder ---

_ROW_STYLE = (
    "display:flex; justify-content:space-between; padding:1px 8px; "
    "font-variant-numeric: tabular-nums;"
)
_CELL = "width:33%; text-align:right; white-space:nowrap;"


def _ladder_row(price: float, qty: float, cum: float, max_cum: float, color: str, tint: str) -> str:
    pct = 0.0 if max_cum <= 0 else min(cum / max_cum * 100.0, 100.0)
    background = f"background: linear-gradient(to left, {tint} {pct:.1f}%, transparent {pct:.1f}%);"
    return (
        f"<div style='{_ROW_STYLE} {background}'>"
        f"<span style='{_CELL} text-align:left; color:{color}'>{fmt_price(price)}</span>"
        f"<span style='{_CELL}'>{fmt_qty(qty)}</span>"
        f"<span style='{_CELL} opacity:.8'>{fmt_qty(cum)}</span>"
        f"</div>"
    )


def ladder_html(snapshot: OrderBookSnapshot, depth: int = 15) -> str:
    asks = list(snapshot.asks[:depth])
    bids = list(snapshot.bids[:depth])

    ask_rows = []
    cum = 0.0
    for price, qty in asks:
        cum += qty
        ask_rows.append((price, qty, cum))
    bid_rows = []
    cum = 0.0
    for price, qty in bids:
        cum += qty
        bid_rows.append((price, qty, cum))

    max_cum = max(
        ask_rows[-1][2] if ask_rows else 0.0,
        bid_rows[-1][2] if bid_rows else 0.0,
    )

    parts: List[str] = []
    parts.append(
        "<div style='border:1px solid rgba(128,128,128,.25); border-radius:10px; "
        "padding:8px 2px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; "
        "font-size:13px; line-height:1.45;'>"
    )
    parts.append(
        f"<div style='{_ROW_STYLE} opacity:.6; font-size:11px;'>"
        f"<span style='{_CELL} text-align:left'>PRICE</span>"
        f"<span style='{_CELL}'>SIZE</span>"
        f"<span style='{_CELL}'>TOTAL</span></div>"
    )
    # Asks: worst at top, best ask adjacent to the spread row.
    for price, qty, cum in reversed(ask_rows):
        parts.append(_ladder_row(price, qty, cum, max_cum, RED, "rgba(246,70,93,.16)"))
    # Spread row.
    parts.append(
        "<div style='text-align:center; padding:5px 0; margin:3px 0; "
        "border-top:1px solid rgba(128,128,128,.25); border-bottom:1px solid rgba(128,128,128,.25); "
        "font-size:13px;'>"
        f"<b>{fmt_price(snapshot.mid_price)}</b>"
        f"<span style='opacity:.65'> &nbsp;spread {fmt_price(snapshot.spread)} "
        f"({snapshot.spread_bps:.2f} bps)</span></div>"
    )
    for price, qty, cum in bid_rows:
        parts.append(_ladder_row(price, qty, cum, max_cum, GREEN, "rgba(14,203,129,.16)"))
    parts.append("</div>")
    return "".join(parts)


# --- depth chart ---

def render_depth_chart(snapshot: OrderBookSnapshot, levels: int = 50) -> None:
    bids = snapshot.bids[:levels]
    asks = snapshot.asks[:levels]
    bid_prices = [p for p, _ in bids][::-1]
    bid_cum = np.cumsum([q for _, q in bids])[::-1]
    ask_prices = [p for p, _ in asks]
    ask_cum = np.cumsum([q for _, q in asks])

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=bid_prices, y=bid_cum, name="Bids", fill="tozeroy",
            line=dict(color=GREEN, width=1.5), mode="lines",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ask_prices, y=ask_cum, name="Asks", fill="tozeroy",
            line=dict(color=RED, width=1.5), mode="lines",
        )
    )
    fig.add_vline(x=snapshot.mid_price, line_dash="dot", line_color="rgba(200,200,200,.5)")
    fig.update_layout(
        template="plotly_dark",
        height=280,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis_title=None,
        yaxis_title="Cumulative size",
    )
    st.plotly_chart(fig)


# --- spread history ---

def render_spread_history(history: List[SpreadPoint]) -> None:
    if len(history) < 3:
        st.info("Collecting spread history…")
        return
    times = [datetime.fromtimestamp(point[0]) for point in history]
    spread_bps = [point[3] for point in history]
    mids = [point[4] for point in history]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=times, y=spread_bps, name="Spread (bps)", fill="tozeroy",
            line=dict(color=YELLOW, width=1.5), mode="lines",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=times, y=mids, name="Mid price",
            line=dict(color="rgba(160,160,255,.9)", width=1.2), mode="lines",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        template="plotly_dark",
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="bps", secondary_y=False)
    fig.update_yaxes(title_text="mid", secondary_y=True, showgrid=False)
    st.plotly_chart(fig)


# --- depth heatmap ---

def render_heatmap(history: List[DepthPoint], bins: int = 60, top_levels: int = 25) -> None:
    if len(history) < 3:
        st.info("Collecting depth history for the heatmap… leave the app running for a minute.")
        return

    all_prices: List[float] = []
    for _, snapshot in history:
        all_prices.extend(p for p, _ in snapshot.bids[:top_levels])
        all_prices.extend(p for p, _ in snapshot.asks[:top_levels])
    lo, hi = np.percentile(all_prices, [1, 99])
    if hi <= lo:
        st.info("Not enough price dispersion for a heatmap yet.")
        return

    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    z = np.zeros((bins, len(history)))
    times = []
    mids = []
    for j, (ts, snapshot) in enumerate(history):
        times.append(datetime.fromtimestamp(ts))
        mids.append(snapshot.mid_price)
        for price, qty in list(snapshot.bids[:top_levels]) + list(snapshot.asks[:top_levels]):
            idx = int(np.searchsorted(edges, price)) - 1
            if 0 <= idx < bins:
                z[idx, j] += qty

    fig = go.Figure()
    fig.add_trace(
        go.Heatmap(
            x=times, y=centers, z=np.log1p(z),
            colorscale="Viridis", showscale=False,
            hovertemplate="%{x}<br>price %{y:.2f}<br>log-size %{z:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=times, y=mids, name="Mid",
            line=dict(color="rgba(255,255,255,.85)", width=1.2), mode="lines",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=320,
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title="Price",
        showlegend=False,
    )
    st.plotly_chart(fig)


# --- multi-exchange comparison ---

def render_comparison(
    snapshots: Dict[str, OrderBookSnapshot],
    statuses: Dict[str, FeedStatus],
) -> None:
    live = {name: snap for name, snap in snapshots.items()}
    if not live:
        st.info("Waiting for data from at least one venue…")
        return

    rows = []
    for name, snap in sorted(live.items()):
        status = statuses.get(name, FeedStatus())
        rows.append(
            {
                "Venue": name,
                "Pair": snap.symbol,
                "Best Bid": snap.best_bid,
                "Best Ask": snap.best_ask,
                "Spread (bps)": round(snap.spread_bps, 2),
                "Bid Size (top5)": round(sum(q for _, q in snap.bids[:5]), 4),
                "Ask Size (top5)": round(sum(q for _, q in snap.asks[:5]), 4),
                "Source": status.source,
                "Updated": _age_label(status),
            }
        )

    nbbo_bid_venue, nbbo_bid = max(
        ((name, s.best_bid) for name, s in live.items()), key=lambda item: item[1]
    )
    nbbo_ask_venue, nbbo_ask = min(
        ((name, s.best_ask) for name, s in live.items()), key=lambda item: item[1]
    )
    nbbo_mid = (nbbo_bid + nbbo_ask) / 2.0
    nbbo_spread_bps = ((nbbo_ask - nbbo_bid) / nbbo_mid) * 10_000.0 if nbbo_mid > 0 else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("NBBO Bid", f"${fmt_price(nbbo_bid)}", nbbo_bid_venue, delta_color="off")
    c2.metric("NBBO Ask", f"${fmt_price(nbbo_ask)}", nbbo_ask_venue, delta_color="off")
    crossed = " (crossed!)" if nbbo_spread_bps < 0 else ""
    c3.metric("NBBO Spread", f"{nbbo_spread_bps:.2f} bps{crossed}")

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Note: venues quote different pairs (USD vs USDT); NBBO across them is approximate."
    )
