import os

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_UNPOOLED")
    if database_url:
        return database_url
    return "postgresql+psycopg2://localhost:5432/appdb"


engine = None
SessionLocal = None


def _sqlite_strpos(haystack: str | None, needle: str | None) -> int:
    if haystack is None or needle is None:
        return 0
    if needle == "":
        return 1
    index = haystack.find(needle)
    return index + 1 if index >= 0 else 0


def init_engine(database_url: str | None = None):
    global engine, SessionLocal
    url = database_url or get_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def register_sqlite_functions(dbapi_connection, _connection_record):
            dbapi_connection.create_function("strpos", 2, _sqlite_strpos)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine
