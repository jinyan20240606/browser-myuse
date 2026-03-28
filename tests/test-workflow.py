"""Workflow DSL 回归测试集合。

该文件主要覆盖：
- Parser / Compiler
- StepExecutor 控制流
- WorkflowPlanner prompt / history / repair hint
- WorkflowRuntime record / replay 主链
- workflow-native 结果边界与 MCP 适配

测试风格：
- 不启动真实浏览器
- 不调用真实 LLM
- 通过 fake tools / fake browser session / fake planner llm 做轻量回归
"""

from pathlib import Path

from browser_use.agent.views import ActionResult, AgentHistoryList
from browser_use.workflow_dsl import WorkflowCompiler, WorkflowDocument, WorkflowParser, WorkflowStep
from browser_use.workflow_dsl.events import WorkflowArtifactsSavedEvent
from browser_use.workflow_dsl.executor import StepExecutor
from browser_use.workflow_dsl.planner import PlannerStepPlan, WorkflowPlanner
from browser_use.workflow_dsl.runtime import WorkflowRuntime
from browser_use.workflow_dsl.views import ExecutionErrorFeedback, WorkflowAgentRunResult, WorkflowArtifacts, WorkflowPlannerTurn


class FakeRegistryAction:
    """模拟 registry 中单个 action 的 schema 元信息。"""
    def __init__(self, name: str):
        self.description = f'{name} action'
        self.domains = None
        self.param_model = type('ParamModel', (), {'model_json_schema': staticmethod(lambda: {'type': 'object'})})


class FakeRegistryContainer:
    """模拟 tools.registry.registry，提供 planner 读取 action schema 所需的数据。"""

    def __init__(self):
        self.actions = {
            'click': FakeRegistryAction('click'),
            'input': FakeRegistryAction('input'),
            'navigate': FakeRegistryAction('navigate'),
        }

    @staticmethod
    def _match_domains(domains, url):
        """测试中忽略域名限制，统一返回允许。"""
        return True


class FakeEventBus:
    """收集 runtime 分发的事件，便于断言 workflow event 是否产生。"""
    def __init__(self):
        self.events: list[object] = []

    def dispatch(self, event):
        self.events.append(event)
        return event


class FakeRegistry:
    """模拟 action 执行入口，并记录调用顺序供断言使用。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.registry = FakeRegistryContainer()

    async def execute_action(self, action_name, params, browser_session=None):
        """根据 action_name 返回稳定的假执行结果。"""
        self.calls.append((action_name, params))
        if action_name == 'exists':
            return ActionResult(extracted_content='1')
        if action_name == 'click':
            return ActionResult(extracted_content='clicked')
        if action_name == 'input':
            return ActionResult(extracted_content=params.get('text'))
        if action_name == 'done':
            return ActionResult(extracted_content=params.get('answer'))
        if action_name == 'navigate':
            return ActionResult(extracted_content=f"navigated:{params.get('url')}")
        return ActionResult(extracted_content='ok')


class FakeTools:
    """最小化 Tools 替身，只暴露当前测试实际需要的方法。"""
    def __init__(self):
        self.registry = FakeRegistry()

    def exclude_action(self, _action_name):
        return None

    def set_coordinate_clicking(self, _enabled):
        return None

    def get_output_model(self):
        return None

    def use_structured_output_action(self, _output_model):
        return None


class FakeBrowserProfile:
    """最小浏览器配置对象，仅提供 runtime 访问到的字段。"""

    demo_mode = False
    keep_alive = False
    downloads_path = None


class FakeBrowserSession:
    """模拟 BrowserSession，供 planner/runtime/executor 获取页面状态。"""
    def __init__(self):
        self.event_bus = FakeEventBus()
        self.browser_profile = FakeBrowserProfile()
        self.id = 'fake-browser-session'
        self.cdp_url = None
        self.agent_focus_target_id = None

    async def get_browser_state_summary(self, include_screenshot=False):
        class State:
            url = 'https://example.com'
            title = 'Example'
            tabs = []
            dom_state = FakeDomState()
            screenshot = None

        return State()


class FakeDomState:
    """模拟页面 DOM 摘要，供 planner 构造 prompt。"""

    def llm_representation(self):
        return '[1]<button>Submit</button>'


class FakeBrowserStateSummary:
    """模拟 planner.build_context() 使用的 browser state summary。"""
    url = 'https://example.com'
    title = 'Example'
    tabs = []
    dom_state = FakeDomState()


class FakeLLMResponse:
    """模拟 LLM 返回对象，只有 `completion` 字段。"""

    def __init__(self, completion):
        self.completion = completion


class FakePlannerLLM:
    """按顺序返回预设 PlannerStepPlan，用于驱动 record 流程。"""
    provider = 'test'
    model = 'fake-workflow-planner'

    def __init__(self, plans):
        self.plans = plans
        self.calls = 0

    async def ainvoke(self, messages, output_format=None):
        plan = self.plans[self.calls]
        self.calls += 1
        return FakeLLMResponse(plan)


def test_parser_and_compiler_roundtrip() -> None:
    """手工构造 WorkflowDocument，验证 compiler->parser roundtrip 不丢语义。"""

    document = WorkflowDocument(
        id='test_doc',
        name='Test Workflow',
        task='Open a page and extract content',
        start_url='https://example.com',
        variables={'reply_text': 'hello'},
        input_variables=['reply_text'],
        steps=[
            WorkflowStep(id='step_1', action='navigate', params={'url': 'https://example.com'}),
            WorkflowStep(id='step_2', action='input', params={'index': 1, 'text': '${reply_text}'}),
        ],
    )

    markdown = WorkflowCompiler.to_markdown(document)
    parsed = WorkflowParser.parse_markdown(markdown)

    assert parsed.id == document.id
    assert parsed.name == document.name
    assert parsed.input_variables == ['reply_text']
    assert parsed.start_url == 'https://example.com'
    assert len(parsed.steps) == 2
    assert parsed.steps[0].action == 'navigate'
    assert parsed.steps[1].params['text'] == '${reply_text}'

    print('\n[测试成功] markdown from WorkflowCompiler.to_markdown(document):\n')
    print(markdown)


def test_parser_file_api() -> None:
    """验证 parser 的文件读取入口，覆盖 parse_file 路径。"""

    path = Path('tmp_workflow_test.md')
    path.write_text(
        """---
id: sample
name: Sample
steps:
  - action: navigate
    url: https://example.com
---
""",
        encoding='utf-8',
    )
    try:
        parsed = WorkflowParser.parse_file(path)
        assert parsed.id == 'sample'
        assert parsed.steps[0].action == 'navigate'
        assert parsed.steps[0].params['url'] == 'https://example.com'
    finally:
        path.unlink(missing_ok=True)

    print('\n[测试成功] test_parser_file_api ->', {'id': parsed.id, 'action': parsed.steps[0].action, 'url': parsed.steps[0].params['url']})


def test_parser_supports_control_flow() -> None:
    """验证 parser 能正确解析 loop_for / if / then_steps / else_steps 嵌套结构。"""

    parsed = WorkflowParser.parse_markdown(
        """---
name: Control Flow
steps:
  - action: loop_for
    items:
      - A
      - B
    item_variable: tabName
    steps:
      - action: if
        variable: tabName
        operator: equals
        expected: A
        then_steps:
          - action: click
            index: 1
        else_steps:
          - action: click
            index: 2
---
"""
    )

    loop_step = parsed.steps[0]
    assert loop_step.action == 'loop_for'
    assert len(loop_step.steps) == 1
    assert loop_step.steps[0].action == 'if'
    assert len(loop_step.steps[0].then_steps) == 1
    assert len(loop_step.steps[0].else_steps) == 1

    print('\n[测试成功] test_parser_supports_control_flow ->', {'root_action': loop_step.action, 'nested_action': loop_step.steps[0].action, 'then_count': len(loop_step.steps[0].then_steps), 'else_count': len(loop_step.steps[0].else_steps)})


def test_compiler_collects_nested_variables() -> None:
    """验证 compiler 能从嵌套控制流 step 中提取 input_variables。"""

    document = WorkflowCompiler.compile(
        successful_steps=[
            WorkflowStep(
                id='step_1',
                action='if',
                params={'variable': 'sendReply', 'operator': 'truthy'},
                then_steps=[WorkflowStep(id='step_1_1', action='input', params={'text': '${reply_text}', 'index': 1})],
            )
        ],
        task='Reply task',
        original_variables={'sendReply': True},
    )

    assert 'reply_text' in document.input_variables

    print('\n[测试成功] test_compiler_collects_nested_variables ->', {'input_variables': document.input_variables, 'task': document.task})


def test_compiler_record_mode_keeps_duplicate_steps() -> None:
    """验证 record 保留重复 step，而 replay 会压缩重复 step。"""

    steps = [
        WorkflowStep(id='step_1', action='click', params={'index': 1}),
        WorkflowStep(id='step_2', action='click', params={'index': 1}),
    ]
    record_document = WorkflowCompiler.compile(steps, task='record', mode='record')
    replay_document = WorkflowCompiler.compile(steps, task='replay', mode='replay')

    assert len(record_document.steps) == 2
    assert len(replay_document.steps) == 1

    print('\n[测试成功] test_compiler_record_mode_keeps_duplicate_steps ->', {'record_steps': len(record_document.steps), 'replay_steps': len(replay_document.steps)})


def test_step_executor_supports_control_flow() -> None:
    """验证 executor 能执行 loop_for + if 的嵌套控制流，并按分支触发 action。"""

    tools = FakeTools()
    browser_session = FakeBrowserSession()
    executor = StepExecutor(tools=tools, browser_session=browser_session)  # type: ignore[arg-type]
    variables = {'tabs': ['朋友私信', '陌生人私信'], 'chosen_text': 'hi', 'sendReply': True}

    loop_step = WorkflowStep(
        id='loop_tabs',
        action='loop_for',
        params={'items': '{{tabs}}', 'item_variable': 'tabName', 'index_variable': 'tabIndex'},
        steps=[
            WorkflowStep(
                id='branch',
                action='if',
                params={'variable': 'tabIndex', 'operator': 'equals', 'expected': 0},
                then_steps=[WorkflowStep(id='click_first', action='click', params={'index': 1})],
                else_steps=[WorkflowStep(id='click_second', action='click', params={'index': 2})],
            )
        ],
    )

    result = __import__('asyncio').run(executor.execute(loop_step, variables))
    assert result.success is True
    assert tools.registry.calls[0] == ('click', {'index': 1})
    assert tools.registry.calls[1] == ('click', {'index': 2})

    print('\n[测试成功] test_step_executor_supports_control_flow ->', {'calls': tools.registry.calls, 'success': result.success})


def test_step_executor_set_variable_expression() -> None:
    """验证 executor 的 set_variable 支持表达式求值与 generated_variables 回填。"""

    tools = FakeTools()
    browser_session = FakeBrowserSession()
    executor = StepExecutor(tools=tools, browser_session=browser_session)  # type: ignore[arg-type]
    variables = {'repliedCount': 1}

    step = WorkflowStep(id='set_count', action='set_variable', params={'name': 'repliedCount', 'expression': 'repliedCount + 1'})
    result = __import__('asyncio').run(executor.execute(step, variables))

    assert result.success is True
    assert variables['repliedCount'] == 2
    assert result.generated_variables['repliedCount'] == 2

    print('\n[测试成功] test_step_executor_set_variable_expression ->', {'variables': variables, 'generated_variables': result.generated_variables})


def test_planner_message_generation() -> None:
    """验证 planner 能基于 context 生成包含 task、actions、history 的 prompt。"""

    planner = WorkflowPlanner(llm=object(), tools=FakeTools(), max_actions_per_step=2)  # type: ignore[arg-type]
    context = planner.build_context(
        task='Reply to unread messages',
        browser_state_summary=FakeBrowserStateSummary(),  # type: ignore[arg-type]
        runtime_variables={'reply_text': 'hello'},
        successful_steps=[WorkflowStep(id='step_1', action='click', params={'index': 1})],
        last_error=None,
        planner_turns=[],
        available_file_paths=['results.md'],
    )
    messages = planner.build_messages(context)

    assert len(messages) == 2
    assert 'Workflow Planner' in messages[0].text
    assert 'Reply to unread messages' in messages[1].text
    assert 'click' in messages[1].text
    assert '<planner_history_summary>' in messages[1].text

    print('\n[测试成功] test_planner_message_generation ->', messages)


def test_planner_history_and_error_specialization() -> None:
    """验证 planner 能结合 last_error 与 planner_turn history 生成 repair specialization。"""

    planner = WorkflowPlanner(llm=object(), tools=FakeTools(), max_actions_per_step=2)  # type: ignore[arg-type]
    error = ExecutionErrorFeedback(
        failed_step=WorkflowStep(id='failed', action='click', params={'index': 3}),
        action='click',
        params={'index': 3},
        resolved_params={'index': 3},
        error_type='ExecutionError',
        error_message='Element not found',
        page_snapshot='URL: https://example.com',
        runtime_variables={'reply_text': 'hello'},
    )
    error.repair_hint = planner.build_repair_hint(error)
    context = planner.build_context(
        task='Reply',
        browser_state_summary=FakeBrowserStateSummary(),  # type: ignore[arg-type]
        runtime_variables={'reply_text': 'hello'},
        successful_steps=[WorkflowStep(id='ok', action='click', params={'index': 1})],
        last_error=error,
        planner_turns=[
            WorkflowPlannerTurn(
                step_number=1,
                plan=[WorkflowStep(id='ok', action='click', params={'index': 1})],
                result_summary='- ok: success',
                error=None,
            )
        ],
        available_file_paths=[],
    )

    assert context.last_error is not None
    assert context.last_error.repair_hint is not None
    assert 'Re-evaluate current page state' in context.last_error.repair_hint
    assert '<turn step="1">' in context.planner_history_summary

    print('\n[测试成功] test_planner_history_and_error_specialization ->', {'repair_hint': context.last_error.repair_hint, 'planner_history_summary': context.planner_history_summary})


def test_workflow_runtime_replay() -> None:
    """验证 runtime replay 主链可跑通，并能产出 replay history artifacts。"""

    runtime = WorkflowRuntime(tools=FakeTools(), browser_session=FakeBrowserSession())  # type: ignore[arg-type]
    document = WorkflowDocument(
        id='replay_doc',
        task='Replay test',
        steps=[WorkflowStep(id='step_1', action='click', params={'index': 1})],
    )
    result = __import__('asyncio').run(runtime.replay(document, runtime_variables={}))
    assert result.success is True
    assert result.completed_steps == 1
    assert runtime.last_artifacts is not None
    assert runtime.last_artifacts.history is not None
    assert runtime.last_artifacts.history.mode == 'replay'

    print('\n[测试成功] test_workflow_runtime_replay ->', {'completed_steps': result.completed_steps, 'history_mode': runtime.last_artifacts.history.mode, 'outputs': result.outputs})
    print('\n[replay execution result]\n')
    print(result.model_dump_json(indent=2))
    print('\n[replay artifacts]\n')
    print(runtime.last_artifacts.model_dump_json(indent=2))


def build_record_runtime() -> WorkflowRuntime:
    """构造一个可稳定结束的 record runtime，供多个测试复用。"""

    llm = FakePlannerLLM(
        [
            PlannerStepPlan(
                thinking='plan first step',
                evaluation_previous_goal='start',
                memory='recording',
                next_goal='click submit',
                is_done=False,
                steps=[WorkflowStep(id='step_1', action='click', params={'index': 1})],
            ),
            PlannerStepPlan(
                thinking='finish task',
                evaluation_previous_goal='success',
                memory='completed one step',
                next_goal='done',
                is_done=True,
                done_text='Workflow completed',
                steps=[],
            ),
        ]
    )
    return WorkflowRuntime(
        tools=FakeTools(),  # type: ignore[arg-type]
        browser_session=FakeBrowserSession(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        max_planner_steps=2,
    )


def test_workflow_runtime_record() -> None:
    """验证 runtime record 主链、artifact bundle 保存与 event 分发。"""

    runtime = build_record_runtime()
    result, summary, document = __import__('asyncio').run(
        runtime.record(
            task='Record test',
            runtime_variables={'reply_text': 'hello'},
            max_turns=3,
            output_path='tmp_record_workflow.md',
            document_id='record_doc',
            document_name='Record Doc',
        )
    )
    try:
        assert result.success is True
        assert summary is not None
        assert document is not None
        assert summary.mode == 'planner'
        assert summary.planner_turns == 1
        assert len(document.steps) == 1
        assert runtime.last_artifacts is not None
        assert runtime.last_artifacts.history is not None
        assert runtime.last_artifacts.record_document is not None
        assert runtime.last_artifacts.replay_document is not None
        assert runtime.last_artifacts.history.mode == 'record'
        assert Path('tmp_record_workflow/manifest.json').exists()
        assert any(isinstance(event, WorkflowArtifactsSavedEvent) for event in runtime.browser_session.event_bus.events)  # type: ignore[attr-defined]

        print('\n[测试成功] test_workflow_runtime_record ->', {'summary': summary.model_dump() if summary else None, 'record_steps': len(document.steps), 'history_mode': runtime.last_artifacts.history.mode})
    finally:
        if Path('tmp_record_workflow').exists():
            import shutil

            shutil.rmtree('tmp_record_workflow')


def test_agent_exposes_workflow_artifacts() -> None:
    """验证 workflow_artifacts 可以作为 Agent 外露结果被访问。"""

    runtime = build_record_runtime()
    result, _, _ = __import__('asyncio').run(
        runtime.record(
            task='Record test',
            runtime_variables={'reply_text': 'hello'},
            max_turns=3,
            output_path='tmp_agent_record_workflow.md',
            document_id='record_doc',
            document_name='Record Doc',
        )
    )
    try:
        class FakeAgent:
            workflow_artifacts = runtime.last_artifacts

        agent = FakeAgent()
        assert agent.workflow_artifacts is not None
        assert agent.workflow_artifacts.history is not None
        assert agent.workflow_artifacts.record_document is not None

        print('\n[测试成功] test_agent_exposes_workflow_artifacts ->', {'has_history': agent.workflow_artifacts.history is not None, 'has_record_document': agent.workflow_artifacts.record_document is not None})
    finally:
        if Path('tmp_agent_record_workflow').exists():
            import shutil

            shutil.rmtree('tmp_agent_record_workflow')


def test_planner_step_plan_schema() -> None:
    """验证 PlannerStepPlan 基础 schema 可实例化并保留 steps 结构。"""

    plan = PlannerStepPlan(
        thinking='analyze page',
        evaluation_previous_goal='success',
        memory='have one unread chat',
        next_goal='click unread chat',
        is_done=False,
        steps=[WorkflowStep(id='step_1', action='click', params={'index': 1})],
    )
    assert plan.steps[0].action == 'click'
    assert plan.is_done is False

    print('\n[测试成功] test_planner_step_plan_schema ->', {'step_action': plan.steps[0].action, 'is_done': plan.is_done})


def test_workflow_run_should_not_return_agent_history() -> None:
    """验证 workflow 结果边界已脱离旧 AgentHistoryList 语义。"""

    runtime = build_record_runtime()
    result, summary, _ = __import__('asyncio').run(
        runtime.record(
            task='Record test',
            runtime_variables={'reply_text': 'hello'},
            max_turns=3,
            output_path=None,
            document_id='record_doc',
            document_name='Record Doc',
        )
    )

    assert not isinstance(runtime.last_artifacts, AgentHistoryList)
    assert runtime.last_artifacts is not None
    assert runtime.last_artifacts.history is not None
    # 这里故意用 output_path=None，所以 record() 不会保存 bundle，也就不会生成 WorkflowRecordSummary
    assert summary is None
    assert result.success is True

    print('\n[测试成功] test_workflow_run_should_not_return_agent_history ->', {'artifacts_type': type(runtime.last_artifacts).__name__, 'summary': summary, 'result_success': result.success})


def test_mcp_workflow_result_formatter() -> None:
    """验证 MCP 已能消费统一 WorkflowAgentRunResult facade。"""

    from browser_use.mcp.server import BrowserUseServer

    runtime = build_record_runtime()
    execution, summary, _ = __import__('asyncio').run(
        runtime.record(
            task='Record test',
            runtime_variables={'reply_text': 'hello'},
            max_turns=3,
            output_path=None,
            document_id='record_doc',
            document_name='Record Doc',
        )
    )

    server = BrowserUseServer.__new__(BrowserUseServer)
    artifacts = runtime.last_artifacts
    assert isinstance(artifacts, WorkflowArtifacts)
    workflow_result = WorkflowAgentRunResult(
        mode='record',
        execution=execution,
        artifacts=artifacts,
        record_summary=summary,
    )

    lines = BrowserUseServer._format_workflow_run_result(server, workflow_result)

    assert any('Workflow completed in' in line for line in lines)
    assert any('Workflow mode: record' in line for line in lines)
    assert any('Record steps:' in line for line in lines)

    print('\n[测试成功] test_mcp_workflow_result_formatter ->', {'workflow_result': workflow_result.model_dump(), 'formatted_lines': lines})


if __name__ == '__main__':
    # 1) Parser / Compiler：验证 DSL 文档构造、解析、roundtrip、变量收集、record/replay 编译差异
    test_parser_and_compiler_roundtrip()
    # exit()  # ✅ 也能用

    test_parser_file_api()
    # exit()
    test_parser_supports_control_flow()
    # exit()
    test_compiler_collects_nested_variables()
    test_compiler_record_mode_keeps_duplicate_steps()

    # 2) Executor：验证控制流执行与 set_variable 表达式求值
    test_step_executor_supports_control_flow()
    test_step_executor_set_variable_expression()

    # 3) Planner：验证 context、prompt、planner history 与 repair hint specialization
    test_planner_message_generation()
    test_planner_history_and_error_specialization()
    test_planner_step_plan_schema()

    # 4) Runtime：验证 replay / record 主链、artifact bundle 与 event 分发
    test_workflow_runtime_replay()
    test_workflow_runtime_record()
    test_agent_exposes_workflow_artifacts()

    # 5) 结果边界 / 调用方适配：验证 workflow-native facade 与 MCP formatter
    test_workflow_run_should_not_return_agent_history()
    test_mcp_workflow_result_formatter()

    print('workflow dsl tests passed')
