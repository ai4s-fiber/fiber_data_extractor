import os
import socket
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings

db_url = settings.DATABASE_URL

def is_postgres_running(url: str) -> bool:
    """Helper to ping PostgreSQL port to avoid blocking or exceptions on connect."""
    if "postgresql" not in url:
        return False
    host = "localhost"
    port = 5432
    try:
        parts = url.split("@")
        if len(parts) > 1:
            host_port_part = parts[1].split("/")[0]
            if ":" in host_port_part:
                host, port_str = host_port_part.split(":")
                port = int(port_str)
            else:
                host = host_port_part
    except Exception:
        pass

    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def _enable_sqlite_wal(dbapi_connection, connection_record):
    """Enable WAL mode and set busy timeout for SQLite connections."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

# Smart routing
if is_postgres_running(db_url):
    print("PostgreSQL detected on port. Initializing high-performance PostgreSQL engine.")
    engine = create_async_engine(
        db_url,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
    )
else:
    print("PostgreSQL not accessible. Automatically falling back to local SQLite engine (local_dev_fallback.db).")
    sqlite_fallback_url = "sqlite+aiosqlite:///./local_dev_fallback.db"
    engine = create_async_engine(
        sqlite_fallback_url,
        echo=settings.DEBUG,
        connect_args={
            "timeout": 30,
        },
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_wal)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)




async def get_db() -> AsyncSession:
    """Dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
