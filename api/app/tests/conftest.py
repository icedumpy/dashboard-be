# app/tests/conftest.py
import os
os.environ.setdefault("ANYIO_BACKEND", "asyncio")

from pathlib import Path

import sys

API_DIR = Path(__file__).resolve().parents[2] 
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import typing as t
from pathlib import Path

import pytest

from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


from app.main import app

from app.core.db.session import get_db as get_session

from testcontainers.core.container import DockerContainer, LogMessageWaitStrategy

def _start_pg():
    c = (
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_DB", "qc_test")
        .with_env("POSTGRES_USER", "postgres")
        .with_env("POSTGRES_PASSWORD", "postgres")
        .with_exposed_ports(5432)
        .waiting_for(LogMessageWaitStrategy("database system is ready to accept connections"))
    )
    c.start()
    host = c.get_container_host_ip()
    port = int(c.get_exposed_port(5432))
    url = f"postgresql+psycopg2://postgres:postgres@{host}:{port}/qc_test"
    return c, url


def _alembic_config_for_url(db_url: str) -> Config:
    here = Path(__file__).resolve()
    api_root = here.parents[2]  
    ini_path = api_root / "alembic.ini"
    migrations_dir = api_root / 'app' / 'core' / "migrations"

    if not ini_path.exists():
        raise FileNotFoundError(f"alembic.ini not found: {ini_path}")
    if not migrations_dir.exists():
        raise FileNotFoundError(f"migrations folder not found: {migrations_dir}")

    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    # <-- this line fixes your error explicitly
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg


@pytest.fixture(scope="session")
def _pg_container():
    pg = PostgresContainer("postgres:16-alpine")
    pg.start()
    try:
        yield pg
    finally:
        # Always called: pass, fail, or interrupt
        pg.stop()

@pytest.fixture(scope="session")
def _pg_url():
    c, url = _start_pg()
    try:
        yield url
    finally:
        c.stop()


@pytest.fixture(scope="session")
def _migrated_db(_pg_url: str) -> str:
    """Run Alembic migrations once against the container DB."""
    cfg = _alembic_config_for_url(_pg_url)
    command.upgrade(cfg, "head")
    return _pg_url


@pytest.fixture
def _connection(_migrated_db: str) -> t.Iterator[Connection]:
    """Per-test transaction for isolation."""
    engine = create_engine(_migrated_db, future=True, poolclass=NullPool)
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()
        engine.dispose()


@pytest.fixture
def _session_factory(_connection: Connection):
    return sessionmaker(bind=_connection, expire_on_commit=False, future=True)


@pytest.fixture
async def async_client(_session_factory):
    """ASGI client + dependency override."""
    db = _session_factory()

    def _override_get_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_session] = _override_get_session

    async with LifespanManager(app):
        async with AsyncClient(app=app, base_url="http://testserver") as client:
            try:
                yield client
            finally:
                app.dependency_overrides.pop(get_session, None)
                db.close()

@pytest.fixture
async def async_client(_session_factory):
    db = _session_factory()

    def _override_get_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_session] = _override_get_session

    # Try httpx>=0.28 first (supports lifespan arg). If not, fallback.
    try:
        transport = ASGITransport(app=app, lifespan="on")  # httpx >= 0.28
        # Transport handles startup/shutdown; no LifespanManager needed.
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            try:
                yield client
            finally:
                app.dependency_overrides.pop(get_session, None)
                db.close()
    except TypeError:
        # Older httpx: no lifespan arg; manage lifespan ourselves.
        transport = ASGITransport(app=app)  # httpx < 0.28
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                try:
                    yield client
                finally:
                    app.dependency_overrides.pop(get_session, None)
                    db.close()