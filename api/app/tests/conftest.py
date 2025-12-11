# api/app/tests/conftest.py
import os
os.environ.setdefault("ANYIO_BACKEND", "asyncio")

import sys
from pathlib import Path
API_ROOT = Path(__file__).resolve().parents[2] 
if str(API_ROOT) not in sys.path: 
    sys.path.insert(0, str(API_ROOT))

import pytest_asyncio
import logging
import typing as t

import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


from app.main import app
from app.core.db.session import get_db as get_session

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(levelname)s %(name)s: %(message)s",
)
logging.getLogger("alembic").setLevel(logging.INFO)

# ---------- Paths / sys.path ----------
API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

# ---------- Alembic helpers ----------
def _alembic_config_for_url(db_url: str) -> Config:
    ini_path = API_ROOT / "alembic.ini"
    migrations_dir = API_ROOT / "app" / "core" / "migrations"
    if not ini_path.exists():
        raise FileNotFoundError(f"alembic.ini not found: {ini_path}")
    if not migrations_dir.exists():
        raise FileNotFoundError(f"migrations folder not found: {migrations_dir}")
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg

def _ensure_psycopg2_url(url: str) -> str:
    # Alembic + sync engine should use psycopg2 explicitly
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url

# ---------- Containers & Migration ----------

@pytest.fixture(scope="session", autouse=True)
def _pg_url():
    """
    Single Postgres container for the whole test run.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        yield _ensure_psycopg2_url(pg.get_connection_url())

@pytest.fixture(scope="session", autouse=True)
def _migrated_db(_pg_url: str) -> str:
    log = logging.getLogger("tests.migration")
    log.info("Running alembic upgrade head on %s", _pg_url)
    print("[tests.migration] upgrading to head…", flush=True)

    cfg = _alembic_config_for_url(_pg_url)
    command.upgrade(cfg, "head")  # sync call

    log.info("Alembic upgrade completed.")
    print("[tests.migration] upgrade completed.", flush=True)
    return _pg_url

# ---------- DB Connection, Transaction, Session (per test) ----------
from sqlalchemy.orm import Session as SASession
@pytest.fixture
def db_session(_connection: Connection) -> t.Iterator[SASession]:
    """Per-test Session with SAVEPOINT and auto-reopen on commit."""
    session = SASession(bind=_connection, future=True)

    # 1) begin nested transaction (SAVEPOINT)
    session.begin_nested()

    # 2) re-open SAVEPOINT after each commit in the test
    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        # only restart the SAVEPOINT when the nested transaction ends
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def _connection(_migrated_db: str) -> t.Iterator[Connection]:
    engine = create_engine(_migrated_db, future=True, poolclass=NullPool)
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        # teardown in strict order
        try:
            if trans.is_active:
                trans.rollback()
        finally:
            try:
                conn.close()
            finally:
                engine.dispose()

@pytest.fixture
def session_factory(_connection: Connection):
    """Create a plain SQLAlchemy Session bound to the per-test connection."""
    return sessionmaker(bind=_connection, expire_on_commit=False, future=True)

# ---------- HTTP Client + Dependency Override ----------

@pytest.fixture(scope="session", autouse=True)
def _ping_conftest_loaded():
    logging.getLogger(__name__).info(">>> conftest.py loaded!")

@pytest_asyncio.fixture
async def async_client(db_session: SASession):
    """Async test client; overrides get_db to yield the per-test db_session."""
    def _override_get_session():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override_get_session

    # httpx >= 0.28 supports lifespan arg; also raise_app_exceptions by default
    try:
        transport = ASGITransport(app=app, lifespan="on")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    except TypeError:
        # Older httpx
        transport = ASGITransport(app=app)
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client
    finally:
        app.dependency_overrides.pop(get_session, None)
        # db_session is closed by its own fixture