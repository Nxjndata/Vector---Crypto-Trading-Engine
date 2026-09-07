"""Async database engine, session management, and connectivity verification."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_platform.core.config import DatabaseConfig
from trading_platform.core.exceptions import DatabaseError
from trading_platform.core.logging import get_logger

logger = get_logger("db.session")


def get_async_engine(db_config: DatabaseConfig) -> AsyncEngine:
    """Create configured AsyncEngine for PostgreSQL or SQLite."""
    url = db_config.async_url

    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            echo=db_config.echo,
            connect_args={"check_same_thread": False},
        )

    return create_async_engine(
        url,
        echo=db_config.echo,
        pool_size=db_config.pool_size,
        max_overflow=db_config.max_overflow,
        pool_pre_ping=True,
    )


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create async sessionmaker."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


@asynccontextmanager
async def get_db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager yielding a transactional async session."""
    session: AsyncSession = session_factory()
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Database session rolled back due to error: {e}")
        raise
    finally:
        await session.close()


async def check_database_health(engine: AsyncEngine) -> dict[str, bool | str]:
    """Verify database connection and query basic metadata."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1;"))
            val = result.scalar()
            if val != 1:
                raise DatabaseError(f"Unexpected health check query response: {val}")

            # Check for TimescaleDB extension if PostgreSQL
            is_timescale_available = False
            if engine.dialect.name == "postgresql":
                ext_result = await conn.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'timescaledb';")
                )
                is_timescale_available = ext_result.scalar() is not None

            logger.info(
                f"Database health check passed. Dialect: {engine.dialect.name}, "
                f"TimescaleDB: {is_timescale_available}"
            )
            return {
                "connected": True,
                "dialect": engine.dialect.name,
                "timescaledb": is_timescale_available,
            }
    except Exception as e:
        msg = f"Database health check failed: {e}"
        logger.warning(msg)
        return {
            "connected": False,
            "dialect": engine.dialect.name if hasattr(engine, "dialect") else "unknown",
            "timescaledb": False,
            "error": str(e),
        }
