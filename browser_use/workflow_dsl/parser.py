from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .views import WorkflowDocument, WorkflowStep

logger = logging.getLogger(__name__)


class WorkflowParser:
    """Parser for markdown/YAML workflow DSL documents."""

    STEP_METADATA_KEYS = {'id', 'name', 'comment', 'timeout_ms', 'optional'}
    STEP_NESTED_KEYS = {'then_steps', 'else_steps', 'steps'}

    @classmethod
    def parse_markdown(cls, markdown_content: str) -> WorkflowDocument:
        """Parse markdown or YAML content into a workflow document."""
        try:
            payload = cls._extract_yaml_payload(markdown_content)
            data = yaml.safe_load(payload)
            if not isinstance(data, dict):
                raise ValueError('Parsed workflow DSL must be a mapping')
            return cls._build_document(data)
        except Exception as exc:
            logger.error(f'Failed to parse workflow DSL: {exc}')
            raise

    @classmethod
    def parse_file(cls, path: str | Path) -> WorkflowDocument:
        return cls.parse_markdown(Path(path).read_text(encoding='utf-8'))

    @staticmethod
    def _extract_yaml_payload(content: str) -> str:
        stripped = content.strip()
        if not stripped:
            raise ValueError('Workflow DSL content is empty')

        if stripped.startswith('---'):
            remainder = stripped[3:].lstrip('\n')
            closing_index = remainder.find('\n---')
            if closing_index != -1:
                return remainder[:closing_index]
            return remainder
        return stripped

    @classmethod
    def _build_document(cls, data: dict[str, Any]) -> WorkflowDocument:
        raw_steps = data.get('steps', [])
        if raw_steps is None:
            raw_steps = []
        if not isinstance(raw_steps, list):
            raise ValueError("Workflow 'steps' must be a list")

        steps = [cls._build_step(index, raw_step) for index, raw_step in enumerate(raw_steps, start=1)]

        variables = data.get('variables', {}) or {}
        if not isinstance(variables, dict):
            raise ValueError("Workflow 'variables' must be a mapping")

        input_variables = data.get('input_variables', []) or []
        if not isinstance(input_variables, list):
            raise ValueError("Workflow 'input_variables' must be a list")

        return WorkflowDocument(
            id=data.get('id'),
            name=data.get('name'),
            version=str(data.get('version', '0.1.0')),
            task=data.get('task'),
            start_url=data.get('start_url'),
            variables=variables,
            input_variables=[str(item) for item in input_variables],
            steps=steps,
        )

    @classmethod
    def _build_step(cls, index: int, raw_step: Any, parent_path: str = 'steps') -> WorkflowStep:
        step_path = f'{parent_path}[{index - 1}]'
        if not isinstance(raw_step, dict):
            raise ValueError(f'{step_path} must be a mapping')
        if 'action' not in raw_step:
            raise ValueError(f"{step_path} is missing required field 'action'")

        action = str(raw_step['action'])
        metadata: dict[str, Any] = {}
        params: dict[str, Any] = {}
        nested_steps: dict[str, list[WorkflowStep]] = {}

        raw_params = raw_step.get('params')
        if raw_params is not None:
            if not isinstance(raw_params, dict):
                raise ValueError(f"{step_path}.params must be a mapping")
            params.update(raw_params)

        for key, value in raw_step.items():
            if key == 'action' or key == 'params':
                continue
            if key in cls.STEP_NESTED_KEYS:
                nested_steps[key] = cls._build_nested_steps(key, value, step_path)
                continue
            if key in cls.STEP_METADATA_KEYS and key not in params:
                metadata[key] = value
                continue
            params[key] = value

        return WorkflowStep(
            id=metadata.get('id', f'step_{index}'),
            name=metadata.get('name'),
            comment=metadata.get('comment'),
            action=action,
            params=params,
            timeout_ms=metadata.get('timeout_ms'),
            optional=bool(metadata.get('optional', False)),
            then_steps=nested_steps.get('then_steps', []),
            else_steps=nested_steps.get('else_steps', []),
            steps=nested_steps.get('steps', []),
        )

    @classmethod
    def _build_nested_steps(cls, key: str, raw_steps: Any, parent_path: str) -> list[WorkflowStep]:
        nested_path = f'{parent_path}.{key}'
        if raw_steps is None:
            return []
        if not isinstance(raw_steps, list):
            raise ValueError(f'{nested_path} must be a list')
        return [cls._build_step(index, raw_step, nested_path) for index, raw_step in enumerate(raw_steps, start=1)]
