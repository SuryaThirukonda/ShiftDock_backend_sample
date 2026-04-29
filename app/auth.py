from datetime import datetime, timedelta, timezone
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.exc import UnknownHashError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
http_bearer = HTTPBearer(auto_error=True)


def _credentials_exception(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def hash_pin(plain: str) -> str:
    return pwd_context.hash(plain)


def is_hashed_pin(stored: str | None) -> bool:
    if not stored:
        return False
    return pwd_context.identify(stored) is not None


def verify_pin(plain: str, hashed: str | None) -> bool:
    if hashed is None:
        return False

    if not is_hashed_pin(hashed):
        return secrets.compare_digest(plain, hashed)

    try:
        return pwd_context.verify(plain, hashed)
    except UnknownHashError:
        return secrets.compare_digest(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])

    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise _credentials_exception("Invalid or expired token") from exc


def get_current_employee(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    db: Session = Depends(get_db),
) -> models.Employee:
    payload = decode_token(credentials.credentials)
    employee_id = payload.get("sub")
    if employee_id is None:
        raise _credentials_exception("Token missing subject")

    try:
        employee_id_int = int(employee_id)
    except (TypeError, ValueError) as exc:
        raise _credentials_exception("Invalid token subject") from exc

    employee = (
        db.query(models.Employee)
        .filter(models.Employee.id == employee_id_int, models.Employee.is_active.is_(True))
        .first()
    )
    if employee is None:
        raise _credentials_exception("Employee not found or inactive")

    return employee


def require_manager(current_employee: models.Employee = Depends(get_current_employee)) -> models.Employee:
    is_manager = current_employee.role == models.RoleType.manager
    if not is_manager and not current_employee.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required")
    return current_employee


def require_owner(current_employee: models.Employee = Depends(get_current_employee)) -> models.Employee:
    if not current_employee.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return current_employee


# Compatibility wrappers for older modules.
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return verify_pin(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return hash_pin(password)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    db: Session = Depends(get_db),
) -> models.Employee:
    return get_current_employee(credentials=credentials, db=db)
