"""Shared immutable inputs; replay state and resources remain test-owned."""

import pytest


@pytest.fixture(scope="session")
def memory_source():
    """Build the unchanged full demo only when a consumer requests it.

    The frozen source holds tuples and read-only mappings, not replay cursors,
    sessions, files or mutable reducer state. Consumers must create those anew.
    """
    from src.workstation.demo_memory import MemoryReplaySource

    return MemoryReplaySource.demo()
