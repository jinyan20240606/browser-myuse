# Record / Replay 架构文档

## 0. 核心架构原则

本项目基于 browser-use 开源项目（浏览器自动化 Agent，架构成熟稳定），在此基础上增加录制回放能力。核心原则如下：

1. **最大程度复用原 React 主链路**：避免重复实现已有能力，减少踩坑风险
2. **原项目支持的，就复用**：如 Agent 推理、MessageManager、multi_act、history 记录等
3. **原项目不支持的，在 `workflow_dsl/` 独立扩展**：如控制流 action（loop/if/set_variable）、DSL 解析器、产物编译器、回放执行引擎
4. **避免侵入原始代码**：新增能力通过旁路（旁路 Recorder）或独立模块实现，不破坏 React 主链路稳定性

---

## 1. 当前架构（React + Recorder）

当前实现已从旧的独立 `workflow-dsl` 架构切换为 **React 主链路兼容录制**：

- `record` 模式不再走独立的 Planner / Executor / Runtime
- `record` = React 原生推理 + 旁路 Recorder
- `replay` 保留对 `WorkflowRuntime.replay()` 的消费能力，由 `StepExecutor` 确定性执行 DSL
- 未来目标：Agent 承担 replay 编排与兜底，`workflow_dsl/` 中的解析/执行/编译/控制流等能力继续作为独立引擎被复用

---

## 2. 三种模式执行流程

### 2.1 react 模式（默认）

```
Agent.run()
  → React 主循环 (while n_steps <= max_steps)
    → step()
      → _prepare_context()：获取浏览器状态
      → _get_next_action()：LLM 推理（MessageManager + AgentOutput）
      → _execute_actions()：multi_act()
      → _post_process()：失败计数、下载检查
      → _finalize()：记录 AgentHistory
  → 返回 AgentHistoryList
```

### 2.2 record 模式（React + Recorder）

```
Agent.run()
  → React 主循环（与 react 模式完全相同）
    → step()
      → _prepare_context()
      → _get_next_action()：使用 Agent 原生 LLM 推理
      → _execute_actions()：multi_act()
      → _post_process()
      → _finalize()
        → _record_workflow_steps()  ★ 旁路录制
          将成功执行的 action 映射为 WorkflowStep
  → _build_record_result()  ★ 编译产物
    → 自动补齐 navigate 首步（如果有 initial_url）
    → WorkflowCompiler.compile() → record.md + replay.md
    → 保存 history.json + manifest.json
  → 返回 WorkflowAgentRunResult
```

### 2.3 replay 模式（消费 DSL）

```
Agent.run()
  → WorkflowRuntime.replay()
    → 解析 replay DSL（markdown / YAML）
    → StepExecutor 逐步执行
    → 生成 WorkflowHistory
  → 返回 WorkflowAgentRunResult
```

---

## 3. 核心代码位置

| 模块 | 文件 | 说明 |
|------|------|------|
| Agent 主链路 | [`browser_use/agent/service.py`](browser_use/agent/service.py) | 三种模式的统一入口 |
| 录制步骤映射 | [`_record_workflow_steps()`](browser_use/agent/service.py) | 在 `_finalize()` 中将成功 action → WorkflowStep |
| 产物编译保存 | [`_build_record_result()`](browser_use/agent/service.py) | run 结束时编译 record/replay 文档并落盘 |
| DSL 数据模型 | [`browser_use/workflow_dsl/views.py`](browser_use/workflow_dsl/views.py) | WorkflowStep / WorkflowDocument 等 |
| DSL 编译器 | [`browser_use/workflow_dsl/compiler.py`](browser_use/workflow_dsl/compiler.py) | 编译成 record.md / replay.md |
| DSL 解析器 | [`browser_use/workflow_dsl/parser.py`](browser_use/workflow_dsl/parser.py) | 解析 DSL 文件 |
| Replay 执行 | [`browser_use/workflow_dsl/runtime.py`](browser_use/workflow_dsl/runtime.py) | replay 模式的 DSL 消费引擎 |
| Step 执行器 | [`browser_use/workflow_dsl/executor.py`](browser_use/workflow_dsl/executor.py) | replay 模式的步骤执行器 |

---

## 4. 录制产物结构

record 模式会在指定路径下生成 bundle 目录：

```
tests/acceptance_record/
├── record.md        # 录制态 DSL（保留原始顺序）
├── replay.md        # 回放态 DSL（去重压缩后）
├── history.json     # 录制历史（每步的 planned/executed steps）
└── manifest.json    # 产物清单
```

### 产物示例（record.md）

```yaml
---
id: xxx_workflow
name: 打开百度搜索张雪峰
task: 打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务
start_url: https://www.baidu.com
steps:
- id: step_0
  action: navigate
  url: https://www.baidu.com
- id: step_1
  action: input
  text: 张雪峰
  clear: true
  locator:
    element_hash: 114676202747150647
    stable_hash: 17869200503948011978
    xpath: html/body/div[1]/div[2]/div[3]/div/.../textarea
    attributes:
      id: chat-textarea
- id: step_2
  action: click
  locator:
    element_hash: 4146854060744292804
    stable_hash: 4146854060744292804
    xpath: html/body/div[1]/div[2]/div[3]/div/.../button
    attributes:
      id: chat-submit-button
---
```

> 注意：录制产物不再保存 volatile `index`，改为使用嵌套的 `locator` 字段存储稳定定位信息。回放引擎在执行时会根据 `locator` 在当前页面重建实时 index。

---

## 5. 关键设计决策

### 5.1 为什么 record 不走独立 Planner

| 维度 | 旧方案（独立 Planner） | 新方案（React + Recorder） |
|------|----------------------|--------------------------|
| LLM 推理 | 独立 WorkflowPlanner | Agent 原生推理链 |
| 执行器 | 独立 StepExecutor | Agent.multi_act() |
| 消息管理 | 无 MessageManager | 复用 MessageManager |
| 历史记录 | WorkflowHistory only | AgentHistory + WorkflowHistory |
| Done 判定 | planner.is_done | Agent 原生 done action |
| 错误恢复 | repair loop (re-plan) | Agent 原生 consecutive_failures |
| System Prompt | workflow planner 角色 | Agent 标准 system prompt |
| 产物格式 | 相同 | 相同 |

核心收益：
- **复用 React 全部能力**（thinking/memory/evaluation/fallback LLM/demo mode/skills/tools）
- **录制器只是旁路观察者**，不影响执行链路
- **产物质量更高**，因为只录制真实执行成功的动作

### 5.2 录制时的过滤策略

- ✅ 只录制执行成功的浏览器动作（input / click / navigate / scroll 等）
- ❌ 跳过 `done` 动作（非浏览器动作，replay 不需要）
- ❌ 跳过执行失败的动作（不计入产物）
- ✅ 自动补齐 `navigate` 首步（从 `initial_url` 推断）

### 5.3 日志标识

录制相关日志统一使用 `📝 Record:` 前缀：

```
INFO  📝 Record: 已录制 step_1 → input({'index': 12, 'text': '张雪峰', 'clear': True})
INFO  📝 Record: 已录制 step_2 → click({'index': 367})
INFO  📝 Record: 跳过 done 动作（非浏览器动作，不纳入录制产物）
INFO  📝 Record: 自动补齐导航步骤 → navigate(url=https://www.baidu.com)
INFO  📝 Record: 共录制 3 个步骤，开始编译产物...
INFO  💾 Workflow record artifacts 已保存到 bundle: tests/acceptance_record
```

---

## 6. 使用方式

### 6.1 record 模式

```python
from browser_use import Agent, ChatOpenAI

agent = Agent(
    task='打开 https://www.baidu.com ，搜索张雪峰，点击搜索按钮，然后结束任务',
    llm=ChatOpenAI(model='gpt-4.1-mini'),
    workflow_mode='record',
    workflow_dsl_path='output/my_workflow.md',
)

result = agent.run_sync(max_steps=10)

# 访问产物
print(result)                          # WorkflowAgentRunResult
print(agent.workflow_record_summary)   # WorkflowRecordSummary
print(agent.workflow_artifacts)        # WorkflowArtifacts
```

### 6.2 replay 模式

```python
agent = Agent(
    task='Replay test',
    llm=ChatOpenAI(model='gpt-4.1-mini'),
    workflow_mode='replay',
    workflow_dsl_path='output/my_workflow/replay.md',
)

result = agent.run_sync(max_steps=10)
```

### 6.3 react 模式（默认）

```python
agent = Agent(
    task='打开 https://www.baidu.com ，搜索张雪峰',
    llm=ChatOpenAI(model='gpt-4.1-mini'),
)

result = agent.run_sync(max_steps=10)
# 返回 AgentHistoryList（与原始 browser-use 兼容）
```

---

## 7. 验收脚本

```bash
python tests/test_readme_acceptance.py
```

验收项：
1. **record 模式**：React 主链路执行 + 自动产出 bundle（record.md / replay.md / history.json / manifest.json）
2. **replay 模式**：消费 replay.md 并成功回放
3. **react 模式**：标准 Agent 执行，返回 AgentHistoryList

---

## 8. TODO：后续优化计划

### 8.1 P0：逐步删除 workflow-dsl 旧 record 侧代码 【已完成】

- [x] 删除 [`WorkflowPlanner`](browser_use/workflow_dsl/planner.py)（record 已不需要独立 planner）
- [x] 删除 [`WorkflowRuntime.record()`](browser_use/workflow_dsl/runtime.py)（record 已由 Agent 主链路处理）
- [x] 删除 [`browser_use/workflow_dsl/planner_engine.py`](browser_use/workflow_dsl/planner_engine.py)（当前仓库中已不存在）
- [x] 精简 [`browser_use/workflow_dsl/events.py`](browser_use/workflow_dsl/events.py) 中仅 record 使用的事件
- [x] 清理 [`browser_use/workflow_dsl/__init__.py`](browser_use/workflow_dsl/__init__.py) 的旧导出

### 8.2 P1：replay 改造

- [ ] replay 支持运行时变量注入

> 设计原则：record 的旧独立智能规划链路已迁入 Agent 主链路；replay 相关确定性执行能力继续保留在独立 `workflow_dsl` 模块中，以复用为先、避免不必要迁移，保持解耦。

### 8.3 P2：录制质量提升

- [ ] 录制时增加页面变化检测（before/after browser state 对比）
- [ ] 录制时增加 post-condition validation（click 后页面是否推进）
- [ ] 录制时增加更丰富的元数据（URL / title / DOM 摘要 / 截图路径）
- [ ] 支持录制时的步骤合并 / 去重优化

### 8.4 P3：产物增强

- [ ] 产物中增加截图时间线
- [ ] 产物中增加执行耗时统计
- [ ] 支持从产物中提取可参数化的 workflow template
- [ ] 支持 workflow 版本管理和 diff

### 8.5 P4：整体架构清理

- [ ] 统一 Agent 返回类型（不再区分 AgentHistoryList 和 WorkflowAgentRunResult）
- [ ] 优化对外暴露的 API，隐藏内部 DSL 实现细节
