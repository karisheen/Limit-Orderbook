"""Opaque, bounded managed captures; all browser inputs are IDs, never paths."""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from .recording import (
    Recorder,
    RecorderLimits,
    RecordingReader,
    SessionManifest,
    recover_recordings,
)
from .subscriptions import RecorderBinding

if TYPE_CHECKING:
    from .runtime import CaptureIdentity


MAX_CAPTURES = 64
MAX_ACTIVE = 2
# Native sessions own at most 16 book/trade pairs and eight context/reference pairs.
MAX_CAPTURE_CHANNELS = 48
MAX_CAPTURE_INSTRUMENTS = 24
MAX_METADATA_BYTES = 64 * 1024
CAPTURE_LIMITS = RecorderLimits(
    managed_budget_bytes=128 * 1024 * 1024,
    retention_max_sessions=1,
)


@dataclass
class ActiveCapture:
    owner: str
    recorder: Recorder
    binding: RecorderBinding


class CaptureService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.state_dir / "recordings"
        self.rows: dict[str, dict] = {}
        self.active: dict[str, ActiveCapture] = {}
        self.lock = asyncio.Lock()
        self.closed = False

    def _root(self):
        if self.root.is_symlink():
            raise ValueError("managed recordings directory cannot be a symlink")
        self.root.mkdir(mode=0o700, exist_ok=True)
        return self.root

    def _scan(self):
        found: list[
            tuple[
                dict[str, object],
                Path | None,
                tuple[SessionManifest, CaptureIdentity] | None,
            ]
        ] = []
        for directory in self._root().iterdir():
            if not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                continue
            if directory.is_symlink() or not directory.is_dir():
                continue
            if len(found) == MAX_CAPTURES:
                raise ValueError("managed capture catalog limit exceeded")
            row = self._row(directory.name)
            descriptor = None
            path = None
            try:
                metadata = directory / "capture.json"
                if (
                    metadata.is_symlink()
                    or metadata.stat().st_size > MAX_METADATA_BYTES
                ):
                    raise ValueError("invalid capture metadata")
                saved = json.loads(metadata.read_text())
                session_id = saved["session_id"]
                instrument_id = saved.get("instrument_id")
                if not isinstance(instrument_id, str) or len(instrument_id) > 512:
                    raise ValueError("invalid capture instrument")
                row["label"] += " / " + instrument_id
                scope = saved.get("scope", "instrument")
                instruments = saved.get("instruments", [instrument_id])
                channels = saved.get("channels", [])
                self._validate_scope(scope, instruments, channels, instrument_id)
                row.update(scope=scope, instruments=instruments, channels=channels)
                if not isinstance(session_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id
                ):
                    raise ValueError("invalid capture identity")
                recover_recordings(directory)
                path = directory / session_id
                if path.is_symlink():
                    raise ValueError("invalid capture boundary")
                if not (path / "manifest.json").exists():
                    row.update(status="incomplete", reasons=["capture_interrupted"])
                else:
                    with RecordingReader(path, allow_incomplete=True) as reader:
                        manifest = reader.manifest
                    if "channels" not in saved:
                        # Older captures were instrument-only; derive channels from
                        # their verified evidence, never from today's live selection.
                        row["channels"] = self._channels(
                            {spec.key for spec in manifest.streams}
                        )
                        self._validate_scope(
                            scope, instruments, row["channels"], instrument_id
                        )
                    row.update(
                        status="complete" if manifest.complete else "incomplete",
                        reasons=list(manifest.incomplete_reasons),
                    )
                    if manifest.complete:
                        descriptor = self.runtime._capture_descriptor(path)
            except Exception:
                row.update(status="error", reasons=["capture_verification_failed"])
            found.append((row, path, descriptor))
        return found

    @staticmethod
    def _row(capture_id):
        return {
            "id": capture_id,
            "label": "Live capture " + capture_id[:8],
            "status": "recording",
            "source_id": None,
            "reasons": [],
            "scope": "instrument",
            "instruments": [],
            "channels": [],
        }

    @staticmethod
    def _channels(keys):
        return [
            {"venue": venue, "instrument_id": instrument_id, "stream": stream}
            for venue, instrument_id, stream in sorted(
                {(key.venue, str(key.instrument_id), key.channel) for key in keys}
            )
        ]

    @staticmethod
    def _validate_scope(scope, instruments, channels, primary):
        if scope not in ("instrument", "cohort"):
            raise ValueError("invalid capture scope")
        if (
            not isinstance(instruments, list)
            or not 1 <= len(instruments) <= MAX_CAPTURE_INSTRUMENTS
            or any(
                not isinstance(item, str) or not 1 <= len(item) <= 512
                for item in instruments
            )
            or len(set(instruments)) != len(instruments)
            or primary not in instruments
            or (scope == "instrument" and instruments != [primary])
        ):
            raise ValueError("invalid capture instruments")
        if not isinstance(channels, list) or len(channels) > MAX_CAPTURE_CHANNELS:
            raise ValueError("invalid capture channels")
        for channel in channels:
            if (
                not isinstance(channel, dict)
                or set(channel) != {"venue", "instrument_id", "stream"}
                or any(
                    not isinstance(channel[field], str)
                    or not 1 <= len(channel[field]) <= bound
                    for field, bound in (
                        ("venue", 64),
                        ("instrument_id", 512),
                        ("stream", 64),
                    )
                )
                or channel["instrument_id"] not in instruments
            ):
                raise ValueError("invalid capture channel")

    def _register(self, row, path, descriptor):
        manifest, _identity = descriptor
        source_id = "capture-" + row["id"]
        self.runtime.capture_map[source_id] = path
        self.runtime.capture_descriptors[source_id] = descriptor
        instruments = {}
        for spec in manifest.streams:
            identity = str(spec.instrument.instrument_id)
            if identity not in instruments or spec.key.channel == "book":
                instruments[identity] = spec.instrument
        self.runtime.source_instruments[source_id] = tuple(instruments.values())
        self.runtime.instruments.update(instruments)
        self.runtime.sources[source_id] = {
            "id": source_id,
            "mode": "replay",
            "label": row["label"],
            "status": "ready",
            "synthetic": False,
            "recording": True,
            "duration_ns": None,
            "streams": len(manifest.streams),
            "reasons": [],
        }
        row["source_id"] = source_id

    async def load(self):
        for row, path, descriptor in await self.runtime._catalog_work(self._scan):
            self.rows[row["id"]] = row
            if descriptor is not None:
                self._register(row, path, descriptor)

    def listing(self):
        rows = []
        for capture_id, row in self.rows.items():
            result = {
                **row,
                "reasons": list(row["reasons"]),
                "instruments": list(row["instruments"]),
                "channels": [dict(channel) for channel in row["channels"]],
            }
            active = self.active.get(capture_id)
            result["session_id"] = active.owner if active is not None else None
            if active is not None:
                health = active.recorder.health
                result["reasons"] = list(health.incomplete_reasons)
                if health.terminal_failure:
                    result["status"] = "error"
                elif health.incomplete_reasons:
                    result["status"] = "incomplete"
            rows.append(result)
        return {"version": "1", "recordings": rows}

    def _create(self, capture_id, registry, instrument_id, metadata):
        directory = self._root() / capture_id
        directory.mkdir(mode=0o700)
        # Persist the frozen scope, not any later browser/native selection.
        with (directory / "capture.json").open("x") as handle:
            json.dump(
                {
                    "session_id": registry.session_id,
                    "instrument_id": instrument_id,
                    **metadata,
                },
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        for parent in (directory, directory.parent, self.runtime.state_dir):
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return Recorder(
            directory,
            session_id=registry.session_id,
            clock_id=registry.clock_id,
            config=registry.recording_config,
            limits=CAPTURE_LIMITS,
        )

    async def start(self, session, scope="instrument"):
        from .runtime import RuntimeFailure

        async with self.lock:
            if self.closed or self.runtime.closed:
                raise RuntimeFailure(503, "recording service closed")
            if session.mode != "live" or self.runtime.registry is None:
                raise RuntimeFailure(422, "recording requires a live session")
            if scope not in ("instrument", "cohort"):
                raise RuntimeFailure(422, "invalid recording scope")
            if any(item.owner == session.id for item in self.active.values()):
                raise RuntimeFailure(409, "session already has an active recording")
            if len(self.active) >= MAX_ACTIVE or len(self.rows) >= MAX_CAPTURES:
                raise RuntimeFailure(429, "managed recording capacity reached")
            capture_id = uuid4().hex
            row = self._row(capture_id)
            row["label"] += " / " + session.instrument_id
            keys = frozenset(
                key
                for key in session.live_keys
                if scope == "cohort" or str(key.instrument_id) == session.instrument_id
            )
            if not keys:
                raise RuntimeFailure(422, "selected scope has no live streams")
            metadata = {
                "scope": scope,
                "instruments": sorted({str(key.instrument_id) for key in keys}),
                "channels": self._channels(keys),
            }
            try:
                self._validate_scope(
                    scope,
                    metadata["instruments"],
                    metadata["channels"],
                    session.instrument_id,
                )
                if len(keys) > MAX_CAPTURE_CHANNELS:
                    raise ValueError("too many capture subscriptions")
            except ValueError as exc:
                raise RuntimeFailure(
                    422, "recording scope exceeds native bounds"
                ) from exc
            row.update(metadata)
            self.rows[capture_id] = row
            recorder = None
            try:
                recorder = await self.runtime._catalog_work(
                    self._create,
                    capture_id,
                    self.runtime.registry,
                    session.instrument_id,
                    metadata,
                )
                binding = await RecorderBinding.start(
                    self.runtime.registry, recorder, keys
                )
                self.active[capture_id] = ActiveCapture(session.id, recorder, binding)
            except BaseException:
                row.update(status="error", reasons=["capture_start_failed"])
                if recorder is not None:
                    recorder.mark_incomplete("capture_start_failed")
                    await recorder.finalize()
                raise
            return {**self.listing(), "recording_id": capture_id}

    async def _stop(self, capture_id):
        active = self.active[capture_id]
        row = self.rows[capture_id]
        try:
            manifest = await active.binding.stop()
            row.update(
                status="complete" if manifest.complete else "incomplete",
                reasons=list(manifest.incomplete_reasons),
            )
            if manifest.complete:
                descriptor = await self.runtime._catalog_work(
                    self.runtime._capture_descriptor, active.recorder.path
                )
                self._register(row, active.recorder.path, descriptor)
        except Exception:
            active.recorder.mark_incomplete("capture_finalization_failed")
            await active.recorder.finalize()
            row.update(status="error", reasons=["capture_finalization_failed"])
        finally:
            self.active.pop(capture_id, None)

    async def stop(self, session, capture_id):
        from .runtime import RuntimeFailure

        async with self.lock:
            active = self.active.get(capture_id)
            if active is None or active.owner != session.id:
                raise RuntimeFailure(404, "active recording not found for session")
            await self._stop(capture_id)
            return {**self.listing(), "recording_id": capture_id}

    async def close_session(self, session_id):
        async with self.lock:
            for capture_id, active in tuple(self.active.items()):
                if active.owner == session_id:
                    await self._stop(capture_id)

    async def close(self):
        async with self.lock:
            self.closed = True
            for capture_id in tuple(self.active):
                await self._stop(capture_id)
