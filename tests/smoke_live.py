"""Manual live smoke test: hits real exchange REST endpoints.

Run directly (not via pytest): .venv/bin/python tests/smoke_live.py
"""
from __future__ import annotations

import sys

from src.data.clients import HttpClient
from src.domain.orderbook_service import ADAPTER_FACTORIES


def main() -> int:
    http = HttpClient()
    failures = 0
    for name, factory in ADAPTER_FACTORIES.items():
        adapter = factory()
        try:
            snap = adapter.fetch_snapshot(http, "BTC")
            print(
                f"OK   {name:<11} {snap.symbol:<9} "
                f"bid={snap.best_bid:,.2f} ask={snap.best_ask:,.2f} "
                f"spread={snap.spread_bps:.2f}bps levels={len(snap.bids)}/{len(snap.asks)}"
            )
        except Exception as exc:
            failures += 1
            print(f"FAIL {name:<11} {type(exc).__name__}: {str(exc)[:120]}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
