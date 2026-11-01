"""Supported thin Streamlit observation client of the local workstation API."""

from __future__ import annotations

import streamlit as st

from src.ui import components
from src.ui.api_client import ApiClient, ApiError, DEFAULT_BACKEND, instrument_label

st.set_page_config(page_title="Native Order Book — Legacy Client", layout="wide")
st.title("Native Order Book — Legacy Client")
st.caption(
    "Observation only · one shared native backend · no exchange execution or private credentials"
)


def unavailable(error: ApiError) -> None:
    st.error(str(error))
    st.info(
        "Run `python -m src.workstation.cli serve` in another terminal from the repository root. "
        "The default source is an offline synthetic scenario. For an operator-managed capture use "
        "`--capture PATH`; live public data requires explicit `--network`. "
        "Then use Connect / reconnect. This client never starts a collector."
    )


if "legacy_api" not in st.session_state:
    st.session_state.legacy_api = ApiClient()

with st.sidebar:
    st.header("Local backend")
    with st.form("backend_connection"):
        backend_url = st.text_input("Loopback API origin", value=DEFAULT_BACKEND)
        reconnect = st.form_submit_button("Connect / reconnect")
    if reconnect:
        try:
            # Validate before releasing a working connection.
            replacement = ApiClient(backend_url)
        except ApiError as error:
            unavailable(error)
        else:
            try:
                st.session_state.legacy_api.close()
            except ApiError as error:
                st.warning(
                    f"Previous session cleanup failed: {error}. The backend idle reaper also releases abandoned HTTP sessions."
                )
            st.session_state.legacy_api = replacement
            st.session_state.pop("legacy_cost_request", None)
    client: ApiClient = st.session_state.legacy_api
    st.link_button("Open full React terminal", client.base_url)
    if st.button("Close this document's API session"):
        try:
            client.close_session()
        except ApiError as error:
            unavailable(error)
        st.session_state.pop("legacy_cost_request", None)
    st.caption(
        "Each Streamlit document owns its own API session. Closed/vanished documents are reaped after 60 idle seconds; reconnect after expiry."
    )
    st.button("Refresh source catalog")
    try:
        catalog = client.catalog()
        sources = {source["id"]: source for source in catalog["sources"]}
        if not sources:
            st.info("No sources registered. Check backend startup / capture options.")
            st.stop()
        source_id = st.selectbox(
            "Native source",
            list(sources),
            format_func=lambda value: (
                f"{sources[value]['label']} · {sources[value]['mode']} · {sources[value]['status']}"
            ),
        )
        source = sources[source_id]
        components.reasons(source["reasons"], label="Source")
        st.caption(
            f"Synthetic: {source['synthetic']} · Recorded source: {source['recording']}"
        )
        # The discovery union must never override source-specific metadata.
        source_catalog = client.catalog(source_id)
        instruments = {
            item["instrument_id"]: item for item in source_catalog["instruments"]
        }
        if not instruments:
            st.info(
                "Source metadata is loading or unavailable. Refresh the catalog once the backend is ready."
            )
            st.stop()
        instrument_id = st.selectbox(
            "Exact native instrument",
            list(instruments),
            format_func=lambda value: instrument_label(instruments[value]),
            key=f"native_selection:{source_id}",
        )
        if st.button(
            "Open / apply native selection", disabled=source["status"] != "ready"
        ):
            st.session_state.pop("legacy_cost_request", None)
            if client.frame is not None and client.frame["source"]["id"] == source_id:
                client.select(instrument_id)
            else:
                client.open(source["mode"], source_id, instrument_id)
    except ApiError as error:
        unavailable(error)
        st.stop()
    auto_refresh = st.toggle("Poll this session", value=True)
    refresh_seconds = st.select_slider(
        "Poll interval (seconds)", options=[2, 5, 10], value=2
    )
    st.caption(
        "Polling is bounded HTTP observation, not a browser-paint or exchange-latency measurement."
    )

if client.session_id is None:
    st.info(
        "Choose a source and exact native instrument, then Open / apply native selection. The default backend provides offline synthetic demo data."
    )
    st.stop()


def replay_action(context: tuple[str, str, str], action: str, values: dict) -> None:
    """Consume operator intent before polling/rendering can request another run."""
    active = st.session_state.legacy_api
    try:
        if (active.session_id, active.generation) != context[:2]:
            raise ApiError(
                "Replay selection changed; retry against the current controls."
            )
        active.replay(action, **values)
    except ApiError as error:
        st.session_state.legacy_replay_error = str(error)
    else:
        st.session_state.pop("legacy_replay_error", None)


def replay_controls(frame: dict) -> None:
    replay = frame["replay"]
    if replay is None:
        st.caption(
            "Live source: replay controls are not applicable."
            if frame["source"]["mode"] == "live"
            else "Replay state unavailable; inspect the source/quality reasons."
        )
        return
    st.subheader("Replay")
    if error := st.session_state.get("legacy_replay_error"):
        st.error(f"Replay operation rejected: {error}")
    context = (
        frame["session_id"],
        frame["generation"],
        frame["selection"]["instrument_id"],
    )
    identity = ":".join(context)
    prefix = "replay:" + identity + ":"
    controls = st.columns(4)
    for column, action in zip(controls, ("play", "pause", "step", "reset")):
        column.button(
            action.title(),
            key=f"replay_{action}:{identity}",
            on_click=replay_action,
            args=(context, action, {"count": 1} if action == "step" else {}),
        )

    def submit() -> None:
        action = st.session_state[prefix + "action"]
        values = {
            "seek": {"delivery_index": st.session_state[prefix + "target"]},
            "step": {"count": st.session_state[prefix + "count"]},
            "speed": {"speed": st.session_state[prefix + "speed"]},
        }[action]
        replay_action(context, action, values)

    with st.expander("Exact replay seek, step and speed"):
        with st.form("replay_parameters:" + identity):
            st.selectbox(
                "Replay operation", ["seek", "step", "speed"], key=prefix + "action"
            )
            st.text_input(
                "Delivery index (exact integer)",
                value=replay["cursor"]["delivery_index"],
                max_chars=24,
                key=prefix + "target",
            )
            st.number_input(
                "Deliveries to step",
                min_value=1,
                max_value=1000,
                value=10,
                step=1,
                key=prefix + "count",
            )
            st.text_input(
                "Speed (exact decimal, 0.01–100)",
                value=replay["speed"],
                max_chars=128,
                key=prefix + "speed",
            )
            st.form_submit_button("Apply replay operation", on_click=submit)


def cost_controls(context: tuple[str, str, str]) -> None:
    prefix = "cost:" + ":".join(context) + ":"

    def submit() -> None:
        state = st.session_state
        values = {
            "side": state[prefix + "side"],
            state[prefix + "kind"]: state[prefix + "size"],
            "latency_bps": state[prefix + "latency"],
            "rebalance_quote": state[prefix + "rebalance"],
        }
        if state[prefix + "known_fee"]:
            values.update(
                fee_bps=state[prefix + "fee"],
                fee_provenance=state[prefix + "provenance"],
                fee_date=state[prefix + "date"],
            )
        state.legacy_cost_request = (context, values)

    def clear() -> None:
        st.session_state.pop("legacy_cost_request", None)

    with st.expander("Native size-cost comparison controls"):
        st.caption(
            "Inputs are exact text. Fees are unknown unless explicitly supplied; no default exchange schedule, implicit FX conversion, or account balances."
        )
        with st.form("native_costs:" + ":".join(context)):
            st.selectbox("Side", ["buy", "sell"], key=prefix + "side")
            st.selectbox(
                "Size denomination",
                ["base_quantity", "quote_budget"],
                key=prefix + "kind",
            )
            st.text_input(
                "Exact size / budget", value="0.01", max_chars=128, key=prefix + "size"
            )
            st.checkbox(
                "Use an explicit assumed fee for both comparison legs",
                key=prefix + "known_fee",
            )
            st.text_input(
                "Assumed fee bps", value="0", max_chars=128, key=prefix + "fee"
            )
            st.text_input(
                "Fee assumption provenance",
                value="User scenario",
                key=prefix + "provenance",
            )
            st.text_input(
                "Fee assumption date (YYYY-MM-DD)",
                value="",
                max_chars=10,
                key=prefix + "date",
            )
            st.text_input(
                "Latency scenario bps (explicit assumption)",
                value="0",
                max_chars=128,
                key=prefix + "latency",
            )
            st.text_input(
                "Rebalance quote cost (explicit assumption)",
                value="0",
                max_chars=128,
                key=prefix + "rebalance",
            )
            st.form_submit_button(
                "Calculate / refresh native comparison", on_click=submit
            )
        st.button("Clear comparison", on_click=clear)


# Operator widgets belong to full app reruns, never the timed observation
# fragment. Polling must not replace a form while its submission is pending.
try:
    initial_frame = client.refresh()
    selected = initial_frame["selection"]["instrument_id"]
    active_catalog = client.catalog(initial_frame["source"]["id"])
    instrument = next(
        (
            item
            for item in active_catalog["instruments"]
            if item["instrument_id"] == selected
        ),
        None,
    )
    if instrument is None:
        raise ApiError(
            "Selected source metadata is unavailable; reconnect rather than substituting another source's instrument."
        )
except ApiError as error:
    unavailable(error)
    st.stop()

context = (initial_frame["session_id"], initial_frame["generation"], selected)
stored = st.session_state.get("legacy_cost_request")
if stored is not None and stored[0] != context:
    st.session_state.pop("legacy_cost_request", None)
replay_controls(initial_frame)
st.button("Refresh current observation")
with st.expander("Server display controls"):
    st.caption(
        "Server-native quanta and anchored price geometry. No automatic local rebinning. Blank quantum uses the backend default; unknown/variable ticks remain explicit."
    )
    quantum = st.text_input(
        "Exact price quantum",
        value=instrument.get("tick_size") or "",
        key="quantum:" + ":".join(context),
        max_chars=128,
    )
    depth = st.slider("Ladder rows per side", 1, 100, 20)
    rows = st.select_slider("Heatmap price rows", options=[32, 64, 128, 256], value=64)
    columns = st.slider("Maximum history columns", 1, 180, 60)
    bucket = st.selectbox(
        "Source sampling quantum (ns)", ["1000000000", "5000000000", "10000000000"]
    )
    offset_key = "offset:" + ":".join(context)
    offset = st.number_input(
        "Price viewport offset (rows)",
        min_value=-100000,
        max_value=100000,
        value=0,
        step=1,
        key=offset_key,
    )
    side = st.radio("Resting liquidity side", ["bid", "ask"], horizontal=True)
    color_ceiling = st.text_input(
        "Fixed heatmap color ceiling (base units; plotting only)",
        value="1",
        key="color:" + ":".join(context),
        max_chars=128,
    )

    def recenter_viewport() -> None:
        st.session_state[offset_key] = 0
        st.session_state.legacy_recenter = context

    st.button("Recenter server price anchor", on_click=recenter_viewport)
    start = st.text_input(
        "Optional start source ns (exact, blank = server window)",
        key="start:" + ":".join(context),
        max_chars=24,
    )
    end = st.text_input(
        "Optional end source ns (exact, blank = observed cursor)",
        key="end:" + ":".join(context),
        max_chars=24,
    )
cost_controls(context)
price = {"price_step": quantum} if quantum else {}
view_request = {
    "rows": rows,
    "max_columns": columns,
    "bucket_ns": bucket,
    "price_offset_rows": offset,
    **price,
}
if start:
    view_request["start_ns"] = start
if end:
    view_request["end_ns"] = end


@st.fragment(run_every=refresh_seconds if auto_refresh else None)
def dashboard() -> None:
    try:
        frame = client.refresh()
        current = (
            frame["session_id"],
            frame["generation"],
            frame["selection"]["instrument_id"],
        )
        if current != context:
            st.rerun()
        requested = st.session_state.get("legacy_cost_request")
        comparison = st.empty()
        if requested is not None and requested[0] == current:
            with comparison.container():
                try:
                    components.render_costs(client.costs(**requested[1]))
                except ApiError as error:
                    st.error(f"Native comparison unavailable: {error}")
        # Replace generation-bound financial output before slower plot rendering.
        components.render_identity(frame, instrument)
        replay = frame["replay"]
        if replay is not None:
            st.caption(
                f"Delivery {replay['cursor']['delivery_index']} / {replay['total_deliveries']} · speed {replay['speed']} · {replay['status']}"
            )
        try:
            components.render_ladder(client.ladder(rows=depth, **price))
        except ApiError as error:
            st.error(f"Native ladder unavailable: {error}")
        recenter = st.session_state.pop("legacy_recenter", None) == context
        try:
            grid = client.visualization(**view_request, recenter=recenter)
            st.caption(
                "Supported exact server price steps: " + ", ".join(grid["step_options"])
            )
            components.render_heatmap(grid, side, color_ceiling)
        except ApiError as error:
            st.error(f"Server history unavailable: {error}")
        components.render_analytics(frame["analytics"])
    except ApiError as error:
        unavailable(error)


dashboard()
