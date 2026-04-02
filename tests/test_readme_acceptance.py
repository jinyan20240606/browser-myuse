"""README 功能点真实场景验收脚本。

目标：
- 不使用 fake 环境
- 直接用真实 `Agent` 跑真实浏览器任务
- 验证 `record/replay/react` 三种模式在当前“record 兼容 React 主链路”实现下的可用性

注意：
- 这是“手工验收脚本”，不是单元测试
- 本脚本按当前源码中 [`Agent`](browser_use/agent/service.py) 暴露的属性访问：
  - `workflow_record_summary`
  - `workflow_artifacts`
- 当前实现中：
  - `record` 为 React 主链路执行 + 旁路录制产物
  - `replay` 仍由 workflow runtime 负责消费 replay DSL
- 运行前需要：
  1. 已正确配置 `.env`
  2. 本机可正常启动浏览器
  3. LLM 可正常调用
- 建议逐项运行并人工观察浏览器行为、终端日志、产物目录
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from browser_use import Agent, ChatOpenAI


def build_llm() -> ChatOpenAI:
    """从环境变量读取 OpenAI-compatible 模型配置。"""
    api_key = os.environ.get('OPENAI_API_KEY') or None
    base_url = os.environ.get('OPENAI_API_BASE') or None
    model = os.environ.get('OPENAI_MODEL_NAME') or 'gpt-4.1-mini'
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        add_schema_to_system_prompt=True,
    )


def print_section(title: str) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}\n")


def run_record_acceptance(selected_task: str) -> None:
    """验收项 1：真实 record 模式是否能产出 workflow artifacts bundle。"""

    print_section('验收项 1：record 模式真实录制测试')
    output_path = Path('tests/acceptance_record.md')
    print(f"[当前测试任务]: {selected_task}\n")

    agent = Agent(
        task=selected_task,
        llm=build_llm(),
        workflow_mode='record',
        workflow_dsl_path=output_path,
    )

    result = agent.run_sync(max_steps=10)

    print('[运行结果]')
    print(result)
    print('\n[workflow_record_summary]')
    print(agent.workflow_record_summary)
    print('\n[workflow_artifacts]')
    print(agent.workflow_artifacts)
    print('\n[产物目录检查]')
    bundle_dir = output_path.with_suffix('')
    if bundle_dir.exists():
        print(f'已生成 bundle 目录: {bundle_dir}')
        for item in sorted(bundle_dir.iterdir()):
            print(f'- {item.name}')
    else:
        print(f'未发现 bundle 目录: {bundle_dir}')
    print('\n[预期检查点]')
    print('- 浏览器真实启动')
    print('- 任务执行结束')
    print('- 生成 tests/acceptance_record/ 目录')
    print('- 目录内应包含 record.md / replay.md / history.json / manifest.json')
    print('- record 模式日志应体现 React step 执行链路（而非 planner turn 循环）')


def run_replay_acceptance() -> None:
    """验收项 2：真实 replay 模式是否能消费 replay DSL 并成功回放。"""

    print_section('验收项 2：replay 模式真实回放测试')
    replay_path = Path('tests/acceptance_record/replay.md')
    if not replay_path.exists():
        print(f'[跳过] 未找到回放 DSL: {replay_path}')
        print('请先运行“验收项 1：record 模式真实录制测试”')
        return

    agent = Agent(
        task='Replay acceptance test',
        llm=build_llm(),
        workflow_mode='replay',
        workflow_dsl_path=replay_path,
    )

    result = agent.run_sync(max_steps=10)

    print('[运行结果]')
    print(result)
    print('\n[workflow_artifacts]')
    print(agent.workflow_artifacts)
    print('\n[replay DSL 文件检查]')
    print(f'回放 DSL 路径: {replay_path}')
    print(f'是否存在: {replay_path.exists()}')
    print('\n[预期检查点]')
    print('- 浏览器真实启动')
    print('- replay.md 被真实消费')
    print('- workflow_artifacts.history.mode 应为 replay')
    print('- 终端日志应看到 Workflow replay 相关输出')


def run_react_acceptance() -> None:
    """验收项 3：react 模式下真实用户任务是否能正常完成。"""

    print_section('验收项 3：react 模式真实任务测试')

    agent = Agent(
        task='打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务',
        llm=build_llm(),
        workflow_mode='react',
    )

    result = agent.run_sync(max_steps=10)

    print('[运行结果]')
    print(result)
    print('\n[预期检查点]')
    print('- 浏览器真实启动')
    print('- AgentHistoryList 正常返回')
    print('- 终端中能看到普通 Agent / step 执行链路')


def main() -> None:
    """逐项运行 README 功能点的真实场景验收。"""
        # 测试任务列表，可根据需要切换不同的任务描述
    tasks = [
        # 0: 基础任务
        '打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务',
        # 1: 带有控制流的任务
        '打开 https://www.baidu.com ，搜索 Python官方，如果搜索结果列表中某一项出现“官方”字样，就点击进去然后结束任务，否则也结束任务',
        # 2: 循环任务
        '打开 Hacker News (https://news.ycombinator.com)，提取前 3 个新闻的标题并循环输出，然后结束任务'
    ]

    print_section('README 功能点真实场景验收说明')
    print('本脚本不使用 fake 环境，而是直接调真实 Agent + 真实浏览器 + 真实 LLM。')
    print('建议按顺序执行：')

    print('1. record 模式录制（React 主链路 + 录制产物）')
    run_record_acceptance(tasks[1])
    print('2. replay 模式回放')
    # run_replay_acceptance()
    print('3. react 模式普通任务')
    # run_react_acceptance()

    # print_section('验收完成')
    # print('请结合浏览器行为、终端日志、产物目录，人工判断是否达到理想效果。')


if __name__ == '__main__':
    main()
