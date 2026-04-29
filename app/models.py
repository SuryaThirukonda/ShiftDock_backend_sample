from __future__ import annotations

import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import deferred, relationship, synonym
from sqlalchemy.sql import func

from .database import Base


class RoleType(str, enum.Enum):
    owner = "owner"
    manager = "manager"
    opening_server = "opening_server"
    closing_server = "closing_server"
    opening_kitchen = "opening_kitchen"
    closing_kitchen = "closing_kitchen"
    opening_cashier = "opening_cashier"
    closing_cashier = "closing_cashier"
    opening_dishwasher = "opening_dishwasher"
    closing_dishwasher = "closing_dishwasher"


class ShiftStatus(str, enum.Enum):
    scheduled = "scheduled"
    active = "active"
    completed = "completed"


class AlertType(str, enum.Enum):
    no_checkin = "no_checkin"
    incomplete_tasks = "incomplete_tasks"
    no_checkout = "no_checkout"
    manual = "manual"


class MessageSenderType(str, enum.Enum):
    manager = "manager"
    employee = "employee"


class AvailabilityApprovalStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class TemporaryLeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class TaskScope(str, enum.Enum):
    individual = "individual"
    role_shared = "role_shared"
    global_shared = "global_shared"


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", name="uq_roles_name"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users = relationship("User", back_populates="role")
    tasks = relationship("Task", back_populates="legacy_role")
    shift_assignments = relationship("ShiftAssignment", back_populates="role")


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    email_notifications_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    phone = Column(String(30), nullable=True)
    hourly_wage = Column(Numeric(10, 2), default=11.0, nullable=False, server_default="11.00")
    pin = deferred(Column(String(255), nullable=False))
    role = Column(SAEnum(RoleType, name="role_type", native_enum=False), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_owner = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employee_shifts = relationship("EmployeeShift", back_populates="employee")
    schedule_slots = relationship("ScheduleSlotEmployee", back_populates="employee")
    created_schedule_presets = relationship("SchedulePreset", back_populates="created_by_employee")
    preset_slot_assignments = relationship("SchedulePresetSlotEmployee", back_populates="employee")
    manager_alerts = relationship("ManagerAlert", back_populates="employee")
    employee_alert_copies = relationship("EmployeeAlert", back_populates="employee")
    availability_windows = relationship(
        "AvailabilityWindow",
        foreign_keys="AvailabilityWindow.employee_id",
        back_populates="employee",
        cascade="all, delete-orphan",
    )
    temporary_leave_requests = relationship(
        "TemporaryLeaveRequest",
        foreign_keys="TemporaryLeaveRequest.employee_id",
        back_populates="employee",
        cascade="all, delete-orphan",
    )
    sent_direct_messages = relationship(
        "DirectMessage",
        foreign_keys="DirectMessage.manager_id",
        back_populates="manager",
    )
    received_direct_messages = relationship(
        "DirectMessage",
        foreign_keys="DirectMessage.employee_id",
        back_populates="employee",
    )
    authored_direct_messages = relationship(
        "DirectMessage",
        foreign_keys="DirectMessage.sender_employee_id",
    )
    dropped_shift_pool_entries = relationship(
        "ShiftPoolEntry",
        foreign_keys="ShiftPoolEntry.dropped_by_employee_id",
        back_populates="dropped_by_employee",
    )
    claimed_shift_pool_entries = relationship(
        "ShiftPoolEntry",
        foreign_keys="ShiftPoolEntry.claimed_by_employee_id",
        back_populates="claimed_by_employee",
    )
    approved_shift_pool_entries = relationship(
        "ShiftPoolEntry",
        foreign_keys="ShiftPoolEntry.approved_by_employee_id",
        back_populates="approved_by_employee",
    )
    reviewed_availability_windows = relationship(
        "AvailabilityWindow",
        foreign_keys="AvailabilityWindow.reviewed_by_employee_id",
        back_populates="reviewed_by_employee",
    )
    reviewed_leave_requests = relationship(
        "TemporaryLeaveRequest",
        foreign_keys="TemporaryLeaveRequest.reviewed_by_employee_id",
        back_populates="reviewed_by_employee",
    )
    individually_assigned_tasks = relationship(
        "Task",
        foreign_keys="Task.individual_employee_id",
        back_populates="individual_employee",
    )
    role_assignments = relationship(
        "EmployeeRole",
        back_populates="employee",
        cascade="all, delete-orphan",
    )

    @property
    def roles(self) -> list[RoleType]:
        if self.role_assignments:
            unique_roles: list[RoleType] = []
            seen: set[RoleType] = set()
            for assignment in self.role_assignments:
                role_value = assignment.role
                if role_value in seen:
                    continue
                seen.add(role_value)
                unique_roles.append(role_value)
            if unique_roles:
                return unique_roles
        return [self.role] if self.role is not None else []


class EmployeeRole(Base):
    __tablename__ = "employee_roles"
    __table_args__ = (
        UniqueConstraint("employee_id", "role", name="uq_employee_roles_employee_role"),
        Index("ix_employee_roles_employee_id", "employee_id"),
        Index("ix_employee_roles_role", "role"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    role = Column(SAEnum(RoleType, name="employee_role_type", native_enum=False), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employee = relationship("Employee", back_populates="role_assignments")


class TaskGroup(Base):
    __tablename__ = "task_groups"
    __table_args__ = (
        Index("ix_task_groups_role", "role"),
        UniqueConstraint("role", "name", name="uq_task_groups_role_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    role = Column(SAEnum(RoleType, name="task_group_role_type", native_enum=False), nullable=False)
    name = Column(String(120), nullable=False)
    order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tasks = relationship("Task", back_populates="task_group")
    employee_shifts = relationship("EmployeeShift", back_populates="task_group")
    preset_slot_assignments = relationship("SchedulePresetSlotEmployee", back_populates="task_group")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_role", "role"),)

    id = Column(Integer, primary_key=True, index=True)
    role = Column(SAEnum(RoleType, name="task_role_type", native_enum=False), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_global = Column(Boolean, default=False, nullable=False)
    scope = Column(
        SAEnum(TaskScope, name="task_scope_type", native_enum=False),
        default=TaskScope.role_shared,
        nullable=False,
        index=True,
    )
    individual_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    task_group_id = Column(Integer, ForeignKey("task_groups.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Compatibility fields used by existing routers.
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True, index=True)
    category = Column(String(40), nullable=False, default="service")
    priority = Column(String(20), nullable=False, default="medium")
    estimated_minutes = Column(Integer, nullable=True)
    due_offset_minutes = Column(Integer, nullable=True)
    is_required = Column(Boolean, default=True, nullable=False)
    alert_on_miss = Column(Boolean, default=True, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    completions = relationship("TaskCompletion", back_populates="task")
    task_group = relationship("TaskGroup", back_populates="tasks")
    individual_employee = relationship(
        "Employee",
        foreign_keys=[individual_employee_id],
        back_populates="individually_assigned_tasks",
    )

    # Legacy relationships.
    legacy_role = relationship("Role", back_populates="tasks")
    task_instances = relationship("TaskInstance", back_populates="task")


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        Index("ix_shifts_status", "status"),
        Index("ix_shifts_date_start_time", "date", "start_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String(10), nullable=False)
    name = Column(String(100), nullable=True)
    role = Column(
        SAEnum(RoleType, name="shift_role_type", native_enum=False),
        nullable=True,
    )
    start_time = Column(String(8), nullable=True)
    end_time = Column(String(8), nullable=True)
    is_override = Column(Boolean, default=False, nullable=False)
    recurring_slot_id = Column(Integer, ForeignKey("schedule_slots.id"), nullable=True)
    status = Column(
        SAEnum(ShiftStatus, name="shift_status", native_enum=False),
        default=ShiftStatus.scheduled,
        nullable=False,
    )
    actual_start = Column(DateTime(timezone=True), nullable=True)
    actual_end = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    # Short message visible to assigned employees (e.g. reminders); separate from internal notes.
    announcement = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Compatibility timestamps.
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employee_shifts = relationship("EmployeeShift", back_populates="shift")
    manager_alerts = relationship("ManagerAlert", back_populates="shift")
    shift_pool_entries = relationship("ShiftPoolEntry", back_populates="shift")
    schedule_slot = relationship("ScheduleSlot", back_populates="shifts")

    # Legacy relationships.
    shift_assignments = relationship("ShiftAssignment", back_populates="shift")
    task_instances = relationship("TaskInstance", back_populates="shift")
    checkin_events = relationship("CheckInEvent", back_populates="shift")
    legacy_alert_events = relationship("AlertEvent", back_populates="shift")


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"
    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_schedule_slots_day_of_week"),
        Index("ix_schedule_slots_day_of_week", "day_of_week"),
    )

    id = Column(Integer, primary_key=True, index=True)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(8), nullable=False)
    end_time = Column(String(8), nullable=False)
    name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    slots = relationship(
        "ScheduleSlotEmployee",
        back_populates="slot",
        cascade="all, delete-orphan",
    )
    shifts = relationship("Shift", back_populates="schedule_slot")


class ScheduleSlotEmployee(Base):
    __tablename__ = "schedule_slot_employees"
    __table_args__ = (
        UniqueConstraint("slot_id", "employee_id", name="uq_schedule_slot_employee"),
    )

    id = Column(Integer, primary_key=True, index=True)
    slot_id = Column(Integer, ForeignKey("schedule_slots.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    role = Column(SAEnum(RoleType, name="slot_employee_role_type", native_enum=False), nullable=False)

    slot = relationship("ScheduleSlot", back_populates="slots")
    employee = relationship("Employee", back_populates="schedule_slots")


class SchedulePreset(Base):
    __tablename__ = "schedule_presets"
    __table_args__ = (
        Index("ix_schedule_presets_active", "is_active"),
        Index("ix_schedule_presets_created_by", "created_by_employee_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    created_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    is_active = Column(Boolean, default=True, nullable=False)

    created_by_employee = relationship("Employee", back_populates="created_schedule_presets")
    slots = relationship(
        "SchedulePresetSlot",
        back_populates="preset",
        cascade="all, delete-orphan",
    )


class SchedulePresetSlot(Base):
    __tablename__ = "schedule_preset_slots"
    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_schedule_preset_slots_day_of_week"),
        UniqueConstraint(
            "preset_id",
            "day_of_week",
            "start_time",
            "end_time",
            name="uq_schedule_preset_slot_time",
        ),
        Index(
            "ix_schedule_preset_slots_preset_day_time",
            "preset_id",
            "day_of_week",
            "start_time",
            "end_time",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    preset_id = Column(Integer, ForeignKey("schedule_presets.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(8), nullable=False)
    end_time = Column(String(8), nullable=False)
    slot_name = Column(String(120), nullable=True)

    preset = relationship("SchedulePreset", back_populates="slots")
    employees = relationship(
        "SchedulePresetSlotEmployee",
        back_populates="preset_slot",
        cascade="all, delete-orphan",
    )


class SchedulePresetSlotEmployee(Base):
    __tablename__ = "schedule_preset_slot_employees"
    __table_args__ = (
        UniqueConstraint("preset_slot_id", "employee_id", name="uq_schedule_preset_slot_employee"),
    )

    id = Column(Integer, primary_key=True, index=True)
    preset_slot_id = Column(Integer, ForeignKey("schedule_preset_slots.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    task_group_id = Column(Integer, ForeignKey("task_groups.id"), nullable=True)
    role = Column(
        SAEnum(RoleType, name="preset_slot_employee_role_type", native_enum=False),
        nullable=False,
    )

    preset_slot = relationship("SchedulePresetSlot", back_populates="employees")
    employee = relationship("Employee", back_populates="preset_slot_assignments")
    task_group = relationship("TaskGroup", back_populates="preset_slot_assignments")


class EmployeeShift(Base):
    __tablename__ = "employee_shifts"
    __table_args__ = (
        UniqueConstraint("shift_id", "employee_id", name="uq_employee_shifts_shift_employee"),
        Index("ix_employee_shifts_checked_in", "checked_in_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    task_group_id = Column(Integer, ForeignKey("task_groups.id"), nullable=True, index=True)
    role = Column(SAEnum(RoleType, name="employee_shift_role_type", native_enum=False), nullable=True)
    checked_in_at = Column(DateTime(timezone=True), nullable=True)
    checked_out_at = Column(DateTime(timezone=True), nullable=True)
    is_excused_absence = Column(Boolean, default=False, nullable=False)
    absence_reason = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    # Compatibility fields for transition safety.
    status = Column(String(30), default="assigned", nullable=False)
    scheduled_start_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_end_at = Column(DateTime(timezone=True), nullable=True)
    assigned_role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    is_no_show = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    shift = relationship("Shift", back_populates="employee_shifts")
    employee = relationship("Employee", back_populates="employee_shifts")
    task_group = relationship("TaskGroup", back_populates="employee_shifts")
    task_completions = relationship("TaskCompletion", back_populates="employee_shift")
    shift_pool_entries = relationship("ShiftPoolEntry", back_populates="employee_shift")

    assigned_role = relationship("Role", foreign_keys=[assigned_role_id])

    check_in_time = synonym("checked_in_at")
    check_out_time = synonym("checked_out_at")


class TaskCompletion(Base):
    __tablename__ = "task_completions"
    __table_args__ = (
        UniqueConstraint("employee_shift_id", "task_id", name="uq_task_completions_employee_task"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_shift_id = Column(Integer, ForeignKey("employee_shifts.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    completed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_by_manager = Column(Boolean, default=False, nullable=False)
    notes = Column(Text, nullable=True)

    employee_shift = relationship("EmployeeShift", back_populates="task_completions")
    task = relationship("Task", back_populates="completions")


class ManagerAlert(Base):
    """Canonical alert row for managers (owner/manager Alerts UI, counts, history)."""

    __tablename__ = "manager_alerts"

    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    alert_type = Column(SAEnum(AlertType, name="alert_type", native_enum=False), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acknowledged = Column(Boolean, default=False, nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    shift = relationship("Shift", back_populates="manager_alerts")
    employee = relationship("Employee", back_populates="manager_alerts")
    employee_inbox_rows = relationship(
        "EmployeeAlert",
        back_populates="manager_alert",
        passive_deletes=True,
    )


class EmployeeAlert(Base):
    """Per-employee inbox copy; deleting this row removes the alert from that employee's view only."""

    __tablename__ = "employee_alerts"
    __table_args__ = (
        UniqueConstraint("manager_alert_id", "employee_id", name="uq_employee_alerts_manager_employee"),
        Index("ix_employee_alerts_employee_id", "employee_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    manager_alert_id = Column(
        Integer,
        ForeignKey("manager_alerts.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)

    manager_alert = relationship("ManagerAlert", back_populates="employee_inbox_rows")
    employee = relationship("Employee", back_populates="employee_alert_copies")


class DirectMessage(Base):
    """Private manager/employee direct messages visible only to participants."""

    __tablename__ = "direct_messages"
    __table_args__ = (
        Index("ix_direct_messages_employee_sent_at", "employee_id", "sent_at"),
        Index("ix_direct_messages_manager_sent_at", "manager_id", "sent_at"),
        Index("ix_direct_messages_sender_sent_at", "sender_employee_id", "sent_at"),
        CheckConstraint(
            "(sender_type = 'manager' AND sender_employee_id = manager_id) OR "
            "(sender_type = 'employee' AND sender_employee_id = employee_id)",
            name="ck_direct_messages_sender_matches_participant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    manager_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    sender_employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    sender_type = Column(
        SAEnum(MessageSenderType, name="direct_message_sender_type", native_enum=False),
        nullable=False,
        default=MessageSenderType.manager,
    )
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)

    manager = relationship("Employee", foreign_keys=[manager_id], back_populates="sent_direct_messages")
    employee = relationship("Employee", foreign_keys=[employee_id], back_populates="received_direct_messages")
    sender = relationship("Employee", foreign_keys=[sender_employee_id], back_populates="authored_direct_messages")


# Backward-compatible alias for existing imports.
Alert = ManagerAlert


# -----------------------------------------------------------------------------
# Legacy compatibility models kept for existing routers while Prompt 2 names
# are introduced. These can be removed once routes are fully migrated.
# -----------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    phone = Column(String(25), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    is_employee = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    role = relationship("Role", back_populates="users")
    shift_assignments = relationship("ShiftAssignment", back_populates="user")
    direct_task_instances = relationship(
        "TaskInstance",
        back_populates="assigned_user",
        foreign_keys="TaskInstance.assigned_user_id",
    )
    completed_task_instances = relationship(
        "TaskInstance",
        back_populates="completed_by",
        foreign_keys="TaskInstance.completed_by_user_id",
    )
    checkin_events = relationship(
        "CheckInEvent",
        back_populates="user",
        foreign_keys="CheckInEvent.user_id",
    )
    recorded_checkin_events = relationship(
        "CheckInEvent",
        back_populates="recorded_by",
        foreign_keys="CheckInEvent.recorded_by_user_id",
    )
    acknowledged_alerts = relationship(
        "AlertEvent",
        back_populates="acknowledged_by",
        foreign_keys="AlertEvent.acknowledged_by_user_id",
    )
    legacy_alert_events = relationship(
        "AlertEvent",
        back_populates="employee",
        foreign_keys="AlertEvent.employee_id",
    )


class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        UniqueConstraint("shift_id", "user_id", name="uq_shift_assignments_shift_user"),
        Index("ix_shift_assignments_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    assigned_role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    status = Column(String(30), nullable=False, default="assigned")
    scheduled_start_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_end_at = Column(DateTime(timezone=True), nullable=True)
    check_in_time = Column(DateTime(timezone=True), nullable=True)
    check_out_time = Column(DateTime(timezone=True), nullable=True)
    is_no_show = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="shift_assignments")
    shift = relationship("Shift", back_populates="shift_assignments")
    role = relationship("Role", back_populates="shift_assignments")
    task_instances = relationship("TaskInstance", back_populates="assignment")
    checkin_events = relationship("CheckInEvent", back_populates="assignment")
    alerts = relationship("AlertEvent", back_populates="assignment")


class TaskInstance(Base):
    __tablename__ = "task_instances"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "shift_id", "assignment_id", name="uq_task_instance_template_shift_assignment"
        ),
        Index("ix_task_instances_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("shift_assignments.id"), nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    proof_photo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    task = relationship("Task", back_populates="task_instances")
    shift = relationship("Shift", back_populates="task_instances")
    assignment = relationship("ShiftAssignment", back_populates="task_instances")
    assigned_user = relationship(
        "User", back_populates="direct_task_instances", foreign_keys=[assigned_user_id]
    )
    completed_by = relationship(
        "User", back_populates="completed_task_instances", foreign_keys=[completed_by_user_id]
    )
    alerts = relationship("AlertEvent", back_populates="task_instance")


class CheckInEvent(Base):
    __tablename__ = "checkin_events"
    __table_args__ = (Index("ix_checkin_events_event_type", "event_type"),)

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("shift_assignments.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    event_type = Column(String(30), nullable=False)
    event_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    assignment = relationship("ShiftAssignment", back_populates="checkin_events")
    user = relationship("User", back_populates="checkin_events", foreign_keys=[user_id])
    shift = relationship("Shift", back_populates="checkin_events")
    recorded_by = relationship(
        "User", back_populates="recorded_checkin_events", foreign_keys=[recorded_by_user_id]
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_status", "status"),)

    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assignment_id = Column(Integer, ForeignKey("shift_assignments.id"), nullable=True)
    task_instance_id = Column(Integer, ForeignKey("task_instances.id"), nullable=True)
    alert_type = Column(String(40), nullable=False, index=True)
    channel = Column(String(20), nullable=False, default="sms", index=True)
    status = Column(String(20), nullable=False, default="pending")
    recipient = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    provider_sid = Column(String(255), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    acknowledged_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    shift = relationship("Shift", back_populates="legacy_alert_events")
    employee = relationship("User", back_populates="legacy_alert_events", foreign_keys=[employee_id])
    assignment = relationship("ShiftAssignment", back_populates="alerts")
    task_instance = relationship("TaskInstance", back_populates="alerts")
    acknowledged_by = relationship(
        "User", back_populates="acknowledged_alerts", foreign_keys=[acknowledged_by_user_id]
    )


# Backward-compatible alias for historical naming.
Shift_Assignment = ShiftAssignment


class SwapStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    approved = "approved"
    denied = "denied"
    cancelled = "cancelled"


class ShiftPoolStatus(str, enum.Enum):
    open = "open"
    claimed = "claimed"
    cancelled = "cancelled"


class ShiftPoolClaimMode(str, enum.Enum):
    manager_selects = "manager_selects"
    first_come_first_serve = "first_come_first_serve"


class ShiftPoolBidStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    withdrawn = "withdrawn"


class ShiftSwapRequest(Base):
    """
    Represents a shift-swap or coverage request between two employees.

    Lifecycle:
      pending  -> accepted/declined by target employee
      accepted -> approved/denied by manager (approved actually swaps the EmployeeShift rows)
      pending  -> cancelled by requester
    """

    __tablename__ = "shift_swap_requests"

    id = Column(Integer, primary_key=True, index=True)

    # Employee who initiated the request.
    requester_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    # Employee being asked to cover / swap.
    target_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)

    # The EmployeeShift the requester is offering.
    offered_shift_id = Column(Integer, ForeignKey("employee_shifts.id"), nullable=False)
    # The EmployeeShift the requester wants in return (nullable = one-way coverage request).
    requested_shift_id = Column(Integer, ForeignKey("employee_shifts.id"), nullable=True)

    status = Column(
        SAEnum(SwapStatus, name="swap_status", native_enum=False),
        default=SwapStatus.pending,
        nullable=False,
        index=True,
    )

    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # ORM relationships
    requester = relationship("Employee", foreign_keys=[requester_id])
    target = relationship("Employee", foreign_keys=[target_id])
    offered_shift = relationship("EmployeeShift", foreign_keys=[offered_shift_id])
    requested_shift = relationship("EmployeeShift", foreign_keys=[requested_shift_id])


class ShiftPoolEntry(Base):
    __tablename__ = "shift_pool_entries"
    __table_args__ = (
        Index("ix_shift_pool_entries_status_created_at", "status", "created_at"),
        Index("ix_shift_pool_entries_shift_status", "shift_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False, index=True)
    employee_shift_id = Column(Integer, ForeignKey("employee_shifts.id"), nullable=False, index=True)
    dropped_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    status = Column(
        SAEnum(ShiftPoolStatus, name="shift_pool_status", native_enum=False),
        default=ShiftPoolStatus.open,
        nullable=False,
        index=True,
    )
    claim_mode = Column(
        SAEnum(ShiftPoolClaimMode, name="shift_pool_claim_mode", native_enum=False),
        default=ShiftPoolClaimMode.first_come_first_serve,
        nullable=False,
    )
    leave_request_id = Column(
        Integer,
        ForeignKey("temporary_leave_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    claimed_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    shift = relationship("Shift", back_populates="shift_pool_entries")
    employee_shift = relationship("EmployeeShift", back_populates="shift_pool_entries")
    dropped_by_employee = relationship(
        "Employee",
        foreign_keys=[dropped_by_employee_id],
        back_populates="dropped_shift_pool_entries",
    )
    claimed_by_employee = relationship(
        "Employee",
        foreign_keys=[claimed_by_employee_id],
        back_populates="claimed_shift_pool_entries",
    )
    approved_by_employee = relationship(
        "Employee",
        foreign_keys=[approved_by_employee_id],
        back_populates="approved_shift_pool_entries",
    )
    bids = relationship("ShiftPoolBid", back_populates="entry", cascade="all, delete-orphan")
    leave_request = relationship(
        "TemporaryLeaveRequest",
        foreign_keys=[leave_request_id],
        back_populates="released_pool_entries",
    )


class ShiftPoolBid(Base):
    __tablename__ = "shift_pool_bids"
    __table_args__ = (
        UniqueConstraint("entry_id", "bidder_employee_id", name="uq_shift_pool_bids_entry_bidder"),
        Index("ix_shift_pool_bids_entry_status_created", "entry_id", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("shift_pool_entries.id", ondelete="CASCADE"), nullable=False)
    bidder_employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    status = Column(
        SAEnum(ShiftPoolBidStatus, name="shift_pool_bid_status", native_enum=False),
        default=ShiftPoolBidStatus.pending,
        nullable=False,
        index=True,
    )
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)

    entry = relationship("ShiftPoolEntry", back_populates="bids")
    bidder_employee = relationship("Employee", foreign_keys=[bidder_employee_id])
    resolved_by_employee = relationship("Employee", foreign_keys=[resolved_by_employee_id])


class AvailabilityWindow(Base):
    """Positive weekly availability: employee is available during this (day, start, end).

    Multiple rows per (employee, day_of_week) are allowed to support multiple windows per day.
    """

    __tablename__ = "availability_windows"
    __table_args__ = (
        CheckConstraint(
            "day_of_week >= 0 AND day_of_week <= 6",
            name="ck_availability_windows_day_of_week",
        ),
        Index("ix_availability_windows_employee_day", "employee_id", "day_of_week"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(String(5), nullable=False)  # HH:MM
    end_time = Column(String(5), nullable=False)  # HH:MM
    approval_status = Column(
        SAEnum(AvailabilityApprovalStatus, name="availability_approval_status", native_enum=False),
        default=AvailabilityApprovalStatus.draft,
        nullable=False,
        index=True,
    )
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employee = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="availability_windows",
    )
    reviewed_by_employee = relationship(
        "Employee",
        foreign_keys=[reviewed_by_employee_id],
        back_populates="reviewed_availability_windows",
    )


class TemporaryLeaveRequest(Base):
    """Time-bounded leave request that needs manager approval.

    Once approved, any of the employee's assigned shifts that overlap the leave
    window are dropped into the shift pool as first-come-first-serve.
    """

    __tablename__ = "temporary_leave_requests"
    __table_args__ = (
        Index("ix_temporary_leave_requests_employee_status", "employee_id", "status"),
        Index("ix_temporary_leave_requests_dates", "start_date", "end_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date = Column(String(10), nullable=False)  # YYYY-MM-DD inclusive
    end_date = Column(String(10), nullable=False)  # YYYY-MM-DD inclusive
    start_time = Column(String(5), nullable=True)  # HH:MM (None = all-day window)
    end_time = Column(String(5), nullable=True)
    reason = Column(Text, nullable=False)
    status = Column(
        SAEnum(TemporaryLeaveStatus, name="temporary_leave_status", native_enum=False),
        default=TemporaryLeaveStatus.pending,
        nullable=False,
        index=True,
    )
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employee = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="temporary_leave_requests",
    )
    reviewed_by_employee = relationship(
        "Employee",
        foreign_keys=[reviewed_by_employee_id],
        back_populates="reviewed_leave_requests",
    )
    released_pool_entries = relationship(
        "ShiftPoolEntry",
        foreign_keys="ShiftPoolEntry.leave_request_id",
        back_populates="leave_request",
    )


class TaskSkipFlag(Base):
    __tablename__ = "task_skip_flags"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    skip_count = Column(Integer, nullable=False)
    shifts_checked = Column(Integer, nullable=False)
    resolved = Column(Boolean, default=False, nullable=False, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)

    task = relationship("Task")
    resolved_by_employee = relationship("Employee")


class AppDocument(Base):
    """Simple document store for application settings.

    Each document lives in a ``collection_name`` and has a text ``id``.
    The payload is stored as JSON in the ``data`` column.
    """

    __tablename__ = "app_documents"

    collection_name = Column(Text, nullable=False, primary_key=True)
    id = Column(Text, nullable=False, primary_key=True)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
