#!/usr/bin/env python3
"""Opt-in public-feed dependency probes and bounded headless supervisor soaks."""

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import json
import math
from pathlib import Path
import platform
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VENUES = (
    "coinbase",
    "kraken",
    "binance-global",
    "binance-us",
    "bybit",
    "kucoin",
    "hyperliquid",
)
DEPTHS = {"kraken": 25, "binance-us": 20, "bybit": 50, "kucoin": 20, "hyperliquid": 20}


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(type(value).__name__)


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, default=json_default) + "\n")
    temporary.replace(path)


def failure(exc):
    return {
        "status": "failed",
        "error_type": type(exc).__name__,
        "detail": str(exc)[:4096],
        "access": getattr(exc, "reason", "unknown"),
    }


def blank_product(venue, base):
    return {
        "venue": venue,
        "requested_base": base,
        "instrument": None,
        "metadata": {"status": "unrun"},
        "snapshot": {"status": "unrun"},
        "rest_trades": {"status": "unrun"},
        "ws_book": {"status": "unrun"},
        "ws_trades": {"status": "unrun"},
        "streams": {},
        "execution_eligibility": "unknown_not_tested",
        "full_dependencies_passed": False,
    }


class Observation:
    """Bounded counters only; never retains full books or fabricates feed events."""

    def __init__(self):
        self.counts = Counter()
        self.diagnostics = Counter()
        self.endpoints = Counter()
        self.last_raw_sha256 = None
        self.last_book = None
        self.awaiting_recovery = False
        self.first_valid_mono = None
        self.started = time.monotonic()
        self.last_lifecycle = None
        self.lifecycle_samples = Counter()
        self.queue_count_high_water_sampled = 0
        self.queue_bytes_high_water_sampled = 0

    def raw(self, frame):
        self.counts["raw_frames"] += 1
        self.counts["raw_bytes"] += len(frame.payload)
        self.counts[f"raw_{frame.transport}_frames"] += 1
        self.endpoints[frame.endpoint_id] += 1
        self.last_raw_sha256 = frame.payload_sha256

    def events(self, events):
        from src.workstation.events import BookUpdate, Gap, Trade

        for event in events:
            self.counts["normalized_events"] += 1
            self.counts[type(event.payload).__name__] += 1
            if isinstance(event.payload, BookUpdate):
                self.counts[f"book_{event.payload.kind}_events"] += 1
                self.counts["book_level_changes"] += len(event.payload.changes)
                if event.checksum is not None:
                    self.counts[f"event_checksum_{event.checksum.result}"] += 1
            if isinstance(event.payload, Trade):
                mode = "rest" if event.source_mode == "rest_snapshot" else "ws"
                kind = "history" if event.payload.is_subscription_history else "live"
                self.counts[f"{mode}_{kind}_trades"] += 1
            if isinstance(event.payload, Gap):
                self.awaiting_recovery = True

    def book(self, book):
        self.counts["book_publications"] += 1
        self.counts[f"book_integrity_{book.quality.integrity}"] += 1
        if book.quality.integrity == "valid":
            mode = "rest" if book.quality.source_mode == "rest_snapshot" else "ws"
            self.counts[f"{mode}_valid_book_publications"] += 1
            if self.first_valid_mono is None:
                self.first_valid_mono = time.monotonic()
            if self.awaiting_recovery:
                self.counts["recoveries_to_valid_book"] += 1
                self.awaiting_recovery = False
        self.last_book = {
            "revision": book.revision,
            "connection_epoch": book.connection_epoch,
            "segment_id": str(book.segment_id),
            "bid_levels": len(book.bids),
            "ask_levels": len(book.asks),
            "quality": asdict(book.quality),
        }

    def diagnostic(self, reason):
        # A malformed remote error must not grow memory without bound on a soak.
        key = reason[:256]
        if key not in self.diagnostics and len(self.diagnostics) >= 100:
            key = "other_diagnostics"
        self.diagnostics[key] += 1

    def sample(self, supervisor, task):
        lifecycle = supervisor.lifecycle
        self.lifecycle_samples[lifecycle] += 1
        if lifecycle != self.last_lifecycle:
            self.counts["sampled_lifecycle_transitions"] += 1
            self.last_lifecycle = lifecycle
        self.queue_count_high_water_sampled = max(
            self.queue_count_high_water_sampled, supervisor.queue.qsize()
        )
        self.queue_bytes_high_water_sampled = max(
            self.queue_bytes_high_water_sampled, supervisor.queued_bytes
        )
        elapsed = time.monotonic() - self.started
        task_error = None
        if task.done() and not task.cancelled():
            error = task.exception()
            if error is not None:
                task_error = f"{type(error).__name__}: {error}"[:4096]
        return {
            "elapsed_seconds": elapsed,
            "counts": dict(self.counts),
            "raw_frames_per_second": self.counts["raw_frames"] / elapsed
            if elapsed
            else 0,
            "diagnostics": dict(self.diagnostics),
            "endpoints": dict(self.endpoints),
            "last_raw_sha256": self.last_raw_sha256,
            "last_book": self.last_book,
            "time_to_first_valid_book_seconds": None
            if self.first_valid_mono is None
            else self.first_valid_mono - self.started,
            "lifecycle": lifecycle,
            "reason": supervisor.reason,
            "epoch": supervisor.epoch,
            "source_mode": supervisor.source_mode,
            "reconnects": supervisor.reconnects,
            "duplicate_trades": supervisor.duplicate_trades,
            "continuity_capture_complete": supervisor.capture_complete,
            "raw_capture_persisted": False,
            "queue_count": supervisor.queue.qsize(),
            "queue_bytes": supervisor.queued_bytes,
            "queue_count_high_water_sampled": self.queue_count_high_water_sampled,
            "queue_bytes_high_water_sampled": self.queue_bytes_high_water_sampled,
            "lifecycle_samples": dict(self.lifecycle_samples),
            "task_done": task.done(),
            "task_error": task_error,
        }


async def rest_dependency(adapter, instrument, key, timeout, boundary):
    from src.workstation.adapters.base import wrap_decoded
    from src.workstation.events import BookUpdate, Trade
    from src.workstation.ingestion import make_snapshot_reducer
    from src.workstation.transport import fetch_public

    started = time.monotonic()
    report = {"status": "unrun"}
    try:
        request = adapter.snapshot_request(instrument, key)
        if request is None:
            return {
                "status": "unsupported",
                "detail": "adapter exposes no REST request for this channel",
            }
        report["endpoint"] = request.endpoint_id
        async with asyncio.timeout(timeout):
            raw = await fetch_public(request)
            frame = boundary.frame(
                "rest-probe", "rest-probe", 0, "rest", request.endpoint_id, raw
            )
            result = await asyncio.to_thread(adapter.decode, frame, instrument, key)
            report.update(raw_bytes=len(raw), raw_sha256=frame.payload_sha256)
            if result.error:
                report.update(status="failed", decode_error=asdict(result.error))
            else:
                expected = BookUpdate if key.channel == "book" else Trade
                count = sum(
                    isinstance(event.payload, expected) for event in result.events
                )
                report.update(
                    status="passed" if count else "failed",
                    decoded_expected_events=count,
                    decoded_controls=len(result.controls),
                )
                if key.channel == "book" and count:
                    reducer = make_snapshot_reducer(adapter, instrument, key)
                    events = wrap_decoded(
                        frame,
                        instrument,
                        key,
                        result,
                        reducer.segment_id,
                        "rest_snapshot",
                    )
                    reduction = await asyncio.to_thread(reducer.apply, events)
                    report["integrity"] = (
                        reduction.book.quality.integrity
                        if reduction.book
                        else "unknown"
                    )
                    report["recovery"] = (
                        asdict(reduction.recovery) if reduction.recovery else None
                    )
                    if reduction.book:
                        report["coverage"] = asdict(reduction.book.quality.coverage)
                    if (
                        reduction.recovery
                        or not reduction.book
                        or reduction.book.quality.integrity != "valid"
                    ):
                        report["status"] = "failed"
    except Exception as exc:
        report.update(failure(exc))
    report["elapsed_seconds"] = time.monotonic() - started
    return report


def choose_instrument(instruments, venue, base):
    quote = (
        "USD"
        if venue in ("coinbase", "kraken")
        else None
        if venue == "hyperliquid"
        else "USDT"
    )
    market = "perpetual" if venue == "hyperliquid" else "spot"
    candidates = [
        item
        for item in instruments
        if item.base.ticker == base
        and (quote is None or item.quote.ticker == quote)
        and item.market_type == market
        and item.status == "active"
        and (venue != "hyperliquid" or item.dex_namespace == "default")
    ]
    # Never equate wrapped spot tickers with canonical assets or silently change
    # a USD cohort to a stablecoin. Hyperliquid's default perp namespace is first.
    candidates.sort(
        key=lambda item: (item.dex_namespace not in (None, ""), str(item.instrument_id))
    )
    return (
        (candidates[0] if candidates else None),
        candidates[0].quote.ticker if candidates else quote,
        market,
        [str(item.instrument_id) for item in candidates],
    )


async def run(report, args):
    from src.workstation.adapter_catalog import create_adapter
    from src.workstation.events import SubscriptionKey
    from src.workstation.ingestion import (
        FeedCallbacks,
        FeedSupervisor,
        ReceiveBoundary,
        SupervisorConfig,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    boundary = ReceiveBoundary()
    owners = []
    selected = []
    semaphore = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    metrics_path = args.metrics or args.output.with_suffix(".metrics.jsonl")
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    async def discover(venue):
        rows = [
            row
            for row in report["products"]
            if row["venue"] == venue and not row.get("selection_excluded")
        ]
        async with semaphore:
            if stop.is_set():
                return
            before = time.monotonic()
            try:
                adapter = create_adapter(venue)
                if (
                    venue == "binance-global"
                    and args.binance_host_profile == "standard"
                ):
                    adapter.rest_host = "https://api.binance.com"
                    adapter.stream_host = "wss://stream.binance.com:9443"
                async with asyncio.timeout(args.timeout):
                    metadata = await adapter.discover()
                report["venues"][venue] = {
                    "status": "passed",
                    "source": metadata.source,
                    "payload_sha256": metadata.payload_hash,
                    "instrument_count": len(metadata.instruments),
                    "elapsed_seconds": time.monotonic() - before,
                }
                for row in rows:
                    item, quote, market, candidates = choose_instrument(
                        metadata.instruments, venue, row["requested_base"]
                    )
                    row["selection"] = {
                        "quote": quote,
                        "market_type": market,
                        "candidate_ids": candidates,
                    }
                    row["metadata"] = {
                        "status": "passed" if item else "unsupported",
                        "detail": "exact product discovered"
                        if item
                        else "no active exact cohort product in metadata",
                    }
                    if item:
                        row["instrument"] = asdict(item)
                        row["metadata_execution_access"] = item.execution_access
                        selected.append((adapter, item, row))
            except Exception as exc:
                outcome = failure(exc)
                outcome["elapsed_seconds"] = time.monotonic() - before
                report["venues"][venue] = outcome
                for row in rows:
                    row["metadata"] = dict(outcome)

    async def preflight(adapter, item, row):
        book_key = SubscriptionKey.for_instrument(item, depth=DEPTHS.get(item.venue))
        trade_key = SubscriptionKey.for_instrument(item, channel="trades")
        async with semaphore:
            for name, key in (("snapshot", book_key), ("rest_trades", trade_key)):
                if stop.is_set():
                    return
                row[name] = await rest_dependency(
                    adapter, item, key, args.timeout, boundary
                )
        for channel, key in (("book", book_key), ("trades", trade_key)):
            if stop.is_set():
                return
            try:
                endpoint = adapter.ws_endpoint(item, key)
                row[f"ws_{channel}"] = {
                    "status": "running" if endpoint else "unsupported",
                    "endpoint": endpoint,
                }
                observation = Observation()
                supervisor = FeedSupervisor(
                    adapter,
                    item,
                    key,
                    boundary=boundary,
                    callbacks=FeedCallbacks(
                        raw=observation.raw,
                        events=observation.events,
                        book=observation.book,
                        diagnostic=observation.diagnostic,
                    ),
                    config=SupervisorConfig(),
                )
                task = asyncio.create_task(
                    supervisor.run(), name=f"{item.instrument_id}:{channel}"
                )
                owners.append((row, channel, observation, supervisor, task))
            except Exception as exc:
                row[f"ws_{channel}"] = failure(exc)

    def sample(final=False):
        for row, channel, observation, supervisor, task in owners:
            row["streams"][channel] = observation.sample(supervisor, task)
            evidence = row[f"ws_{channel}"]
            if evidence["status"] != "unsupported":
                count = observation.counts[
                    "ws_valid_book_publications"
                    if channel == "book"
                    else "ws_live_trades"
                ]
                evidence.update(
                    status="passed" if count else "failed" if final else "running",
                    observed_count=count,
                    criterion="valid non-REST book publication"
                    if channel == "book"
                    else "non-history non-REST trade event",
                )
                if final and not count:
                    evidence["detail"] = (
                        "no qualifying observation in this run; not proof of universal unavailability"
                    )
            row["full_dependencies_passed"] = all(
                row[name]["status"] == "passed"
                for name in (
                    "metadata",
                    "snapshot",
                    "rest_trades",
                    "ws_book",
                    "ws_trades",
                )
            )
        for row in report["products"]:
            row["currently_live_ws_dependencies"] = len(row["streams"]) == 2 and all(
                stream["lifecycle"] == "live"
                and stream["source_mode"] in ("ws_snapshot", "ws_delta")
                and stream["continuity_capture_complete"]
                and not stream["task_done"]
                for stream in row["streams"].values()
            )
        report["elapsed_seconds"] = time.monotonic() - started
        report["sampled_wall_ns"] = time.time_ns()
        report["binance_preference_by_base"] = {}
        for base in args.bases:
            report["binance_preference_by_base"][base] = next(
                (
                    venue
                    for venue in ("binance-global", "binance-us")
                    if any(
                        row["venue"] == venue
                        and row["requested_base"] == base
                        and row["full_dependencies_passed"]
                        and row["currently_live_ws_dependencies"]
                        for row in report["products"]
                    )
                ),
                None,
            )

    try:
        await asyncio.gather(*(discover(venue) for venue in args.venues))
        save(args.output, report)
        await asyncio.gather(*(preflight(*entry) for entry in selected))
        report["status"] = "running"
        streaming_started = time.monotonic()
        deadline = streaming_started + args.duration
        with metrics_path.open("w") as metrics:
            while True:
                sample()
                report["streaming_elapsed_seconds"] = (
                    time.monotonic() - streaming_started
                )
                save(args.output, report)
                metrics.write(
                    json.dumps(
                        {
                            "wall_ns": report["sampled_wall_ns"],
                            "elapsed_seconds": report["elapsed_seconds"],
                            "streams": [
                                {
                                    "instrument_id": row["instrument"]["instrument_id"],
                                    "channel": channel,
                                    **row["streams"][channel],
                                }
                                for row, channel, *_ in owners
                            ],
                        },
                        default=json_default,
                    )
                    + "\n"
                )
                metrics.flush()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or stop.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        stop.wait(), min(args.metrics_interval, remaining)
                    )
                except TimeoutError:
                    pass
        sample(final=True)
        report["status"] = "interrupted" if stop.is_set() else "completed"
    except Exception as exc:
        report["status"] = "failed"
        report["fatal_error"] = failure(exc)
    finally:
        # Snapshot health before cancellation changes lifecycle to disconnected.
        sample(final=True)
        for *_, task in owners:
            task.cancel()
        await asyncio.gather(*(entry[-1] for entry in owners), return_exceptions=True)
        report["shutdown"] = {
            "owners_started": len(owners),
            "owners_joined": sum(entry[-1].done() for entry in owners),
            "clean": all(entry[-1].done() for entry in owners),
        }
        report["metrics_file"] = str(metrics_path)
        report["finished_wall_ns"] = time.time_ns()
        save(args.output, report)
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly allow real public Internet requests",
    )
    parser.add_argument("--venues", nargs="+", choices=VENUES, default=list(VENUES))
    parser.add_argument(
        "--bases",
        nargs="+",
        choices=("BTC", "ETH", "SOL"),
        default=["BTC", "ETH", "SOL"],
    )
    parser.add_argument(
        "--include-hype",
        action="store_true",
        help="also probe discovered Hyperliquid HYPE perpetual",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60,
        help="stream seconds after bounded preflight; 1800 or 86400 for soaks",
    )
    parser.add_argument(
        "--timeout", type=float, default=30, help="per discovery/REST operation timeout"
    )
    parser.add_argument("--metrics-interval", type=float, default=10)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="bounded discovery/REST product concurrency",
    )
    parser.add_argument(
        "--binance-host-profile", choices=("data-only", "standard"), default="data-only"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evidence/m1-feed-probe.json")
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        help="periodic JSONL output; defaults beside evidence JSON",
    )
    args = parser.parse_args()
    if not all(
        math.isfinite(value) and value > 0
        for value in (args.duration, args.timeout, args.metrics_interval)
    ):
        parser.error(
            "duration, timeout and metrics interval must be finite and positive"
        )
    if not 1 <= args.concurrency <= 20:
        parser.error("concurrency must be 1..20")
    args.venues = list(dict.fromkeys(args.venues))
    args.bases = list(dict.fromkeys(args.bases))
    report = {
        "schema_version": 1,
        "kind": "real_public_adapter_supervisor_probe",
        "status": "unrun",
        "started_wall_ns": time.time_ns(),
        "runtime": sys.version,
        "platform": platform.platform(),
        "configuration": {
            "venues": args.venues,
            "bases": args.bases,
            "duration_seconds": args.duration,
            "operation_timeout_seconds": args.timeout,
            "metrics_interval_seconds": args.metrics_interval,
            "binance_host_profile": args.binance_host_profile,
            "include_hype": args.include_hype,
        },
        "venues": {venue: {"status": "unrun"} for venue in VENUES},
        "products": [
            blank_product(venue, base)
            for venue in VENUES
            for base in ("BTC", "ETH", "SOL")
        ],
        "limitations": [
            "Public readability does not establish execution eligibility; no credentials, wallet, signing or orders.",
            "Evidence applies only to exact selected products, hosts, channels and observation interval.",
            "Missing events are never synthesized; a quiet trade stream may fail the observation criterion.",
            "REST fallback remains REST/degraded and cannot satisfy a WebSocket dependency.",
            "Book counts are supervisor publications, not necessarily distinct venue updates; raw/event counts are separate.",
            "Only hashes and counters are retained, not a replayable raw capture. Integrity is the actual adapter/core verdict.",
            "Queue high-water and lifecycle transitions are sampled and can miss between-sample peaks/transitions.",
            "No forced disconnect/slow-consumer fault injection; recovery counters cover naturally observed incidents only.",
            "No UI latency or exchange-to-client latency measurement. Long soak success requires actually running it.",
        ],
    }
    if args.include_hype:
        report["products"].append(blank_product("hyperliquid", "HYPE"))
    if not args.live:
        report["reason"] = "network opt-in --live absent; all dependencies unrun"
        save(args.output, report)
    else:
        # Preserve rows for every initial venue/base, including deliberately unrun selections.
        for row in report["products"]:
            if row["venue"] not in args.venues or (
                row["requested_base"] not in args.bases
                and row["requested_base"] != "HYPE"
            ):
                row["selection_excluded"] = True
        try:
            report = asyncio.run(run(report, args))
        except (Exception, KeyboardInterrupt) as exc:
            report["status"] = "failed"
            report["fatal_error"] = failure(exc)
            report["finished_wall_ns"] = time.time_ns()
            save(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "fully_observed_products": sum(
                    row["full_dependencies_passed"] for row in report["products"]
                ),
            }
        )
    )
    return 1 if report["status"] in ("failed", "interrupted") else 0


if __name__ == "__main__":
    raise SystemExit(main())
