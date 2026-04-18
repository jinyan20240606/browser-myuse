from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from browser_use.agent.views import ActionResult


@dataclass
class RecordStepFeedback:
    """Structured feedback for a single record-mode planning/execution round."""

    planned_actions_json: str
    successful_actions_json: str
    failed_actions_json: str
    failure_summary: str
    repair_instruction: str
    has_failures: bool


@dataclass
class RecordActionClassification:
    """Partition one record batch into all-or-nothing success/failure views."""

    successful_actions: list[dict[str, Any]]
    failed_actions: list[dict[str, Any]]


class RecordFeedbackBuilder:
    """Build record-mode DSL repair feedback independent from normal react history."""

    @staticmethod
    def build(actions: list[Any] | None, results: list[ActionResult] | None) -> RecordStepFeedback:
        serialized_actions = RecordFeedbackBuilder._serialize_actions(actions)
        classification = RecordFeedbackBuilder._classify_actions(serialized_actions, results)
        has_failures = len(classification.failed_actions) > 0
        repair_instruction = RecordFeedbackBuilder._build_repair_instruction(has_failures)

        successful_actions_for_history = [] if has_failures else classification.successful_actions

        return RecordStepFeedback(
            planned_actions_json=json.dumps(serialized_actions, ensure_ascii=False, indent=2),
            successful_actions_json=json.dumps(successful_actions_for_history, ensure_ascii=False, indent=2),
            failed_actions_json=json.dumps(classification.failed_actions, ensure_ascii=False, indent=2),
            failure_summary=RecordFeedbackBuilder._build_failure_summary(classification.failed_actions),
            repair_instruction=repair_instruction,
            has_failures=has_failures,
        )

    @staticmethod
    def _classify_actions(
        serialized_actions: list[dict[str, Any]], results: list[ActionResult] | None
    ) -> RecordActionClassification:
        results = results or []
        successful_actions: list[dict[str, Any]] = []
        failed_actions: list[dict[str, Any]] = []

        for index, action_dict in enumerate(serialized_actions):
            result = results[index] if index < len(results) else None
            action_name = next(iter(action_dict.keys()), 'unknown_action') if action_dict else 'unknown_action'

            if result is None:
                failed_actions.append(
                    {
                        'index': index,
                        'status': 'not_executed',
                        'action': action_dict,
                        'error': 'No execution result was produced for this action.',
                    }
                )
                continue

            semantic_error = RecordFeedbackBuilder._get_control_flow_semantic_error(result)
            if result.error or semantic_error:
                failed_actions.append(
                    {
                        'index': index,
                        'status': 'failed',
                        'action': action_dict,
                        'error': result.error or semantic_error,
                        'result': RecordFeedbackBuilder._summarize_result(result, action_name),
                    }
                )
                continue

            successful_actions.append(
                {
                    'index': index,
                    'status': 'success',
                    'action': action_dict,
                    'result': RecordFeedbackBuilder._summarize_result(result, action_name),
                }
            )

        if failed_actions:
            failed_indexes = {item['index'] for item in failed_actions}
            rolled_back_successes = []
            for item in successful_actions:
                rolled_back_success = dict(item)
                rolled_back_success['status'] = 'rolled_back_due_to_batch_failure'
                rolled_back_success['error'] = (
                    'This DSL action executed successfully, but the same record batch contained a failed action, '
                    'so the whole batch must be regenerated and none of its actions should be treated as recorded success.'
                )
                if item['index'] not in failed_indexes:
                    failed_actions.append(rolled_back_success)
                rolled_back_successes.append(rolled_back_success)
            successful_actions = []

        failed_actions.sort(key=lambda item: item['index'])
        return RecordActionClassification(successful_actions=successful_actions, failed_actions=failed_actions)

    @staticmethod
    def format_for_history(feedback: RecordStepFeedback) -> str:
        status = 'HAS_FAILURES' if feedback.has_failures else 'ALL_SUCCEEDED'
        return (
            'RECORD_DSL_FEEDBACK\n'
            f'status={status}\n'
            'planned_dsl_json=\n'
            f'{feedback.planned_actions_json}\n'
            'successful_dsl_actions=\n'
            f'{feedback.successful_actions_json}\n'
            'failed_dsl_actions=\n'
            f'{feedback.failed_actions_json}\n'
            'failure_summary=\n'
            f'{feedback.failure_summary}\n'
            'repair_instruction=\n'
            f'{feedback.repair_instruction}'
        )

    @staticmethod
    def _build_failure_summary(failed_actions: list[dict[str, Any]]) -> str:
        if not failed_actions:
            return 'None'

        summary_lines: list[str] = []
        for item in failed_actions:
            index = item.get('index', '?')
            status = item.get('status', 'failed')
            action = item.get('action') or {}
            action_name = next(iter(action.keys()), 'unknown_action') if isinstance(action, dict) and action else 'unknown_action'
            error = str(item.get('error') or 'Unknown error')
            compact_error = ' '.join(error.split())
            if len(compact_error) > 200:
                compact_error = compact_error[:197] + '...'
            summary_lines.append(f'- step[{index}] {action_name} ({status}): {compact_error}')

        return '\n'.join(summary_lines)

    @staticmethod
    def _serialize_actions(actions: list[Any] | None) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for action in actions or []:
            if hasattr(action, 'model_dump'):
                serialized.append(action.model_dump(exclude_unset=True))
            elif isinstance(action, dict):
                serialized.append(action)
            else:
                serialized.append({'unknown_action': {'repr': repr(action)}})
        return serialized

    @staticmethod
    def _summarize_result(result: ActionResult, action_name: str) -> dict[str, Any]:
        summary: dict[str, Any] = {'action_name': action_name}
        if result.is_done is not None:
            summary['is_done'] = result.is_done
        if result.success is not None:
            summary['success'] = result.success
        if result.extracted_content:
            summary['extracted_content'] = result.extracted_content
        if result.long_term_memory:
            summary['long_term_memory'] = result.long_term_memory
        if result.metadata:
            summary['metadata'] = result.metadata
        return summary

    @staticmethod
    def _get_control_flow_semantic_error(result: ActionResult) -> str | None:
        metadata = result.metadata or {}
        control_flow = metadata.get('control_flow') if isinstance(metadata, dict) else None
        if not isinstance(control_flow, dict):
            return None
        if control_flow.get('semantic_success', True):
            return None

        generated_variables = control_flow.get('generated_variables') or {}
        if isinstance(generated_variables, dict):
            semantic_error = generated_variables.get('__semantic_error__')
            if semantic_error:
                return str(semantic_error)

        error_message = control_flow.get('error_message')
        if error_message:
            return str(error_message)

        return f"Control-flow action '{control_flow.get('action', 'unknown')}' completed technically but failed semantically"

    @staticmethod
    def _build_repair_instruction(has_failures: bool) -> str:
        if not has_failures:
            return (
                'All DSL actions in the previous record batch succeeded. '
                'Your next step must continue by generating the next DSL fragment or finish with done if the task is complete. '
                'Do not restate prior reasoning.'
            )

        return (
            'One or more DSL actions in the previous record batch failed. '
            'Your highest priority is to repair and regenerate only the failed DSL fragment while preserving successful DSL actions. '
            'Do not bypass the failed workflow with a one-off shortcut action such as a direct click. '
            'Do not switch back to generic react-style handling. '
            'Your next response must be either: '
            '(1) a repaired DSL action batch that replaces only the failed fragment, or '
            '(2) a done action with success=false that explicitly states the missing required action capability. '
            'After a control-flow fragment fails, you must not output a standalone direct click to finish the task unless that click is nested inside the repaired control-flow fragment itself. '
            'If the task requires inspecting items one by one, matching text, or first-match-stop behavior, and the registered actions cannot produce that inspection result as a runtime variable, you must fail explicitly instead of improvising a semantic click. '
            'Never continue with a top-level click that was not produced by the repaired DSL fragment.'
        )
