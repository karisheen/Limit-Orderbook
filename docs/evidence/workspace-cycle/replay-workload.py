#!/usr/bin/env python3
"""Replay the local reference recording with fixed arguments; emit full JSON."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.benchmark_recording import replay_pass

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / "data/m6-reference/4498db3b-f4a2-4fff-a32e-a97221fa21ee"
report = {
    mode: replay_pass(path, False, 20000, mode, describe=mode == "normalized")
    for mode in ("normalized", "raw")
}
print(json.dumps(report, indent=2))
