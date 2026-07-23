import socket
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings

db_url = settings.DATABASE_URL


def is_postgres_running(url: str) -> bool:
    """Helper to ping PostgreSQL port to avoid blocking or exceptions on connect."""
    if "postgresql" not in url:
        return False
    try:
        parsed = make_url(url)
    except Exception:
        return False

    host = parsed.host or "localhost"
    port = parsed.port or 5432

    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _enable_sqlite_wal(dbapi_connection, _connection_record):
    """Enable WAL mode and set busy timeout for SQLite connections."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=120000")  # 120s - extraction holds DB for minutes
    # Do not switch journal_mode on every async connection; that can block while
    # another connection has the SQLite WAL files open. Existing local DBs are
    # already WAL, and startup schema repair can run without changing it here.
    cursor.execute("PRAGMA synchronous=NORMAL")  # Better perf with WAL
    cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor.close()

# Smart routing — production must not silently fall back to SQLite
postgres_reachable = is_postgres_running(db_url)
_use_sqlite = (
    settings.ALLOW_SQLITE_FALLBACK
    and "postgresql" in db_url
    and not postgres_reachable
)
if postgres_reachable or ("postgresql" in db_url and not settings.ALLOW_SQLITE_FALLBACK):
    if not postgres_reachable:
        raise RuntimeError(
            "PostgreSQL is configured but unreachable. "
            "Refusing to start (set ALLOW_SQLITE_FALLBACK=true only for local dev)."
        )
    print("PostgreSQL detected on port. Initializing high-performance PostgreSQL engine.")
    engine = create_async_engine(
        db_url,
        echo=settings.DEBUG,
        pool_size=5,
        max_overflow=10,
    )
elif _use_sqlite:
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
elif "sqlite" in db_url:
    engine = create_async_engine(
        db_url,
        echo=settings.DEBUG,
        connect_args={"timeout": 30},
    )
    event.listen(engine.sync_engine, "connect", _enable_sqlite_wal)
else:
    raise RuntimeError(f"Unsupported or unreachable database configuration: {db_url}")

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


async def close_database() -> None:
    """Release pooled connections and SQLite worker threads on shutdown."""
    await engine.dispose()
