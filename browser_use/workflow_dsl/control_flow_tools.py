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

from pydantic import BaseModel, Field

from browser_use.agent.views import ActionResult
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from .executor import StepExecutor
from .views import WorkflowStep

logger = logging.getLogger(__name__)


# ─── param models ────────────────────────────────────────────────────────────


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


class LoopUntilActionParams(BaseModel):
    """Parameters for 'loop_until' control flow action.

    Repeats steps until a condition variable satisfies the operator, or max_loops is reached.
    """

    variable: str = Field(..., description='Variable name to evaluate for the exit condition')
    operator: str = Field(
        default='truthy',
        description='Comparison operator. Supported: truthy, falsy, equals, not_equals, contains, not_empty, empty',
    )
    expected: Any = Field(default=None, description='Expected value for comparison operators')
    max_loops: int = Field(default=10, description='Maximum iterations to prevent infinite loops')
    loop_interval: int = Field(default=0, description='Wait time in milliseconds between each iteration')
    steps: list[dict[str, Any]] = Field(
        ...,
        description=(
            'List of actions to execute in each iteration. '
            'Each action: {"action_name": {"param1": val1, ...}}'
        ),
    )


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


# ─── helpers ─────────────────────────────────────────────────────────────────


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
        # 浅拷贝，避免修改原始字典
        params_raw = dict(params_raw)

    # 从 params_raw 中提取嵌套步骤字段
    # 在 ActionModel schema 中它们与普通参数平级，但 WorkflowStep 将其单独存储
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


# ─── public API ──────────────────────────────────────────────────────────────


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
        if not result.success:
            return ActionResult(error=result.error_message or 'if action failed')
        branch = result.generated_variables.get('__branch_taken__', 'unknown')
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = f'Executed if branch: {branch}'
        logger.info(f'🔀 Record: if action completed (branch={branch})')
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    # action 名称必须为 'if'，以保持 DSL 兼容性
    if_action_impl.__name__ = 'if'

    tools.registry.action(
        description=(
            'Execute a conditional block. Evaluate a variable with an operator; '
            'run then_steps if true, else_steps if false.'
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
        if not result.success:
            return ActionResult(error=result.error_message or 'loop_for action failed')
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = 'loop_for action completed successfully'
        logger.info('🔄 Record: loop_for action completed')
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    loop_for_impl.__name__ = 'loop_for'

    tools.registry.action(
        description='Iterate over a list and execute steps for each item.',
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
        if not result.success:
            return ActionResult(error=result.error_message or 'loop_until action failed')
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = 'loop_until action completed successfully'
        logger.info('🔄 Record: loop_until action completed')
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    loop_until_impl.__name__ = 'loop_until'

    tools.registry.action(
        description='Repeat a block of steps until a condition variable meets the operator criterion.',
        param_model=LoopUntilActionParams,
    )(loop_until_impl)

    # ── set_variable ─────────────────────────────────────────────────────────

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
        if not result.success:
            return ActionResult(error=result.error_message or 'set_variable action failed')
        if result.generated_variables:
            runtime_vars.update(result.generated_variables)
        memory = f"set_variable: '{params.name}' = {result.output!r}"
        logger.info(f'📌 Record: set_variable {params.name} = {result.output!r}')
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    set_variable_impl.__name__ = 'set_variable'

    tools.registry.action(
        description='Assign a static value or evaluate a Python expression and store the result in a named variable.',
        param_model=SetVariableActionParams,
    )(set_variable_impl)

    logger.info('✅ 已成功注册控制流 action：if, loop_for, loop_until, set_variable')
