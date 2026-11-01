"""Owned render-benchmark child; fingerprint outside measured cohorts only.

SIGUSR1 writes the next atomic runtime snapshot. Normal CLI shutdown writes the
final snapshot. This is tooling, not an application endpoint or feed observer.
"""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import sys
import sysconfig

MAX_MODULES = 8192
MAX_FILES = 4096
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024


def fingerprint() -> dict:
    distributions = {}
    for distribution in importlib.metadata.distributions():
        name, version = distribution.metadata["Name"], distribution.version
        if not name or len(name) > 256 or len(version) > 256:
            raise ValueError("Invalid distribution identity")
        distributions[name] = version
        if len(distributions) > MAX_MODULES:
            raise ValueError("Distribution count bound exceeded")
    executable = Path(sys.executable).resolve(strict=True)
    shared_library = None
    if sysconfig.get_config_var("Py_ENABLE_SHARED"):
        library = sysconfig.get_config_var("LDLIBRARY")
        directory = sysconfig.get_config_var("LIBDIR")
        if not library or not directory:
            raise ValueError("Shared Python library location is unavailable")
        shared_library = (Path(directory) / library).resolve(strict=True)
    modules = tuple(sys.modules.items())
    if len(modules) > MAX_MODULES:
        raise ValueError("Loaded module count bound exceeded")
    paths, virtual = {}, {}
    files = {executable}
    if shared_library is not None:
        files.add(shared_library)
    for name, module in modules:
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename).resolve(strict=True)
            if len(str(path)) > 4096:
                raise ValueError("Loaded module path bound exceeded")
            files.add(path)
            paths[name] = str(path)
        else:
            virtual[name] = getattr(getattr(module, "__spec__", None), "origin", None)
    if len(files) > MAX_FILES:
        raise ValueError("Loaded file count bound exceeded")
    digests, total = {}, 0
    for path in sorted(files):
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not path.is_file() or not 0 <= before.st_size <= MAX_FILE_BYTES:
                raise ValueError("Runtime file is not bounded and regular")
            total += before.st_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Runtime total byte bound exceeded")
            digest = hashlib.file_digest(source, "sha256").hexdigest()
            after = path.stat()
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise ValueError(f"Runtime file changed while hashing: {path}")
            digests[str(path)] = {"sha256": digest, "size_bytes": before.st_size}
    return {
        "schema": "1",
        "pid": os.getpid(),
        "version": sys.version,
        "executable": str(executable),
        "shared_library": str(shared_library) if shared_library else None,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "distributions": distributions,
        "module_paths": paths,
        "virtual_modules": virtual,
        "files": digests,
        "total_file_bytes": total,
        "bounds": {
            "modules": MAX_MODULES,
            "files": MAX_FILES,
            "per_file_bytes": MAX_FILE_BYTES,
            "total_bytes": MAX_TOTAL_BYTES,
            "report_bytes": MAX_REPORT_BYTES,
        },
    }


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Python 3.13 is required")
    root = Path(sys.argv[1]).resolve(strict=True)
    destination = Path(sys.argv[2]).resolve(strict=True)
    sys.path.insert(0, str(root))
    sys.argv = ["src.workstation.cli", *sys.argv[3:]]
    index = 0

    def snapshot(name: str) -> None:
        try:
            value = fingerprint()
            payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
            if len(payload) > MAX_REPORT_BYTES:
                raise ValueError("Runtime report byte bound exceeded")
        except Exception as error:
            payload = json.dumps({"error": str(error)[:8000]}).encode()
        temporary = destination / (name + ".partial")
        with temporary.open("xb") as stream:
            stream.write(payload)
        temporary.replace(destination / name)

    def before_cohort(signum, frame) -> None:
        nonlocal index
        name = f"runtime-{index}.json"
        index += 1
        snapshot(name)

    signal.signal(signal.SIGUSR1, before_cohort)
    print(
        "LOB_BENCHMARK_PROCESS "
        + json.dumps(
            {"executable": sys.executable, "version": sys.version, "root": str(root)}
        ),
        flush=True,
    )
    try:
        from src.workstation.cli import main as run_cli

        return run_cli()
    finally:
        snapshot("runtime-final.json")


if __name__ == "__main__":
    raise SystemExit(main())
