import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_employee, require_manager
from ..database import get_db
from ..services.cache_keys import invalidate_dashboard_and_schedule_cache
from ..services.websocket_manager import manager


router = APIRouter()


def _invalidate_shift_related_cache() -> None:
    invalidate_dashboard_and_schedule_cache()


def _time_to_minutes(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        parts = value.strip().split(":")
        if len(parts) < 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    return hour * 60 + minute


def _validate_shift_time_window(start_time: Optional[str], end_time: Optional[str]) -> None:
    if start_time is None or end_time is None:
        return
    start_minutes = _time_to_minutes(start_time)
    end_minutes = _time_to_minutes(end_time)
    if start_minutes is None or end_minutes is None:
        raise HTTPException(status_code=400, detail="Invalid start/end time values")
    if start_minutes >= end_minutes:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")


def _time_ranges_overlap(
    start_a: Optional[str],
    end_a: Optional[str],
    start_b: Optional[str],
    end_b: Optional[str],
) -> bool:
    start_a_minutes = _time_to_minutes(start_a)
    end_a_minutes = _time_to_minutes(end_a)
    start_b_minutes = _time_to_minutes(start_b)
    end_b_minutes = _time_to_minutes(end_b)
    if None in {start_a_minutes, end_a_minutes, start_b_minutes, end_b_minutes}:
        return False
    return start_a_minutes < end_b_minutes and start_b_minutes < end_a_minutes


def _normalize_shift_name(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _find_conflicting_shift_for_times(
    db: Session,
    date: str,
    start_time: Optional[str],
    end_time: Optional[str],
    name: Optional[str],
    role: Optional[models.RoleType],
    *,
    exclude_shift_id: Optional[int] = None,
) -> Optional[models.Shift]:
    del db, date, start_time, end_time, name, role, exclude_shift_id
    # Overlapping shifts are intentionally allowed, including same-role overlaps.
    return None


def _delete_shift_and_dependencies(db: Session, shift_id: int) -> None:
    ti_ids = [
        row[0]
        for row in db.query(models.TaskInstance.id)
        .filter(models.TaskInstance.shift_id == shift_id)
        .all()
    ]
    if ti_ids:
        db.query(models.AlertEvent).filter(models.AlertEvent.task_instance_id.in_(ti_ids)).delete(
            synchronize_session=False
        )

    db.query(models.AlertEvent).filter(models.AlertEvent.shift_id == shift_id).delete(
        synchronize_session=False
    )

    db.query(models.TaskInstance).filter(models.TaskInstance.shift_id == shift_id).delete(
        synchronize_session=False
    )

    db.query(models.CheckInEvent).filter(models.CheckInEvent.shift_id == shift_id).delete(
        synchronize_session=False
    )

    assignment_ids = [
        row[0]
        for row in db.query(models.ShiftAssignment.id)
        .filter(models.ShiftAssignment.shift_id == shift_id)
        .all()
    ]
    if assignment_ids:
        db.query(models.AlertEvent).filter(
            models.AlertEvent.assignment_id.in_(assignment_ids)
        ).delete(synchronize_session=False)

    db.query(models.ShiftAssignment).filter(models.ShiftAssignment.shift_id == shift_id).delete(
        synchronize_session=False
    )

    es_ids = [
        row[0]
        for row in db.query(models.EmployeeShift.id)
        .filter(models.EmployeeShift.shift_id == shift_id)
        .all()
    ]
    if es_ids:
        db.query(models.TaskCompletion).filter(
            models.TaskCompletion.employee_shift_id.in_(es_ids)
        ).delete(synchronize_session=False)

    db.query(models.EmployeeShift).filter(models.EmployeeShift.shift_id == shift_id).delete(
        synchronize_session=False
    )

    db.query(models.ManagerAlert).filter(models.ManagerAlert.shift_id == shift_id).delete(synchronize_session=False)

    shift = db.query(models.Shift).filter(models.Shift.id == shift_id).first()
    if shift is not None:
        db.delete(shift)


def _delete_shift_or_400(db: Session, shift_id: int) -> None:
    checked_in = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.checked_in_at.isnot(None),
        )
        .first()
    )
    if checked_in is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a shift after an employee has checked in",
        )

    _delete_shift_and_dependencies(db, shift_id)


def _delete_employee_shift_dependencies(db: Session, employee_shift_id: int) -> None:
    """Delete rows that must not outlive an EmployeeShift assignment."""
    db.query(models.TaskCompletion).filter(
        models.TaskCompletion.employee_shift_id == employee_shift_id
    ).delete(synchronize_session=False)

    pool_entries = (
        db.query(models.ShiftPoolEntry)
        .filter(models.ShiftPoolEntry.employee_shift_id == employee_shift_id)
        .all()
    )
    for entry in pool_entries:
        db.delete(entry)

    db.query(models.ShiftSwapRequest).filter(
        or_(
            models.ShiftSwapRequest.offered_shift_id == employee_shift_id,
            models.ShiftSwapRequest.requested_shift_id == employee_shift_id,
        )
    ).delete(synchronize_session=False)


def _effective_assignment_role(employee_shift: models.EmployeeShift) -> Optional[models.RoleType]:
    if employee_shift.role is not None:
        return employee_shift.role
    if employee_shift.employee is not None:
        return employee_shift.employee.role
    return None


def _build_tasks_with_completion(
    db: Session,
    employee_shift: models.EmployeeShift,
    shift: models.Shift,
) -> list[schemas.TaskWithCompletion]:
    effective_role = _effective_assignment_role(employee_shift)
    if effective_role is None:
        return []

    assignment_employee = employee_shift.employee
    if assignment_employee is None:
        assignment_employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == employee_shift.employee_id)
            .first()
        )

    template_query = (
        db.query(models.Task)
        .filter(
            models.Task.is_active.is_(True),
            or_(
                models.Task.scope == models.TaskScope.global_shared,
                models.Task.role == effective_role,
            ),
        )
    )
    templates = template_query.order_by(models.Task.order.asc(), models.Task.id.asc()).all()

    visible_templates: list[models.Task] = []
    for task in templates:
        if task.scope == models.TaskScope.global_shared:
            visible_templates.append(task)
            continue
        if task.role != effective_role:
            continue
        if task.scope == models.TaskScope.individual:
            if task.individual_employee_id != employee_shift.employee_id:
                continue
            if employee_shift.task_group_id is not None and task.task_group_id not in {
                None,
                employee_shift.task_group_id,
            }:
                continue
        visible_templates.append(task)

    completion_rows = (
        db.query(models.TaskCompletion)
        .filter(models.TaskCompletion.employee_shift_id == employee_shift.id)
        .all()
    )
    completion_by_task_id = {completion.task_id: completion for completion in completion_rows}

    shared_task_ids = {
        task.id
        for task in visible_templates
        if task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared}
    }
    shared_completion_by_task_id: dict[int, tuple[models.TaskCompletion, int, str]] = {}
    if shared_task_ids:
        shared_rows = (
            db.query(
                models.TaskCompletion,
                models.Employee.id.label("completed_by_employee_id"),
                models.Employee.name.label("completed_by_name"),
                models.EmployeeShift.role.label("completion_shift_role"),
                models.Employee.role.label("completion_employee_role"),
            )
            .join(
                models.EmployeeShift,
                models.TaskCompletion.employee_shift_id == models.EmployeeShift.id,
            )
            .join(models.Employee, models.EmployeeShift.employee_id == models.Employee.id)
            .filter(
                models.EmployeeShift.shift_id == shift.id,
                models.TaskCompletion.task_id.in_(shared_task_ids),
            )
            .order_by(models.TaskCompletion.completed_at.asc(), models.TaskCompletion.id.asc())
            .all()
        )
        task_scope_by_id = {task.id: task.scope for task in visible_templates}
        for (
            completion,
            completed_by_employee_id,
            completed_by_name,
            completion_shift_role,
            completion_employee_role,
        ) in shared_rows:
            if completion.task_id in shared_completion_by_task_id:
                continue
            task_scope = task_scope_by_id.get(completion.task_id)
            if task_scope is None:
                continue
            completion_role = completion_shift_role or completion_employee_role
            if task_scope == models.TaskScope.role_shared and completion_role != effective_role:
                continue
            shared_completion_by_task_id[completion.task_id] = (
                    completion,
                    completed_by_employee_id,
                    completed_by_name,
                )

    tasks: list[schemas.TaskWithCompletion] = []
    for task in visible_templates:
        completion = completion_by_task_id.get(task.id)
        completed_by_employee_id: int | None = None
        completed_by_name: str | None = None

        if task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared}:
            shared_completion = shared_completion_by_task_id.get(task.id)
            if shared_completion is None:
                completion = None
            else:
                completion, completed_by_employee_id, completed_by_name = shared_completion
        elif completion is not None:
            completed_by_employee_id = employee_shift.employee_id
            completed_by_name = assignment_employee.name if assignment_employee is not None else None

        tasks.append(
            schemas.TaskWithCompletion(
                id=task.id,
                role=task.role,
                title=task.title,
                description=task.description,
                order=task.order,
                is_active=task.is_active,
                is_global=task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared},
                scope=task.scope,
                individual_employee_id=task.individual_employee_id,
                individual_employee_name=(
                    task.individual_employee.name if task.individual_employee is not None else None
                ),
                task_group_id=task.task_group_id,
                task_group_name=task.task_group.name if task.task_group is not None else None,
                completed=completion is not None,
                completed_at=completion.completed_at if completion else None,
                completed_by_manager=completion.completed_by_manager if completion else False,
                completion_notes=completion.notes if completion else None,
                completion_id=completion.id if completion else None,
                completed_by_employee_id=completed_by_employee_id,
                completed_by_name=completed_by_name,
            )
        )

    return tasks


def _build_employee_shift_out(
    db: Session,
    employee_shift: models.EmployeeShift,
    shift: models.Shift,
) -> schemas.EmployeeShiftOut:
    employee = employee_shift.employee
    if employee is None:
        employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == employee_shift.employee_id)
            .first()
        )

    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_shift.employee_id} was not found")

    tasks = _build_tasks_with_completion(db=db, employee_shift=employee_shift, shift=shift)
    effective_role = _effective_assignment_role(employee_shift) or employee.role

    return schemas.EmployeeShiftOut(
        id=employee_shift.id,
        shift_id=employee_shift.shift_id,
        employee_id=employee_shift.employee_id,
        employee_name=employee.name,
        employee_role=effective_role,
        role=effective_role,
        checked_in_at=employee_shift.checked_in_at,
        checked_out_at=employee_shift.checked_out_at,
        is_excused_absence=employee_shift.is_excused_absence,
        absence_reason=employee_shift.absence_reason,
        notes=employee_shift.notes,
        task_group_id=employee_shift.task_group_id,
        task_group_name=employee_shift.task_group.name if employee_shift.task_group is not None else None,
        tasks=tasks,
    )


def _get_shift_or_404(db: Session, shift_id: int) -> models.Shift:
    shift = db.query(models.Shift).filter(models.Shift.id == shift_id).first()
    if shift is None:
        raise HTTPException(status_code=404, detail=f"Shift {shift_id} was not found")
    return shift


@router.post("/", response_model=schemas.ShiftOut, status_code=status.HTTP_201_CREATED)
def create_shift(
    payload: schemas.ShiftCreate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    _validate_shift_time_window(payload.start_time, payload.end_time)
    overlap = _find_conflicting_shift_for_times(
        db,
        payload.date,
        payload.start_time,
        payload.end_time,
        payload.name,
        payload.role,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=409,
            detail="A shift with the same role already overlaps this time",
        )

    shift = models.Shift(
        date=payload.date,
        name=payload.name,
        role=payload.role,
        start_time=payload.start_time,
        end_time=payload.end_time,
        is_override=payload.is_override,
        status=payload.status or models.ShiftStatus.scheduled,
        notes=payload.notes,
    )
    db.add(shift)
    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(shift)
    return shift


@router.get("/", response_model=list[schemas.ShiftOut])
def list_shifts(
    date: str | None = None,
    status: models.ShiftStatus | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 30,
    db: Session = Depends(get_db),
):
    query = db.query(models.Shift)
    if date is not None:
        query = query.filter(models.Shift.date == date)
    if start_date is not None:
        query = query.filter(models.Shift.date >= start_date)
    if end_date is not None:
        query = query.filter(models.Shift.date <= end_date)
    if status is not None:
        query = query.filter(models.Shift.status == status)

    return query.order_by(models.Shift.date.desc(), models.Shift.start_time.desc(), models.Shift.id.desc()).limit(limit).all()


@router.get("/today")
def get_today_shifts(db: Session = Depends(get_db)):
    today = datetime.now().date().isoformat()

    shifts = (
        db.query(models.Shift)
        .filter(models.Shift.date == today)
        .order_by(models.Shift.start_time.asc(), models.Shift.id.asc())
        .all()
    )

    opening = shifts[0] if shifts else None
    closing = shifts[-1] if len(shifts) > 1 else None

    return {
        "opening": schemas.ShiftOut.model_validate(opening).model_dump() if opening else None,
        "closing": schemas.ShiftOut.model_validate(closing).model_dump() if closing else None,
        "shifts": [schemas.ShiftOut.model_validate(shift).model_dump() for shift in shifts],
        "date": today,
    }


@router.get("/range", response_model=list[schemas.ShiftRangeItem])
def get_shifts_in_range(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    shifts = (
        db.query(models.Shift)
        .filter(models.Shift.date >= start_date, models.Shift.date <= end_date)
        .order_by(models.Shift.date.asc(), models.Shift.start_time.asc(), models.Shift.id.asc())
        .all()
    )

    response: list[schemas.ShiftRangeItem] = []
    for shift in shifts:
        assignment_rows = (
            db.query(models.EmployeeShift)
            .filter(models.EmployeeShift.shift_id == shift.id)
            .all()
        )
        my_assignment = next(
            (row for row in assignment_rows if row.employee_id == current_employee.id),
            None,
        )
        response.append(
            schemas.ShiftRangeItem(
                shift=shift,
                employee_count=len(assignment_rows),
                current_employee_shift=(
                    _build_employee_shift_out(db=db, employee_shift=my_assignment, shift=shift)
                    if my_assignment is not None
                    else None
                ),
            )
        )

    return response


@router.get("/my", response_model=list[schemas.MyShiftItem])
def get_my_shifts(
    from_date: str | None = None,
    limit: int = 14,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    effective_from_date = from_date or datetime.now().date().isoformat()
    today = datetime.now().date().isoformat()

    assignment_rows = (
        db.query(models.EmployeeShift)
        .join(models.Shift, models.EmployeeShift.shift_id == models.Shift.id)
        .filter(
            models.EmployeeShift.employee_id == current_employee.id,
            models.Shift.date >= effective_from_date,
        )
        .order_by(models.Shift.date.asc(), models.Shift.start_time.asc(), models.Shift.id.asc())
        .limit(limit)
        .all()
    )

    output: list[schemas.MyShiftItem] = []
    for assignment in assignment_rows:
        shift = assignment.shift
        if shift is None:
            continue

        employee_shift_out = _build_employee_shift_out(db=db, employee_shift=assignment, shift=shift)
        tasks_remaining = len([task for task in (employee_shift_out.tasks or []) if not task.completed])
        output.append(
            schemas.MyShiftItem(
                shift=shift,
                employee_shift=employee_shift_out,
                is_today=shift.date == today,
                tasks_remaining=tasks_remaining,
            )
        )

    return output


@router.post("/batch", response_model=schemas.ShiftBatchCreateResponse)
def create_shifts_batch(
    payload: schemas.ShiftBatchCreateRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    created_models: list[models.Shift] = []
    failed: list[schemas.ShiftBatchFailure] = []

    for index, shift_payload in enumerate(payload.shifts):
        try:
            _validate_shift_time_window(shift_payload.start_time, shift_payload.end_time)
            overlap = _find_conflicting_shift_for_times(
                db,
                shift_payload.date,
                shift_payload.start_time,
                shift_payload.end_time,
                shift_payload.name,
                shift_payload.role,
            )
            if overlap is not None:
                failed.append(
                    schemas.ShiftBatchFailure(
                        index=index,
                        detail="A shift with the same role already overlaps this time",
                        shift=shift_payload,
                    )
                )
                continue

            shift = models.Shift(
                date=shift_payload.date,
                name=shift_payload.name,
                role=shift_payload.role,
                start_time=shift_payload.start_time,
                end_time=shift_payload.end_time,
                is_override=shift_payload.is_override,
                status=shift_payload.status or models.ShiftStatus.scheduled,
                notes=shift_payload.notes,
            )
            db.add(shift)
            db.flush()
            created_models.append(shift)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Invalid shift payload"
            failed.append(
                schemas.ShiftBatchFailure(
                    index=index,
                    detail=detail,
                    shift=shift_payload,
                )
            )

    if created_models:
        db.commit()
        _invalidate_shift_related_cache()
        for row in created_models:
            db.refresh(row)
    else:
        db.rollback()

    return schemas.ShiftBatchCreateResponse(created=created_models, failed=failed)


@router.get("/{shift_id}", response_model=schemas.ShiftOut)
def get_shift(shift_id: int, db: Session = Depends(get_db)):
    return _get_shift_or_404(db=db, shift_id=shift_id)


@router.delete("/{shift_id}")
def delete_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    _get_shift_or_404(db=db, shift_id=shift_id)
    _delete_shift_or_400(db, shift_id)
    db.commit()
    _invalidate_shift_related_cache()
    return {"ok": True}


@router.post("/{shift_id}/delete")
def delete_shift_compat(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    """Compatibility route for environments where DELETE can be blocked by proxy/method filters."""
    del current_employee
    _get_shift_or_404(db=db, shift_id=shift_id)
    _delete_shift_or_400(db, shift_id)
    db.commit()
    _invalidate_shift_related_cache()
    return {"ok": True}


@router.patch("/{shift_id}", response_model=schemas.ShiftOut)
def update_shift(
    shift_id: int,
    payload: schemas.ShiftUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    update_data = payload.model_dump(exclude_unset=True)

    next_start = update_data.get("start_time", shift.start_time)
    next_end = update_data.get("end_time", shift.end_time)
    next_date = update_data.get("date", shift.date)
    next_name = update_data["name"] if "name" in update_data else shift.name
    next_role = update_data["role"] if "role" in update_data else shift.role
    _validate_shift_time_window(next_start, next_end)

    overlap = _find_conflicting_shift_for_times(
        db,
        next_date,
        next_start,
        next_end,
        next_name,
        next_role,
        exclude_shift_id=shift.id,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=409,
            detail="A shift with the same role already overlaps this time",
        )

    for field, value in update_data.items():
        setattr(shift, field, value)

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(shift)
    return shift


@router.patch("/{shift_id}/announcement", response_model=schemas.ShiftOut)
def update_shift_announcement(
    shift_id: int,
    payload: schemas.ShiftAnnouncementUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    """Set or clear the employee-visible shift announcement (max 300 characters)."""
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    shift.announcement = payload.announcement
    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(shift)
    return shift


@router.post("/{shift_id}/assign", response_model=schemas.EmployeeShiftOut)
def assign_employee_to_shift(
    shift_id: int,
    payload: schemas.AssignEmployeeRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    employee = (
        db.query(models.Employee)
        .filter(models.Employee.id == payload.employee_id, models.Employee.is_active.is_(True))
        .first()
    )
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {payload.employee_id} was not found")

    existing_assignment = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.employee_id == payload.employee_id,
        )
        .first()
    )
    if existing_assignment is not None:
        raise HTTPException(status_code=409, detail="Employee already assigned to this shift")

    effective_role = payload.role or shift.role or employee.role
    task_group_id = payload.task_group_id
    if task_group_id is not None:
        task_group = (
            db.query(models.TaskGroup)
            .filter(
                models.TaskGroup.id == task_group_id,
                models.TaskGroup.is_active.is_(True),
            )
            .first()
        )
        if task_group is None:
            raise HTTPException(status_code=404, detail=f"Task group {task_group_id} was not found")
        if task_group.role != effective_role:
            raise HTTPException(
                status_code=400,
                detail="Task group role does not match the assignment role",
            )

    employee_shift = models.EmployeeShift(
        shift_id=shift_id,
        employee_id=payload.employee_id,
        status="assigned",
        assigned_role_id=None,
        role=effective_role,
        task_group_id=task_group_id,
    )
    db.add(employee_shift)
    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(employee_shift)

    return _build_employee_shift_out(db=db, employee_shift=employee_shift, shift=shift)


@router.delete("/{shift_id}/assign/{employee_id}")
def remove_employee_from_shift(
    shift_id: int,
    employee_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    assignment = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.employee_id == employee_id,
        )
        .first()
    )
    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail=f"Employee assignment for shift {shift_id} and employee {employee_id} was not found",
        )

    if assignment.checked_in_at is not None:
        raise HTTPException(status_code=400, detail="Cannot remove employee after check-in")

    _delete_employee_shift_dependencies(db=db, employee_shift_id=assignment.id)
    db.delete(assignment)
    db.commit()
    _invalidate_shift_related_cache()
    return {"ok": True}


@router.get("/{shift_id}/employees", response_model=list[schemas.EmployeeShiftOut])
def list_shift_employees(shift_id: int, db: Session = Depends(get_db)):
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    assignments = (
        db.query(models.EmployeeShift)
        .filter(models.EmployeeShift.shift_id == shift_id)
        .order_by(models.EmployeeShift.id.asc())
        .all()
    )

    return [
        _build_employee_shift_out(db=db, employee_shift=assignment, shift=shift)
        for assignment in assignments
    ]


@router.patch(
    "/{shift_id}/employees/{employee_id}/role",
    response_model=schemas.EmployeeShiftOut,
)
def update_employee_shift_role(
    shift_id: int,
    employee_id: int,
    payload: schemas.UpdateEmployeeShiftRoleRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    assignment = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.employee_id == employee_id,
        )
        .first()
    )
    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail=f"Employee assignment for shift {shift_id} and employee {employee_id} was not found",
        )

    assignment.role = payload.role
    if assignment.task_group_id is not None:
        task_group = (
            db.query(models.TaskGroup)
            .filter(models.TaskGroup.id == assignment.task_group_id)
            .first()
        )
        if task_group is None or task_group.role != payload.role:
            assignment.task_group_id = None

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(assignment)
    return _build_employee_shift_out(db=db, employee_shift=assignment, shift=shift)


@router.patch(
    "/{shift_id}/employees/{employee_id}/task-group",
    response_model=schemas.EmployeeShiftOut,
)
def update_employee_shift_task_group(
    shift_id: int,
    employee_id: int,
    payload: schemas.UpdateEmployeeShiftTaskGroupRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    assignment = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.employee_id == employee_id,
        )
        .first()
    )
    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail=f"Employee assignment for shift {shift_id} and employee {employee_id} was not found",
        )

    if payload.task_group_id is None:
        assignment.task_group_id = None
    else:
        task_group = (
            db.query(models.TaskGroup)
            .filter(
                models.TaskGroup.id == payload.task_group_id,
                models.TaskGroup.is_active.is_(True),
            )
            .first()
        )
        if task_group is None:
            raise HTTPException(status_code=404, detail=f"Task group {payload.task_group_id} was not found")

        effective_role = assignment.role
        if effective_role is None:
            employee = assignment.employee
            if employee is None:
                employee = (
                    db.query(models.Employee)
                    .filter(models.Employee.id == assignment.employee_id)
                    .first()
                )
            if employee is None:
                raise HTTPException(status_code=404, detail=f"Employee {assignment.employee_id} was not found")
            effective_role = employee.role

        if task_group.role != effective_role:
            raise HTTPException(
                status_code=400,
                detail="Task group role does not match this employee's shift role",
            )
        assignment.task_group_id = task_group.id

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(assignment)
    return _build_employee_shift_out(db=db, employee_shift=assignment, shift=shift)


@router.get("/{shift_id}/export")
def export_shift_csv(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    assignments = (
        db.query(models.EmployeeShift)
        .filter(models.EmployeeShift.shift_id == shift_id)
        .order_by(models.EmployeeShift.id.asc())
        .all()
    )

    csv_buffer = StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(
        [
            "shift_id",
            "shift_date",
            "shift_name",
            "start_time",
            "end_time",
            "employee_id",
            "employee_name",
            "assignment_role",
            "checked_in_at",
            "checked_out_at",
            "tasks_completed",
            "tasks_total",
            "completion_pct",
            "incomplete_tasks",
        ]
    )

    for assignment in assignments:
        employee = assignment.employee
        if employee is None:
            employee = (
                db.query(models.Employee)
                .filter(models.Employee.id == assignment.employee_id)
                .first()
            )

        tasks = _build_tasks_with_completion(db=db, employee_shift=assignment, shift=shift)
        tasks_total = len(tasks)
        tasks_completed = len([task for task in tasks if task.completed])
        completion_pct = round((tasks_completed / tasks_total) * 100, 1) if tasks_total > 0 else 0.0
        incomplete_tasks = "; ".join(task.title for task in tasks if not task.completed)

        assignment_role = assignment.role
        if assignment_role is None and employee is not None:
            assignment_role = employee.role

        writer.writerow(
            [
                shift.id,
                shift.date,
                shift.name or "",
                shift.start_time or "",
                shift.end_time or "",
                assignment.employee_id,
                employee.name if employee is not None else f"Unknown employee {assignment.employee_id}",
                assignment_role.value if assignment_role is not None else "",
                assignment.checked_in_at.isoformat() if assignment.checked_in_at else "",
                assignment.checked_out_at.isoformat() if assignment.checked_out_at else "",
                tasks_completed,
                tasks_total,
                completion_pct,
                incomplete_tasks,
            ]
        )

    filename = f"shift-{shift.id}-{shift.date}.csv"
    return StreamingResponse(
        iter([csv_buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{shift_id}/clone", response_model=schemas.ShiftOut, status_code=status.HTTP_201_CREATED)
def clone_shift(
    shift_id: int,
    payload: schemas.CloneShiftRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    source_shift = _get_shift_or_404(db=db, shift_id=shift_id)

    _validate_shift_time_window(source_shift.start_time, source_shift.end_time)
    overlap = _find_conflicting_shift_for_times(
        db,
        payload.target_date,
        source_shift.start_time,
        source_shift.end_time,
        source_shift.name,
        source_shift.role,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=409,
            detail="A shift with the same role already overlaps this time on the target date",
        )

    cloned = models.Shift(
        date=payload.target_date,
        name=source_shift.name,
        role=source_shift.role,
        start_time=source_shift.start_time,
        end_time=source_shift.end_time,
        is_override=True,
        status=models.ShiftStatus.scheduled,
        notes=source_shift.notes,
        announcement=source_shift.announcement,
        recurring_slot_id=source_shift.recurring_slot_id,
    )
    db.add(cloned)
    db.flush()

    source_assignments = (
        db.query(models.EmployeeShift)
        .filter(models.EmployeeShift.shift_id == source_shift.id)
        .all()
    )
    for assignment in source_assignments:
        db.add(
            models.EmployeeShift(
                shift_id=cloned.id,
                employee_id=assignment.employee_id,
                role=assignment.role,
                task_group_id=assignment.task_group_id,
                status="assigned",
            )
        )

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(cloned)
    return cloned


@router.post("/{shift_id}/start", response_model=schemas.ShiftOut)
async def start_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    now = datetime.now(timezone.utc)
    shift.status = models.ShiftStatus.active
    if shift.actual_start is None:
        shift.actual_start = now

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(shift)

    payload = {
        "shift_id": shift.id,
        "status": shift.status.value,
        "actual_start": shift.actual_start.isoformat() if shift.actual_start else None,
        "actual_end": shift.actual_end.isoformat() if shift.actual_end else None,
    }
    await manager.broadcast_to_room(
        room=f"shift_{shift.id}",
        event_type=schemas.WS_SHIFT_UPDATE,
        payload=payload,
    )
    await manager.broadcast_to_managers(event_type=schemas.WS_SHIFT_UPDATE, payload=payload)

    return shift


@router.post("/{shift_id}/complete", response_model=schemas.ShiftOut)
async def complete_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    shift = _get_shift_or_404(db=db, shift_id=shift_id)

    now = datetime.now(timezone.utc)
    shift.status = models.ShiftStatus.completed
    shift.actual_end = now
    if shift.actual_start is None:
        shift.actual_start = now

    db.commit()
    _invalidate_shift_related_cache()
    db.refresh(shift)

    payload = {
        "shift_id": shift.id,
        "status": shift.status.value,
        "actual_start": shift.actual_start.isoformat() if shift.actual_start else None,
        "actual_end": shift.actual_end.isoformat() if shift.actual_end else None,
    }
    await manager.broadcast_to_room(
        room=f"shift_{shift.id}",
        event_type=schemas.WS_SHIFT_UPDATE,
        payload=payload,
    )
    await manager.broadcast_to_managers(event_type=schemas.WS_SHIFT_UPDATE, payload=payload)

    return shift
