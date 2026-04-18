from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

from .views import WorkflowDocument, WorkflowStep


class WorkflowCompiler:
    """Compile workflow recording artifacts into record-ready or replay-ready DSL documents."""

    @staticmethod
    def compile(
        successful_steps: list[WorkflowStep],
        task: str,
        original_variables: dict[str, Any] | None = None,
        document_id: str | None = None,
        document_name: str | None = None,
        start_url: str | None = None,
        mode: Literal['record', 'replay'] = 'replay',
    ) -> WorkflowDocument:
        steps = WorkflowCompiler._clone_steps(successful_steps)
        if mode == 'replay':
            steps = WorkflowCompiler._deduplicate_steps(steps)

        extracted_variables = dict(original_variables or {})
        input_variables = sorted(WorkflowCompiler._collect_input_variables(steps))
        return WorkflowDocument(
            id=document_id,
            name=document_name,
            task=task,
            start_url=start_url,
            variables=extracted_variables,
            input_variables=input_variables,
            steps=steps,
        )

    @staticmethod
    def _clone_steps(steps: list[WorkflowStep]) -> list[WorkflowStep]:
        return [WorkflowCompiler._clone_step(step) for step in steps]

    @staticmethod
    def _deduplicate_steps(steps: list[WorkflowStep]) -> list[WorkflowStep]:
        if not steps:
            return []

        cleaned: list[WorkflowStep] = []
        previous_signature: tuple[Any, ...] | None = None
        for step in steps:
            signature = WorkflowCompiler._step_signature(step)
            if signature != previous_signature:
                cleaned.append(step)
                previous_signature = signature
        return cleaned

    @staticmethod
    def _clone_step(step: WorkflowStep) -> WorkflowStep:
        return WorkflowStep(
            id=step.id,
            name=step.name,
            comment=step.comment,
            action=step.action,
            params=dict(step.params),
            timeout_ms=step.timeout_ms,
            optional=step.optional,
            then_steps=[WorkflowCompiler._clone_step(child) for child in step.then_steps],
            else_steps=[WorkflowCompiler._clone_step(child) for child in step.else_steps],
            steps=[WorkflowCompiler._clone_step(child) for child in step.steps],
        )

    @staticmethod
    def _step_signature(step: WorkflowStep) -> tuple[Any, ...]:
        return (
            step.action,
            tuple(sorted(step.params.items())),
            step.timeout_ms,
            step.optional,
            tuple(WorkflowCompiler._step_signature(child) for child in step.then_steps),
            tuple(WorkflowCompiler._step_signature(child) for child in step.else_steps),
            tuple(WorkflowCompiler._step_signature(child) for child in step.steps),
        )

    @staticmethod
    def _collect_input_variables(steps: list[WorkflowStep]) -> set[str]:
        variables: set[str] = set()
        for step in steps:
            variables.update(WorkflowCompiler._collect_input_variables_from_value(step.params))
            variables.update(WorkflowCompiler._collect_input_variables(step.then_steps))
            variables.update(WorkflowCompiler._collect_input_variables(step.else_steps))
            variables.update(WorkflowCompiler._collect_input_variables(step.steps))
        return variables

    @staticmethod
    def _collect_input_variables_from_value(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, str):
            import re

            for match in re.finditer(r'\{\{(.*?)\}\}|\$\{(.*?)\}', value):
                variable_name = (match.group(1) or match.group(2) or '').strip()
                if variable_name:
                    found.add(variable_name.split('.')[0])
            return found
        if isinstance(value, list):
            for item in value:
                found.update(WorkflowCompiler._collect_input_variables_from_value(item))
            return found
        if isinstance(value, dict):
            for item in value.values():
                found.update(WorkflowCompiler._collect_input_variables_from_value(item))
            return found
        return found

    @staticmethod
    def to_dict(document: WorkflowDocument) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if document.id:
            data['id'] = document.id
        if document.name:
            data['name'] = document.name
        if document.version:
            data['version'] = document.version
        if document.task:
            data['task'] = document.task
        if document.start_url:
            data['start_url'] = document.start_url
        if document.variables:
            data['variables'] = document.variables
        if document.input_variables:
            data['input_variables'] = document.input_variables

        data['steps'] = [WorkflowCompiler._step_to_dict(step) for step in document.steps]
        return data

    @staticmethod
    def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
        step_dict: dict[str, Any] = {}
        if step.id:
            step_dict['id'] = step.id
        if step.name:
            step_dict['name'] = step.name
        if step.comment:
            step_dict['comment'] = step.comment
        step_dict['action'] = step.action
        if step.params:
            step_dict['params'] = dict(step.params)
        if step.timeout_ms is not None:
            step_dict['timeout_ms'] = step.timeout_ms
        if step.optional:
            step_dict['optional'] = step.optional
        if step.then_steps:
            step_dict['then_steps'] = [WorkflowCompiler._step_to_dict(child) for child in step.then_steps]
        if step.else_steps:
            step_dict['else_steps'] = [WorkflowCompiler._step_to_dict(child) for child in step.else_steps]
        if step.steps:
            step_dict['steps'] = [WorkflowCompiler._step_to_dict(child) for child in step.steps]
        return step_dict

    @classmethod
    def to_markdown(cls, document: WorkflowDocument) -> str:
        yaml_str = yaml.safe_dump(cls.to_dict(document), allow_unicode=True, sort_keys=False)
        return f'---\n{yaml_str}---\n'

    @classmethod
    def save(cls, document: WorkflowDocument, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cls.to_markdown(document), encoding='utf-8')
        return target
