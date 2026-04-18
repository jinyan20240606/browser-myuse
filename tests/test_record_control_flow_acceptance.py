"""record 模式控制流生成功能测试。

目标：
- 接入真实大模型
- 尽量复用真实 Agent 内部链路
- 不依赖真实浏览器页面变化
- 用固定 mock 上下文测试 record 模式下模型是否会直接生成控制流 action
- 观察在不同任务描述下，模型何时能稳定输出 `if / loop_for / loop_until / set_variable`
- 同时检查输出是否贴近当前真实 schema，避免“看起来是控制流，实际上字段名不兼容”

说明：
- 这是功能测试脚本，不是单元测试
- 风格参考 [`tests/test_readme_acceptance.py`](tests/test_readme_acceptance.py)
- 运行前需要：
  1. 已正确配置 `.env`
  2. LLM 可正常调用
  3. 当前环境能导入项目依赖
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent, ChatOpenAI
from browser_use.agent.prompts import AgentMessagePrompt
from browser_use.agent.views import AgentStepInfo
from browser_use.browser.views import BrowserStateSummary, PageInfo, TabInfo
from browser_use.filesystem.file_system import FileSystem
from browser_use.llm.messages import BaseMessage


class FakeDomState:
    def __init__(self, llm_text: str):
        self._llm_text = llm_text
        self._root = None
        self.selector_map = {}

    def llm_representation(self, include_attributes=None):
        return self._llm_text


def build_llm() -> ChatOpenAI:
    api_key = os.environ.get('OPENAI_API_KEY') or None
    base_url = os.environ.get('OPENAI_API_BASE') or None
    model = os.environ.get('OPENAI_MODEL_NAME') or 'gpt-4.1-mini'
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=1,
        max_completion_tokens=1500,
        add_schema_to_system_prompt=True,
        dont_force_structured_output=False,
    )


def print_section(title: str) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}\n")


def build_browser_state(dom_text: str, url: str, title: str) -> BrowserStateSummary:
    return BrowserStateSummary(
        dom_state=cast(Any, FakeDomState(dom_text)),
        url=url,
        title=title,
        tabs=[TabInfo(target_id='tab_1', url=url, title=title)],
        screenshot=None,
        page_info=PageInfo(
            viewport_width=1280,
            viewport_height=800,
            page_width=1280,
            page_height=2400,
            scroll_x=0,
            scroll_y=0,
            pixels_above=0,
            pixels_below=1600,
            pixels_left=0,
            pixels_right=0,
        ),
        pixels_above=0,
        pixels_below=1600,
        browser_errors=[],
        is_pdf_viewer=False,
        recent_events=None,
        closed_popup_messages=[],
    )


def build_agent(task: str) -> Agent:
    return Agent(
        task=task,
        llm=build_llm(),
        workflow_mode='record',
    )


def build_messages_from_agent(agent: Agent, dom_text: str, url: str, title: str) -> list[BaseMessage]:
    file_system = FileSystem(base_dir=PROJECT_ROOT / 'tmp' / 'record_prompt_acceptance')
    step_info = AgentStepInfo(step_number=2, max_steps=10)
    browser_state = build_browser_state(dom_text=dom_text, url=url, title=title)

    user_message = AgentMessagePrompt(
        browser_state_summary=browser_state,
        file_system=file_system,
        agent_history_description='',
        read_state_description='',
        task=agent.task,
        include_attributes=agent.settings.include_attributes,
        step_info=step_info,
        vision_detail_level=agent.settings.vision_detail_level,
        include_recent_events=agent.include_recent_events,
        llm_screenshot_size=agent.browser_session.llm_screenshot_size,
    ).get_user_message(use_vision=False)

    return [agent._message_manager.system_prompt, user_message]


def extract_json_block(raw_text: str) -> str:
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f'未找到可解析 JSON：{raw_text}')
    return raw_text[start : end + 1]


def summarize_actions(parsed: dict[str, Any]) -> list[str]:
    actions = parsed.get('action') or []
    action_names: list[str] = []
    for item in actions:
        if isinstance(item, dict) and item:
            action_names.append(next(iter(item.keys())))
    return action_names


def validate_control_flow_shape(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    actions = parsed.get('action') or []
    if not isinstance(actions, list):
        return ['action 字段不是 list']

    for idx, item in enumerate(actions):
        if not isinstance(item, dict) or not item:
            errors.append(f'action[{idx}] 不是有效对象')
            continue

        action_name = next(iter(item.keys()))
        params = item[action_name]
        if not isinstance(params, dict):
            errors.append(f'action[{idx}]::{action_name} 参数不是 dict')
            continue

        if action_name == 'if':
            if 'variable' not in params:
                errors.append(f'action[{idx}]::if 缺少 variable')
            if 'then_steps' not in params and 'else_steps' not in params:
                errors.append(f'action[{idx}]::if 缺少 then_steps/else_steps')
            if 'condition' in params:
                errors.append(f'action[{idx}]::if 使用了非当前 schema 字段 condition')
            if 'then' in params:
                errors.append(f'action[{idx}]::if 使用了非当前 schema 字段 then')

        if action_name == 'loop_for':
            if 'items' not in params:
                errors.append(f'action[{idx}]::loop_for 缺少 items')
            if 'steps' not in params:
                errors.append(f'action[{idx}]::loop_for 缺少 steps')
            if 'item_var' in params:
                errors.append(f'action[{idx}]::loop_for 使用了非当前 schema 字段 item_var，应为 item_variable')
            if 'body' in params:
                errors.append(f'action[{idx}]::loop_for 使用了非当前 schema 字段 body，应为 steps')

        if action_name == 'loop_until':
            if 'steps' not in params:
                errors.append(f'action[{idx}]::loop_until 缺少 steps')
            if 'condition' in params:
                errors.append(f'action[{idx}]::loop_until 使用了非当前 schema 字段 condition')

        if action_name == 'set_variable':
            if 'name' not in params:
                errors.append(f'action[{idx}]::set_variable 缺少 name')
            if 'value' not in params and 'expression' not in params:
                errors.append(f'action[{idx}]::set_variable 缺少 value/expression')

        if action_name == 'click_element':
            errors.append(f'action[{idx}] 使用了非当前 canonical action: click_element，应为 click')

    return errors


async def run_case(name: str, task: str, dom_text: str, url: str, title: str) -> None:
    print_section(f'控制流生成测试: {name}')
    print(f'[任务描述]\n{task}\n')
    print(f'[页面快照]\n{dom_text}\n')

    agent = build_agent(task)
    messages = build_messages_from_agent(agent=agent, dom_text=dom_text, url=url, title=title)

    print('[请求配置]')
    print(f'- model: {agent.llm.model}')
    print(f'- base_url: {agent.llm.base_url}')
    print(f'- timeout: {agent.llm.timeout}')
    print(f'- max_retries: {agent.llm.max_retries}')
    print(f'- workflow_mode: {agent.workflow_mode}')
    print(f'- action_model: {agent.ActionModel.__name__}')
    print('\n[开始请求]')

    response = await agent.get_model_output(messages)
    parsed = response.model_dump(mode='json') if hasattr(response, 'model_dump') else cast(dict[str, Any], response)
    raw_text = json.dumps(parsed, ensure_ascii=False)

    print('[请求结束]')
    print('[解析模式] agent_structured_output')

    print('[系统 Prompt 预览]')
    print(str(messages[0].content)[:1800])
    print('\n[用户上下文预览]')
    print(str(messages[1].content)[:2400])
    print('\n[模型返回]')
    print(raw_text)

    action_names = summarize_actions(parsed)
    has_control_flow = any(name in {'if', 'loop_for', 'loop_until', 'set_variable'} for name in action_names)
    schema_errors = validate_control_flow_shape(parsed)

    print('\n[解析后的 JSON]')
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print('\n[动作摘要]')
    print(action_names)
    print(f'[是否包含控制流 action] {has_control_flow}')
    print('\n[Schema 兼容性检查]')
    if schema_errors:
        for error in schema_errors:
            print(f'- {error}')
    else:
        print('- 通过：控制流字段名与当前 schema 基本兼容')


async def main() -> None:
    cases = [
        {
            'name': 'if_first_official_result',
            'task': '打开百度搜索 Python官方，遍历搜索结果，如果某一项出现“官方”字样，就点击第一个符合条件的结果后结束，否则结束任务。',
            'url': 'https://www.baidu.com/s?wd=Python%E5%AE%98%E6%96%B9',
            'title': 'Python官方_百度搜索',
            'dom_text': """[12]<input aria-label='搜索框'>Python官方</input>
[21]<button>百度一下</button>
[301]<a>Python.org - Welcome to Python.org</a>
[302]<span>官方</span>
[303]<a>其它结果A</a>
[304]<a>其它结果B</a>
""",
        },
        {
            'name': 'loop_top3_news',
            'task': '打开 Hacker News 首页，遍历前 3 条新闻，依次提取标题并保存，然后结束任务。',
            'url': 'https://news.ycombinator.com/',
            'title': 'Hacker News',
            'dom_text': """[10]<a>Story 1: First title</a>
[11]<a>Story 2: Second title</a>
[12]<a>Story 3: Third title</a>
[13]<a>Story 4: Fourth title</a>
""",
        },
        {
            'name': 'loop_until_popup_disappears',
            'task': '如果页面有弹窗就关闭，并重复检查直到弹窗消失，然后继续点击提交按钮。',
            'url': 'https://example.com/form',
            'title': 'Example Form',
            'dom_text': """[50]<div>订阅弹窗</div>
[51]<button>关闭弹窗</button>
[90]<button>提交</button>
""",
        },
    ]

    for case in cases:
        await run_case(**case)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
