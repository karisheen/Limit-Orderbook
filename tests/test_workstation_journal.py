"""Exact authored evidence persists independently from cursor-derived paper state."""

from dataclasses import replace
from decimal import Decimal

import pytest

from src.workstation.journal import JournalEntry, JournalStore
from src.workstation.quality import ClockReading


def entry(**changes):
    return replace(
        JournalEntry(
            "note-1",
            "capture:session:manifest-sha256",
            "ui-session-1",
            27,
            ClockReading("capture-clock", 123456789, 1700000000000000000),
            "venue:spot:BTC-USD",
            "venue",
            "Observed spread widened.\nPaper entry only.",
            entry=Decimal("100.000000000000000001"),
            stop=Decimal("99.125"),
            context="Known-depth bound; no implied liquidity beyond recorded levels.",
            run_id="run-1",
            order_id="order-1",
        ),
        **changes,
    )


def test_exact_entry_linkage_and_authored_text_survive_restart(tmp_path):
    path = tmp_path / "journal.sqlite"
    store = JournalStore(path)
    original = entry()
    assert store.put(original) == original
    store.close()
    reopened = JournalStore(path)
    assert reopened.list(source_id=original.source_id, run_id="run-1") == (original,)
    changed = replace(
        original,
        rationale="Revised interpretation, not revised market history.",
        stop=Decimal("98.75"),
    )
    reopened.put(changed)
    assert reopened.list() == (changed,)
    assert reopened.list()[0].entry == Decimal("100.000000000000000001")
    reopened.close()


def test_filtering_and_bounded_query_are_independent_of_replay_cursor(tmp_path):
    store = JournalStore(tmp_path / "journal.sqlite")
    first = entry()
    second = entry(entry_id="note-2", run_id="run-2", order_id=None, delivery_index=100)
    third = entry(
        entry_id="note-3", source_id="another-capture", run_id=None, order_id=None
    )
    for value in (first, second, third):
        store.put(value)
    assert store.list(limit=2) == (third, second)
    assert store.list(source_id=first.source_id) == (second, first)
    assert store.list(run_id="run-1") == (first,)
    for limit in (0, 1001, True, 1.5):
        with pytest.raises(ValueError):
            store.list(limit=limit)
    store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"rationale": " "},
        {"rationale": "x" * 8193},
        {"context": "x" * 16385},
        {"context": "bad\x00text"},
        {"entry": 1.5},
        {"stop": Decimal("NaN")},
        {"stop": Decimal("0")},
        {"delivery_index": -1},
        {"run_id": None},
        {"session_id": ""},
        {"at": None},
    ],
)
def test_invalid_authored_values_and_unbounded_text_are_rejected(changes):
    with pytest.raises(ValueError):
        entry(**changes)


def test_edit_cannot_reassign_durable_source_identity(tmp_path):
    store = JournalStore(tmp_path / "journal.sqlite")
    original = entry()
    store.put(original)
    with pytest.raises(ValueError, match="source identity"):
        store.put(replace(original, source_id="different-capture"))
    assert store.list() == (original,)
    store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "other-session"},
        {"delivery_index": 28},
        {"at": ClockReading("capture-clock", 123456790, 1700000000000000001)},
        {"instrument_id": "venue:spot:ETH-USD"},
        {"venue": "other-venue"},
        {"run_id": "other-run"},
        {"order_id": "other-order"},
    ],
)
def test_edit_preserves_all_original_provenance(tmp_path, changes):
    store = JournalStore(tmp_path / "journal.sqlite")
    original = entry()
    store.put(original)
    with pytest.raises(ValueError, match="origin provenance"):
        store.put(replace(original, **changes))
    assert store.get(original.entry_id) == original
    store.close()


def test_reopened_edit_retrieves_origin_and_changes_only_authored_fields(tmp_path):
    path = tmp_path / "journal.sqlite"
    store = JournalStore(path)
    original = entry()
    store.put(original)
    store.close()
    reopened = JournalStore(path)
    saved = reopened.get(original.entry_id)
    assert saved == original
    revised = replace(
        saved,
        rationale="New session interpretation",
        entry=Decimal("101.25"),
        stop=Decimal("97.5"),
        context="Revised authored context",
    )
    reopened.put(revised)
    assert reopened.get(original.entry_id) == revised
    assert revised.session_id == "ui-session-1"
    assert revised.delivery_index == original.delivery_index
    assert reopened.get("missing") is None
    for invalid in ("", "x" * 513):
        with pytest.raises(ValueError):
            reopened.get(invalid)
    reopened.close()
