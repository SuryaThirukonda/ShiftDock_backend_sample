from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_employee, verify_pin
from ..database import get_db


router = APIRouter()


def _is_manager_or_owner(employee: models.Employee) -> bool:
    return employee.role == models.RoleType.manager or employee.is_owner


def _get_shift_or_404(db: Session, shift_id: int) -> models.Shift:
    shift = db.query(models.Shift).filter(models.Shift.id == shift_id).first()
    if shift is None:
        raise HTTPException(status_code=404, detail=f"Shift {shift_id} was not found")
    return shift


def _get_employee_or_404(db: Session, employee_id: int) -> models.Employee:
    employee = (
        db.query(models.Employee)
        .filter(models.Employee.id == employee_id, models.Employee.is_active.is_(True))
        .first()
    )
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} was not found")
    return employee


def _get_employee_shift_or_404(db: Session, shift_id: int, employee_id: int) -> models.EmployeeShift:
    employee_shift = (
        db.query(models.EmployeeShift)
        .filter(
            models.EmployeeShift.shift_id == shift_id,
            models.EmployeeShift.employee_id == employee_id,
        )
        .first()
    )
    if employee_shift is None:
        raise HTTPException(
            status_code=404,
            detail=f"Employee {employee_id} is not assigned to shift {shift_id}",
        )
    return employee_shift


def _build_tasks_with_completion(
    db: Session,
    employee_shift: models.EmployeeShift,
    shift: models.Shift,
) -> list[schemas.TaskWithCompletion]:
    employee = employee_shift.employee
    if employee is None:
        employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == employee_shift.employee_id)
            .first()
        )
    if employee is None:
        return []

    effective_role = employee_shift.role or employee.role
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
            completed_by_name = employee.name

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
    employee = employee_shift.employee or _get_employee_or_404(db=db, employee_id=employee_shift.employee_id)
    tasks = _build_tasks_with_completion(db=db, employee_shift=employee_shift, shift=shift)

    return schemas.EmployeeShiftOut(
        id=employee_shift.id,
        shift_id=employee_shift.shift_id,
        employee_id=employee_shift.employee_id,
        employee_name=employee.name,
        employee_role=employee_shift.role or employee.role,
        role=employee_shift.role or employee.role,
        checked_in_at=employee_shift.checked_in_at,
        checked_out_at=employee_shift.checked_out_at,
        is_excused_absence=employee_shift.is_excused_absence,
        absence_reason=employee_shift.absence_reason,
        notes=employee_shift.notes,
        task_group_id=employee_shift.task_group_id,
        task_group_name=employee_shift.task_group.name if employee_shift.task_group is not None else None,
        tasks=tasks,
    )


def _enforce_task_access(
    current_employee: models.Employee,
    target_employee_id: int,
) -> None:
    if current_employee.id == target_employee_id:
        return
    if _is_manager_or_owner(current_employee):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for this employee")


@router.post("/shifts/{shift_id}/checkin", response_model=schemas.EmployeeShiftOut)
async def check_in(
    shift_id: int,
    payload: schemas.CheckInRequest,
    db: Session = Depends(get_db),
):
    employee = _get_employee_or_404(db=db, employee_id=payload.employee_id)
    if not verify_pin(payload.pin, employee.pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")

    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    employee_shift = _get_employee_shift_or_404(db=db, shift_id=shift_id, employee_id=employee.id)

    if employee_shift.status == "dropped":
        raise HTTPException(status_code=409, detail="This shift was dropped to the shift pool")

    if employee_shift.checked_in_at is not None:
        raise HTTPException(status_code=400, detail="Employee already checked in")

    now = datetime.now(timezone.utc)
    employee_shift.checked_in_at = now
    employee_shift.status = "checked_in"
    employee_shift.is_no_show = False
    if employee_shift.role is None:
        employee_shift.role = employee.role

    if shift.status == models.ShiftStatus.scheduled:
        shift.status = models.ShiftStatus.active
        if shift.actual_start is None:
            shift.actual_start = now

    if shift.actual_start is None:
        shift.actual_start = now

    db.commit()
    db.refresh(employee_shift)
    db.refresh(shift)

    return _build_employee_shift_out(db=db, employee_shift=employee_shift, shift=shift)


@router.post("/shifts/{shift_id}/checkout", response_model=schemas.EmployeeShiftOut)
async def check_out(
    shift_id: int,
    payload: schemas.CheckOutRequest,
    db: Session = Depends(get_db),
):
    employee = _get_employee_or_404(db=db, employee_id=payload.employee_id)
    if not verify_pin(payload.pin, employee.pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")

    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    employee_shift = _get_employee_shift_or_404(db=db, shift_id=shift_id, employee_id=employee.id)

    if employee_shift.checked_in_at is None:
        raise HTTPException(status_code=400, detail="Employee has not checked in")
    if employee_shift.checked_out_at is not None:
        raise HTTPException(status_code=400, detail="Employee already checked out")

    now = datetime.now(timezone.utc)
    employee_shift.checked_out_at = now
    employee_shift.notes = payload.notes
    employee_shift.status = "checked_out"

    db.commit()
    db.refresh(employee_shift)

    return _build_employee_shift_out(db=db, employee_shift=employee_shift, shift=shift)


@router.post(
    "/shifts/{shift_id}/employees/{employee_id}/tasks/{task_id}/complete",
    response_model=schemas.TaskWithCompletion,
)
async def complete_task(
    shift_id: int,
    employee_id: int,
    task_id: int,
    payload: schemas.CompleteTaskRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    _enforce_task_access(current_employee=current_employee, target_employee_id=employee_id)

    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    employee_shift = _get_employee_shift_or_404(db=db, shift_id=shift_id, employee_id=employee_id)

    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")

    target_employee = employee_shift.employee
    if target_employee is None:
        target_employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == employee_shift.employee_id)
            .first()
        )
    if target_employee is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_shift.employee_id} was not found")

    effective_role = employee_shift.role or target_employee.role
    if task.scope != models.TaskScope.global_shared and task.role != effective_role:
        raise HTTPException(
            status_code=400,
            detail="Task does not belong to this employee role",
        )
    if task.scope == models.TaskScope.individual and task.individual_employee_id != employee_shift.employee_id:
        raise HTTPException(status_code=403, detail="Task is assigned to a different employee")

    if employee_shift.task_group_id is not None and task.scope == models.TaskScope.individual:
        if task.task_group_id not in {None, employee_shift.task_group_id}:
            raise HTTPException(
                status_code=403,
                detail="Task does not belong to the employee's assigned task group",
            )

    completed_by_employee_id = employee_id
    completed_by_name = target_employee.name

    existing = None
    if task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared}:
        shared_query = (
            db.query(
                models.TaskCompletion,
                models.Employee.id.label("completed_by_employee_id"),
                models.Employee.name.label("completed_by_name"),
            )
            .join(
                models.EmployeeShift,
                models.TaskCompletion.employee_shift_id == models.EmployeeShift.id,
            )
            .join(models.Employee, models.EmployeeShift.employee_id == models.Employee.id)
            .filter(
                models.EmployeeShift.shift_id == shift.id,
                models.TaskCompletion.task_id == task_id,
            )
            .order_by(models.TaskCompletion.completed_at.asc(), models.TaskCompletion.id.asc())
        )
        if task.scope == models.TaskScope.role_shared:
            shared_query = shared_query.filter(
                or_(
                    models.EmployeeShift.role == effective_role,
                    and_(
                        models.EmployeeShift.role.is_(None),
                        models.Employee.role == effective_role,
                    ),
                )
            )

        existing_shared = shared_query.first()
        if existing_shared is not None:
            existing, completed_by_employee_id, completed_by_name = existing_shared
    else:
        existing = (
            db.query(models.TaskCompletion)
            .filter(
                models.TaskCompletion.employee_shift_id == employee_shift.id,
                models.TaskCompletion.task_id == task_id,
            )
            .first()
        )

    if existing is not None:
        return schemas.TaskWithCompletion(
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
            completed=True,
            completed_at=existing.completed_at,
            completed_by_manager=existing.completed_by_manager,
            completion_notes=existing.notes,
            completion_id=existing.id,
            completed_by_employee_id=completed_by_employee_id,
            completed_by_name=completed_by_name,
        )

    completion = models.TaskCompletion(
        employee_shift_id=employee_shift.id,
        task_id=task_id,
        completed_by_manager=_is_manager_or_owner(current_employee) and current_employee.id != employee_id,
        notes=payload.notes,
    )
    db.add(completion)
    db.commit()
    db.refresh(completion)

    task_out = schemas.TaskWithCompletion(
        id=task.id,
        role=task.role,
        title=task.title,
        description=task.description,
        order=task.order,
        is_active=task.is_active,
        is_global=task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared},
        scope=task.scope,
        individual_employee_id=task.individual_employee_id,
        individual_employee_name=task.individual_employee.name if task.individual_employee is not None else None,
        task_group_id=task.task_group_id,
        task_group_name=task.task_group.name if task.task_group is not None else None,
        completed=True,
        completed_at=completion.completed_at,
        completed_by_manager=completion.completed_by_manager,
        completion_notes=completion.notes,
        completion_id=completion.id,
        completed_by_employee_id=completed_by_employee_id,
        completed_by_name=completed_by_name,
    )

    return task_out


@router.delete("/shifts/{shift_id}/employees/{employee_id}/tasks/{task_id}/complete")
async def uncomplete_task(
    shift_id: int,
    employee_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    _enforce_task_access(current_employee=current_employee, target_employee_id=employee_id)

    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    employee_shift = _get_employee_shift_or_404(db=db, shift_id=shift_id, employee_id=employee_id)

    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")

    assignment_employee = employee_shift.employee
    if assignment_employee is None:
        assignment_employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == employee_shift.employee_id)
            .first()
        )
    effective_role = employee_shift.role or (assignment_employee.role if assignment_employee is not None else None)

    if task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared}:
        completion_query = (
            db.query(models.TaskCompletion)
            .join(
                models.EmployeeShift,
                models.TaskCompletion.employee_shift_id == models.EmployeeShift.id,
            )
            .filter(
                models.EmployeeShift.shift_id == shift_id,
                models.TaskCompletion.task_id == task_id,
            )
            .order_by(models.TaskCompletion.completed_at.asc(), models.TaskCompletion.id.asc())
        )
        if task.scope == models.TaskScope.role_shared:
            if effective_role is None:
                raise HTTPException(status_code=400, detail="Unable to determine role for this shift assignment")
            completion_query = completion_query.join(
                models.Employee,
                models.EmployeeShift.employee_id == models.Employee.id,
            ).filter(
                or_(
                    models.EmployeeShift.role == effective_role,
                    and_(
                        models.EmployeeShift.role.is_(None),
                        models.Employee.role == effective_role,
                    ),
                )
            )
        completion = completion_query.first()
    else:
        completion = (
            db.query(models.TaskCompletion)
            .filter(
                models.TaskCompletion.employee_shift_id == employee_shift.id,
                models.TaskCompletion.task_id == task_id,
            )
            .first()
        )

    if completion is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Task completion for task {task_id}, employee {employee_id}, "
                f"and shift {shift_id} was not found"
            ),
        )

    if task.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared} and not _is_manager_or_owner(current_employee):
        completion_employee_shift = (
            db.query(models.EmployeeShift)
            .filter(models.EmployeeShift.id == completion.employee_shift_id)
            .first()
        )
        if completion_employee_shift is None or completion_employee_shift.employee_id != current_employee.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the completer or a manager can undo this shared task",
            )

    db.delete(completion)
    db.commit()

    return {"ok": True}


@router.get("/shifts/{shift_id}/employees/{employee_id}/tasks", response_model=list[schemas.TaskWithCompletion])
def list_employee_tasks(
    shift_id: int,
    employee_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(get_current_employee),
):
    _enforce_task_access(current_employee=current_employee, target_employee_id=employee_id)

    shift = _get_shift_or_404(db=db, shift_id=shift_id)
    employee_shift = _get_employee_shift_or_404(db=db, shift_id=shift_id, employee_id=employee_id)
    return _build_tasks_with_completion(db=db, employee_shift=employee_shift, shift=shift)
