from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from .events import (
    WorkflowHistoryEvent,
    WorkflowRepairEvent,
    WorkflowStepBatchEvent,
)
from .executor import StepExecutor
from .parser import WorkflowParser
from .views import (
    ExecutionErrorFeedback,
    WorkflowArtifacts,
    WorkflowDocument,
    WorkflowExecutionResult,
    WorkflowHistory,
    WorkflowHistoryEntry,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


class WorkflowRuntime:
    """Workflow-first runtime coordinating executor, compiler, and replay."""

    def __init__(
        self,
        tools: Tools,
        browser_session: BrowserSession,
    ):
        self.tools = tools
        self.browser_session = browser_session
        self.executor = StepExecutor(tools=tools, browser_session=browser_session)
        self.last_artifacts: WorkflowArtifacts | None = None

    async def replay(
        self,
        dsl: WorkflowDocument | str | Path,
        runtime_variables: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        document = self._resolve_document(dsl)
        runtime_vars = dict(document.variables)
        if runtime_variables:
            runtime_vars.update(runtime_variables)

        missing = [name for name in document.input_variables if name not in runtime_vars]
        if missing:
            raise ValueError(f"Missing required workflow runtime variables: {', '.join(missing)}")

        outputs: list[Any] = []
        history_entries: list[WorkflowHistoryEntry] = []
        completed_steps = 0
        last_success: WorkflowStep | None = None

        logger.info(f'▶️ 开始 Workflow replay：{document.id or document.name or "unnamed"}')

        if document.start_url and (not document.steps or document.steps[0].action not in {'navigate', 'open'}):
            start_step = WorkflowStep(id='start_url', action='navigate', params={'url': document.start_url})
            start_result = await self.executor.execute(start_step, runtime_vars)
            if not start_result.success:
                error = await self.executor.create_error_feedback(
                    step=start_step,
                    error_msg=start_result.error_message or '',
                    runtime_vars=runtime_vars,
                    last_success=last_success,
                    resolved_params=start_result.resolved_params,
                )
                entry = WorkflowHistoryEntry(
                    turn_number=0,
                    planned_steps=[start_step],
                    executed_steps=[],
                    outputs=[],
                    runtime_variables=dict(runtime_vars),
                    error=error,
                    done=False,
                )
                history_entries.append(entry)
                self._emit_event(WorkflowHistoryEvent(workflow_mode='replay', task=document.task or '', entry=entry))
                self.last_artifacts = WorkflowArtifacts(
                    replay_document=document,
                    history=WorkflowHistory(task=document.task or '', mode='replay', entries=history_entries),
                )
                return WorkflowExecutionResult(
                    success=False,
                    completed_steps=0,
                    total_steps=len(document.steps),
                    error=error,
                    runtime_variables=runtime_vars,
                    outputs=outputs,
                )

        for index, step in enumerate(document.steps, start=1):
            result = await self.executor.execute(step, runtime_vars)
            entry_outputs: list[str] = []
            if result.output is not None:
                outputs.append(result.output)
                entry_outputs.append(str(result.output))
            if result.generated_variables:
                runtime_vars.update(result.generated_variables)

            if result.success:
                last_success = step
                completed_steps += 1
                entry = WorkflowHistoryEntry(
                    turn_number=index,
                    planned_steps=[step],
                    executed_steps=[step],
                    outputs=entry_outputs,
                    runtime_variables=dict(runtime_vars),
                    error=None,
                    done=False,
                )
                history_entries.append(entry)
                self._emit_event(WorkflowHistoryEvent(workflow_mode='replay', task=document.task or '', entry=entry))
                self._emit_event(
                    WorkflowStepBatchEvent(
                        workflow_mode='replay',
                        task=document.task or '',
                        turn_number=index,
                        executed_steps=[step],
                        outputs=entry_outputs,
                        runtime_variables=dict(runtime_vars),
                    )
                )
                logger.info(f'✅ Workflow step 已回放: {step.action}')
                continue

            if step.optional:
                entry = WorkflowHistoryEntry(
                    turn_number=index,
                    planned_steps=[step],
                    executed_steps=[],
                    outputs=entry_outputs,
                    runtime_variables=dict(runtime_vars),
                    error=None,
                    done=False,
                )
                history_entries.append(entry)
                self._emit_event(WorkflowHistoryEvent(workflow_mode='replay', task=document.task or '', entry=entry))
                logger.info(f'⚠️ 已跳过可选 Workflow step: {step.action}')
                continue

            error = await self.executor.create_error_feedback(
                step=step,
                error_msg=result.error_message or '',
                runtime_vars=runtime_vars,
                last_success=last_success,
                resolved_params=result.resolved_params,
            )
            entry = WorkflowHistoryEntry(
                turn_number=index,
                planned_steps=[step],
                executed_steps=[],
                outputs=entry_outputs,
                runtime_variables=dict(runtime_vars),
                error=error,
                done=False,
            )
            history_entries.append(entry)
            self._emit_event(WorkflowHistoryEvent(workflow_mode='replay', task=document.task or '', entry=entry))
            self._emit_event(WorkflowRepairEvent(workflow_mode='replay', task=document.task or '', turn_number=index, error=error))
            self.last_artifacts = WorkflowArtifacts(
                replay_document=document,
                history=WorkflowHistory(task=document.task or '', mode='replay', entries=history_entries),
            )
            logger.error(f'❌ Workflow replay 失败，Workflow step={step.action}')
            return WorkflowExecutionResult(
                success=False,
                completed_steps=completed_steps,
                total_steps=len(document.steps),
                error=error,
                runtime_variables=runtime_vars,
                outputs=outputs,
            )

        self.last_artifacts = WorkflowArtifacts(
            replay_document=document,
            history=WorkflowHistory(task=document.task or '', mode='replay', entries=history_entries),
        )
        logger.info('🏁 Workflow replay 已成功完成')
        return WorkflowExecutionResult(
            success=True,
            completed_steps=completed_steps,
            total_steps=len(document.steps),
            runtime_variables=runtime_vars,
            outputs=outputs,
        )

    def _resolve_document(self, dsl: WorkflowDocument | str | Path) -> WorkflowDocument:
        if isinstance(dsl, WorkflowDocument):
            return dsl
        if isinstance(dsl, Path):
            return WorkflowParser.parse_file(dsl)
        if isinstance(dsl, str):
            candidate = Path(dsl)
            if candidate.exists():
                return WorkflowParser.parse_file(candidate)
            return WorkflowParser.parse_markdown(dsl)
        raise ValueError(f'Unsupported workflow DSL input type: {type(dsl)}')

    def _emit_event(self, event: Any) -> None:
        event_bus = getattr(self.browser_session, 'event_bus', None)
        if event_bus is None:
            return
        try:
            event_bus.dispatch(event)
        except Exception:
            logger.debug('Failed to dispatch workflow-native event', exc_info=True)
