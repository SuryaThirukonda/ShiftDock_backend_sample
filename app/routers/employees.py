from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload
from datetime import datetime, timezone
import hashlib
import json

from .. import models, schemas
from ..auth import create_access_token, get_current_employee, hash_pin, require_manager, verify_pin
from ..database import get_db


router = APIRouter()


class EmployeePublic(BaseModel):
    id: int
    name: str
    role: models.RoleType
    roles: list[models.RoleType] = Field(default_factory=list)
    has_upcoming_shift: bool = False

    model_config = {"from_attributes": True}


def _normalize_roles(
    *,
    explicit_roles: list[models.RoleType] | None,
    fallback_role: models.RoleType | None,
) -> list[models.RoleType]:
    incoming = (
        explicit_roles
        if explicit_roles is not None
        else ([fallback_role] if fallback_role is not None else [])
    )
    normalized: list[models.RoleType] = []
    for role in incoming:
        if role not in normalized:
            normalized.append(role)
    return normalized


def _sync_employee_roles(
    *,
    employee: models.Employee,
    db: Session,
    roles: list[models.RoleType],
) -> None:
    existing_by_role = {row.role: row for row in employee.role_assignments}

    for role in roles:
        if role in existing_by_role:
            continue
        db.add(models.EmployeeRole(employee_id=employee.id, role=role))

    for existing_role, row in existing_by_role.items():
        if existing_role in roles:
            continue
        db.delete(row)


@router.get("/public", response_model=list[EmployeePublic])
def list_employees_public(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """No auth required - returns only id/name/role data for active employees."""
    employees = (
        db.query(models.Employee)
        .options(selectinload(models.Employee.role_assignments))
        .filter(models.Employee.is_active.is_(True))
        .order_by(models.Employee.name.asc())
        .all()
    )

    today = datetime.now(timezone.utc).date().isoformat()
    upcoming_employee_ids = {
        int(row[0])
        for row in (
            db.query(models.EmployeeShift.employee_id)
            .join(models.Shift, models.Shift.id == models.EmployeeShift.shift_id)
            .filter(
                models.EmployeeShift.employee_id.isnot(None),
                models.Shift.date >= today,
                models.Shift.status != models.ShiftStatus.completed,
            )
            .distinct()
            .all()
        )
        if row and row[0] is not None
    }

    payload = [
        EmployeePublic(
            id=employee.id,
            name=employee.name,
            role=employee.role,
            roles=employee.roles,
            has_upcoming_shift=employee.id in upcoming_employee_ids,
        )
        for employee in employees
    ]

    # Compute a stable ETag so clients can revalidate with If-None-Match.
    etag_payload = [
        {
            "id": row.id,
            "name": row.name,
            "role": row.role.value if isinstance(row.role, models.RoleType) else str(row.role),
            "roles": [
                role.value if isinstance(role, models.RoleType) else str(role)
                for role in (row.roles or [])
            ],
            "has_upcoming_shift": bool(row.has_upcoming_shift),
        }
        for row in payload
    ]
    etag_source = json.dumps(etag_payload, sort_keys=True, separators=(",", ":"))
    etag = hashlib.sha1(etag_source.encode("utf-8")).hexdigest()
    normalized_if_none_match = request.headers.get("if-none-match", "").replace('"', "").strip()

    response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    response.headers["ETag"] = etag

    if normalized_if_none_match and normalized_if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={
            "Cache-Control": "public, max-age=0, must-revalidate",
            "ETag": etag,
        })

    return payload


class ResetPinRequest(BaseModel):
    new_pin: str

    @field_validator("new_pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        if not value.isdigit() or not 4 <= len(value) <= 8:
            raise ValueError("new_pin must be 4-8 digits")
        return value


class BootstrapOwnerRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    hourly_wage: float = 11.0
    pin: str

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, value: str) -> str:
        if not value.isdigit() or not 4 <= len(value) <= 8:
            raise ValueError("PIN must be 4-8 digits")
        return value

    @field_validator("hourly_wage")
    @classmethod
    def validate_hourly_wage(cls, value: float) -> float:
        if value < 0:
            raise ValueError("hourly_wage must be >= 0")
        return round(float(value), 2)


def _build_token_response(employee: models.Employee) -> schemas.TokenResponse:
    access_token = create_access_token({"sub": employee.id})
    return schemas.TokenResponse(
        access_token=access_token,
        employee=schemas.EmployeeOut.model_validate(employee),
    )


@router.post("/bootstrap-owner", response_model=schemas.TokenResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_owner(
    payload: BootstrapOwnerRequest,
    db: Session = Depends(get_db),
):
    existing_employee = db.query(models.Employee.id).first()
    if existing_employee is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bootstrap is only available when the database has no employees",
        )

    if payload.email:
        duplicate = db.query(models.Employee).filter(models.Employee.email == payload.email).first()
        if duplicate is not None:
            raise HTTPException(status_code=400, detail="Employee with that email already exists")

    employee = models.Employee(
        name=payload.name,
        email=payload.email,
        email_notifications_enabled=True,
        phone=payload.phone,
        hourly_wage=payload.hourly_wage,
        pin=hash_pin(payload.pin),
        role=models.RoleType.owner,
        is_owner=True,
        is_active=True,
    )
    db.add(employee)
    db.flush()
    _sync_employee_roles(employee=employee, db=db, roles=[models.RoleType.owner])
    db.commit()
    db.refresh(employee, attribute_names=["role_assignments"])
    return _build_token_response(employee)


@router.post("/login", response_model=schemas.TokenResponse)
def login_employee(
    payload: schemas.LoginRequest,
    db: Session = Depends(get_db),
):
    employee = (
        db.query(models.Employee)
        .options(selectinload(models.Employee.role_assignments))
        .filter(
            models.Employee.id == payload.employee_id,
            models.Employee.is_active.is_(True),
        )
        .first()
    )
    if employee is None or not verify_pin(payload.pin, employee.pin):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid employee ID or PIN")

    return _build_token_response(employee)


@router.post("/", response_model=schemas.EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: schemas.EmployeeCreate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    if payload.email:
        existing_email = db.query(models.Employee).filter(models.Employee.email == payload.email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Employee with that email already exists")

    normalized_roles = _normalize_roles(explicit_roles=payload.roles, fallback_role=payload.role)
    if not normalized_roles:
        raise HTTPException(status_code=400, detail="At least one role is required")

    employee = models.Employee(
        name=payload.name,
        email=payload.email,
        email_notifications_enabled=payload.email_notifications_enabled,
        phone=payload.phone,
        hourly_wage=payload.hourly_wage,
        pin=hash_pin(payload.pin),
        role=normalized_roles[0],
        is_owner=payload.is_owner,
        is_active=True,
    )
    db.add(employee)
    db.flush()
    _sync_employee_roles(employee=employee, db=db, roles=normalized_roles)
    db.commit()
    db.refresh(employee, attribute_names=["role_assignments"])
    del current_employee

    return employee


@router.get("/", response_model=list[schemas.EmployeeOut])
def list_employees(
    role: models.RoleType | None = None,
    is_active: bool | None = True,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    query = db.query(models.Employee).options(selectinload(models.Employee.role_assignments))
    if role is not None:
        query = query.outerjoin(
            models.EmployeeRole,
            models.EmployeeRole.employee_id == models.Employee.id,
        ).filter(or_(models.Employee.role == role, models.EmployeeRole.role == role))
    if is_active is not None:
        query = query.filter(models.Employee.is_active == is_active)
    return query.distinct().order_by(models.Employee.name.asc()).all()


@router.get("/me", response_model=schemas.EmployeeOut)
def get_my_employee_profile(
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    employee = (
        db.query(models.Employee)
        .options(selectinload(models.Employee.role_assignments))
        .filter(models.Employee.id == current_employee.id)
        .first()
    )
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {current_employee.id} was not found")
    return employee


@router.patch("/me", response_model=schemas.EmployeeOut)
def update_my_employee_profile(
    payload: schemas.EmployeeSelfSettingsUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    update_data = payload.model_dump(exclude_unset=True)

    if "email" in update_data and update_data["email"]:
        duplicate = (
            db.query(models.Employee)
            .filter(
                models.Employee.email == update_data["email"],
                models.Employee.id != current_employee.id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=400, detail="Employee with that email already exists")

    employee = db.query(models.Employee).filter(models.Employee.id == current_employee.id).first()
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {current_employee.id} was not found")

    for field, value in update_data.items():
        setattr(employee, field, value)

    db.commit()
    db.refresh(employee, attribute_names=["role_assignments"])
    return employee


@router.get("/{employee_id}", response_model=schemas.EmployeeOut)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    employee = (
        db.query(models.Employee)
        .options(selectinload(models.Employee.role_assignments))
        .filter(models.Employee.id == employee_id)
        .first()
    )
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} was not found")
    return employee


@router.patch("/{employee_id}", response_model=schemas.EmployeeOut)
def update_employee(
    employee_id: int,
    payload: schemas.EmployeeUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    employee = (
        db.query(models.Employee)
        .options(selectinload(models.Employee.role_assignments))
        .filter(models.Employee.id == employee_id)
        .first()
    )
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} was not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "email" in update_data and update_data["email"]:
        duplicate = (
            db.query(models.Employee)
            .filter(models.Employee.email == update_data["email"], models.Employee.id != employee_id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=400, detail="Employee with that email already exists")

    roles_payload = update_data.pop("roles", None)

    if "pin" in update_data and update_data["pin"] is not None:
        update_data["pin"] = hash_pin(update_data["pin"])

    for field, value in update_data.items():
        setattr(employee, field, value)

    normalized_roles = _normalize_roles(
        explicit_roles=roles_payload,
        fallback_role=update_data.get("role"),
    )
    if roles_payload is not None or "role" in update_data:
        if not normalized_roles:
            raise HTTPException(status_code=400, detail="At least one role is required")
        employee.role = normalized_roles[0]
        _sync_employee_roles(employee=employee, db=db, roles=normalized_roles)

    db.commit()
    db.refresh(employee, attribute_names=["role_assignments"])
    return employee


@router.delete("/{employee_id}")
def soft_delete_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    employee = db.query(models.Employee).filter(models.Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} was not found")

    employee.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{employee_id}/reset-pin")
def reset_employee_pin(
    employee_id: int,
    payload: ResetPinRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    employee = db.query(models.Employee).filter(models.Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} was not found")

    employee.pin = hash_pin(payload.new_pin)
    db.commit()
    return {"ok": True}
