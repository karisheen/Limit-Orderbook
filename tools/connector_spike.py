#!/usr/bin/env python3
"""M0 offline contract fixture and opt-in pinned source audit; stdlib only.

This does not import or execute Cryptofeed/CCXT, nor connect to an exchange.
See docs/SPIKE_METHOD.md for evidence boundaries and reproducible invocations.
"""

import argparse
import base64
import hashlib
import json
import platform
import sys
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


CRYPTOFEED_REV = "61438078e330bc5277f30a4ef241de34b28fab76"
CCXT_REV = "a1bd1dd550c67db735107447b698a34a34c2efa6"
KRAKEN_SPEC = "https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2"
# Kraken's published checksum vector, with decimal strings represented as JSON
# number tokens to exercise precision-preserving decoding of WebSocket v2 data.
KRAKEN_VECTOR = b"""{"channel":"book","type":"snapshot","data":[{
"symbol":"BTC/USD","bids":[
{"price":45283.5,"qty":0.10000000},{"price":45283.4,"qty":1.54582015},
{"price":45282.1,"qty":0.10000000},{"price":45281.0,"qty":0.10000000},
{"price":45280.3,"qty":1.54592586},{"price":45279.0,"qty":0.07990000},
{"price":45277.6,"qty":0.03310103},{"price":45277.5,"qty":0.30000000},
{"price":45277.3,"qty":1.54602737},{"price":45276.6,"qty":0.15445238}],
"asks":[
{"price":45285.2,"qty":0.00100000},{"price":45286.4,"qty":1.54571953},
{"price":45286.6,"qty":1.54571109},{"price":45289.6,"qty":1.54560911},
{"price":45290.2,"qty":0.15890660},{"price":45291.8,"qty":1.54553491},
{"price":45294.7,"qty":0.04454749},{"price":45296.1,"qty":0.35380000},
{"price":45297.5,"qty":0.09945542},{"price":45299.5,"qty":0.18772827}],
"checksum":3310070434}]}"""
# Deliberately synthetic precision stress, NOT an assertion of tradable ticks.
PRECISION_VECTOR = b"""{"price":100.000000000000000001,"qty":0.00100000,
"sequence":9007199254740993}"""

# Markers are evidence navigation aids, not proofs of library correctness.
SOURCE_SPECS = (
    ("cryptofeed", "LICENSE", ("GNU Affero General Public License", "ADDITIONAL TERM")),
    (
        "cryptofeed",
        "pyproject.toml",
        ('requires-python = ">=3.13"', '"Cython>=3.1"', '"order_book>=1.1.0"'),
    ),
    (
        "cryptofeed",
        "cryptofeed/exchanges/kraken.py",
        (
            "parse_float=Decimal",
            "checksum_format='KRAKEN'",
            "raw=msg",
            "checksum=checksum",
        ),
    ),
    (
        "cryptofeed",
        "cryptofeed/capture/recorder.py",
        ("class PcapRecorder", "async def on_ws"),
    ),
    ("ccxt", "LICENSE.txt", ("MIT License",)),
    (
        "ccxt",
        "python/ccxt/pro/kraken.py",
        (
            "'checksum': False",
            "self.safe_number(delta, 'price')",
            "self.safe_number(delta, 'qty')",
            "client.resolve(orderbook, messageHash)",
        ),
    ),
    (
        "ccxt",
        "python/ccxt/async_support/base/ws/client.py",
        ("json_parser.loads", "on_message_callback"),
    ),
)


def reject_constant(value):
    raise ValueError("Non-finite JSON constant: " + value)


def decode(raw):
    return json.loads(raw, parse_float=Decimal, parse_constant=reject_constant)


def decimal_text(value):
    if isinstance(value, float):
        raise ValueError("Authoritative decimal cannot originate from float")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise ValueError("Checksum fixture levels must be finite and positive")
    return format(number, "f")


def checksum(book):
    """Independent implementation of the published Kraken top-10 algorithm."""
    parts = []
    for side, descending in (("asks", False), ("bids", True)):
        levels = sorted(
            book[side], key=lambda row: Decimal(row["price"]), reverse=descending
        )
        for row in levels[:10]:
            for field in ("price", "qty"):
                parts.append(decimal_text(row[field]).replace(".", "").lstrip("0"))
    return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF


def wire_book(book):
    return {
        "symbol": book["symbol"],
        "checksum": str(book["checksum"]),
        "asks": [
            {key: decimal_text(row[key]) for key in ("price", "qty")}
            for row in book["asks"]
        ],
        "bids": [
            {key: decimal_text(row[key]) for key in ("price", "qty")}
            for row in book["bids"]
        ],
    }


def fixture_report():
    book = decode(KRAKEN_VECTOR)["data"][0]
    precision = decode(PRECISION_VECTOR)
    envelope = {
        "schema_version": 1,
        "raw_base64": base64.b64encode(KRAKEN_VECTOR).decode("ascii"),
        "book": wire_book(book),
        "precision": {
            "price": decimal_text(precision["price"]),
            "qty": decimal_text(precision["qty"]),
            "sequence": str(precision["sequence"]),
        },
    }
    stored = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    restored = json.loads(stored)
    raw_restored = base64.b64decode(restored["raw_base64"], validate=True)
    original_crc = checksum(book)
    replay_crc = checksum(restored["book"])
    changed = json.loads(stored)["book"]
    changed["asks"][0]["qty"] = "0.00100001"
    changed_crc = checksum(changed)
    naive = json.loads(PRECISION_VECTOR)
    naive_price = Decimal(str(naive["price"]))
    rejected = []
    for value in (float("inf"), "NaN", "Infinity", "-1", "0"):
        try:
            decimal_text(value)
        except ValueError:
            rejected.append(str(value))
    checks = {
        "published_kraken_crc_matches": original_crc == 3310070434,
        "raw_bytes_roundtrip_exact": raw_restored == KRAKEN_VECTOR,
        "raw_replay_crc_matches": checksum(decode(raw_restored)["data"][0])
        == original_crc,
        "normalized_string_roundtrip_crc_matches": replay_crc == original_crc,
        "decimal_scale_preserved": restored["book"]["asks"][0]["qty"] == "0.00100000",
        "precision_stress_roundtrip_exact": Decimal(restored["precision"]["price"])
        == precision["price"],
        "large_id_transported_as_string": restored["precision"]["sequence"]
        == "9007199254740993",
        "corruption_changes_checksum": changed_crc != original_crc,
        "float_first_counterexample_detected": naive_price != precision["price"],
        "invalid_authoritative_values_rejected": len(rejected) == 5,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "scope": "Independent direct-contract fixture, NOT a production adapter or library runtime test",
        "provenance": {
            "kraken_vector": KRAKEN_SPEC,
            "precision_vector": "synthetic, not a listed product",
        },
        "checks": checks,
        "fixture_sha256": hashlib.sha256(
            KRAKEN_VECTOR + b"\n" + PRECISION_VECTOR
        ).hexdigest(),
        "normalized_envelope_sha256": hashlib.sha256(stored).hexdigest(),
        "expected_crc32": 3310070434,
        "actual_crc32": original_crc,
        "replay_crc32": replay_crc,
        "corrupted_crc32": changed_crc,
        "precision_counterexample": {
            "original": str(precision["price"]),
            "float_first_then_decimal": str(naive_price),
            "note": "Generic lossy-path control; not an executed CCXT result",
        },
    }


def source_report():
    reports = []
    for library, path, markers in SOURCE_SPECS:
        repo, revision = (
            ("bmoscon/cryptofeed", CRYPTOFEED_REV)
            if library == "cryptofeed"
            else ("ccxt/ccxt", CCXT_REV)
        )
        url = "https://raw.githubusercontent.com/{}/{}/{}".format(repo, revision, path)
        entry = {"library": library, "revision": revision, "path": path, "url": url}
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "limit-orderbook-m0-source-audit"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise ValueError("Source exceeds bounded audit size")
            lines = payload.decode("utf-8").splitlines()
            found = {
                marker: [n for n, line in enumerate(lines, 1) if marker in line]
                for marker in markers
            }
            entry.update(
                {
                    "status": "markers_found"
                    if all(found.values())
                    else "review_required",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "marker_lines": found,
                }
            )
        except (OSError, urllib.error.URLError, UnicodeError, ValueError) as error:
            entry.update({"status": "unavailable", "error": str(error)})
        reports.append(entry)
    return {
        "mode": "Pinned source/API inspection only; source is never imported or executed",
        "status": "inspected"
        if all(row["status"] == "markers_found" for row in reports)
        else "incomplete",
        "files": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inspect-sources",
        action="store_true",
        help="Opt in to HTTPS reads of pinned primary source files",
    )
    parser.add_argument(
        "--output", type=Path, help="Also save the JSON report at this path"
    )
    args = parser.parse_args()
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "fixtures": fixture_report(),
        "library_runtime_tests": "not_run; neither competing ingestion stack is installed by this spike",
        "sources": source_report()
        if args.inspect_sources
        else {"status": "not_requested"},
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return (
        0
        if report["fixtures"]["status"] == "passed"
        and report["sources"]["status"] in ("inspected", "not_requested")
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
