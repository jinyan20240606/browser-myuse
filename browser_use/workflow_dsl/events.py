from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from uuid_extensions import uuid7str

from .views import ExecutionErrorFeedback, WorkflowHistoryEntry, WorkflowStep


class WorkflowEventBase(BaseModel):
    """Base event payload for workflow-native runtime events."""

    id: str = Field(default_factory=uuid7str)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    workflow_mode: Literal['record', 'replay']
    task: str


class WorkflowStepBatchEvent(WorkflowEventBase):
    """Event emitted after a workflow step batch execution."""

    turn_number: int
    executed_steps: list[WorkflowStep] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    runtime_variables: dict[str, Any] = Field(default_factory=dict)


class WorkflowRepairEvent(WorkflowEventBase):
    """Event emitted when repair loop feedback is produced."""

    turn_number: int
    error: ExecutionErrorFeedback


class WorkflowHistoryEvent(WorkflowEventBase):
    """Event emitted when workflow history entry is created."""

    entry: WorkflowHistoryEntry
