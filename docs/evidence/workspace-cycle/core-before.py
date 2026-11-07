#!/usr/bin/env python3
"""Run the existing core benchmark with the verified historical book module."""

import hashlib
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
BASELINE_REVISION = "49f2d0a4bb07a4d0816c0644890756d740b3cc7d"
ORIGINAL_BOOK_SHA256 = (
    "f908201f41719b1d7726af4a38df0a3a166660c7312d21c4e9428e63053e4a74"
)
sys.path.insert(0, str(ROOT))


def load_baseline(name):
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_REVISION}:src/workstation/book.py"],
        cwd=ROOT,
    )
    digest = hashlib.sha256(source).hexdigest()
    if digest != ORIGINAL_BOOK_SHA256:
        raise RuntimeError(f"Original book SHA256 mismatch: {digest}")
    module = ModuleType(name)
    # The benchmark reads __file__ to hash the current checkout, not this source.
    # ORIGINAL_BOOK_SHA256 above is authoritative for the executed book module.
    module.__file__ = str(ROOT / "src/workstation/book.py")
    module.__package__ = "src.workstation"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


if __name__ == "__main__":
    import src.workstation

    src.workstation.book = load_baseline("src.workstation.book")
    print(
        f"Executed book: {BASELINE_REVISION}:src/workstation/book.py "
        f"SHA256={ORIGINAL_BOOK_SHA256}; benchmark core_source_sha256 "
        "hashes the current checkout instead.",
        file=sys.stderr,
    )
    runpy.run_path(str(ROOT / "tools/benchmark_core.py"), run_name="__main__")
