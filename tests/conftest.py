import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

TEST_DATABASE_URL = "postgresql+asyncpg://lab51:lab51_secret@localhost:5439/lab51_auth_test"


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for all tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a fresh engine per test function."""
    import app.models  # noqa: F401 — ensure all models are registered in Base.metadata
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    """Override get_db with test engine, provide HTTP client.
    
    Rate limiting is mocked before importing the app to avoid Redis dependency.
    """
    # Mock rate limiting BEFORE importing app (which imports routers)
    with patch("app.core.rate_limit.check_rate_limit", new=AsyncMock()):
        from app.main import app

        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
            async with test_session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
                finally:
                    await session.close()

        app.dependency_overrides[get_db] = override_get_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        app.dependency_overrides.clear()