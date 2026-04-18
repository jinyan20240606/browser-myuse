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
                    idx for idx, elem in selector_map.items() if elem.attributes and elem.attributes.get(attr_key) == attr_val
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
                '❌ DSL locator failed to resolve element on current page. '
                'Tried: EXACT hash → STABLE hash → XPATH → ATTRIBUTE matching. '
                'Action may fail or target wrong element.'
            )

        return clean_params

    async def _execute_action_step(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        logger.info(
            f'⚙️ ACTION开始: step={step.id or step.action}, action={step.action}, '
            f'resolved_params={resolved_params}, available_vars={sorted(variables.keys())}'
        )

        if 'locator' in resolved_params:
            resolved_params = await self._resolve_element_index(resolved_params)
            logger.info(
                f'⚙️ ACTION定位后参数: step={step.id or step.action}, action={step.action}, '
                f'resolved_params={resolved_params}'
            )

        max_retries = 1 if step.optional else 3
        last_error: str | None = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    f'⚙️ ACTION执行: step={step.id or step.action}, action={step.action}, '
                    f'attempt={attempt + 1}/{max_retries}'
                )
                result = await self.tools.registry.execute_action(
                    action_name=step.action,
                    params=resolved_params,
                    browser_session=self.browser_session,
                )
                action_result = self._normalize_action_result(result)
                logger.info(
                    f'⚙️ ACTION结果: step={step.id or step.action}, action={step.action}, '
                    f'extracted_content={action_result.extracted_content!r}, '
                    f'long_term_memory={action_result.long_term_memory!r}, '
                    f'metadata={action_result.metadata!r}, error={action_result.error!r}'
                )
                if action_result.error:
                    raise RuntimeError(action_result.error)

                validation_error = self._validate_action_result(step, resolved_params, action_result)
                if validation_error:
                    logger.warning(
                        f'⚙️ ACTION校验失败: step={step.id or step.action}, action={step.action}, '
                        f'validation_error={validation_error}'
                    )
                    raise RuntimeError(validation_error)

                generated_variables = self._extract_output_variables(resolved_params, action_result)
                if generated_variables:
                    logger.info(
                        f'⚙️ ACTION产出变量: step={step.id or step.action}, action={step.action}, '
                        f'generated_variables={generated_variables}'
                    )
                variables.update(generated_variables)
                logger.info(
                    f'⚙️ ACTION完成: step={step.id or step.action}, action={step.action}, '
                    f'available_vars_after={sorted(variables.keys())}'
                )
                return StepExecutionResult(
                    step=step,
                    success=True,
                    output=action_result,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                    state_changed=self._did_action_change_state(step.action, action_result),
                    semantic_success=True,
                    debug_trace=[
                        {
                            'action': step.action,
                            'resolved_params': resolved_params,
                            'generated_variables': generated_variables,
                            'available_vars_before': sorted(variables.keys()),
                        }
                    ],
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    f'⚙️ ACTION失败: step={step.id or step.action}, action={step.action}, '
                    f'attempt={attempt + 1}/{max_retries}, error={last_error}'
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        return StepExecutionResult(
            step=step,
            success=False,
            error_message=f'Failed after {max_retries} attempts. Last error: {last_error}',
            resolved_params=resolved_params,
            semantic_success=False,
            debug_trace=[
                {
                    'action': step.action,
                    'resolved_params': resolved_params,
                    'error': last_error,
                    'available_vars': sorted(variables.keys()),
                }
            ],
        )

    async def _execute_if(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        condition_result, condition_details = self._evaluate_condition_with_details(resolved_params, variables)
        branch_name = 'then' if condition_result else 'else'
        branch = step.then_steps if condition_result else step.else_steps
        logger.info(
            f'🔀 IF执行: step={step.id or step.action}, variable={resolved_params.get("variable")!r}, '
            f'operator={resolved_params.get("operator", "truthy")!r}, expected={resolved_params.get("expected")!r}, '
            f'branch={branch_name}, nested_steps={len(branch)}'
        )

        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {
            '__branch_taken__': branch_name,
            '__condition_evaluated__': True,
            '__condition_result__': condition_result,
        }
        debug_trace: list[dict[str, Any]] = [
            {
                'action': 'if',
                'branch_taken': branch_name,
                'condition': condition_details,
            }
        ]
        semantic_success = True
        state_changed = False

        if not condition_details['variable_exists']:
            missing_var = condition_details['variable_name']
            error_message = (
                f"if condition variable '{missing_var}' was not established at runtime; "
                'repair the prior DSL fragment or use an action that stores the inspection result before branching'
            )
            generated_variables['__semantic_success__'] = False
            generated_variables['__semantic_error__'] = error_message
            logger.warning(f'🔀 IF条件变量缺失: step={step.id or step.action}, variable={missing_var!r}')
            return StepExecutionResult(
                step=step,
                success=False,
                output=outputs,
                error_message=error_message,
                resolved_params=resolved_params,
                generated_variables=generated_variables,
                semantic_success=False,
                debug_trace=debug_trace,
            )

        for nested_step in branch:
            logger.info(f'🔀 IF分支子步骤: parent={step.id or step.action}, child={nested_step.id or nested_step.action}')
            nested_result = await self.execute(nested_step, variables)
            if nested_result.output is not None:
                outputs.append(nested_result.output)
            if nested_result.generated_variables:
                variables.update(nested_result.generated_variables)
                generated_variables.update(nested_result.generated_variables)
            if nested_result.debug_trace:
                debug_trace.extend(nested_result.debug_trace)
            state_changed = state_changed or nested_result.state_changed
            semantic_success = semantic_success and nested_result.semantic_success
            if not nested_result.success and not nested_step.optional:
                logger.warning(
                    f'🔀 IF分支失败: parent={step.id or step.action}, child={nested_step.id or nested_step.action}, '
                    f'error={nested_result.error_message}'
                )
                return StepExecutionResult(
                    step=step,
                    success=False,
                    output=outputs,
                    error_message=nested_result.error_message,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                    state_changed=state_changed,
                    semantic_success=semantic_success,
                    debug_trace=debug_trace,
                )

        generated_variables['__semantic_success__'] = semantic_success
        logger.info(
            f'🔀 IF完成: step={step.id or step.action}, branch={branch_name}, '
            f'generated_keys={list(generated_variables.keys())}'
        )
        return StepExecutionResult(
            step=step,
            success=True,
            output=outputs,
            resolved_params=resolved_params,
            generated_variables=generated_variables,
            state_changed=state_changed,
            semantic_success=semantic_success,
            debug_trace=debug_trace,
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
                semantic_success=False,
                debug_trace=[{'action': 'loop_for', 'error': "items_not_list", 'resolved_params': resolved_params}],
            )

        item_variable = resolved_params.get('item_variable', 'item')
        index_variable = resolved_params.get('index_variable')
        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {}
        debug_trace: list[dict[str, Any]] = []
        semantic_success = True
        state_changed = False
        matched_iterations: list[int] = []
        previous_values = {
            item_variable: variables.get(item_variable),
            index_variable: variables.get(index_variable) if index_variable else None,
        }
        logger.info(
            f'🔁 LOOP_FOR开始: step={step.id or step.action}, items_count={len(items)}, '
            f'item_variable={item_variable}, index_variable={index_variable}'
        )

        try:
            for index, item in enumerate(items):
                logger.info(f'🔁 LOOP_FOR迭代: step={step.id or step.action}, index={index}, item={item!r}')
                variables[item_variable] = item
                generated_variables[item_variable] = item
                if index_variable:
                    variables[index_variable] = index
                    generated_variables[index_variable] = index

                iteration_trace: dict[str, Any] = {
                    'iteration': index,
                    'item': item,
                    'nested_steps': [],
                }

                for nested_step in step.steps:
                    logger.info(
                        f'🔁 LOOP_FOR子步骤: parent={step.id or step.action}, index={index}, '
                        f'child={nested_step.id or nested_step.action}'
                    )
                    nested_result = await self.execute(nested_step, variables)
                    if nested_result.output is not None:
                        outputs.append(nested_result.output)
                    if nested_result.generated_variables:
                        variables.update(nested_result.generated_variables)
                        generated_variables.update(nested_result.generated_variables)
                    if nested_result.debug_trace:
                        iteration_trace['nested_steps'].extend(nested_result.debug_trace)
                    state_changed = state_changed or nested_result.state_changed
                    semantic_success = semantic_success and nested_result.semantic_success

                    if nested_result.generated_variables.get('__branch_taken__') == 'then':
                        matched_iterations.append(index)

                    if not nested_result.success and not nested_step.optional:
                        logger.warning(
                            f'🔁 LOOP_FOR子步骤失败: parent={step.id or step.action}, index={index}, '
                            f'child={nested_step.id or nested_step.action}, error={nested_result.error_message}'
                        )
                        iteration_trace['error'] = nested_result.error_message
                        debug_trace.append(iteration_trace)
                        return StepExecutionResult(
                            step=step,
                            success=False,
                            output=outputs,
                            error_message=nested_result.error_message,
                            resolved_params=resolved_params,
                            generated_variables=generated_variables,
                            state_changed=state_changed,
                            semantic_success=semantic_success,
                            debug_trace=debug_trace,
                        )

                iteration_trace['state_changed'] = state_changed
                debug_trace.append(iteration_trace)
        finally:
            self._restore_loop_variables(variables, previous_values, item_variable, index_variable)

        generated_variables['__loop_iterations__'] = len(items)
        generated_variables['__loop_matched_iterations__'] = matched_iterations
        generated_variables['__semantic_success__'] = semantic_success
        logger.info(
            f'🔁 LOOP_FOR完成: step={step.id or step.action}, iterations={len(items)}, '
            f'generated_keys={list(generated_variables.keys())}'
        )
        return StepExecutionResult(
            step=step,
            success=True,
            output=outputs,
            resolved_params=resolved_params,
            generated_variables=generated_variables,
            state_changed=state_changed,
            semantic_success=semantic_success,
            debug_trace=debug_trace,
        )

    async def _execute_loop_until(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        max_loops = int(resolved_params.get('max_loops', 10))
        loop_interval_ms = int(resolved_params.get('loop_interval', 0) or 0)
        outputs: list[Any] = []
        generated_variables: dict[str, Any] = {}
        debug_trace: list[dict[str, Any]] = []
        semantic_success = True
        state_changed = False
        logger.info(
            f'🔂 LOOP_UNTIL开始: step={step.id or step.action}, variable={resolved_params.get("variable")!r}, '
            f'operator={resolved_params.get("operator", "truthy")!r}, expected={resolved_params.get("expected")!r}, '
            f'max_loops={max_loops}, loop_interval_ms={loop_interval_ms}'
        )

        for iteration in range(max_loops):
            condition_met, condition_details = self._evaluate_condition_with_details(resolved_params, variables)
            debug_trace.append({'iteration': iteration, 'condition': condition_details, 'condition_met': condition_met})
            logger.info(
                f'🔂 LOOP_UNTIL检查: step={step.id or step.action}, iteration={iteration}, condition_met={condition_met}'
            )
            if condition_met:
                generated_variables['__loop_until_exit_iteration__'] = iteration
                generated_variables['__semantic_success__'] = semantic_success
                logger.info(f'🔂 LOOP_UNTIL提前结束: step={step.id or step.action}, iteration={iteration}')
                return StepExecutionResult(
                    step=step,
                    success=True,
                    output=outputs,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                    state_changed=state_changed,
                    semantic_success=semantic_success,
                    debug_trace=debug_trace,
                )

            if not condition_details['variable_exists'] and iteration == 0:
                error_message = (
                    f"loop_until condition variable '{condition_details['variable_name']}' was not established at runtime; "
                    'repair the preceding DSL fragment or save the inspection result before looping'
                )
                generated_variables['__semantic_success__'] = False
                generated_variables['__semantic_error__'] = error_message
                logger.warning(
                    f'🔂 LOOP_UNTIL条件变量缺失: step={step.id or step.action}, '
                    f"variable={condition_details['variable_name']!r}"
                )
                return StepExecutionResult(
                    step=step,
                    success=False,
                    output=outputs,
                    error_message=error_message,
                    resolved_params=resolved_params,
                    generated_variables=generated_variables,
                    state_changed=state_changed,
                    semantic_success=False,
                    debug_trace=debug_trace,
                )

            for nested_step in step.steps:
                logger.info(
                    f'🔂 LOOP_UNTIL子步骤: parent={step.id or step.action}, iteration={iteration}, '
                    f'child={nested_step.id or nested_step.action}'
                )
                nested_result = await self.execute(nested_step, variables)
                if nested_result.output is not None:
                    outputs.append(nested_result.output)
                if nested_result.generated_variables:
                    variables.update(nested_result.generated_variables)
                    generated_variables.update(nested_result.generated_variables)
                if nested_result.debug_trace:
                    debug_trace.extend(nested_result.debug_trace)
                state_changed = state_changed or nested_result.state_changed
                semantic_success = semantic_success and nested_result.semantic_success
                if not nested_result.success and not nested_step.optional:
                    logger.warning(
                        f'🔂 LOOP_UNTIL子步骤失败: parent={step.id or step.action}, iteration={iteration}, '
                        f'child={nested_step.id or nested_step.action}, error={nested_result.error_message}'
                    )
                    return StepExecutionResult(
                        step=step,
                        success=False,
                        output=outputs,
                        error_message=nested_result.error_message,
                        resolved_params=resolved_params,
                        generated_variables=generated_variables,
                        state_changed=state_changed,
                        semantic_success=semantic_success,
                        debug_trace=debug_trace,
                    )

            if loop_interval_ms > 0:
                logger.info(
                    f'🔂 LOOP_UNTIL等待: step={step.id or step.action}, iteration={iteration}, sleep_ms={loop_interval_ms}'
                )
                await asyncio.sleep(loop_interval_ms / 1000)

        final_condition_met, final_condition_details = self._evaluate_condition_with_details(resolved_params, variables)
        debug_trace.append({'final_condition': final_condition_details, 'final_condition_met': final_condition_met})
        logger.info(
            f'🔂 LOOP_UNTIL结束检查: step={step.id or step.action}, final_condition_met={final_condition_met}'
        )
        if final_condition_met:
            generated_variables['__semantic_success__'] = semantic_success
            return StepExecutionResult(
                step=step,
                success=True,
                output=outputs,
                resolved_params=resolved_params,
                generated_variables=generated_variables,
                state_changed=state_changed,
                semantic_success=semantic_success,
                debug_trace=debug_trace,
            )

        logger.warning(f'🔂 LOOP_UNTIL超限失败: step={step.id or step.action}, max_loops={max_loops}')
        return StepExecutionResult(
            step=step,
            success=False,
            output=outputs,
            error_message=f'loop_until condition was not met after {max_loops} loops',
            resolved_params=resolved_params,
            generated_variables=generated_variables,
            state_changed=state_changed,
            semantic_success=False,
            debug_trace=debug_trace,
        )

    async def _execute_set_variable(self, step: WorkflowStep, variables: dict[str, Any]) -> StepExecutionResult:
        resolved_params = self._resolve_variables(step.params, variables)
        logger.info(
            f'🧮 SET_VARIABLE开始: step={step.id or step.action}, resolved_params={resolved_params}, '
            f'available_vars={sorted(variables.keys())}'
        )
        variable_name = resolved_params.get('name')
        if not variable_name or not isinstance(variable_name, str):
            return StepExecutionResult(
                step=step,
                success=False,
                error_message="set_variable requires a string 'name'",
                resolved_params=resolved_params,
                semantic_success=False,
                debug_trace=[{'action': 'set_variable', 'error': 'missing_name', 'resolved_params': resolved_params}],
            )

        expression = resolved_params.get('expression')
        try:
            if expression not in (None, ''):
                value = self._evaluate_expression(str(expression), variables)
                logger.info(
                    f'🧮 SET_VARIABLE表达式: step={step.id or step.action}, name={variable_name}, '
                    f'expression={expression!r}, value={value!r}'
                )
            else:
                value = resolved_params.get('value')
                logger.info(f'🧮 SET_VARIABLE赋值: step={step.id or step.action}, name={variable_name}, value={value!r}')
        except Exception as exc:
            logger.error(
                f'🧮 SET_VARIABLE失败: step={step.id or step.action}, name={variable_name}, '
                f'expression={expression!r}, error={exc}, available_vars={variables}'
            )
            return StepExecutionResult(
                step=step,
                success=False,
                error_message=f'set_variable evaluation failed: {exc}',
                resolved_params=resolved_params,
                semantic_success=False,
                debug_trace=[
                    {
                        'action': 'set_variable',
                        'name': variable_name,
                        'expression': expression,
                        'error': str(exc),
                        'available_vars': dict(variables),
                    }
                ],
            )

        variables[variable_name] = value
        logger.info(
            f'🧮 SET_VARIABLE完成: step={step.id or step.action}, name={variable_name}, '
            f'value={value!r}, available_vars_after={sorted(variables.keys())}'
        )
        return StepExecutionResult(
            step=step,
            success=True,
            output=value,
            resolved_params=resolved_params,
            generated_variables={variable_name: value},
            state_changed=True,
            semantic_success=True,
            debug_trace=[
                {
                    'action': 'set_variable',
                    'name': variable_name,
                    'value': value,
                    'available_vars_before': sorted(variables.keys()),
                }
            ],
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

        if step.action in {'evaluate', 'query_elements', 'element_exists', 'get_text', 'get_attribute'}:
            output_variable = resolved_params.get('output_variable')
            if not output_variable or not isinstance(output_variable, str):
                return f"{step.action} action in workflow control flow requires output_variable so later branches can read the result"

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

        extracted_value = self._select_output_variable_value(action_result)
        logger.info(
            f'🧾 输出变量写入: output_variable={output_variable}, '
            f'value_source={self._describe_output_variable_source(action_result)}, value={extracted_value!r}'
        )
        return {output_variable: extracted_value}

    def _select_output_variable_value(self, action_result: ActionResult) -> Any:
        if action_result.extracted_content is not None:
            return action_result.extracted_content
        if action_result.long_term_memory is not None:
            return action_result.long_term_memory
        if action_result.metadata is not None:
            return action_result.metadata
        return None

    def _describe_output_variable_source(self, action_result: ActionResult) -> str:
        if action_result.extracted_content is not None:
            return 'extracted_content'
        if action_result.long_term_memory is not None:
            return 'long_term_memory'
        if action_result.metadata is not None:
            return 'metadata'
        return 'none'

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
        condition_result, _ = self._evaluate_condition_with_details(resolved_params, variables)
        return condition_result

    def _evaluate_condition_with_details(
        self, resolved_params: dict[str, Any], variables: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        variable_ref = resolved_params.get('variable')
        operator = str(resolved_params.get('operator', 'truthy'))
        expected = resolved_params.get('expected')

        variable_name: str | None = variable_ref if isinstance(variable_ref, str) else None
        looked_up_value = self._lookup_variable(variable_name, variables) if variable_name else None
        variable_exists = looked_up_value is not None if variable_name else True

        if isinstance(variable_ref, str):
            actual = looked_up_value if variable_exists else variable_ref
        else:
            actual = variable_ref

        result = self._apply_condition_operator(actual, operator, expected)
        details = {
            'variable_name': variable_name,
            'variable_exists': variable_exists,
            'actual': actual,
            'expected': expected,
            'operator': operator,
        }
        return result, details

    def _apply_condition_operator(self, actual: Any, operator: str, expected: Any) -> bool:
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
                try:
                    return str(expected) in str(actual)
                except Exception:
                    return False
        raise ValueError(f'Unsupported workflow condition operator: {operator}')

    def _evaluate_expression(self, expression: str, variables: dict[str, Any]) -> Any:
        safe_globals = {'__builtins__': {}}
        safe_locals = dict(variables)
        return eval(expression, safe_globals, safe_locals)

    def _did_action_change_state(self, action_name: str, action_result: ActionResult) -> bool:
        if action_name in {'click', 'input', 'fill', 'navigate', 'open', 'scroll', 'send_keys', 'select_option'}:
            return True
        if action_name == 'evaluate':
            return False
        return bool(action_result.extracted_content or action_result.long_term_memory or action_result.metadata)

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
