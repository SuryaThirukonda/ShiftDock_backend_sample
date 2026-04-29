from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings
from .db_session_context import DatabaseRequestContext, get_request_db_context


database_url = settings.DATABASE_URL
if not database_url:
    raise RuntimeError("DATABASE_URL must be configured")

if not database_url.startswith("sqlite"):
    raise RuntimeError("Only sqlite DATABASE_URL values are supported in this backend sample")

connect_args = {"check_same_thread": False}

engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(Session, "after_begin")
def apply_rls_session_context(session, transaction, connection):
    del session, transaction, connection


def get_db():
    db = SessionLocal()
    db.info["db_request_context"] = get_request_db_context() or DatabaseRequestContext.privileged()
    try:
        yield db
    finally:
        db.close()


def get_privileged_db() -> Session:
    """Return a new SQLite session.

    The caller is responsible for closing the session.
    """
    db = SessionLocal()
    db.info["db_request_context"] = DatabaseRequestContext.privileged()
    return db
