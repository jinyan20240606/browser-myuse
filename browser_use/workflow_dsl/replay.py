from __future__ import annotations

from pathlib import Path
from typing import Any

from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools

from .runtime import WorkflowRuntime
from .views import WorkflowDocument, WorkflowExecutionResult


class ReplayEngine:
    """0-token replay engine delegating to the workflow runtime."""

    def __init__(self, tools: Tools, browser_session: BrowserSession):
        self.runtime = WorkflowRuntime(tools=tools, browser_session=browser_session)

    @classmethod
    def from_file(cls, dsl_path: str | Path, tools: Tools, browser_session: BrowserSession) -> 'ReplayEngine':
        return cls(tools=tools, browser_session=browser_session)

    async def replay(
        self,
        dsl: WorkflowDocument | str | Path,
        runtime_vars: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        return await self.runtime.replay(dsl, runtime_variables=runtime_vars)
