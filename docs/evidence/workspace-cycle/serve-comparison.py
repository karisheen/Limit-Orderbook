#!/usr/bin/env python3
"""Descriptive measurement reproducer: loopback built UI, native backend, no live source."""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    dist = args.dist.expanduser().resolve(strict=True)
    if not dist.is_dir() or not (dist / "index.html").is_file():
        parser.error("--dist must contain a built index.html")

    import uvicorn
    from src.workstation.api import create_app
    from src.workstation.runtime import TerminalRuntime

    # No registry, collector, private credentials, wallet, or live source.
    # An optional local verified capture is read-only; synthetic remains available.
    # Temporary runtime state is removed on normal exit or startup failure.
    with TemporaryDirectory(prefix="lob-ui-comparison-") as state:
        runtime = TerminalRuntime(
            captures=(args.capture,) if args.capture else (),
            registry=None,
            enable_live=False,
            state_dir=Path(state),
        )
        app = create_app(
            runtime=runtime,
            frontend_dist=dist,
            allowed_hosts=("127.0.0.1",),
            allowed_origins=(f"http://127.0.0.1:{args.port}",),
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            workers=1,
            access_log=False,
            timeout_graceful_shutdown=10,
        )


if __name__ == "__main__":
    main()
