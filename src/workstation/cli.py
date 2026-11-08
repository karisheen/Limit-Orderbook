"""Headless public capture, verified inspection and deterministic offline replay."""

import argparse
import asyncio
from dataclasses import asdict, is_dataclass, replace
from decimal import Decimal, InvalidOperation
import importlib.util
import json
from pathlib import Path
import sys
from typing import Sequence


COMPLETE = 0
FAILURE = 1
INCOMPLETE = 2


def _json_value(value):
    """Keep arbitrary-size identities/clocks/ordinals exact on CLI JSON surfaces."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def _emit(value: dict, *, error: bool = False) -> None:
    print(
        json.dumps(_json_value(value), sort_keys=True),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def _positive_decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("expected a positive finite decimal") from exc
    if not value.is_finite() or value <= 0 or value > Decimal("1000000"):
        raise argparse.ArgumentTypeError("expected 0 < value <= 1000000")
    return value


def _nonnegative(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="serve the local FastAPI/React terminal; offline demo by default"
    )
    serve.add_argument(
        "--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1")
    )
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--capture",
        type=Path,
        action="append",
        default=[],
        help="operator-managed verified recording; HTTP clients receive only opaque IDs",
    )
    serve.add_argument(
        "--network",
        action="store_true",
        help="explicitly enable shared public live discovery/collection",
    )
    serve.add_argument("--max-sessions", type=int, default=8)
    serve.add_argument(
        "--state-dir",
        type=Path,
        default=Path("data/workstation-state"),
        help="durable local paper, manual journal and research databases",
    )
    demo = commands.add_parser(
        "demo",
        help="generate and replay explicit synthetic markets and Hyperliquid context; no network",
    )
    demo.add_argument("--root", type=Path, default=Path("data/workstation"))
    demo.add_argument("--mode", choices=("raw", "normalized"), default="normalized")
    demo.add_argument(
        "--duration",
        type=_nonnegative,
        default=180,
        help="1..86400 synthetic virtual seconds at 1 Hz, generated without wall-clock waiting",
    )
    inspect = commands.add_parser(
        "inspect",
        help="verify manifest/files/catalog and query local Parquet with DuckDB",
    )
    inspect.add_argument("capture", type=Path)
    inspect.add_argument("--limit", type=_nonnegative, default=10)
    inspect.add_argument("--stream")
    inspect.add_argument("--allow-incomplete", action="store_true")
    replay = commands.add_parser(
        "replay", help="replay verified local source transactions, without a browser"
    )
    replay.add_argument("capture", type=Path)
    replay.add_argument("--mode", choices=("raw", "normalized"), default="normalized")
    replay.add_argument(
        "--speed",
        type=_positive_decimal,
        help="wall-clock pacing multiplier; omitted is unpaced",
    )
    replay.add_argument("--seek", type=_nonnegative)
    replay.add_argument("--event-index", type=_nonnegative, default=0)
    replay.add_argument(
        "--steps",
        type=_nonnegative,
        help="pause after this many complete source batches",
    )
    replay.add_argument(
        "--interactive",
        action="store_true",
        help="stdin commands: play SPEED, pause, step N, seek ORD [INDEX], run [SPEED], status, quit",
    )
    replay.add_argument("--allow-incomplete", action="store_true")
    record = commands.add_parser(
        "record", help="finite opt-in capture of actual public market data"
    )
    record.add_argument(
        "--network",
        action="store_true",
        help="required explicit permission for public network access",
    )
    record.add_argument("--root", type=Path, default=Path("data/workstation"))
    record.add_argument(
        "--duration",
        type=_positive_decimal,
        default=Decimal("30"),
        help="finite capture duration in seconds, after discovery/probes",
    )
    record.add_argument(
        "--venue",
        action="append",
        choices=("binance-global", "binance-us", "hyperliquid"),
        help="repeat for separate venues; default Binance global after dependency probes",
    )
    record.add_argument(
        "--assets",
        nargs="+",
        choices=("BTC", "ETH", "SOL", "HYPE"),
        default=["BTC", "ETH", "SOL"],
    )
    record.add_argument(
        "--probe-timeout", type=_positive_decimal, default=Decimal("25")
    )
    research = commands.add_parser(
        "research", help="offline causal experiments; all outcomes retained locally"
    ).add_subparsers(dest="research_command", required=True)
    experiment = research.add_parser(
        "run", help="run a bounded native replay experiment"
    )
    source = experiment.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true")
    source.add_argument("--capture", type=Path)
    experiment.add_argument(
        "--duration", type=_nonnegative, help="demo virtual seconds; default 900"
    )
    experiment.add_argument(
        "--config", type=Path, help="exact JSON ResearchConfig, at most 64 KiB"
    )
    attempts = research.add_parser(
        "list", help="list persisted attempts, including failures"
    )
    report = research.add_parser(
        "show", help="inspect an attempt and a page of all trial results"
    )
    report.add_argument("attempt_id")
    for command in (experiment, attempts, report):
        command.add_argument(
            "--state-dir", type=Path, default=Path("data/workstation-state")
        )
        command.add_argument("--limit", type=_nonnegative, default=50)
    for command in (experiment, report):
        command.add_argument("--offset", type=_nonnegative, default=0)
    return result


def _dependencies() -> None:
    missing = [
        name for name in ("pyarrow", "duckdb") if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise RuntimeError(
            "missing recording dependencies: "
            + ", ".join(missing)
            + "; install the locked project environment"
        )


def _replay_report(session, manifest, *, path: Path) -> dict:
    from .wire import to_wire

    return {
        "command": "replay",
        "capture": str(path),
        "capture_complete": manifest.complete,
        "incomplete_reasons": manifest.incomplete_reasons,
        "mode": session.mode,
        "status": session.status,
        "cursor": session.cursor,
        "state_hash": session.state_hash,
        "book_hash": session.book_hash,
        "books": [to_wire(book, canonical=False) for book in session.books()],
        "source_quality": "Recorded modes and source limitations are retained; playback is not live market access.",
    }


async def _interactive(session) -> bool:
    """Async input keeps playback pausable rather than blocking behind play()."""
    playing: asyncio.Task | None = None
    valid = True
    try:
        while True:
            try:
                line = await asyncio.to_thread(
                    input, "replay> " if sys.stdin.isatty() else ""
                )
            except EOFError:
                break
            words = line.split()
            if not words:
                continue
            command, *args = words
            try:
                if command in ("quit", "exit"):
                    break
                if command == "status" and not args:
                    _emit(
                        {
                            "status": session.status,
                            "cursor": session.cursor,
                            "state_hash": session.state_hash,
                            "book_hash": session.book_hash,
                        }
                    )
                    continue
                session.pause()
                if playing is not None:
                    await playing
                    playing = None
                if command == "play" and len(args) <= 1:
                    speed = _positive_decimal(args[0]) if args else Decimal("1")
                    playing = asyncio.create_task(session.play(speed=speed))
                    await asyncio.sleep(0)
                elif command == "pause" and not args:
                    pass
                elif command == "step" and len(args) <= 1:
                    session.step(_nonnegative(args[0]) if args else 1)
                elif command == "seek" and 1 <= len(args) <= 2:
                    session.seek(
                        _nonnegative(args[0]),
                        _nonnegative(args[1]) if len(args) == 2 else 0,
                    )
                elif command == "run" and len(args) <= 1:
                    await session.run_to_end(
                        speed=_positive_decimal(args[0]) if args else None
                    )
                else:
                    raise ValueError(
                        "commands: play [SPEED], pause, step [N], seek ORD [INDEX], run [SPEED], status, quit"
                    )
            except (ValueError, argparse.ArgumentTypeError) as exc:
                valid = False
                _emit({"status": "invalid_control", "error": str(exc)}, error=True)
    finally:
        session.pause()
        if playing is not None:
            await playing
    return valid


async def _replay(args) -> int:
    from .recording import RecordingReader
    from .replay import ReplaySession

    with RecordingReader(
        args.capture, allow_incomplete=args.allow_incomplete
    ) as reader:
        session = ReplaySession(reader, mode=args.mode)
        try:
            valid_controls = True
            if args.seek is not None:
                session.seek(args.seek, args.event_index)
            if args.steps is not None:
                session.step(args.steps)
            elif not args.interactive:
                await session.run_to_end(speed=args.speed)
            if args.interactive:
                valid_controls = await _interactive(session)
            _emit(_replay_report(session, reader.manifest, path=args.capture))
            if not valid_controls:
                return FAILURE
            return COMPLETE if reader.manifest.complete else INCOMPLETE
        finally:
            session.close()


def _inspect(args) -> int:
    from .recording import RecordingReader

    if not 1 <= args.limit <= 10000:
        raise ValueError("inspect --limit must be between 1 and 10000")
    with RecordingReader(
        args.capture, allow_incomplete=args.allow_incomplete
    ) as reader:
        rows = reader.query_history(stream_id=args.stream, limit=args.limit)
        _emit(
            {
                "command": "inspect",
                "verified": True,
                "capture": args.capture,
                "manifest": reader.manifest.to_dict(),
                "history": rows,
                "verification_scope": "Storage hashes/schema/counts/catalog; source capability limitations are not integrity passes.",
            }
        )
        return COMPLETE if reader.manifest.complete else INCOMPLETE


async def _demo(args) -> int:
    from .demo import create_demo
    from .replay import ReplaySession

    with await create_demo(args.root, duration_seconds=args.duration) as result:
        reader = result.reader
        session = ReplaySession(reader, mode=args.mode)
        try:
            await session.run_to_end()
            report = _replay_report(session, reader.manifest, path=reader.path)
            equivalent = (
                session.state_hash == result.state_hash
                and session.book_hash == result.book_hash
            )
            report.update(
                command="demo",
                synthetic=True,
                fixture_license="CC0-1.0",
                live_projection_equivalent=equivalent,
            )
            _emit(report)
            if not equivalent:
                raise RuntimeError("demo replay differs from source projection")
            return COMPLETE if reader.manifest.complete else INCOMPLETE
        finally:
            session.close()


async def _record(args) -> int:
    from .adapter_catalog import AdapterCatalog, metadata_status
    from .demo import source_tree_revision
    from .events import AssetContext, ReferencePrice, SubscriptionKey, Trade
    from .instruments import InstrumentQuery
    from .recording import Recorder
    from .subscriptions import RecorderBinding, SubscriptionRegistry, SubscriptionStatus

    venues = tuple(dict.fromkeys(args.venue or ("binance-global",)))
    catalog = AdapterCatalog(include_binance_us="binance-us" in venues)
    products = []
    for venue in venues:
        adapter = catalog.adapter(venue)
        snapshot = await adapter.discover()
        catalog.instruments.apply_metadata(snapshot)
        catalog.statuses[venue] = metadata_status(venue, adapter)
        available = catalog.instruments.list(
            InstrumentQuery(venue=venue, status="active")
        )
        for asset in dict.fromkeys(args.assets):
            matches = [
                item
                for item in available
                if item.base.ticker == asset
                and (venue == "hyperliquid" or item.quote.ticker == "USDT")
                and item.market_type
                == ("perpetual" if venue == "hyperliquid" else "spot")
                and (venue != "hyperliquid" or item.dex_namespace == "default")
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"no unique discovered supported {venue} {asset} instrument"
                )
            products.append(matches[0])
    probes = await asyncio.gather(
        *(
            catalog.probe(item, timeout=float(args.probe_timeout))
            for item in products
            if item.venue in ("binance-global", "binance-us")
        )
    )
    if any(not probe.fully_usable for probe in probes):
        _emit(
            {
                "command": "record",
                "status": "dependency_unavailable",
                "complete": False,
                "dependency_evidence": probes,
            },
            error=True,
        )
        return FAILURE
    keys = frozenset(
        catalog.canonical_key(SubscriptionKey.for_instrument(item, channel=channel))
        for item in products
        for channel in (
            ("book", "trades", "reference", "context")
            if item.venue == "hyperliquid"
            else ("book", "trades")
        )
    )
    registry = SubscriptionRegistry(catalog)
    recorder = None
    binding = None
    unsubscribe = None
    seen_observations = set()
    interrupted = False
    capture_error = None
    manifest = None
    statuses: tuple[SubscriptionStatus, ...] = ()

    def observe(events):
        for event in events:
            if event.key in keys and isinstance(
                event.payload, (Trade, ReferencePrice, AssetContext)
            ):
                seen_observations.add(event.key)

    try:
        source_revision = source_tree_revision()
        recorder = Recorder(
            args.root,
            session_id=str(registry.session_id),
            clock_id=registry.clock_id,
            config=registry.recording_config
            + (
                (
                    "capture_scope",
                    "selected_public_book_trades_and_hyperliquid_context",
                ),
                ("duration_seconds", str(args.duration)),
            ),
            code_revision=source_revision,
            provenance="Actual public exchange bytes; discovered immutable metadata; no synthetic overlays.",
            license="Exchange public-data terms apply; no redistribution license asserted.",
        )
        unsubscribe = registry.subscribe_events(observe)
        binding = await RecorderBinding.start(registry, recorder, keys)
        _emit(
            {
                "event": "recording_started",
                "capture": recorder.path,
                "session_id": registry.session_id,
                "code_revision": source_revision,
                "duration_seconds": args.duration,
                "streams": len(keys),
                "readiness": "capture_attached_not_yet_synchronized",
            },
            error=True,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + float(args.duration)
        next_report = started + 5
        while True:
            health = recorder.health
            now = loop.time()
            if health.terminal_failure or now >= deadline:
                break
            if now >= next_report:
                _emit(
                    {
                        "event": "recording_health",
                        "elapsed_seconds": format(now - started, ".3f"),
                        "recorder": health,
                        "terminal_failure": False,
                    },
                    error=True,
                )
                next_report = now + 5
            await asyncio.sleep(min(0.25, deadline - now))
        if health.terminal_failure:
            _emit(
                {
                    "event": "recording_failed",
                    "elapsed_seconds": format(now - started, ".3f"),
                    "recorder": health,
                    "terminal_failure": True,
                },
                error=True,
            )
        statuses = registry.snapshot()
        usable_books = {
            book.key for book in registry.books() if book.quality.integrity == "valid"
        }
        for key in keys:
            if key.channel == "book" and key not in usable_books:
                recorder.mark_incomplete(f"no_valid_book:{key.instrument_id}")
            elif key.channel == "trades" and key not in seen_observations:
                recorder.mark_incomplete(f"no_trade_evidence:{key.instrument_id}")
            elif (
                key.channel in ("reference", "context") and key not in seen_observations
            ):
                recorder.mark_incomplete(
                    f"no_{key.channel}_evidence:{key.instrument_id}"
                )
        for status in statuses:
            if not status.capture_complete:
                recorder.mark_incomplete(
                    f"source_capture_incomplete:{status.stream_id}"
                )
    except asyncio.CancelledError:
        interrupted = True
        if recorder is not None:
            recorder.mark_incomplete("capture_interrupted")
    except Exception as exc:
        capture_error = exc
        if recorder is not None:
            recorder.mark_incomplete("capture_setup_or_runtime_failure")
    finally:
        try:
            if binding is not None:
                manifest = await binding.stop()
            elif recorder is not None:
                manifest = await recorder.finalize()
        finally:
            if unsubscribe is not None:
                unsubscribe()
            await registry.close()
    if recorder is None or manifest is None:
        if capture_error is not None:
            raise capture_error
        raise RuntimeError("capture could not initialize")
    _emit(
        {
            "command": "record",
            "capture": recorder.path,
            "status": "interrupted"
            if interrupted
            else "complete"
            if manifest.complete
            else "incomplete",
            "manifest": manifest.to_dict(),
            "dependency_evidence": probes,
            "source_statuses": statuses,
            "recorder_health": recorder.health,
            "terminal_recorder_failure": recorder.health.terminal_failure,
            "error": str(capture_error)[:4096] if capture_error is not None else None,
        }
    )
    if capture_error is not None:
        return FAILURE
    return COMPLETE if manifest.complete and not interrupted else INCOMPLETE


def _research_integer_fields(data: dict, names: tuple[str, ...]) -> None:
    """Accept exact CLI integer strings, never truncate JSON decimal values."""
    for name in names:
        if name not in data or data[name] is None:
            continue
        value = data[name]
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            value = int(value)
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
        data[name] = value


def _research_config(path: Path | None):
    from .costs import FeeSchedule
    from .quality import EligibilityPolicy
    from .research import ResearchConfig, ResearchScenario

    if path is None:
        return ResearchConfig()
    with path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("research config exceeds 64 KiB")
    data = json.loads(raw, parse_float=Decimal, parse_constant=Decimal)
    if not isinstance(data, dict):
        raise ValueError("research config must be an object")
    _research_integer_fields(
        data,
        (
            "sample_interval_ns",
            "feature_window_ns",
            "embargo_ns",
            "min_samples",
            "max_observations",
        ),
    )
    if "stream_ids" in data:
        if not isinstance(data["stream_ids"], list) or any(
            not isinstance(item, str) for item in data["stream_ids"]
        ):
            raise ValueError("stream_ids must be an array of native stream IDs")
    if "horizons_ns" in data:
        if not isinstance(data["horizons_ns"], list):
            raise ValueError("horizons_ns must be an array")
        horizons = []
        for value in data["horizons_ns"]:
            item = {"horizon_ns": value}
            _research_integer_fields(item, ("horizon_ns",))
            horizons.append(item["horizon_ns"])
        data["horizons_ns"] = tuple(horizons)
    if "fees" in data:
        if not isinstance(data["fees"], list):
            raise ValueError(
                "fees must be an array of [instrument_id, FeeSchedule] pairs"
            )
        fees = []
        for item in data["fees"]:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], dict)
            ):
                raise ValueError(
                    "fees require [native instrument ID, explicit FeeSchedule] pairs"
                )
            _research_integer_fields(item[1], ("asof_wall_ns",))
            fees.append((item[0], FeeSchedule(**item[1])))
        data["fees"] = tuple(fees)
    if "scenarios" in data:
        if not isinstance(data["scenarios"], list):
            raise ValueError("scenarios must be an array")
        scenarios = []
        for item in data["scenarios"]:
            if not isinstance(item, dict):
                raise ValueError("scenario must be an object")
            _research_integer_fields(item, ("latency_ns",))
            scenarios.append(ResearchScenario(**item))
        data["scenarios"] = tuple(scenarios)
    if "policy" in data:
        policy = data["policy"]
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        _research_integer_fields(policy, ("max_age_ns", "max_asof_skew_ns"))
        for name in ("allow_snapshot_quotes", "require_execution_access"):
            if name in policy and type(policy[name]) is not bool:
                raise ValueError(f"{name} must be boolean")
        data["policy"] = EligibilityPolicy(**policy)
    return ResearchConfig(**data)


def _research(args) -> int:
    from .demo_memory import MemoryReplaySource
    from .research import ResearchReport, ResearchStore, run_research

    if not 1 <= args.limit <= 1000:
        raise ValueError("research page limit must be 1..1000")
    config = _research_config(args.config) if args.research_command == "run" else None
    store = ResearchStore(args.state_dir / "research.sqlite3")
    try:
        if args.research_command == "list":
            _emit(
                {
                    "attempts": store.list_attempts(args.limit),
                    "limit": args.limit,
                    "scope": "latest_attempts; older attempts remain addressable by ID",
                }
            )
            return COMPLETE
        report: ResearchReport | None
        if args.research_command == "run":
            if args.capture is not None and args.duration is not None:
                raise ValueError("--duration applies only to --demo")
            source = (
                MemoryReplaySource.demo(
                    duration_seconds=900 if args.duration is None else args.duration
                )
                if args.demo
                else args.capture
            )
            assert config is not None
            report = run_research(source, config, store)
            attempt_id = report.attempt_id
        else:
            attempt_id = args.attempt_id
            report = store.get_report(attempt_id)
        attempt = store.get_attempt(attempt_id)
        if attempt is None:
            raise ValueError("unknown research attempt")
        total = 0 if report is None else len(report.trials)
        shown = (
            None
            if report is None
            else replace(
                report, trials=report.trials[args.offset : args.offset + args.limit]
            )
        )
        _emit(
            {
                "attempt": attempt,
                "config": store.get_config(attempt_id),
                "source": store.get_source(attempt_id),
                "report": shown,
                "trial_total": total,
                "trial_offset": args.offset,
                "trial_limit": args.limit,
                "trial_omitted": max(
                    0, total - (0 if shown is None else len(shown.trials))
                ),
            }
        )
        return COMPLETE
    finally:
        store.close()


async def _serve(args) -> int:
    import uvicorn
    from .api import create_app
    from .runtime import TerminalRuntime

    if not 1 <= args.port <= 65535:
        raise ValueError("port must be 1..65535")
    runtime = TerminalRuntime(
        captures=args.capture,
        enable_live=args.network,
        max_sessions=args.max_sessions,
        state_dir=args.state_dir,
    )
    origins = tuple(
        f"http://{host}:{port}"
        for host in ("127.0.0.1", "localhost", "[::1]")
        for port in (args.port, 5173)
    )
    app = create_app(runtime, allowed_origins=origins)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        ws_max_size=4096,
        ws_max_queue=16,
        limit_concurrency=128,
        timeout_keep_alive=5,
    )
    await uvicorn.Server(config).serve()
    return COMPLETE


async def _dispatch(args) -> int:
    if args.command == "serve":
        return await _serve(args)
    if args.command == "record" and not args.network:
        raise ValueError(
            "record requires --network; demo/replay/inspect never use network"
        )
    _dependencies()
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "replay":
        return await _replay(args)
    if args.command == "demo":
        return await _demo(args)
    if args.command == "research":
        return _research(args)
    return await _record(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        _emit({"status": "interrupted", "complete": False}, error=True)
        return INCOMPLETE
    except Exception as exc:
        _emit(
            {
                "status": "failure",
                "complete": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:4096],
            },
            error=True,
        )
        return FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
