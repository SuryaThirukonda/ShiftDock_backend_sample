from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .models import (
    AlertType,
    AvailabilityApprovalStatus,
    MessageSenderType,
    RoleType,
    TaskScope,
    TemporaryLeaveStatus,
    ShiftPoolClaimMode,
    ShiftStatus,
)

# -----------------------------------------------------------------------------
# Prompt 3 Schemas
# -----------------------------------------------------------------------------


class EmployeeOut(BaseModel):
    id: int
    name: str
    email: Optional[EmailStr] = None
    email_notifications_enabled: bool = True
    phone: Optional[str] = None
    hourly_wage: float = 11.0
    role: RoleType
    roles: list[RoleType] = Field(default_factory=list)
    is_active: bool
    is_owner: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmployeeBrief(BaseModel):
    id: int
    name: str
    role: RoleType
    roles: list[RoleType] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    employee_id: int
    pin: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeOut

    model_config = ConfigDict(from_attributes=True)


class EmployeeCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    email_notifications_enabled: bool = True
    phone: Optional[str] = None
    hourly_wage: float = 11.0
    pin: str
    role: RoleType
    roles: Optional[list[RoleType]] = None
    is_owner: bool = False

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

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: Optional[list[RoleType]]) -> Optional[list[RoleType]]:
        if value is None:
            return value
        if not value:
            raise ValueError("roles must include at least one role")
        unique_roles: list[RoleType] = []
        for role in value:
            if role not in unique_roles:
                unique_roles.append(role)
        return unique_roles


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    email_notifications_enabled: Optional[bool] = None
    phone: Optional[str] = None
    hourly_wage: Optional[float] = None
    pin: Optional[str] = None
    role: Optional[RoleType] = None
    roles: Optional[list[RoleType]] = None
    is_owner: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("pin")
    @classmethod
    def validate_optional_pin(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.isdigit() or not 4 <= len(value) <= 8:
            raise ValueError("PIN must be 4-8 digits")
        return value

    @field_validator("hourly_wage")
    @classmethod
    def validate_optional_hourly_wage(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return value
        if value < 0:
            raise ValueError("hourly_wage must be >= 0")
        return round(float(value), 2)

    @field_validator("roles")
    @classmethod
    def validate_optional_roles(cls, value: Optional[list[RoleType]]) -> Optional[list[RoleType]]:
        if value is None:
            return value
        if not value:
            raise ValueError("roles must include at least one role")
        unique_roles: list[RoleType] = []
        for role in value:
            if role not in unique_roles:
                unique_roles.append(role)
        return unique_roles


class EmployeeSelfSettingsUpdate(BaseModel):
    email: Optional[EmailStr] = None
    email_notifications_enabled: Optional[bool] = None


class TaskCreate(BaseModel):
    role: Optional[RoleType] = None
    template_role_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    order: int = 0
    is_global: bool = False
    scope: Optional[TaskScope] = None
    individual_employee_id: Optional[int] = None
    task_group_id: Optional[int] = None

    # Compatibility fields for current routers.
    role_id: Optional[int] = None
    category: Optional[str] = "service"
    priority: Optional[str] = "medium"
    estimated_minutes: Optional[int] = None
    is_required: Optional[bool] = True
    alert_on_miss: Optional[bool] = True
    due_offset_minutes: Optional[int] = None

    @model_validator(mode="after")
    def normalize_role(self) -> "TaskCreate":
        if self.scope is None:
            self.scope = TaskScope.role_shared
        self.is_global = self.scope in {TaskScope.role_shared, TaskScope.global_shared}

        if self.scope == TaskScope.global_shared:
            self.role = None
            self.template_role_id = None
            self.individual_employee_id = None
            self.task_group_id = None
            return self

        if self.role is None:
            legacy_role_map = {
                1: RoleType.owner,
                2: RoleType.manager,
                3: RoleType.opening_server,
                4: RoleType.opening_server,
                5: RoleType.closing_server,
                6: RoleType.opening_kitchen,
                7: RoleType.opening_dishwasher,
                8: RoleType.opening_cashier,
            }
            if self.role_id is not None and self.role_id in legacy_role_map:
                self.role = legacy_role_map[self.role_id]

        if self.template_role_id is None and self.role_id is not None and self.role is None:
            # Keep compatibility with existing payloads that send role_id for non-enum template roles.
            self.template_role_id = self.role_id

        if self.role is None and self.template_role_id is None:
            raise ValueError("role or template_role_id is required")

        if self.scope == TaskScope.individual and self.individual_employee_id is None:
            raise ValueError("individual_employee_id is required when scope is individual")
        if self.scope != TaskScope.individual:
            self.individual_employee_id = None

        return self


class TaskUpdate(BaseModel):
    role: Optional[RoleType] = None
    template_role_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    order: Optional[int] = None
    is_active: Optional[bool] = None
    is_global: Optional[bool] = None
    scope: Optional[TaskScope] = None
    individual_employee_id: Optional[int] = None
    task_group_id: Optional[int] = None


class TaskOut(BaseModel):
    id: int
    role: Optional[RoleType] = None
    template_role_id: Optional[int] = None
    template_role_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    order: int
    is_active: bool
    is_global: bool = False
    scope: TaskScope = TaskScope.role_shared
    individual_employee_id: Optional[int] = None
    individual_employee_name: Optional[str] = None
    task_group_id: Optional[int] = None
    task_group_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TaskWithCompletion(TaskOut):
    completed: bool
    completed_at: Optional[datetime] = None
    completed_by_manager: bool
    completion_notes: Optional[str] = None
    completion_id: Optional[int] = None
    completed_by_employee_id: Optional[int] = None
    completed_by_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TaskGroupCreate(BaseModel):
    role: RoleType
    name: str = Field(min_length=1, max_length=120)
    order: int = 0


class TaskGroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    order: Optional[int] = None
    is_active: Optional[bool] = None


class TaskGroupOut(BaseModel):
    id: int
    role: RoleType
    name: str
    order: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AssignTaskToGroupRequest(BaseModel):
    task_group_id: Optional[int] = None


class AutoSplitShiftTasksRequest(BaseModel):
    group_count: int = Field(default=2, ge=1, le=12)


class TaskSplitResult(BaseModel):
    shift_id: int
    role: RoleType
    group_count: int
    task_count: int


class UpdateEmployeeShiftTaskGroupRequest(BaseModel):
    task_group_id: Optional[int] = None


class ShiftCreate(BaseModel):
    date: Optional[str] = None
    name: Optional[str] = None
    role: Optional[RoleType] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_override: bool = False
    notes: Optional[str] = None

    # Compatibility fields.
    shift_date: Optional[str] = None
    scheduled_start: Optional[datetime] = None
    status: Optional[ShiftStatus] = None

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("date must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("date must be in YYYY-MM-DD format")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"

    @model_validator(mode="after")
    def apply_compatibility_fields(self) -> "ShiftCreate":
        if not self.date and self.shift_date:
            self.date = self.shift_date

        if not self.date:
            raise ValueError("date is required and must be in YYYY-MM-DD format")

        if self.start_time is None and self.scheduled_start is not None:
            self.start_time = self.scheduled_start.strftime("%H:%M")

        if self.name is not None:
            self.name = self.name.strip() or None
        return self


class ShiftUpdate(BaseModel):
    date: Optional[str] = None
    name: Optional[str] = None
    role: Optional[RoleType] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_override: Optional[bool] = None
    notes: Optional[str] = None
    status: Optional[ShiftStatus] = None

    @field_validator("date")
    @classmethod
    def validate_optional_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("date must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("date must be in YYYY-MM-DD format")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_optional_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"


class ShiftOut(BaseModel):
    id: int
    date: str
    name: Optional[str] = None
    role: Optional[RoleType] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_override: bool = False
    recurring_slot_id: Optional[int] = None
    status: ShiftStatus
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    notes: Optional[str] = None
    announcement: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShiftAnnouncementUpdate(BaseModel):
    """Manager-visible announcement for assigned employees; max 300 chars. Empty clears."""

    announcement: Optional[str] = Field(default=None, max_length=300)

    @field_validator("announcement", mode="before")
    @classmethod
    def strip_announcement(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value


class ShiftBatchCreateRequest(BaseModel):
    shifts: list[ShiftCreate] = Field(min_length=1, max_length=50)


class ShiftBatchFailure(BaseModel):
    index: int
    detail: str
    shift: ShiftCreate


class ShiftBatchCreateResponse(BaseModel):
    created: list[ShiftOut]
    failed: list[ShiftBatchFailure]


class AssignEmployeeRequest(BaseModel):
    employee_id: int
    role: Optional[RoleType] = None
    task_group_id: Optional[int] = None


class CloneShiftRequest(BaseModel):
    target_date: str

    @field_validator("target_date")
    @classmethod
    def validate_target_date(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("target_date must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("target_date must be in YYYY-MM-DD format")
        return value


class UpdateEmployeeShiftRoleRequest(BaseModel):
    role: RoleType


class CheckInRequest(BaseModel):
    employee_id: int
    pin: str


class CheckOutRequest(BaseModel):
    employee_id: int
    pin: str
    notes: Optional[str] = None


class ExcuseAbsenceRequest(BaseModel):
    employee_id: int
    reason: Optional[str] = None


class EmployeeShiftOut(BaseModel):
    id: int
    shift_id: int
    employee_id: int
    employee_name: str
    employee_role: RoleType
    role: Optional[RoleType] = None
    checked_in_at: Optional[datetime] = None
    checked_out_at: Optional[datetime] = None
    is_excused_absence: bool
    absence_reason: Optional[str] = None
    notes: Optional[str] = None
    task_group_id: Optional[int] = None
    task_group_name: Optional[str] = None
    tasks: Optional[list[TaskWithCompletion]] = None

    model_config = ConfigDict(from_attributes=True)


class CompleteTaskRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=500)


class EmployeeShiftSummary(BaseModel):
    employee_id: int
    employee_name: str
    role: RoleType
    checked_in: bool
    checked_in_at: Optional[datetime] = None
    checked_out_at: Optional[datetime] = None
    is_excused_absence: bool
    tasks_total: int
    tasks_completed: int
    completion_pct: float

    model_config = ConfigDict(from_attributes=True)


class ShiftTaskNoteEntry(BaseModel):
    task_id: int
    task_title: str
    note: str
    completed_at: Optional[datetime] = None


class EmployeeShiftTaskNotes(BaseModel):
    employee_id: int
    employee_name: str
    items: list[ShiftTaskNoteEntry]


class ShiftDashboard(BaseModel):
    shift: ShiftOut
    total_assigned: int
    checked_in_count: int
    checked_out_count: int
    no_shows: int
    employees: list[EmployeeShiftSummary]
    task_notes_by_employee: list[EmployeeShiftTaskNotes] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TodayOverview(BaseModel):
    date: str
    shifts: list[ShiftDashboard]
    pending_alerts: int

    model_config = ConfigDict(from_attributes=True)


class AlertOut(BaseModel):
    id: int
    shift_id: Optional[int] = None
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    alert_type: AlertType | str
    message: str
    sent_at: Optional[datetime] = None
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None

    # Compatibility fields for existing alert router response payloads.
    channel: Optional[str] = None
    status: Optional[str] = None
    recipient: Optional[str] = None
    error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DirectMessageCreate(BaseModel):
    employee_id: int
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("message is required")
        return normalized


class DirectMessageOut(BaseModel):
    id: int
    manager_id: int
    manager_name: Optional[str] = None
    employee_id: int
    employee_name: Optional[str] = None
    sender_employee_id: int
    sender_name: Optional[str] = None
    sender_type: MessageSenderType | str
    message: str
    sent_at: datetime
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MessageContactOut(BaseModel):
    id: int
    name: str
    role: RoleType
    is_owner: bool = False

    model_config = ConfigDict(from_attributes=True)


class ConversationMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=500)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("message is required")
        return normalized


def _normalize_optional_hhmm(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) != 5 or normalized[2] != ":":
        raise ValueError("time must be in HH:MM 24h format")

    hh, mm = normalized.split(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError("time must be in HH:MM 24h format")

    hour = int(hh)
    minute = int(mm)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("time must be in HH:MM 24h format")

    return f"{hour:02d}:{minute:02d}"


def _require_hhmm(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("time must be in HH:MM 24h format")
    normalized = _normalize_optional_hhmm(value)
    if normalized is None:
        raise ValueError("time must be in HH:MM 24h format")
    return normalized


def _validate_iso_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("date must be in YYYY-MM-DD format")
    normalized = value.strip()
    if len(normalized) != 10 or normalized[4] != "-" or normalized[7] != "-":
        raise ValueError("date must be in YYYY-MM-DD format")
    yyyy, mm, dd = normalized.split("-")
    if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
        raise ValueError("date must be in YYYY-MM-DD format")
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date must be in YYYY-MM-DD format") from exc
    return normalized


class AvailabilityWindowInput(BaseModel):
    """A single weekly positive-availability window."""

    day_of_week: int
    start_time: str
    end_time: str

    @field_validator("day_of_week")
    @classmethod
    def validate_day_of_week(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 and 6")
        return value

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_hhmm(value)

    @model_validator(mode="after")
    def validate_order(self) -> "AvailabilityWindowInput":
        start_hour, start_minute = self.start_time.split(":")
        end_hour, end_minute = self.end_time.split(":")
        start_total = int(start_hour) * 60 + int(start_minute)
        end_total = int(end_hour) * 60 + int(end_minute)
        if start_total >= end_total:
            raise ValueError("end_time must be after start_time")
        return self


class AvailabilityWindowsReplaceRequest(BaseModel):
    """Replace the whole set of the current employee's draft windows."""

    windows: list[AvailabilityWindowInput] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_no_overlap_per_day(self) -> "AvailabilityWindowsReplaceRequest":
        by_day: dict[int, list[tuple[int, int]]] = {}
        for window in self.windows:
            sh, sm = window.start_time.split(":")
            eh, em = window.end_time.split(":")
            start_total = int(sh) * 60 + int(sm)
            end_total = int(eh) * 60 + int(em)
            existing = by_day.setdefault(window.day_of_week, [])
            for other_start, other_end in existing:
                if start_total < other_end and other_start < end_total:
                    raise ValueError(
                        f"Overlapping availability windows on day {window.day_of_week}"
                    )
            existing.append((start_total, end_total))
        return self


class AvailabilityWindowOut(BaseModel):
    id: int
    employee_id: int
    day_of_week: int
    start_time: str
    end_time: str
    approval_status: AvailabilityApprovalStatus = AvailabilityApprovalStatus.draft
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_employee_id: Optional[int] = None
    review_note: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailabilitySubmitOut(BaseModel):
    submitted_count: int
    pending_count: int


class AvailabilityDecisionRequest(BaseModel):
    review_note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("review_note", mode="before")
    @classmethod
    def normalize_review_note(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class AvailabilityReviewEmployeeOut(BaseModel):
    employee_id: int
    employee_name: str
    submitted_at: Optional[datetime] = None
    entries: list[AvailabilityWindowOut] = Field(default_factory=list)


class TemporaryLeaveCreate(BaseModel):
    start_date: str
    end_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        return _validate_iso_date(value)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_optional_time(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_hhmm(value)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason is required")
        return normalized

    @model_validator(mode="after")
    def validate_range(self) -> "TemporaryLeaveCreate":
        if self.start_date > self.end_date:
            raise ValueError("end_date must be on or after start_date")

        if (self.start_time is None) != (self.end_time is None):
            raise ValueError(
                "start_time and end_time must both be set or both omitted"
            )
        if self.start_time is not None and self.end_time is not None:
            sh, sm = self.start_time.split(":")
            eh, em = self.end_time.split(":")
            if int(sh) * 60 + int(sm) >= int(eh) * 60 + int(em):
                raise ValueError("end_time must be after start_time")
        return self


class TemporaryLeaveOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    start_date: str
    end_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    reason: str
    status: TemporaryLeaveStatus
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_employee_id: Optional[int] = None
    review_note: Optional[str] = None
    created_at: datetime
    released_shift_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class TemporaryLeaveApprovalOut(BaseModel):
    leave: TemporaryLeaveOut
    released_shift_count: int


class AvailabilityConflictOut(BaseModel):
    source: Literal["missing_availability", "temporary_leave"]
    label: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    all_day: bool
    display: str


class AvailabilityEmployeeDaySummaryOut(BaseModel):
    employee_id: int
    employee_name: str
    status: Literal["available", "partial", "unavailable", "unknown"]
    conflicts: list[AvailabilityConflictOut] = Field(default_factory=list)


class AvailabilitySummaryDayOut(BaseModel):
    date: str
    day_of_week: int
    employees: list[AvailabilityEmployeeDaySummaryOut] = Field(default_factory=list)


class AvailabilitySummaryWeekOut(BaseModel):
    week_start: str
    days: list[AvailabilitySummaryDayOut] = Field(default_factory=list)


class AvailabilityManagerEmployeeOut(BaseModel):
    employee_id: int
    employee_name: str
    active_status: Literal["approved", "pending", "none"]
    windows: list[AvailabilityWindowOut] = Field(default_factory=list)


class AvailabilityManagerWeekOut(BaseModel):
    week_start: str
    employees: list[AvailabilityManagerEmployeeOut] = Field(default_factory=list)


class ScheduleSlotEmployeeOut(BaseModel):
    employee_id: int
    employee_name: str
    role: RoleType


class ScheduleSlotCreate(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str
    name: Optional[str] = None

    @field_validator("day_of_week")
    @classmethod
    def validate_day_of_week(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 and 6")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_slot_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"


class ScheduleSlotUpdate(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("day_of_week")
    @classmethod
    def validate_optional_day_of_week(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 and 6")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_optional_slot_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"


class AssignSlotEmployeeRequest(BaseModel):
    employee_id: int
    role: RoleType


class ScheduleSlotOut(BaseModel):
    id: int
    day_of_week: int
    start_time: str
    end_time: str
    name: Optional[str] = None
    is_active: bool
    employees: list[ScheduleSlotEmployeeOut] = []

    model_config = ConfigDict(from_attributes=True)


class ScheduleWeekSlotItem(BaseModel):
    slot: ScheduleSlotOut
    shift: Optional[ShiftOut] = None
    is_override: bool = False


class ScheduleWeekDay(BaseModel):
    date: str
    day_of_week: int
    slots: list[ScheduleWeekSlotItem]


class ScheduleWeekOut(BaseModel):
    week_start: str
    days: list[ScheduleWeekDay]


class ScheduleGenerateRequest(BaseModel):
    week_start: str

    @field_validator("week_start")
    @classmethod
    def validate_week_start(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("week_start must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("week_start must be in YYYY-MM-DD format")
        return value


class SchedulePresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)


class SchedulePresetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)


class SchedulePresetSlotEmployeeOut(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    role: RoleType
    task_group_id: Optional[int] = None
    task_group_name: Optional[str] = None


class SchedulePresetSlotOut(BaseModel):
    id: int
    day_of_week: int
    start_time: str
    end_time: str
    slot_name: Optional[str] = None
    employees: list[SchedulePresetSlotEmployeeOut] = []


class SchedulePresetOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_by_employee_id: int
    created_at: datetime
    updated_at: datetime
    is_active: bool
    slots: list[SchedulePresetSlotOut] = []

    model_config = ConfigDict(from_attributes=True)


class SchedulePresetSlotCreate(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str
    slot_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("day_of_week")
    @classmethod
    def validate_day_of_week(cls, value: int) -> int:
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 and 6")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_slot_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"


class SchedulePresetSlotUpdate(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    slot_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("day_of_week")
    @classmethod
    def validate_optional_day_of_week(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 0 or value > 6:
            raise ValueError("day_of_week must be between 0 and 6")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_optional_slot_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) != 5 or value[2] != ":":
            raise ValueError("time must be in HH:MM 24h format")
        hh, mm = value.split(":")
        if not (hh.isdigit() and mm.isdigit()):
            raise ValueError("time must be in HH:MM 24h format")
        hour = int(hh)
        minute = int(mm)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM 24h format")
        return f"{hour:02d}:{minute:02d}"


class AssignPresetSlotEmployeeRequest(BaseModel):
    employee_id: int
    role: RoleType
    task_group_id: Optional[int] = None


class SchedulePresetApplyRequest(BaseModel):
    week_start: str

    @field_validator("week_start")
    @classmethod
    def validate_week_start(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("week_start must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("week_start must be in YYYY-MM-DD format")
        return value


class SchedulePresetApplyResult(BaseModel):
    created: int
    skipped: int


class SavePresetFromWeekRequest(BaseModel):
    week_start: str
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("week_start")
    @classmethod
    def validate_week_start(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("week_start must be in YYYY-MM-DD format")
        yyyy, mm, dd = value.split("-")
        if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
            raise ValueError("week_start must be in YYYY-MM-DD format")
        return value


class ShiftRangeItem(BaseModel):
    shift: ShiftOut
    employee_count: int
    current_employee_shift: Optional[EmployeeShiftOut] = None


class MyShiftItem(BaseModel):
    shift: ShiftOut
    employee_shift: EmployeeShiftOut
    is_today: bool
    tasks_remaining: int


class EmployeeEarningsShiftItem(BaseModel):
    employee_shift_id: int
    shift_id: int
    date: str
    shift_name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    checked_in_at: Optional[datetime] = None
    checked_out_at: Optional[datetime] = None
    hours_worked: float = 0.0
    estimated_pay: float = 0.0


class EmployeeEarningsSummary(BaseModel):
    period: Literal["weekly", "monthly"]
    start_date: str
    end_date: str
    hourly_wage: float
    total_hours: float
    total_pay: float
    shifts: list[EmployeeEarningsShiftItem]


# -----------------------------------------------------------------------------
# Compatibility schemas currently used by existing routers/auth layer.
# -----------------------------------------------------------------------------


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[int] = None
    email: Optional[EmailStr] = None


class UserBase(BaseModel):
    name: str
    email: EmailStr
    phone: str
    role_id: int


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserOut(UserBase):
    id: int
    is_employee: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShiftAssignmentCreate(BaseModel):
    user_id: int
    assigned_role_id: Optional[int] = None


class ShiftAssignmentOut(BaseModel):
    id: int
    user_id: int
    shift_id: int
    assigned_role_id: int
    status: str
    is_no_show: bool
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskInstanceOut(BaseModel):
    id: int
    task_id: int
    shift_id: int
    assignment_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    status: str
    completed_at: Optional[datetime] = None
    completed_by_user_id: Optional[int] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TaskInstanceComplete(BaseModel):
    notes: Optional[str] = None


class AlertCreate(BaseModel):
    alert_type: str
    channel: str = "sms"
    message: str
    recipient: Optional[str] = None
    assignment_id: Optional[int] = None
    task_instance_id: Optional[int] = None
