from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from browser_use.agent.views import ActionResult
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from .views import ExecutionErrorFeedback, StepExecutionResult, WorkflowStep

logger = logging.getLogger(__name__)


class StepExecutor:
    """Execute workflow steps via the existing tool registry."""

    VARIABLE_PATTERN = re.compile(r'\{\{(.*?)\}\}|\$\{(.*?)\}')
    CONTROL_FLOW_ACTIONS = {'if', 'loop_for', 'loop_until', 'set_variable'}

    def __init__(self, tools: Tools, browser_session: BrowserSession):
        self.tools = tools
        self.browser_session = browser_session

    async def execute(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        logger.info(f'Executing step {step.id or ""}: {step.action}')
        self._validate_step_schema(step)

        if step.action == 'if':
            return await self._execute_if(step, variables)
        if step.action == 'loop_for':
            return await self._execute_loop_for(step, variables)
        if step.action == 'loop_until':
            return await self._execute_loop_until(step, variables)
        if step.action == 'set_variable':
            return await self._execute_set_variable(step, variables)

        return await self._execute_action_step(step, variables)

    async def _resolve_element_index(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resolve stable locators (element_hash/stable_hash/xpath/attributes) to current page index.

        When the DSL was recorded, volatile DOM indices were replaced with stable locators
        inside a 'locator' object.
        At replay time this method resolves the correct index from the live selector map using
        the same cascading matching strategy as `_update_action_indices` in Agent service.

        The locator keys are stripped from the params before the action is dispatched.
        """
        if 'locator' not in params or not isinstance(params['locator'], dict):
            return params

        locator = params['locator']
        try:
            state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
        except Exception:
            # If we can't get DOM state, just strip the locator keys and use whatever index is there
            return {k: v for k, v in params.items() if k != 'locator'}

        selector_map = (state.dom_state.selector_map or {}) if state and state.dom_state else {}
        element_hash = locator.get('element_hash')
        stable_hash = locator.get('stable_hash')
        xpath = locator.get('xpath')
        attributes: dict[str, str] = locator.get('attributes') or {}
        resolved_index: int | None = None
        match_level: str | None = None

        logger.debug(
            f'🔍 Resolving DSL locator: '
            f'hash={element_hash} stable_hash={stable_hash} xpath={xpath[-40:] if xpath else None}'
        )

        # Level 1: EXACT hash match
        if element_hash is not None:
            for idx, elem in selector_map.items():
                if hash(elem) == element_hash:
                    resolved_index = idx
                    match_level = 'EXACT'
                    break

        # Level 2: STABLE hash match (dynamic classes filtered)
        if resolved_index is None and stable_hash is not None:
            for idx, elem in selector_map.items():
                if elem.compute_stable_hash() == stable_hash:
                    resolved_index = idx
                    match_level = 'STABLE'
                    break

        # Level 3: XPATH match
        if resolved_index is None and xpath:
            for idx, elem in selector_map.items():
                if elem.xpath == xpath:
                    resolved_index = idx
                    match_level = 'XPATH'
                    break

        # Level 4: Unique attribute fallback (id, name, aria-label)
        if resolved_index is None and attributes:
            for attr_key in ('name', 'id', 'aria-label'):
                attr_val = attributes.get(attr_key)
                if not attr_val:
                    continue
                # Find exactly one match for the attribute to be safe
                candidates = [
                    idx for idx, elem in selector_map.items()
                    if elem.attributes and elem.attributes.get(attr_key) == attr_val
                ]
                if len(candidates) == 1:
                    resolved_index = candidates[0]
                    match_level = f'ATTRIBUTE({attr_key})'
                    break

        # Build clean params (strip all locator metadata)
        clean_params = {k: v for k, v in params.items() if k != 'locator'}
        
        if resolved_index is not None:
            clean_params['index'] = resolved_index
            logger.info(f'✅ DSL locator resolved to index {resolved_index} (matched at {match_level} level)')
        else:
            logger.error(
                f'❌ DSL locator failed to resolve element on current page. '
                f'Tried: EXACT hash → STABLE hash → XPATH → ATTRIBUTE matching. '
                f'Action may fail or target wrong element.'
            )
            # If we completely failed to resolve and there is no fallback index,
            # we should still try to execute (it will likely fail cleanly with a "missing index" error in Pydantic)
            
        return clean_params

    async def _execute_action_step(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)

        # If the step carries stable locators, re-resolve the index
        if 'locator' in resolved_params:
            resolved_params = await self._resolve_element_index(resolved_params)

        max_retries = 1 if step.optional else 3
        last_error: str | None = None

        for attempt in range(max_retries):
            try:
                result = await self.tools.registry.execute_action(
                    action_name=step.action,
                    params=resolved_params,
                    browser_session=self.browser_session,
                )
                action_result = self._normalize_action_result(result)
                if action_result.error:
                    raise RuntimeError(action_result.error)

                validation_error = self._validate_action_result(step, resolved_params, action_result)
                if validation_error:
                    raise RuntimeError(validation_error)

                generated_variables = self._extract_output_variables(resolved_params, action_result)
                variables.update(generated_variables)
                return StepExecutionResult(
                    step=step,
                    success=True,
                    output=action_result,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f'Step {step.id} failed attempt {attempt + 1}/{max_retries}: {last_error}')
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        return StepExecutionResult(
            step=step,
            success=False,
            error_message=f'Failed after {max_retries} attempts. Last error: {last_error}',
            resolved_params=resolved_params,
        )

    async def _execute_if(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        condition_result = self._evaluate_condition(resolved_params, variables)
        branch = step.then_steps if condition_result else step.else_steps

        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {'__branch_taken__': 'then' if condition_result else 'else'}
        for nested_step in branch:
            nested_result = await self.execute(nested_step, variables)
            if nested_result.output is not None:
                outputs.append(nested_result.output)
            if nested_result.generated_variables:
                generated_variables.update(nested_result.generated_variables)
            if not nested_result.success and not nested_step.optional:
                return StepExecutionResult(
                    step=step,
                    success=False,
                    output=outputs,
                    error_message=nested_result.error_message,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                )

        return StepExecutionResult(
            step=step,
            success=True,
            output=outputs,
            resolved_params=resolved_params,
            generated_variables=generated_variables,
        )

    async def _execute_loop_for(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        items = resolved_params.get('items', [])
        if not isinstance(items, list):
            return StepExecutionResult(
                step=step,
                success=False,
                error_message="loop_for 'items' must resolve to a list",
                resolved_params=resolved_params,
            )

        item_variable = resolved_params.get('item_variable', 'item')
        index_variable = resolved_params.get('index_variable')
        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {}
        previous_values = {
            item_variable: variables.get(item_variable),
            index_variable: variables.get(index_variable) if index_variable else None,
        }

        for index, item in enumerate(items):
            variables[item_variable] = item
            generated_variables[item_variable] = item
            if index_variable:
                variables[index_variable] = index
                generated_variables[index_variable] = index

            for nested_step in step.steps:
                nested_result = await self.execute(nested_step, variables)
                if nested_result.output is not None:
                    outputs.append(nested_result.output)
                if nested_result.generated_variables:
                    generated_variables.update(nested_result.generated_variables)
                if not nested_result.success and not nested_step.optional:
                    self._restore_loop_variables(variables, previous_values, item_variable, index_variable)
                    return StepExecutionResult(
                        step=step,
                        success=False,
                        output=outputs,
                        error_message=nested_result.error_message,
                        resolved_params=resolved_params,
                        generated_variables=generated_variables,
                    )

        self._restore_loop_variables(variables, previous_values, item_variable, index_variable)
        return StepExecutionResult(
            step=step,
            success=True,
            output=outputs,
            resolved_params=resolved_params,
            generated_variables=generated_variables,
        )

    async def _execute_loop_until(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        max_loops = int(resolved_params.get('max_loops', 10))
        loop_interval_ms = int(resolved_params.get('loop_interval', 0) or 0)
        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {}

        for _ in range(max_loops):
            if self._evaluate_condition(resolved_params, variables):
                return StepExecutionResult(
                    step=step,
                    success=True,
                    output=outputs,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                )

            for nested_step in step.steps:
                nested_result = await self.execute(nested_step, variables)
                if nested_result.output is not None:
                    outputs.append(nested_result.output)
                if nested_result.generated_variables:
                    generated_variables.update(nested_result.generated_variables)
                if not nested_result.success and not nested_step.optional:
                    return StepExecutionResult(
                        step=step,
                        success=False,
                        output=outputs,
                        error_message=nested_result.error_message,
                        resolved_params=resolved_params,
                        generated_variables=generated_variables,
                    )

            if loop_interval_ms > 0:
                await asyncio.sleep(loop_interval_ms / 1000)

        if self._evaluate_condition(resolved_params, variables):
            return StepExecutionResult(
                step=step,
                success=True,
                output=outputs,
                resolved_params=resolved_params,
                generated_variables=generated_variables,
            )

        return StepExecutionResult(
            step=step,
            success=False,
            output=outputs,
            error_message=f'loop_until condition was not met after {max_loops} loops',
            resolved_params=resolved_params,
            generated_variables=generated_variables,
        )

    async def _execute_set_variable(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        variable_name = resolved_params.get('name')
        if not variable_name or not isinstance(variable_name, str):
            return StepExecutionResult(
                step=step,
                success=False,
                error_message="set_variable requires a string 'name'",
                resolved_params=resolved_params,
            )

        if 'expression' in resolved_params:
            value = self._evaluate_expression(str(resolved_params['expression']), variables)
        else:
            value = resolved_params.get('value')

        variables[variable_name] = value
        return StepExecutionResult(
            step=step,
            success=True,
            output=value,
            resolved_params=resolved_params,
            generated_variables={variable_name: value},
        )

    def _validate_step_schema(self, step: WorkflowStep) -> None:
        if step.action in self.CONTROL_FLOW_ACTIONS:
            return
        if step.action not in self.tools.registry.registry.actions:
            raise ValueError(f"Workflow step uses unregistered action '{step.action}'")

    def _validate_action_result(self, step: WorkflowStep, resolved_params: dict[str, Any], action_result: ActionResult) -> str | None:
        if step.action in {'input', 'fill'}:
            expected_text = resolved_params.get('text')
            if expected_text not in (None, '') and not (
                action_result.extracted_content or action_result.long_term_memory or action_result.metadata
            ):
                return 'Input action completed without any observable confirmation'

        if step.action in {'navigate', 'open'}:
            expected_url = resolved_params.get('url')
            if expected_url and not (action_result.extracted_content or action_result.long_term_memory):
                return 'Navigation action completed without any observable confirmation'

        if resolved_params.get('output_variable') and action_result.extracted_content is None and action_result.long_term_memory is None:
            return 'Action declared output_variable but produced no extractable output'

        return None

    def _restore_loop_variables(
        self,
        variables: dict[str, Any],
        previous_values: dict[str | None, Any],
        item_variable: str,
        index_variable: str | None,
    ) -> None:
        previous_item = previous_values.get(item_variable)
        if previous_item is None and item_variable in variables:
            variables.pop(item_variable, None)
        elif previous_item is not None:
            variables[item_variable] = previous_item

        if index_variable:
            previous_index = previous_values.get(index_variable)
            if previous_index is None and index_variable in variables:
                variables.pop(index_variable, None)
            elif previous_index is not None:
                variables[index_variable] = previous_index

    def _normalize_action_result(self, result: Any) -> ActionResult:
        if isinstance(result, ActionResult):
            return result
        if isinstance(result, str):
            return ActionResult(extracted_content=result)
        if result is None:
            return ActionResult()
        raise ValueError(f'Invalid action result type: {type(result)} of {result}')

    def _extract_output_variables(self, resolved_params: dict[str, Any], action_result: ActionResult) -> dict[str, Any]:
        output_variable = resolved_params.get('output_variable')
        if not output_variable or not isinstance(output_variable, str):
            return {}

        if action_result.extracted_content is not None:
            return {output_variable: action_result.extracted_content}
        if action_result.long_term_memory is not None:
            return {output_variable: action_result.long_term_memory}
        if action_result.metadata is not None:
            return {output_variable: action_result.metadata}
        return {output_variable: None}

    def _resolve_variables(self, params: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        return {key: self._resolve_value(value, variables) for key, value in params.items()}

    def _resolve_value(self, value: Any, variables: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self._resolve_string(value, variables)
        if isinstance(value, list):
            return [self._resolve_value(item, variables) for item in value]
        if isinstance(value, dict):
            return {k: self._resolve_value(v, variables) for k, v in value.items()}
        return value

    def _resolve_string(self, value: str, variables: dict[str, Any]) -> Any:
        matches = list(self.VARIABLE_PATTERN.finditer(value))
        if not matches:
            return value

        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            variable_name = self._extract_variable_name(matches[0])
            return self._lookup_variable(variable_name, variables)

        def replace_match(match: re.Match[str]) -> str:
            variable_name = self._extract_variable_name(match)
            resolved = self._lookup_variable(variable_name, variables)
            return '' if resolved is None else str(resolved)

        return self.VARIABLE_PATTERN.sub(replace_match, value)

    def _extract_variable_name(self, match: re.Match[str]) -> str:
        return (match.group(1) or match.group(2) or '').strip()

    def _lookup_variable(self, variable_name: str, variables: dict[str, Any]) -> Any:
        if not variable_name:
            return None

        current: Any = variables
        for part in variable_name.split('.'):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue

            if isinstance(current, (list, tuple)) and part.isdigit():
                index = int(part)
                if 0 <= index < len(current):
                    current = current[index]
                    continue

            if hasattr(current, part):
                current = getattr(current, part)
                continue

            return None
        return current

    def _evaluate_condition(self, resolved_params: dict[str, Any], variables: dict[str, Any]) -> bool:
        variable_name = resolved_params.get('variable')
        operator = str(resolved_params.get('operator', 'truthy'))
        expected = resolved_params.get('expected')
        actual = self._lookup_variable(str(variable_name), variables) if variable_name else None

        if operator == 'truthy':
            return bool(actual)
        if operator == 'falsy':
            return not bool(actual)
        if operator == 'equals':
            return actual == expected
        if operator == 'not_equals':
            return actual != expected
        if operator == 'greater_than':
            return actual is not None and expected is not None and actual > expected
        if operator == 'greater_than_or_equal':
            return actual is not None and expected is not None and actual >= expected
        if operator == 'less_than':
            return actual is not None and expected is not None and actual < expected
        if operator == 'less_than_or_equal':
            return actual is not None and expected is not None and actual <= expected
        if operator == 'not_empty':
            return actual not in (None, '', [], {}, ())
        if operator == 'empty':
            return actual in (None, '', [], {}, ())
        if operator == 'contains':
            if actual is None:
                return False
            try:
                return bool(expected in actual)
            except TypeError:
                return False
        raise ValueError(f'Unsupported workflow condition operator: {operator}')

    def _evaluate_expression(self, expression: str, variables: dict[str, Any]) -> Any:
        safe_globals = {'__builtins__': {}}
        safe_locals = dict(variables)
        return eval(expression, safe_globals, safe_locals)

    async def create_error_feedback(
        self,
        step: WorkflowStep,
        error_msg: str,
        runtime_vars: dict[str, Any],
        last_success: WorkflowStep | None,
        resolved_params: dict[str, Any] | None = None,
    ) -> ExecutionErrorFeedback:
        snapshot = None
        try:
            state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
            if state:
                snapshot = f'URL: {state.url}, Title: {state.title}'
        except Exception:
            pass

        return ExecutionErrorFeedback(
            failed_step=step,
            action=step.action,
            params=step.params,
            resolved_params=resolved_params or {},
            error_type='ExecutionError',
            error_message=error_msg,
            page_snapshot=snapshot,
            last_success_step=last_success,
            runtime_variables=runtime_vars,
        )
