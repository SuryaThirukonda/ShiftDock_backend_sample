from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseRequestContext:
    mode: str
    claims_json: str | None = None

    @classmethod
    def privileged(cls) -> "DatabaseRequestContext":
        return cls(mode="privileged")

    @classmethod
    def authenticated(cls, claims_json: str | None = None) -> "DatabaseRequestContext":
        return cls(mode="authenticated", claims_json=claims_json or "{}")

    @classmethod
    def anon(cls, claims_json: str | None = None) -> "DatabaseRequestContext":
        return cls(mode="anon", claims_json=claims_json or "{}")


_request_db_context: ContextVar[DatabaseRequestContext | None] = ContextVar(
    "request_db_context",
    default=None,
)


def get_request_db_context() -> DatabaseRequestContext | None:
    return _request_db_context.get()


def set_request_db_context(context: DatabaseRequestContext | None) -> Token:
    return _request_db_context.set(context)


def reset_request_db_context(token: Token) -> None:
    _request_db_context.reset(token)
