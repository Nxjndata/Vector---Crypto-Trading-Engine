"""Pytest shared fixtures and test configuration."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_platform.core.config import AppConfig, load_config
from trading_platform.core.events import EventBus
from trading_platform.core.logging import clear_log_context
from trading_platform.db.base import Base


@pytest.fixture(autouse=True)
def clean_log_context():
    """Ensure log context is wiped between tests."""
    clear_log_context()
    yield
    clear_log_context()


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh in-memory event bus for each test."""
    return EventBus()


@pytest.fixture
def test_config(tmp_path: Path) -> AppConfig:
    """Provide valid base configuration for testing."""
    config = load_config(config_dir="config", env_override="paper")
    # Use in-memory SQLite for test database operations
    db_file = tmp_path / "test_trading.db"
    config.database.sqlite_path = str(db_file)
    return config


@pytest_asyncio.fixture
async def async_test_engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    """Provide isolated in-memory/file async SQLite engine with all tables created."""
    db_file = tmp_path / "test_db.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def async_db_session(async_test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provide transactional async session for database model testing."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()
