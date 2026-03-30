"""Workflow DSL 回归测试集合。

该文件主要覆盖：
- Parser / Compiler
- StepExecutor 控制流
- WorkflowRuntime replay 主链
- workflow-native 结果边界与 MCP 适配

测试风格：
- 不启动真实浏览器
- 不调用真实 LLM
- 通过 fake tools / fake browser session / fake planner llm 做轻量回归
"""

from pathlib import Path

from browser_use.agent.views import ActionResult
from browser_use.workflow_dsl import WorkflowCompiler, WorkflowDocument, WorkflowParser, WorkflowStep
from browser_use.workflow_dsl.executor import StepExecutor
from browser_use.workflow_dsl.runtime import WorkflowRuntime
from browser_use.workflow_dsl.views import ExecutionErrorFeedback, WorkflowAgentRunResult, WorkflowArtifacts


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




if __name__ == '__main__':
    # 1) Parser / Compiler：验证 DSL 文档构造、解析、roundtrip、变量收集、record/replay 编译差异
    test_parser_and_compiler_roundtrip()
    test_parser_file_api()
    test_parser_supports_control_flow()
    test_compiler_collects_nested_variables()
    test_compiler_record_mode_keeps_duplicate_steps()

    # 2) Executor：验证控制流执行与 set_variable 表达式求值
    test_step_executor_supports_control_flow()
    test_step_executor_set_variable_expression()

    # 3) Runtime：验证 replay 主链
    test_workflow_runtime_replay()

    print('workflow dsl tests passed')
