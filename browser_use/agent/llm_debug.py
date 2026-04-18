from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from browser_use.llm.messages import BaseMessage


class LlmDebugRecorder:
    """Persist full LLM request/response context for debugging without coupling prompt logic to react mode."""

    @staticmethod
    def dump_step_context(
        *,
        agent_directory: str | Path,
        step_number: int,
        workflow_mode: str,
        model_name: str,
        input_messages: list[BaseMessage],
        invoke_kwargs: dict[str, Any],
        parsed_output: Any | None,
    ) -> Path:
        debug_dir = Path(agent_directory) / 'llm_debug'
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / f'step_{step_number:04d}.json'

        payload = {
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'step_number': step_number,
            'workflow_mode': workflow_mode,
            'model_name': model_name,
            'invoke_kwargs': LlmDebugRecorder._to_jsonable(invoke_kwargs),
            'input_messages': [LlmDebugRecorder._serialize_message(message) for message in input_messages],
            'parsed_output': LlmDebugRecorder._to_jsonable(parsed_output),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return target

    @staticmethod
    def format_messages_for_log(input_messages: list[BaseMessage]) -> str:
        serialized = [LlmDebugRecorder._serialize_message(message) for message in input_messages]
        return json.dumps(serialized, ensure_ascii=False, indent=2)

    @staticmethod
    def _serialize_message(message: BaseMessage) -> dict[str, Any]:
        return {
            'type': message.__class__.__name__,
            'content': LlmDebugRecorder._to_jsonable(getattr(message, 'content', None)),
            'metadata': LlmDebugRecorder._to_jsonable(getattr(message, 'metadata', None)),
        }

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if isinstance(value, BaseModel):
            try:
                return value.model_dump(mode='json')
            except TypeError:
                return value.model_dump()
        if isinstance(value, type) and issubclass(value, BaseModel):
            return {'pydantic_model_class': value.__name__}
        if hasattr(value, 'model_dump') and callable(getattr(value, 'model_dump')):
            try:
                return value.model_dump(mode='json')
            except TypeError:
                return value.model_dump()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): LlmDebugRecorder._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [LlmDebugRecorder._to_jsonable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return repr(value)
