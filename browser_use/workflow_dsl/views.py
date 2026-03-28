from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class WorkflowStep(BaseModel):
    """A single workflow DSL step."""

    id: str | None = Field(default=None, description='Optional step identifier')
    name: str | None = Field(default=None, description='Optional step name')
    comment: str | None = Field(default=None, description='Optional comment or description')
    action: str = Field(..., description='The action name, must match a registered tool')
    params: dict[str, Any] = Field(default_factory=dict, description='Action parameters matching the tool schema')
    timeout_ms: int | None = Field(default=None, description='Step timeout in milliseconds')
    optional: bool = Field(default=False, description="If true, failure won't stop the workflow")
    then_steps: list['WorkflowStep'] = Field(default_factory=list, description='Conditional success branch steps')
    else_steps: list['WorkflowStep'] = Field(default_factory=list, description='Conditional fallback branch steps')
    steps: list['WorkflowStep'] = Field(default_factory=list, description='Nested child steps for loop constructs')

    @model_validator(mode='after')
    def validate_control_flow_structure(self) -> 'WorkflowStep':
        """Validate nested step fields based on control-flow action type."""
        if self.action == 'if' and not self.then_steps and not self.else_steps:
            raise ValueError("Workflow 'if' step must include then_steps or else_steps")

        if self.action in {'loop_for', 'loop_until'} and not self.steps:
            raise ValueError(f"Workflow '{self.action}' step must include nested steps")

        return self


class WorkflowDocument(BaseModel):
    """Workflow document structure."""

    id: str | None = Field(default=None)
    name: str | None = Field(default=None)
    version: str = Field(default='0.1.0')
    task: str | None = Field(default=None, description='Original task description')
    start_url: str | None = Field(default=None)
    variables: dict[str, Any] = Field(default_factory=dict)
    input_variables: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)


class StepExecutionResult(BaseModel):
    """Result of a single step execution."""

    step: WorkflowStep
    success: bool
    output: Any = None
    error_message: str | None = None
    resolved_params: dict[str, Any] = Field(default_factory=dict)
    generated_variables: dict[str, Any] = Field(default_factory=dict)
    skipped: bool = False


class ExecutionErrorFeedback(BaseModel):
    """Structured execution error feedback."""

    failed_step: WorkflowStep
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    resolved_params: dict[str, Any] = Field(default_factory=dict)
    error_type: str
    error_message: str
    page_snapshot: str | None = None
    last_success_step: WorkflowStep | None = None
    runtime_variables: dict[str, Any] = Field(default_factory=dict)
    repair_hint: str | None = None


class WorkflowExecutionResult(BaseModel):
    """Result of full workflow execution."""

    success: bool
    completed_steps: int
    total_steps: int
    error: ExecutionErrorFeedback | None = None
    runtime_variables: dict[str, Any] = Field(default_factory=dict)
    outputs: list[Any] = Field(default_factory=list)


class WorkflowPlannerContext(BaseModel):
    """Planner-specific structured context for one planning iteration."""

    task: str
    current_url: str
    page_title: str
    available_actions: list[dict[str, Any]] = Field(default_factory=list)
    runtime_variables: dict[str, Any] = Field(default_factory=dict)
    recent_successful_steps: list[WorkflowStep] = Field(default_factory=list)
    last_error: ExecutionErrorFeedback | None = None
    browser_state_summary: str = ''
    planner_history_summary: str = ''
    available_file_paths: list[str] = Field(default_factory=list)


class WorkflowPlannerTurn(BaseModel):
    """Persistent planner turn record used for record-mode summarization."""

    step_number: int
    plan: list[WorkflowStep] = Field(default_factory=list)
    result_summary: str = ''
    error: ExecutionErrorFeedback | None = None


class WorkflowHistoryEntry(BaseModel):
    """Workflow-native execution history entry independent from AgentHistory."""

    turn_number: int
    planned_steps: list[WorkflowStep] = Field(default_factory=list)
    executed_steps: list[WorkflowStep] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    runtime_variables: dict[str, Any] = Field(default_factory=dict)
    error: ExecutionErrorFeedback | None = None
    done: bool = False


class WorkflowHistory(BaseModel):
    """Workflow-native execution history."""

    task: str
    mode: Literal['record', 'replay']
    entries: list[WorkflowHistoryEntry] = Field(default_factory=list)


class WorkflowArtifacts(BaseModel):
    """Structured workflow artifacts emitted by workflow runtime."""

    record_document: WorkflowDocument | None = None
    replay_document: WorkflowDocument | None = None
    history: WorkflowHistory | None = None


class WorkflowRecordSummary(BaseModel):
    """Summary of a recording session."""

    output_path: str
    step_count: int
    workflow_id: str | None = None
    mode: Literal['history', 'planner'] = Field(default='history')
    start_url: str | None = Field(default=None)
    replay_step_count: int | None = Field(default=None)
    planner_turns: int | None = Field(default=None)


class WorkflowAgentRunResult(BaseModel):
    """Unified Agent return value for workflow modes."""

    mode: Literal['record', 'replay']
    execution: WorkflowExecutionResult
    artifacts: WorkflowArtifacts | None = None
    record_summary: WorkflowRecordSummary | None = None
