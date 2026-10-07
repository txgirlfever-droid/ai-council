"""
Test fixtures — mock DB connection and pool.
"""

from __future__ import annotations

import pytest


class MockConnection:
    """Minimal asyncpg.Connection mock for unit tests."""

    def __init__(self):
        self._data: dict = {}
        self.executed: list[tuple] = []

    async def execute(self, query: str, *args):
        self.executed.append((query.strip(), args))

    async def fetchrow(self, query: str, *args):
        return self._data.get("fetchrow")

    async def fetchval(self, query: str, *args):
        return self._data.get("fetchval", 0)

    def transaction(self):
        return MockTransaction()

    def set_return(self, method: str, value):
        self._data[method] = value


class MockTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockUniqueViolation(Exception):
    """Simulates asyncpg.UniqueViolationError."""

    pass


@pytest.fixture
def mock_conn():
    return MockConnection()
