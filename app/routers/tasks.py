from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_manager
from ..config import settings
from ..database import get_db
from ..services.cache_keys import (
    invalidate_dashboard_and_tasks_cache,
    task_groups_key,
    tasks_list_key,
)
from ..services.cache_service import cache_get_or_set_json


router = APIRouter()


def _invalidate_task_read_cache() -> None:
    invalidate_dashboard_and_tasks_cache()


class ReorderItem(BaseModel):
    id: int
    order: int


class ReorderTasksRequest(BaseModel):
    tasks: list[ReorderItem]


class TemplateRoleOut(BaseModel):
    id: int
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class TemplateRoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _normalize_group_name(value: str) -> str:
    return value.strip()


def _task_group_name_for_index(index: int) -> str:
    label = ""
    cursor = index
    while True:
        label = chr(ord("A") + (cursor % 26)) + label
        cursor = (cursor // 26) - 1
        if cursor < 0:
            break
    return f"Group {label}"


def _to_task_out(task: models.Task) -> schemas.TaskOut:
    return schemas.TaskOut(
        id=task.id,
        role=task.role,
        template_role_id=task.role_id,
        template_role_name=task.legacy_role.name if task.legacy_role is not None else None,
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
    )


def _to_task_group_out(task_group: models.TaskGroup) -> schemas.TaskGroupOut:
    return schemas.TaskGroupOut(
        id=task_group.id,
        role=task_group.role,
        name=task_group.name,
        order=task_group.order,
        is_active=task_group.is_active,
    )


def _get_task_group_or_404(db: Session, group_id: int) -> models.TaskGroup:
    group = db.query(models.TaskGroup).filter(models.TaskGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail=f"Task group {group_id} was not found")
    return group


def _resolve_shift_role_or_400(db: Session, shift: models.Shift) -> models.RoleType:
    if shift.role is not None:
        return shift.role

    assignments = (
        db.query(models.EmployeeShift)
        .filter(models.EmployeeShift.shift_id == shift.id)
        .all()
    )
    inferred_roles: set[models.RoleType] = set()
    for assignment in assignments:
        if assignment.role is not None:
            inferred_roles.add(assignment.role)
            continue

        employee = assignment.employee
        if employee is None:
            employee = (
                db.query(models.Employee)
                .filter(models.Employee.id == assignment.employee_id)
                .first()
            )
        if employee is not None:
            inferred_roles.add(employee.role)

    if len(inferred_roles) == 1:
        return next(iter(inferred_roles))

    raise HTTPException(
        status_code=400,
        detail="Shift role is ambiguous. Set a shift role before splitting tasks.",
    )


def _ensure_task_group_role(task_role: models.RoleType, task_group: models.TaskGroup) -> None:
    if task_group.role != task_role:
        raise HTTPException(
            status_code=400,
            detail="Task group role does not match task role",
        )


def _list_active_groups_for_role(db: Session, role: models.RoleType) -> list[models.TaskGroup]:
    return (
        db.query(models.TaskGroup)
        .filter(
            models.TaskGroup.role == role,
            models.TaskGroup.is_active.is_(True),
        )
        .order_by(models.TaskGroup.order.asc(), models.TaskGroup.id.asc())
        .all()
    )


def _list_active_tasks_for_role(db: Session, role: models.RoleType) -> list[models.Task]:
    return (
        db.query(models.Task)
        .filter(
            models.Task.role == role,
            models.Task.is_active.is_(True),
        )
        .order_by(models.Task.order.asc(), models.Task.id.asc())
        .all()
    )


def _normalize_template_role_name(value: str) -> str:
    return value.strip()


@router.get("/template-roles", response_model=list[TemplateRoleOut])
def list_template_roles(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    query = db.query(models.Role)
    if not include_inactive:
        query = query.filter(models.Role.is_active.is_(True))
    return query.order_by(models.Role.name.asc(), models.Role.id.asc()).all()


@router.post("/template-roles", response_model=TemplateRoleOut, status_code=status.HTTP_201_CREATED)
def create_template_role(
    payload: TemplateRoleCreate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    normalized_name = _normalize_template_role_name(payload.name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="name is required")

    duplicate = (
        db.query(models.Role)
        .filter(
            func.lower(models.Role.name) == normalized_name.lower(),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A template role with this name already exists")

    role = models.Role(
        name=normalized_name,
        description="Task template role",
        is_active=True,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.post("/", response_model=schemas.TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    if (
        payload.scope != models.TaskScope.global_shared
        and payload.role is None
        and payload.template_role_id is None
    ):
        raise HTTPException(status_code=400, detail="role or template_role_id is required")

    task_group_id = payload.task_group_id
    if payload.scope != models.TaskScope.individual and task_group_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Only individual tasks can be assigned to a task group",
        )
    if task_group_id is not None and payload.template_role_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Template-role tasks cannot be assigned to task groups",
        )
    if task_group_id is not None and payload.role is not None:
        task_group = _get_task_group_or_404(db=db, group_id=task_group_id)
        _ensure_task_group_role(payload.role, task_group)

    if payload.template_role_id is not None:
        template_role = (
            db.query(models.Role)
            .filter(
                models.Role.id == payload.template_role_id,
                models.Role.is_active.is_(True),
            )
            .first()
        )
        if template_role is None:
            raise HTTPException(status_code=404, detail=f"Template role {payload.template_role_id} was not found")

    individual_employee_id = payload.individual_employee_id
    if payload.scope == models.TaskScope.individual:
        individual_employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == individual_employee_id, models.Employee.is_active.is_(True))
            .first()
        )
        if individual_employee is None:
            raise HTTPException(status_code=404, detail=f"Employee {individual_employee_id} was not found")
    else:
        individual_employee_id = None

    task = models.Task(
        role=payload.role,
        title=payload.title,
        description=payload.description,
        order=payload.order,
        is_active=True,
        is_global=payload.scope in {models.TaskScope.role_shared, models.TaskScope.global_shared},
        scope=payload.scope,
        individual_employee_id=individual_employee_id,
        task_group_id=task_group_id,
        role_id=payload.template_role_id,
    )

    db.add(task)
    db.commit()
    _invalidate_task_read_cache()
    db.refresh(task)
    return _to_task_out(task)


@router.get("/", response_model=list[schemas.TaskOut])
def list_tasks(
    role: models.RoleType | None = None,
    template_role_id: int | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    cache_key = tasks_list_key(
        role=role.value if role is not None else None,
        template_role_id=template_role_id,
        include_inactive=include_inactive,
    )

    def _build_tasks() -> list[schemas.TaskOut]:
        query = db.query(models.Task)
        if role is not None:
            query = query.filter(models.Task.role == role)
        if template_role_id is not None:
            query = query.filter(models.Task.role_id == template_role_id)
        if not include_inactive:
            query = query.filter(models.Task.is_active.is_(True))

        tasks = query.order_by(models.Task.role.asc(), models.Task.order.asc(), models.Task.id.asc()).all()
        return [_to_task_out(task) for task in tasks]

    return cache_get_or_set_json(
        key=cache_key,
        ttl_seconds=settings.CACHE_TTL_TASKS_SECONDS,
        builder=_build_tasks,
    )


@router.post("/reorder")
def reorder_tasks(
    payload: ReorderTasksRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    if not payload.tasks:
        return {"ok": True}

    ids = [item.id for item in payload.tasks]
    tasks = db.query(models.Task).filter(models.Task.id.in_(ids)).all()
    task_map = {task.id: task for task in tasks}

    missing = [task_id for task_id in ids if task_id not in task_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"Tasks not found: {missing}")

    for item in payload.tasks:
        task_map[item.id].order = item.order

    db.commit()
    _invalidate_task_read_cache()
    return {"ok": True}


@router.get("/groups", response_model=list[schemas.TaskGroupOut])
def list_task_groups(
    role: models.RoleType | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    cache_key = task_groups_key(role=role.value if role is not None else None, include_inactive=include_inactive)

    def _build_task_groups() -> list[schemas.TaskGroupOut]:
        query = db.query(models.TaskGroup)
        if role is not None:
            query = query.filter(models.TaskGroup.role == role)
        if not include_inactive:
            query = query.filter(models.TaskGroup.is_active.is_(True))

        groups = query.order_by(
            models.TaskGroup.role.asc(),
            models.TaskGroup.order.asc(),
            models.TaskGroup.id.asc(),
        ).all()
        return [_to_task_group_out(group) for group in groups]

    return cache_get_or_set_json(
        key=cache_key,
        ttl_seconds=settings.CACHE_TTL_TASKS_SECONDS,
        builder=_build_task_groups,
    )


@router.post("/groups", response_model=schemas.TaskGroupOut, status_code=status.HTTP_201_CREATED)
def create_task_group(
    payload: schemas.TaskGroupCreate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    name = _normalize_group_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    duplicate = (
        db.query(models.TaskGroup)
        .filter(
            models.TaskGroup.role == payload.role,
            func.lower(models.TaskGroup.name) == name.lower(),
            models.TaskGroup.is_active.is_(True),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A task group with this name already exists for this role")

    task_group = models.TaskGroup(
        role=payload.role,
        name=name,
        order=payload.order,
        is_active=True,
    )
    db.add(task_group)
    db.commit()
    _invalidate_task_read_cache()
    db.refresh(task_group)
    return _to_task_group_out(task_group)


@router.patch("/groups/{group_id}", response_model=schemas.TaskGroupOut)
def update_task_group(
    group_id: int,
    payload: schemas.TaskGroupUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    task_group = _get_task_group_or_404(db=db, group_id=group_id)
    updates = payload.model_dump(exclude_unset=True)

    if "name" in updates:
        normalized = _normalize_group_name(updates["name"])
        if not normalized:
            raise HTTPException(status_code=400, detail="name is required")

        duplicate = (
            db.query(models.TaskGroup)
            .filter(
                models.TaskGroup.id != task_group.id,
                models.TaskGroup.role == task_group.role,
                func.lower(models.TaskGroup.name) == normalized.lower(),
                models.TaskGroup.is_active.is_(True),
            )
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="A task group with this name already exists for this role")
        task_group.name = normalized

    if "order" in updates:
        task_group.order = updates["order"]

    if "is_active" in updates:
        task_group.is_active = updates["is_active"]
        if updates["is_active"] is False:
            db.query(models.Task).filter(models.Task.task_group_id == task_group.id).update(
                {models.Task.task_group_id: None},
                synchronize_session=False,
            )
            db.query(models.EmployeeShift).filter(models.EmployeeShift.task_group_id == task_group.id).update(
                {models.EmployeeShift.task_group_id: None},
                synchronize_session=False,
            )
            db.query(models.SchedulePresetSlotEmployee).filter(
                models.SchedulePresetSlotEmployee.task_group_id == task_group.id
            ).update(
                {models.SchedulePresetSlotEmployee.task_group_id: None},
                synchronize_session=False,
            )

    db.commit()
    _invalidate_task_read_cache()
    db.refresh(task_group)
    return _to_task_group_out(task_group)


@router.delete("/groups/{group_id}")
def delete_task_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    task_group = _get_task_group_or_404(db=db, group_id=group_id)

    db.query(models.Task).filter(models.Task.task_group_id == task_group.id).update(
        {models.Task.task_group_id: None},
        synchronize_session=False,
    )
    db.query(models.EmployeeShift).filter(models.EmployeeShift.task_group_id == task_group.id).update(
        {models.EmployeeShift.task_group_id: None},
        synchronize_session=False,
    )
    db.query(models.SchedulePresetSlotEmployee).filter(
        models.SchedulePresetSlotEmployee.task_group_id == task_group.id
    ).update(
        {models.SchedulePresetSlotEmployee.task_group_id: None},
        synchronize_session=False,
    )

    db.delete(task_group)
    db.commit()
    _invalidate_task_read_cache()
    return {"ok": True}


@router.post("/shifts/{shift_id}/auto-split", response_model=schemas.TaskSplitResult)
def auto_split_shift_tasks(
    shift_id: int,
    payload: schemas.AutoSplitShiftTasksRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    shift = db.query(models.Shift).filter(models.Shift.id == shift_id).first()
    if shift is None:
        raise HTTPException(status_code=404, detail=f"Shift {shift_id} was not found")

    role = _resolve_shift_role_or_400(db=db, shift=shift)
    tasks = _list_active_tasks_for_role(db=db, role=role)
    groups = _list_active_groups_for_role(db=db, role=role)

    existing_names = {group.name.lower() for group in groups}
    next_order = (max((group.order for group in groups), default=-1) + 1)
    name_index = 0
    while len(groups) < payload.group_count:
        candidate_name = _task_group_name_for_index(name_index)
        name_index += 1
        if candidate_name.lower() in existing_names:
            continue

        new_group = models.TaskGroup(
            role=role,
            name=candidate_name,
            order=next_order,
            is_active=True,
        )
        next_order += 1
        db.add(new_group)
        db.flush()
        groups.append(new_group)
        existing_names.add(candidate_name.lower())

    groups.sort(key=lambda row: (row.order, row.id))
    target_groups = groups[: payload.group_count]

    if target_groups:
        for idx, task in enumerate(tasks):
            group = target_groups[idx % len(target_groups)]
            task.task_group_id = group.id

    db.commit()
    _invalidate_task_read_cache()
    return schemas.TaskSplitResult(
        shift_id=shift.id,
        role=role,
        group_count=len(target_groups),
        task_count=len(tasks),
    )


@router.post("/shifts/{shift_id}/rebalance", response_model=schemas.TaskSplitResult)
def rebalance_shift_tasks(
    shift_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    shift = db.query(models.Shift).filter(models.Shift.id == shift_id).first()
    if shift is None:
        raise HTTPException(status_code=404, detail=f"Shift {shift_id} was not found")

    role = _resolve_shift_role_or_400(db=db, shift=shift)
    tasks = _list_active_tasks_for_role(db=db, role=role)
    groups = _list_active_groups_for_role(db=db, role=role)
    if not groups:
        raise HTTPException(status_code=400, detail="No task groups found for this role")

    shift_group_ids = {
        row.task_group_id
        for row in db.query(models.EmployeeShift)
        .filter(models.EmployeeShift.shift_id == shift.id)
        .all()
        if row.task_group_id is not None
    }
    if shift_group_ids:
        target_groups = [group for group in groups if group.id in shift_group_ids]
        if not target_groups:
            target_groups = groups
    else:
        target_groups = groups

    for idx, task in enumerate(tasks):
        group = target_groups[idx % len(target_groups)]
        task.task_group_id = group.id

    db.commit()
    _invalidate_task_read_cache()
    return schemas.TaskSplitResult(
        shift_id=shift.id,
        role=role,
        group_count=len(target_groups),
        task_count=len(tasks),
    )


@router.patch("/{task_id}/group", response_model=schemas.TaskOut)
def assign_task_group(
    task_id: int,
    payload: schemas.AssignTaskToGroupRequest,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee

    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")

    if payload.task_group_id is None:
        task.task_group_id = None
    else:
        task_group = _get_task_group_or_404(db=db, group_id=payload.task_group_id)
        _ensure_task_group_role(task.role, task_group)
        task.task_group_id = task_group.id

    db.commit()
    _invalidate_task_read_cache()
    db.refresh(task)
    return _to_task_out(task)


@router.get("/{task_id}", response_model=schemas.TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")
    return _to_task_out(task)


@router.patch("/{task_id}", response_model=schemas.TaskOut)
def update_task(
    task_id: int,
    payload: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "scope" not in update_data and "is_global" in update_data:
        update_data["scope"] = (
            models.TaskScope.role_shared if update_data["is_global"] else models.TaskScope.individual
        )

    next_scope = update_data.get("scope", task.scope)
    next_role = update_data.get("role", task.role)
    next_template_role_id = update_data.get("template_role_id", task.role_id)
    next_individual_employee_id = update_data.get(
        "individual_employee_id",
        task.individual_employee_id,
    )
    next_task_group_id = update_data.get("task_group_id", task.task_group_id)

    if next_scope == models.TaskScope.global_shared:
        next_role = None
        next_template_role_id = None
        next_individual_employee_id = None
        next_task_group_id = None
        update_data["role"] = None
        update_data["template_role_id"] = None
        update_data["individual_employee_id"] = None
        update_data["task_group_id"] = None
    elif next_scope != models.TaskScope.individual and next_task_group_id is not None:
        raise HTTPException(status_code=400, detail="Only individual tasks can be assigned to a task group")
    elif next_role is None and next_template_role_id is None:
        raise HTTPException(status_code=400, detail="role or template_role_id is required")

    if next_template_role_id is not None:
        template_role = (
            db.query(models.Role)
            .filter(
                models.Role.id == next_template_role_id,
                models.Role.is_active.is_(True),
            )
            .first()
        )
        if template_role is None:
            raise HTTPException(status_code=404, detail=f"Template role {next_template_role_id} was not found")

    if next_task_group_id is not None:
        if next_template_role_id is not None:
            raise HTTPException(
                status_code=400,
                detail="Template-role tasks cannot be assigned to task groups",
            )
        task_group = _get_task_group_or_404(db=db, group_id=next_task_group_id)
        if next_role is None:
            raise HTTPException(status_code=400, detail="Task group assignment requires a task role")
        _ensure_task_group_role(next_role, task_group)

    if next_scope == models.TaskScope.individual:
        if next_individual_employee_id is None:
            raise HTTPException(status_code=400, detail="individual_employee_id is required for individual tasks")
        individual_employee = (
            db.query(models.Employee)
            .filter(models.Employee.id == next_individual_employee_id, models.Employee.is_active.is_(True))
            .first()
        )
        if individual_employee is None:
            raise HTTPException(status_code=404, detail=f"Employee {next_individual_employee_id} was not found")
    else:
        next_individual_employee_id = None
        update_data["individual_employee_id"] = None

    update_data["is_global"] = next_scope in {models.TaskScope.role_shared, models.TaskScope.global_shared}
    for field, value in update_data.items():
        setattr(task, field, value)

    db.commit()
    _invalidate_task_read_cache()
    db.refresh(task)
    return _to_task_out(task)


@router.delete("/{task_id}")
def soft_delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_employee: models.Employee = Depends(require_manager),
):
    del current_employee
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} was not found")

    task.is_active = False

    db.commit()
    _invalidate_task_read_cache()
    return {"ok": True}
