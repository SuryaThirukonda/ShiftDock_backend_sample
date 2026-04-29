from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings
from .db_session_context import DatabaseRequestContext, get_request_db_context


database_url = settings.DATABASE_URL
if not database_url:
    raise RuntimeError("DATABASE_URL must be configured")


connect_args = {}
if "sqlite" in database_url:
    connect_args["check_same_thread"] = False

# Supabase transaction poolers can break on prepared statements with psycopg.
if "pooler.supabase.com" in database_url and "psycopg" in database_url:
    connect_args["prepare_threshold"] = None

engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(Session, "after_begin")
def apply_rls_session_context(session, transaction, connection):
    del transaction

    if connection.dialect.name != "postgresql":
        return

    context = session.info.get("db_request_context") or get_request_db_context()
    if context is None:
        context = DatabaseRequestContext.privileged()

    if context.mode == "privileged":
        return

    role_name = "authenticated" if context.mode == "authenticated" else "anon"
    connection.exec_driver_sql(f"SET LOCAL ROLE {role_name}")
    connection.exec_driver_sql(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (context.claims_json or "{}",),
    )


def get_db():
    db = SessionLocal()
    db.info["db_request_context"] = get_request_db_context() or DatabaseRequestContext.privileged()
    try:
        yield db
    finally:
        db.close()


def get_privileged_db() -> Session:
    """Return a new session with privileged (RLS-bypassing) context.

    Use this for server-side operations that write to tables the calling user
    may not have direct RLS permission on (e.g. inserting manager_alerts on
    behalf of a non-manager employee).

    The caller is responsible for closing the session.
    """
    db = SessionLocal()
    db.info["db_request_context"] = DatabaseRequestContext.privileged()
    return db
