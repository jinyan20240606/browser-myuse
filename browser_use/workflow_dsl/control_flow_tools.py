"""Control flow action tools for DSL record mode.

This module implements the control flow actions (if, loop_for, loop_until, set_variable)
as Tools registry actions. They are only injected when ``workflow_mode='record'``.

Architecture: DSL_ARCHITECTURE_DESIGN.md §11.2
- Control flow action schema is registered to Tools registry (so LLM can see it).
- Execution logic is delegated to StepExecutor.
- This ensures recording and replay use the same execution semantics.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, model_validator

from browser_use.agent.views import ActionResult
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from .executor import StepExecutor
from .views import WorkflowStep

logger = logging.getLogger(__name__)


class IfActionParams(BaseModel):
    """Parameters for 'if' control flow action.

    Evaluates a runtime variable and executes either then_steps or else_steps.
    """

    variable: str = Field(
        ...,
        description="Variable name to evaluate, e.g. 'my_var' or use variable reference '{{my_var}}'",
    )
    operator: str = Field(
        default='truthy',
        description=(
            'Comparison operator. Supported: truthy, falsy, equals, not_equals, '
            'contains, not_empty, empty, greater_than, less_than'
        ),
    )
    expected: Any = Field(
        default=None,
        description='Expected value for comparison operators like equals/contains',
    )
    then_steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            'List of actions to execute when condition is met. '
            'Each action: {"action_name": {"param1": val1, ...}}'
        ),
    )
    else_steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            'List of actions to execute when condition is NOT met. '
            'Each action: {"action_name": {"param1": val1, ...}}'
        ),
    )

    @model_validator(mode='after')
    def validate_branch_steps(self) -> 'IfActionParams':
        if not self.then_steps and not self.else_steps:
            raise ValueError("'if' requires then_steps or else_steps")
        return self


class LoopForActionParams(BaseModel):
    """Parameters for 'loop_for' control flow action.

    Iterates over a list and executes steps for each item.
    """

    items: Any = Field(
        ...,
        description="List of items to iterate over, or variable reference e.g. '{{my_list}}'",
    )
    item_variable: str = Field(
        default='item',
        description='Name of the variable to store the current item during iteration',
    )
    index_variable: str | None = Field(
        default=None,
        description='Optional variable name to store the current 0-based index during iteration',
    )
    steps: list[dict[str, Any]] = Field(
        ...,
        description=(
            'List of actions to execute for each item. '
            'Each action: {"action_name": {"param1": val1, ...}}'
        ),
    )

    @model_validator(mode='after')
    def validate_loop_steps(self) -> 'LoopForActionParams':
        if not self.steps:
            raise ValueError("'loop_for' requires non-empty steps")
        if any(not step for step in self.steps):
            raise ValueError("'loop_for.steps' must not contain empty action objects")
        return self


class LoopUntilActionParams(BaseModel):
    """Parameters for 'loop_until' control flow action.

    Repeats steps until a condition variable satisfies the operator, or max_loops is reached.
    Schema strategy:
    - `variable` and `steps` are required, because without them loop semantics are incomplete.
    - `operator` defaults to `truthy`, so the model can omit it in the common case.
    - `expected` is optional and only needed for comparison operators like equals/contains.
    - `max_loops` and `loop_interval` stay optional with safe defaults.
    """

    variable: str = Field(
        ...,
        min_length=1,
        description='Required. Variable name to evaluate for the exit condition. Must not be empty.',
    )
    operator: str = Field(
        default='truthy',
        description=(
            'Optional. Comparison operator. Supported: truthy, falsy, equals, not_equals, '
            'contains, not_empty, empty. If omitted, defaults to truthy.'
        ),
    )
    expected: Any = Field(
        default=None,
        description='Optional. Expected value for operators like equals / not_equals / contains.',
    )
    max_loops: int = Field(
        default=10,
        ge=1,
        le=100,
        description='Optional. Maximum iterations to prevent infinite loops. Defaults to 10.',
    )
    loop_interval: int = Field(
        default=0,
        ge=0,
        le=60000,
        description='Optional. Wait time in milliseconds between each iteration. Defaults to 0.',
    )
    steps: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            'Required. Non-empty list of actions to execute in each iteration. '
            'Each action: {"action_name": {"param1": val1, ...}}. Empty objects are not valid.'
        ),
    )

    @model_validator(mode='after')
    def validate_until_steps(self) -> 'LoopUntilActionParams':
        if not self.steps:
            raise ValueError("'loop_until' requires non-empty steps")
        if any(not step for step in self.steps):
            raise ValueError("'loop_until.steps' must not contain empty action objects")
        if self.operator in {'equals', 'not_equals', 'contains'} and self.expected is None:
            raise ValueError(f"'loop_until' operator '{self.operator}' requires 'expected'")
        return self


class SetVariableActionParams(BaseModel):
    """Parameters for 'set_variable' control flow action.

    Assigns a static value or evaluates a Python expression and stores the result.
    """

    name: str = Field(..., description='Variable name to assign')
    value: Any = Field(default=None, description='Static value to assign')
    expression: str | None = Field(
        default=None,
        description='Python expression to evaluate (safe subset). Takes precedence over value.',
    )


def _convert_nested_step(step_dict: dict[str, Any], index: int) -> WorkflowStep:
    """Convert a raw action dict ``{"action_name": params_dict}`` to a WorkflowStep.

    Handles nested control flow (if/loop_for inside loop_for, etc.) by recursively
    converting ``then_steps``, ``else_steps``, and ``steps`` fields.
    """
    if not step_dict:
        raise ValueError(f'Empty step dict at index {index}')
    action_name = next(iter(step_dict.keys()))
    params_raw = step_dict[action_name]
    if not isinstance(params_raw, dict):
        params_raw = {}
    else:
        params_raw = dict(params_raw)

    then_steps_raw: list[dict[str, Any]] = params_raw.pop('then_steps', []) or []
    else_steps_raw: list[dict[str, Any]] = params_raw.pop('else_steps', []) or []
    steps_raw: list[dict[str, Any]] = params_raw.pop('steps', []) or []

    return WorkflowStep(
        id=f'nested_{index}',
        action=action_name,
        params=params_raw,
        then_steps=[_convert_nested_step(s, i) for i, s in enumerate(then_steps_raw) if s],
        else_steps=[_convert_nested_step(s, i) for i, s in enumerate(else_steps_raw) if s],
        steps=[_convert_nested_step(s, i) for i, s in enumerate(steps_raw) if s],
    )


def _convert_raw_steps(steps_data: list[dict[str, Any]]) -> list[WorkflowStep]:
    """Convert a list of raw action dicts to WorkflowStep objects."""
    result = []
    for i, step_dict in enumerate(steps_data or []):
        if not step_dict:
            continue
        try:
            result.append(_convert_nested_step(step_dict, i))
        except Exception as exc:
            logger.warning(f'Skipping invalid nested step at index {i}: {exc}')
    return result


def _build_control_flow_metadata(result: Any, *, action_name: str) -> dict[str, Any]:
    return {
        'control_flow': {
            'action': action_name,
            'semantic_success': result.semantic_success,
            'state_changed': result.state_changed,
            'resolved_params': result.resolved_params,
            'generated_variables': result.generated_variables,
            'debug_trace': result.debug_trace,
            'error_message': result.error_message,
        }
    }


def inject_control_flow_actions(tools: Tools, runtime_vars: dict[str, Any]) -> None:
    """Register control flow actions (if, loop_for, loop_until, set_variable) into *tools*.

    This must be called before ``Agent._setup_action_models()`` so that the registered
    actions are included in the LLM schema.

    Args:
        tools: The Agent's Tools instance.
        runtime_vars: Mutable runtime variable dict shared between the Agent and StepExecutor.
            Variables written by set_variable / loop_for are stored here and available
            to subsequent steps in the same run.
    """
    logger.info('🔧 正在将控制流 action 注入到 Tools 注册表中 (record 模式)...')

    # ── if ───────────────────────────────────────────────────────────────────

    # Note: Do not type hint browser_session with BrowserSession because `from __future__ import annotations`
    # causes type comparison to fail in Registry._normalize_action_function_signature
    async def if_action_impl(params: IfActionParams, browser_session) -> ActionResult:
        executor = StepExecutor(tools=tools, browser_session=browser_session)
        step = WorkflowStep(
            action='if',
            params={
                'variable': params.variable,
                'operator': params.operator,
                'expected': params.expected,
            },
            then_steps=_convert_raw_steps(params.then_steps),
            else_steps=_convert_raw_steps(params.else_steps),
        )
        result = await executor.execute(step, runtime_vars)
        metadata = _build_control_flow_metadata(result, action_name='if')
        if not result.success:
            return ActionResult(error=result.error_message or 'if action failed', metadata=metadata)
        branch = result.generated_variables.get('__branch_taken__', 'unknown')
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = f'Executed if branch: {branch}; semantic_success={result.semantic_success}'
        logger.info(f'🔀 Record: if action completed (branch={branch}, semantic_success={result.semantic_success})')
        return ActionResult(extracted_content=memory, long_term_memory=memory, metadata=metadata)

    if_action_impl.__name__ = 'if'

    tools.registry.action(
        description=(
            'Execute a conditional block. REQUIRED when task logic contains if/else, fallback, '
            'popup handling, optional branch, or stop/continue decisions based on a variable. '
            'Do NOT hide branch logic only in thinking. Evaluate a variable with an operator; '
            'run then_steps if true, else_steps if false. If the condition depends on a prior evaluate step, '
            'that evaluate step must write its result via output_variable before this if reads it.'
        ),
        param_model=IfActionParams,
    )(if_action_impl)

    # ── loop_for ─────────────────────────────────────────────────────────────

    async def loop_for_impl(params: LoopForActionParams, browser_session) -> ActionResult:
        executor = StepExecutor(tools=tools, browser_session=browser_session)
        step = WorkflowStep(
            action='loop_for',
            params={
                'items': params.items,
                'item_variable': params.item_variable,
                'index_variable': params.index_variable,
            },
            steps=_convert_raw_steps(params.steps),
        )
        result = await executor.execute(step, runtime_vars)
        metadata = _build_control_flow_metadata(result, action_name='loop_for')
        if not result.success:
            return ActionResult(error=result.error_message or 'loop_for action failed', metadata=metadata)
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = (
            'loop_for action completed; '
            f"iterations={result.generated_variables.get('__loop_iterations__', 0)}; "
            f"matched_iterations={result.generated_variables.get('__loop_matched_iterations__', [])}; "
            f'semantic_success={result.semantic_success}'
        )
        logger.info(
            '🔄 Record: loop_for action completed '
            f"(semantic_success={result.semantic_success}, matched={result.generated_variables.get('__loop_matched_iterations__', [])})"
        )
        return ActionResult(extracted_content=memory, long_term_memory=memory, metadata=metadata)

    loop_for_impl.__name__ = 'loop_for'

    tools.registry.action(
        description=(
            'Iterate over a list and execute steps for each item. REQUIRED when the task says '
            '遍历/逐个检查/for each/top N/inspect results one by one. Use together with if when you '
            'must stop or act on the first matching item. Do NOT replace iteration with a single guessed click. '
            'If the loop needs per-item inspection, use actions that materialize inspection output as runtime variables; '
            'do not rely on hidden semantic inference.'
        ),
        param_model=LoopForActionParams,
    )(loop_for_impl)

    # ── loop_until ───────────────────────────────────────────────────────────

    async def loop_until_impl(params: LoopUntilActionParams, browser_session) -> ActionResult:
        executor = StepExecutor(tools=tools, browser_session=browser_session)
        step = WorkflowStep(
            action='loop_until',
            params={
                'variable': params.variable,
                'operator': params.operator,
                'expected': params.expected,
                'max_loops': params.max_loops,
                'loop_interval': params.loop_interval,
            },
            steps=_convert_raw_steps(params.steps),
        )
        result = await executor.execute(step, runtime_vars)
        metadata = _build_control_flow_metadata(result, action_name='loop_until')
        if not result.success:
            return ActionResult(error=result.error_message or 'loop_until action failed', metadata=metadata)
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = (
            'loop_until action completed; '
            f"exit_iteration={result.generated_variables.get('__loop_until_exit_iteration__')}; "
            f'semantic_success={result.semantic_success}'
        )
        logger.info(
            '🔄 Record: loop_until action completed '
            f"(semantic_success={result.semantic_success}, exit_iteration={result.generated_variables.get('__loop_until_exit_iteration__')})"
        )
        return ActionResult(extracted_content=memory, long_term_memory=memory, metadata=metadata)

    loop_until_impl.__name__ = 'loop_until'

    tools.registry.action(
        description=(
            'Repeat a block of steps until a condition variable meets the operator criterion. REQUIRED '
            'for until/retry-until/wait-until/find-first-match-then-stop style tasks. Prefer this instead '
            'of manually simulating stop conditions only inside thinking. The condition variable must already '
            'be established by prior valid actions or by nested steps that write output_variable-backed data.'
        ),
        param_model=LoopUntilActionParams,
    )(loop_until_impl)

    async def set_variable_impl(params: SetVariableActionParams, browser_session) -> ActionResult:
        executor = StepExecutor(tools=tools, browser_session=browser_session)
        step = WorkflowStep(
            action='set_variable',
            params={
                'name': params.name,
                'value': params.value,
                'expression': params.expression,
            },
        )
        result = await executor.execute(step, runtime_vars)
        metadata = _build_control_flow_metadata(result, action_name='set_variable')
        if not result.success:
            return ActionResult(error=result.error_message or 'set_variable action failed', metadata=metadata)
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = f"set_variable: '{params.name}' = {result.output!r}"
        logger.info(f'📌 Record: set_variable {params.name} = {result.output!r}')
        return ActionResult(extracted_content=memory, long_term_memory=memory, metadata=metadata)

    set_variable_impl.__name__ = 'set_variable'

    tools.registry.action(
        description=(
            'Assign a static value or evaluate a Python expression and store the result in a named variable. '
            'REQUIRED when extracted data, loop results, flags, or branch decisions must be reused in later steps.'
        ),
        param_model=SetVariableActionParams,
    )(set_variable_impl)

    logger.info('✅ 已成功注册控制流 action：if, loop_for, loop_until, set_variable')
