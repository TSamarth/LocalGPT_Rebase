"""
Shared pytest fixtures for all tests.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client

from app.server import mcp


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    """Provide a FastMCP in-memory client for tool/resource/prompt tests."""
    async with Client(mcp) as c:
        yield c

