import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/appdb")


engine = None
SessionLocal = None


def init_engine(database_url: str | None = None):
    global engine, SessionLocal
    url = database_url or get_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine
