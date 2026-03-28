from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import BaseMessage, SystemMessage, UserMessage
from browser_use.tools.service import Tools

from .views import (
    ExecutionErrorFeedback,
    WorkflowPlannerContext,
    WorkflowPlannerTurn,
    WorkflowStep,
)


class PlannerStepPlan(BaseModel):
    """Planner output for a single record iteration."""

    thinking: str = Field(default='')
    evaluation_previous_goal: str = Field(default='')
    memory: str = Field(default='')
    next_goal: str = Field(default='')
    is_done: bool = Field(default=False)
    done_text: str | None = Field(default=None)
    steps: list[WorkflowStep] = Field(default_factory=list)


class WorkflowRepairLoop:
    """Structured repair-loop helper for workflow planning."""

    @staticmethod
    def build_repair_hint(error: ExecutionErrorFeedback) -> str:
        step_id = error.failed_step.id or error.action
        return (
            f"Step '{step_id}' failed with {error.error_type}. "
            f"Re-evaluate current page state, resolved_params, runtime_variables, and last_success_step before generating replacement DSL steps."
        )


class WorkflowPlanner:
    """LLM planner that emits workflow DSL steps instead of direct tool actions."""

    CONTROL_FLOW_ACTIONS = {'if', 'loop_for', 'loop_until', 'set_variable'}

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Tools,
        max_actions_per_step: int = 3,
        recent_turn_limit: int = 5,
    ):
        self.llm = llm
        self.tools = tools
        self.max_actions_per_step = max_actions_per_step
        self.recent_turn_limit = recent_turn_limit
        self.repair_loop = WorkflowRepairLoop()

    def build_context(
        self,
        task: str,
        browser_state_summary: BrowserStateSummary,
        runtime_variables: dict[str, Any],
        successful_steps: list[WorkflowStep],
        last_error: ExecutionErrorFeedback | None,
        planner_turns: list[WorkflowPlannerTurn],
        available_file_paths: list[str] | None,
    ) -> WorkflowPlannerContext:
        if last_error and not last_error.repair_hint:
            last_error.repair_hint = self.repair_loop.build_repair_hint(last_error)

        return WorkflowPlannerContext(
            task=task,
            current_url=browser_state_summary.url,
            page_title=browser_state_summary.title,
            available_actions=self._build_action_schemas(browser_state_summary.url),
            runtime_variables=runtime_variables,
            recent_successful_steps=successful_steps[-self.max_actions_per_step :],
            last_error=last_error,
            browser_state_summary=self._browser_state_to_text(browser_state_summary),
            planner_history_summary=self._summarize_turns(planner_turns),
            available_file_paths=list(available_file_paths or []),
        )

    def build_messages(self, context: WorkflowPlannerContext) -> list[BaseMessage]:
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(context)
        return [SystemMessage(content=system_prompt), UserMessage(content=user_prompt)]

    async def plan(
        self,
        task: str,
        browser_state_summary: BrowserStateSummary,
        runtime_variables: dict[str, Any],
        successful_steps: list[WorkflowStep],
        last_error: ExecutionErrorFeedback | None,
        planner_turns: list[WorkflowPlannerTurn],
        available_file_paths: list[str] | None,
    ) -> PlannerStepPlan:
        context = self.build_context(
            task=task,
            browser_state_summary=browser_state_summary,
            runtime_variables=runtime_variables,
            successful_steps=successful_steps,
            last_error=last_error,
            planner_turns=planner_turns,
            available_file_paths=available_file_paths,
        )
        messages = self.build_messages(context)
        response = await self.llm.ainvoke(messages, output_format=PlannerStepPlan)
        plan: PlannerStepPlan = response.completion  # type: ignore[assignment]
        self._validate_plan_actions(plan, context.available_actions)
        return plan

    def summarize_execution_feedback(
        self,
        step_results: list[tuple[WorkflowStep, bool, str | None]],
        error: ExecutionErrorFeedback | None,
    ) -> str:
        lines: list[str] = []
        for step, success, message in step_results:
            status = 'success' if success else 'failure'
            suffix = f' - {message}' if message else ''
            lines.append(f'- {step.id or step.action}: {status}{suffix}')
        if error and error.repair_hint:
            lines.append(f'Repair hint: {error.repair_hint}')
        return '\n'.join(lines)

    def build_repair_hint(self, error: ExecutionErrorFeedback) -> str:
        return self.repair_loop.build_repair_hint(error)

    def _build_system_prompt(self) -> str:
        return f"""
You are the Workflow Planner for Browser Use.
Your role is to run the RECORD phase as:
1. inspect browser snapshot
2. generate WorkflowStep DSL
3. let executor run the DSL
4. inspect structured success/failure feedback
5. emit repaired WorkflowStep DSL until task completion

Hard rules:
- Never emit direct tool calls for immediate execution.
- Emit WorkflowStep DSL only.
- DSL is the persistent representation of tool schemas.
- Use only actions present in available_actions.
- For each action, params must align with the provided action schema.
- Control-flow actions are limited to: if, loop_for, loop_until, set_variable.
- Keep steps explicit, stable, and replay-friendly.
- If there is last_error, generate repair-oriented replacement steps instead of repeating the same failure blindly.
- Produce at most {self.max_actions_per_step} top-level steps unless the task is fully complete.
- If task is complete, set is_done=true and provide complete done_text.
""".strip()

    def _build_user_prompt(self, context: WorkflowPlannerContext) -> str:
        return f"""
<planning_goal>
Generate the next workflow DSL batch for the current record iteration.
</planning_goal>
<task>
{context.task}
</task>
<browser_state>
{context.browser_state_summary}
</browser_state>
<available_actions>
{json.dumps(context.available_actions, ensure_ascii=False, indent=2)}
</available_actions>
<runtime_variables>
{json.dumps(context.runtime_variables, ensure_ascii=False, indent=2)}
</runtime_variables>
<recent_successful_steps>
{self._serialize_steps(context.recent_successful_steps)}
</recent_successful_steps>
<planner_history_summary>
{context.planner_history_summary or 'No planner turns yet.'}
</planner_history_summary>
<last_error>
{json.dumps(context.last_error.model_dump(mode='json'), ensure_ascii=False, indent=2) if context.last_error else 'null'}
</last_error>
<available_file_paths>
{json.dumps(context.available_file_paths, ensure_ascii=False, indent=2)}
</available_file_paths>
<requirements>
- Use WorkflowStep schema strictly.
- Put action parameters under the params field.
- Respect the provided action schema names and parameter structure.
- If you use if/loop_for/loop_until, include nested then_steps/else_steps/steps explicitly.
- Prefer one coherent next batch instead of multiple alternative branches.
- If last_error exists, repair the failing area using the structured feedback.
</requirements>
""".strip()

    def _build_action_schemas(self, page_url: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        registry_actions = self.tools.registry.registry.actions
        for action_name, action_info in registry_actions.items():
            if action_info.domains and not self.tools.registry.registry._match_domains(action_info.domains, page_url):
                continue
            actions.append(
                {
                    'name': action_name,
                    'description': action_info.description,
                    'parameters': action_info.param_model.model_json_schema(),
                }
            )

        for action_name in sorted(self.CONTROL_FLOW_ACTIONS):
            actions.append(
                {
                    'name': action_name,
                    'description': 'Workflow control-flow action',
                    'parameters': {},
                }
            )
        return actions

    def _validate_plan_actions(self, plan: PlannerStepPlan, available_actions: list[dict[str, Any]]) -> None:
        allowed_actions = {item['name'] for item in available_actions}
        for step in plan.steps:
            self._validate_step_action(step, allowed_actions)

    def _validate_step_action(self, step: WorkflowStep, allowed_actions: set[str]) -> None:
        if step.action not in allowed_actions:
            raise ValueError(f"Planner emitted unsupported action '{step.action}'")
        for child in step.then_steps:
            self._validate_step_action(child, allowed_actions)
        for child in step.else_steps:
            self._validate_step_action(child, allowed_actions)
        for child in step.steps:
            self._validate_step_action(child, allowed_actions)

    def _browser_state_to_text(self, browser_state_summary: BrowserStateSummary) -> str:
        dom_text = 'empty page'
        if browser_state_summary.dom_state:
            dom_text = browser_state_summary.dom_state.llm_representation()
        return (
            f'Current URL: {browser_state_summary.url}\n'
            f'Title: {browser_state_summary.title}\n'
            f'Open Tabs: {browser_state_summary.tabs}\n'
            f'Interactive Elements:\n{dom_text}'
        )

    def _summarize_turns(self, planner_turns: list[WorkflowPlannerTurn]) -> str:
        if not planner_turns:
            return ''
        recent_turns = planner_turns[-self.recent_turn_limit :]
        lines: list[str] = []
        for turn in recent_turns:
            lines.append(f'<turn step="{turn.step_number}">')
            lines.append('plan:')
            lines.append(self._serialize_steps(turn.plan))
            if turn.result_summary:
                lines.append(f'result_summary:\n{turn.result_summary}')
            if turn.error:
                lines.append(f'error:\n{json.dumps(turn.error.model_dump(mode="json"), ensure_ascii=False, indent=2)}')
            lines.append('</turn>')
        return '\n'.join(lines)

    def _serialize_steps(self, steps: list[WorkflowStep]) -> str:
        if not steps:
            return '[]'
        payload = [self._step_to_payload(step) for step in steps]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _step_to_payload(self, step: WorkflowStep) -> dict[str, Any]:
        payload: dict[str, Any] = {'action': step.action, 'params': step.params}
        if step.id:
            payload['id'] = step.id
        if step.name:
            payload['name'] = step.name
        if step.comment:
            payload['comment'] = step.comment
        if step.timeout_ms is not None:
            payload['timeout_ms'] = step.timeout_ms
        if step.optional:
            payload['optional'] = True
        if step.then_steps:
            payload['then_steps'] = [self._step_to_payload(child) for child in step.then_steps]
        if step.else_steps:
            payload['else_steps'] = [self._step_to_payload(child) for child in step.else_steps]
        if step.steps:
            payload['steps'] = [self._step_to_payload(child) for child in step.steps]
        return payload
