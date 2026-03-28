from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from browser_use.browser.session import BrowserSession
from browser_use.llm.base import BaseChatModel
from browser_use.tools.service import Tools

from .compiler import WorkflowCompiler
from .events import (
    WorkflowArtifactsSavedEvent,
    WorkflowHistoryEvent,
    WorkflowPlannerTurnEvent,
    WorkflowRepairEvent,
    WorkflowStepBatchEvent,
)
from .executor import StepExecutor
from .parser import WorkflowParser
from .planner import WorkflowPlanner
from .views import (
    ExecutionErrorFeedback,
    WorkflowArtifacts,
    WorkflowDocument,
    WorkflowExecutionResult,
    WorkflowHistory,
    WorkflowHistoryEntry,
    WorkflowPlannerTurn,
    WorkflowRecordSummary,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


class WorkflowRuntime:
    """Workflow-first runtime coordinating planner, executor, compiler, and replay."""

    def __init__(
        self,
        tools: Tools,
        browser_session: BrowserSession,
        llm: BaseChatModel | None = None,
        max_planner_steps: int = 3,
    ):
        self.tools = tools
        self.browser_session = browser_session
        self.llm = llm
        self.max_planner_steps = max_planner_steps
        self.executor = StepExecutor(tools=tools, browser_session=browser_session)
        self.planner = WorkflowPlanner(llm=llm, tools=tools, max_actions_per_step=max_planner_steps) if llm else None
        self.last_artifacts: WorkflowArtifacts | None = None

    async def record(
        self,
        task: str,
        runtime_variables: dict[str, Any] | None = None,
        max_turns: int = 100,
        output_path: str | Path | None = None,
        document_id: str | None = None,
        document_name: str | None = None,
        start_url: str | None = None,
    ) -> tuple[WorkflowExecutionResult, WorkflowRecordSummary | None, WorkflowDocument | None]:
        if self.planner is None:
            raise ValueError('WorkflowRuntime.record requires an llm-backed planner')

        runtime_vars = dict(runtime_variables or {})
        successful_steps: list[WorkflowStep] = []
        planner_turns: list[WorkflowPlannerTurn] = []
        history_entries: list[WorkflowHistoryEntry] = []
        last_error: ExecutionErrorFeedback | None = None
        outputs: list[Any] = []
        completed_steps = 0

        for turn_index in range(1, max_turns + 1):
            logger.info(f'📘 Workflow Planner 第 {turn_index} 轮开始')
            browser_state = await self.browser_session.get_browser_state_summary(include_screenshot=True)
            plan = await self.planner.plan(
                task=task,
                browser_state_summary=browser_state,
                runtime_variables=runtime_vars,
                successful_steps=successful_steps,
                last_error=last_error,
                planner_turns=planner_turns,
                available_file_paths=None,
            )
            self._emit_event(
                WorkflowPlannerTurnEvent(
                    workflow_mode='record',
                    task=task,
                    turn_number=turn_index,
                    phase='started',
                    planned_steps=plan.steps,
                )
            )
            logger.info(f'🧭 已规划 Workflow steps: {[step.action for step in plan.steps]}')

            if plan.is_done:
                result = WorkflowExecutionResult(
                    success=True,
                    completed_steps=completed_steps,
                    total_steps=completed_steps,
                    runtime_variables=runtime_vars,
                    outputs=outputs,
                )
                record_document = WorkflowCompiler.compile(
                    successful_steps=successful_steps,
                    task=task,
                    original_variables=runtime_vars,
                    document_id=document_id,
                    document_name=document_name,
                    start_url=start_url,
                    mode='record',
                )
                replay_document = WorkflowCompiler.compile(
                    successful_steps=successful_steps,
                    task=task,
                    original_variables=runtime_vars,
                    document_id=document_id,
                    document_name=document_name,
                    start_url=start_url,
                    mode='replay',
                )
                done_entry = WorkflowHistoryEntry(
                    turn_number=turn_index,
                    planned_steps=[],
                    executed_steps=[],
                    outputs=[plan.done_text or 'Workflow completed'],
                    runtime_variables=dict(runtime_vars),
                    error=None,
                    done=True,
                )
                history_entries.append(done_entry)
                self._emit_event(WorkflowHistoryEvent(workflow_mode='record', task=task, entry=done_entry))
                history = WorkflowHistory(task=task, mode='record', entries=history_entries)
                self.last_artifacts = WorkflowArtifacts(
                    record_document=record_document,
                    replay_document=replay_document,
                    history=history,
                )
                summary = self._save_record_artifacts(record_document, replay_document, history, output_path, planner_turns)
                self._emit_event(
                    WorkflowPlannerTurnEvent(
                        workflow_mode='record',
                        task=task,
                        turn_number=turn_index,
                        phase='finished',
                        planned_steps=[],
                        result_summary=plan.done_text or 'Workflow completed',
                        error=None,
                    )
                )
                logger.info('✅ Workflow 录制完成')
                return result, summary, record_document

            turn_feedback: list[tuple[WorkflowStep, bool, str | None]] = []
            executed_steps: list[WorkflowStep] = []
            turn_outputs: list[str] = []
            last_error = None

            for step in plan.steps:
                step_result = await self.executor.execute(step, runtime_vars)
                turn_feedback.append((step, step_result.success, step_result.error_message))
                if step_result.output is not None:
                    outputs.append(step_result.output)
                    turn_outputs.append(str(step_result.output))
                if step_result.generated_variables:
                    runtime_vars.update(step_result.generated_variables)

                if step_result.success:
                    successful_steps.append(step)
                    executed_steps.append(step)
                    completed_steps += 1
                    continue

                error_feedback = await self.executor.create_error_feedback(
                    step=step,
                    error_msg=step_result.error_message or '',
                    runtime_vars=runtime_vars,
                    last_success=successful_steps[-1] if successful_steps else None,
                    resolved_params=step_result.resolved_params,
                )
                error_feedback.repair_hint = self.planner.build_repair_hint(error_feedback)
                last_error = error_feedback
                self._emit_event(WorkflowRepairEvent(workflow_mode='record', task=task, turn_number=turn_index, error=error_feedback))
                logger.warning(f'🛠️ Workflow 进入 repair，step={step.id or step.action}，错误：{error_feedback.error_message}')
                if not step.optional:
                    break

            planner_turn = WorkflowPlannerTurn(
                step_number=turn_index,
                plan=plan.steps,
                result_summary=self.planner.summarize_execution_feedback(turn_feedback, last_error),
                error=last_error,
            )
            planner_turns.append(planner_turn)
            self._emit_event(
                WorkflowPlannerTurnEvent(
                    workflow_mode='record',
                    task=task,
                    turn_number=turn_index,
                    phase='finished',
                    planned_steps=plan.steps,
                    result_summary=planner_turn.result_summary,
                    error=last_error,
                )
            )
            logger.info(f'🧾 Workflow Planner 第 {turn_index} 轮结束')

            entry = WorkflowHistoryEntry(
                turn_number=turn_index,
                planned_steps=plan.steps,
                executed_steps=executed_steps,
                outputs=turn_outputs,
                runtime_variables=dict(runtime_vars),
                error=last_error,
                done=False,
            )
            history_entries.append(entry)
            self._emit_event(WorkflowHistoryEvent(workflow_mode='record', task=task, entry=entry))
            self._emit_event(
                WorkflowStepBatchEvent(
                    workflow_mode='record',
                    task=task,
                    turn_number=turn_index,
                    executed_steps=executed_steps,
                    outputs=turn_outputs,
                    runtime_variables=dict(runtime_vars),
                )
            )
            logger.info(f'📦 已执行 Workflow steps: {[step.action for step in executed_steps]}')

            if last_error is not None and turn_index >= max_turns:
                history = WorkflowHistory(task=task, mode='record', entries=history_entries)
                self.last_artifacts = WorkflowArtifacts(history=history)
                return (
                    WorkflowExecutionResult(
                        success=False,
                        completed_steps=completed_steps,
                        total_steps=completed_steps + len(plan.steps),
                        error=last_error,
                        runtime_variables=runtime_vars,
                        outputs=outputs,
                    ),
                    None,
                    None,
                )

        history = WorkflowHistory(task=task, mode='record', entries=history_entries)
        self.last_artifacts = WorkflowArtifacts(history=history)
        return (
            WorkflowExecutionResult(
                success=False,
                completed_steps=completed_steps,
                total_steps=completed_steps,
                error=last_error,
                runtime_variables=runtime_vars,
                outputs=outputs,
            ),
            None,
            None,
        )

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

    def _save_record_artifacts(
        self,
        record_document: WorkflowDocument,
        replay_document: WorkflowDocument,
        history: WorkflowHistory,
        output_path: str | Path | None,
        planner_turns: list[WorkflowPlannerTurn],
    ) -> WorkflowRecordSummary | None:
        self.last_artifacts = WorkflowArtifacts(
            record_document=record_document,
            replay_document=replay_document,
            history=history,
        )
        if output_path is None:
            return None

        bundle_dir = Path(output_path).with_suffix('')
        bundle_dir.mkdir(parents=True, exist_ok=True)
        record_path = bundle_dir / 'record.md'
        replay_path = bundle_dir / 'replay.md'
        history_path = bundle_dir / 'history.json'
        turns_path = bundle_dir / 'planner_turns.json'
        manifest_path = bundle_dir / 'manifest.json'

        WorkflowCompiler.save(record_document, record_path)
        WorkflowCompiler.save(replay_document, replay_path)
        history_path.write_text(history.model_dump_json(indent=2), encoding='utf-8')
        turns_path.write_text(json.dumps([turn.model_dump(mode='json') for turn in planner_turns], ensure_ascii=False, indent=2), encoding='utf-8')

        manifest = {
            'record_document': str(record_path),
            'replay_document': str(replay_path),
            'history': str(history_path),
            'planner_turns': str(turns_path),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

        files = [str(record_path), str(replay_path), str(history_path), str(turns_path), str(manifest_path)]
        self._emit_event(
            WorkflowArtifactsSavedEvent(
                workflow_mode='record',
                task=record_document.task or '',
                bundle_dir=str(bundle_dir),
                manifest_path=str(manifest_path),
                files=files,
            )
        )
        logger.info(f'💾 Workflow artifacts 已保存到 bundle: {bundle_dir}')

        return WorkflowRecordSummary(
            output_path=str(record_path),
            step_count=len(record_document.steps),
            workflow_id=record_document.id,
            mode='planner',
            start_url=record_document.start_url,
            replay_step_count=len(replay_document.steps),
            planner_turns=len(planner_turns),
        )

    def _emit_event(self, event: Any) -> None:
        event_bus = getattr(self.browser_session, 'event_bus', None)
        if event_bus is None:
            return
        try:
            event_bus.dispatch(event)
        except Exception:
            logger.debug('Failed to dispatch workflow-native event', exc_info=True)
