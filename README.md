# Workflow DSL 重构收敛总结

## 1. 背景

本次重构目标是将浏览器自动化链路从旧的实时 ReAct action 主导模式，逐步收敛到 [`DSL_ARCHITECTURE_DESIGN.md`](DSL_ARCHITECTURE_DESIGN.md) 定义的 **Workflow-first** 架构：

- 在线阶段：Planner 基于最新页面快照生成 `WorkflowStep DSL`
- 执行阶段：Executor 按 step DSL 调用已有 tool/action
- 修复阶段：失败后生成结构化错误反馈并驱动 Repair Loop
- 沉淀阶段：Compiler 输出录制态 / 回放态 DSL
- 复用阶段：ReplayEngine / Runtime 直接 0-token 回放 DSL

当前实现已经从 MVP 阶段的“事后从成功历史编译 DSL”推进到：

- record 模式由 [`WorkflowRuntime`](browser_use/workflow_dsl/runtime.py) 驱动
- 每轮由 [`WorkflowPlanner`](browser_use/workflow_dsl/planner.py) 生成 DSL step batch
- [`StepExecutor`](browser_use/workflow_dsl/executor.py) 执行并生成结构化反馈
- [`WorkflowArtifacts`](browser_use/workflow_dsl/views.py) 成为 workflow-native 结果出口

---

## 2. 本次关键重构改动点

### 2.1 数据模型与 DSL 视图层

核心文件：
- [`browser_use/workflow_dsl/views.py`](browser_use/workflow_dsl/views.py)

完成内容：
- 重构 [`WorkflowStep`](browser_use/workflow_dsl/views.py)
  - 支持 `params`
  - 支持 `then_steps` / `else_steps` / `steps`
  - 支持 `if` / `loop_for` / `loop_until` / `set_variable` 控制流
- 重构 [`WorkflowDocument`](browser_use/workflow_dsl/views.py)
- 重构 [`ExecutionErrorFeedback`](browser_use/workflow_dsl/views.py)
  - 增加 `resolved_params`
  - 增加 `repair_hint`
  - 保留 `last_success_step` / `runtime_variables`
- 重构 [`WorkflowExecutionResult`](browser_use/workflow_dsl/views.py)
- 增加 workflow-native 结果结构：
  - [`WorkflowPlannerContext`](browser_use/workflow_dsl/views.py)
  - [`WorkflowPlannerTurn`](browser_use/workflow_dsl/views.py)
  - [`WorkflowHistoryEntry`](browser_use/workflow_dsl/views.py)
  - [`WorkflowHistory`](browser_use/workflow_dsl/views.py)
  - [`WorkflowArtifacts`](browser_use/workflow_dsl/views.py)
  - [`WorkflowRecordSummary`](browser_use/workflow_dsl/views.py)

意义：
- 把 workflow mode 的历史、工件、反馈从旧 [`AgentHistory`](browser_use/agent/views.py) 语义中剥离出来。

---

### 2.2 Parser 层

核心文件：
- [`browser_use/workflow_dsl/parser.py`](browser_use/workflow_dsl/parser.py)

完成内容：
- 重构 [`WorkflowParser.parse_markdown()`](browser_use/workflow_dsl/parser.py)
- 支持 markdown / YAML 两种 DSL 文档输入
- 严格校验：
  - `steps` 必须为列表
  - 每个 step 必须有 `action`
  - `then_steps` / `else_steps` / `steps` 必须为列表
- 明确区分：
  - step metadata
  - step params
  - nested control-flow steps

意义：
- 让 DSL 文件格式更稳定、可机器解析，符合设计文档“结构化、可读、适合保存”的要求。

---

### 2.3 Planner 层

核心文件：
- [`browser_use/workflow_dsl/planner.py`](browser_use/workflow_dsl/planner.py)

完成内容：
- 新增 [`WorkflowPlanner`](browser_use/workflow_dsl/planner.py)
- 新增 [`PlannerStepPlan`](browser_use/workflow_dsl/planner.py)
- 新增 [`WorkflowRepairLoop`](browser_use/workflow_dsl/planner.py)
- Planner 现在直接依赖 [`Tools`](browser_use/workflow_dsl/planner.py) registry，而不是只吃自然语言 action 描述
- 直接从 registry 提取结构化 action schema：
  - [`_build_action_schemas()`](browser_use/workflow_dsl/planner.py)
- 对 Planner 输出做 action 合法性校验：
  - [`_validate_plan_actions()`](browser_use/workflow_dsl/planner.py)
- 专门化 Planner prompt：
  - 强调 `snapshot -> step DSL -> executor -> feedback -> repaired step DSL`
  - 强调 DSL 是 tool schema 的持久化表达
  - 强调只能使用 `available_actions`
- 专门化 Planner 上下文：
  - 最新 browser snapshot
  - recent successful steps
  - planner turn summary
  - structured last error
  - runtime variables

意义：
- 让 Planner 更接近设计文档中的“唯一合法 DSL 输出生成器”。

---

### 2.4 Executor 层

核心文件：
- [`browser_use/workflow_dsl/executor.py`](browser_use/workflow_dsl/executor.py)

完成内容：
- 重构 [`StepExecutor.execute()`](browser_use/workflow_dsl/executor.py)
- 复用现有 [`registry.execute_action()`](browser_use/workflow_dsl/executor.py)
- 支持变量递归替换：
  - 字符串
  - 列表
  - 字典
- 支持控制流执行：
  - `if`
  - `loop_for`
  - `loop_until`
  - `set_variable`
- 增加 action schema 基础校验：
  - [`_validate_step_schema()`](browser_use/workflow_dsl/executor.py)
- 增加基础 post-condition validation：
  - [`_validate_action_result()`](browser_use/workflow_dsl/executor.py)
- 增加失败反馈生成：
  - [`create_error_feedback()`](browser_use/workflow_dsl/executor.py)

当前已做的验证能力：
- `input/fill` 需要有基础可观察确认
- `navigate/open` 需要有基础可观察确认
- 使用 `output_variable` 的 action 需要产生可提取输出

当前仍不足的地方见后文“存在问题”。

---

### 2.5 Compiler 层

核心文件：
- [`browser_use/workflow_dsl/compiler.py`](browser_use/workflow_dsl/compiler.py)

完成内容：
- 重构 [`WorkflowCompiler.compile()`](browser_use/workflow_dsl/compiler.py)
- 支持 `mode='record' | 'replay'`
  - `record`：保留原始顺序与重复 steps
  - `replay`：压缩重复 step
- 支持：
  - `start_url`
  - `to_dict()`
  - `to_markdown()`
  - `save()`
- 自动提取 `input_variables`

意义：
- 开始区分录制态 DSL 与回放态 DSL，符合设计文档“record / replay 分层”方向。

---

### 2.6 Runtime / Replay 层

核心文件：
- [`browser_use/workflow_dsl/runtime.py`](browser_use/workflow_dsl/runtime.py)
- [`browser_use/workflow_dsl/replay.py`](browser_use/workflow_dsl/replay.py)

完成内容：
- 新增统一运行主干 [`WorkflowRuntime`](browser_use/workflow_dsl/runtime.py)
  - [`record()`](browser_use/workflow_dsl/runtime.py)
  - [`replay()`](browser_use/workflow_dsl/runtime.py)
- [`ReplayEngine`](browser_use/workflow_dsl/replay.py) 已收敛为 runtime facade
- Runtime 统一协调：
  - Planner
  - Executor
  - Repair Loop
  - Compiler
  - Replay
- workflow-native artifacts 统一沉淀到 [`last_artifacts`](browser_use/workflow_dsl/runtime.py)

### 2.6.1 workflow-native history / artifact

Runtime 现在会产出：
- `record_document`
- `replay_document`
- [`WorkflowHistory`](browser_use/workflow_dsl/views.py)

### 2.6.2 workflow-native artifact bundle 保存

record 模式下会保存 bundle：
- `record.md`
- `replay.md`
- `history.json`
- `planner_turns.json`
- `manifest.json`

保存逻辑位于：
- [`_save_record_artifacts()`](browser_use/workflow_dsl/runtime.py)

意义：
- workflow 结果不再只是一个 DSL 文件，而是一整套可调试、可沉淀、可复盘的 artifacts bundle。

---

### 2.7 workflow-native 事件与日志

核心文件：
- [`browser_use/workflow_dsl/events.py`](browser_use/workflow_dsl/events.py)
- [`browser_use/workflow_dsl/runtime.py`](browser_use/workflow_dsl/runtime.py)

完成内容：
- 新增 workflow-native event：
  - [`WorkflowPlannerTurnEvent`](browser_use/workflow_dsl/events.py)
  - [`WorkflowStepBatchEvent`](browser_use/workflow_dsl/events.py)
  - [`WorkflowRepairEvent`](browser_use/workflow_dsl/events.py)
  - [`WorkflowArtifactsSavedEvent`](browser_use/workflow_dsl/events.py)
  - [`WorkflowHistoryEvent`](browser_use/workflow_dsl/events.py)
- Runtime 中已分发：
  - planner turn start / finish
  - step batch 执行结果
  - repair loop error
  - history entry
  - artifacts 保存完成

### 2.7.1 日志风格统一

现在日志统一为：
- 中文描述
- 保留关键英文关键词：
  - `Workflow Planner`
  - `Workflow step`
  - `Workflow replay`
  - `Workflow artifacts`
  - `bundle`

示例：
- `📘 Workflow Planner 第 N 轮开始`
- `🧭 已规划 Workflow steps: [...]`
- `📦 已执行 Workflow steps: [...]`
- `▶️ 开始 Workflow replay：...`
- `💾 Workflow artifacts 已保存到 bundle: ...`

意义：
- 让 workflow mode 的可观测性正式切到 workflow-native 语义，而不是旧 Agent step 语义。

---

### 2.8 Agent 桥接层收敛

核心文件：
- [`browser_use/agent/service.py`](browser_use/agent/service.py)

完成内容：
- [`Agent.run()`](browser_use/agent/service.py) 的 `record/replay` 已正式委托 [`WorkflowRuntime`](browser_use/workflow_dsl/runtime.py)
- 已删除旧桥接逻辑：
  - `_execute_record_workflow_batch()`
  - `_build_planner_agent_output()`
  - 旧的“事后从 AgentHistory 编译 DSL”路径
- Agent 侧正式暴露：
  - [`workflow_record_summary`](browser_use/agent/service.py)
  - [`workflow_artifacts`](browser_use/agent/service.py)
- [`Agent.run()`](browser_use/agent/service.py) / [`Agent.run_sync()`](browser_use/agent/service.py:3936) 在 workflow mode 下已不再把结果强行包装回旧 [`AgentHistoryList`](browser_use/agent/views.py)
- [`register_done_callback`](browser_use/agent/service.py) 在 workflow mode 下接收 workflow-native payload，而不是固定旧 history

意义：
- workflow mode 的真实执行主干已经从旧 Agent 逻辑中抽离出来。
- Agent 外层接口开始与 workflow-native 结果边界对齐，而不是继续被旧 history 语义绑住。

---

## 3. 当前实现后的整体架构

当前主链可概括为：

1. **record 模式**
   - [`Agent.run()`](browser_use/agent/service.py)
   - -> [`WorkflowRuntime.record()`](browser_use/workflow_dsl/runtime.py)
   - -> [`WorkflowPlanner`](browser_use/workflow_dsl/planner.py) 读取最新页面快照并生成 step DSL
   - -> [`StepExecutor`](browser_use/workflow_dsl/executor.py) 执行 step DSL
   - -> 失败时通过 [`ExecutionErrorFeedback`](browser_use/workflow_dsl/views.py) + [`WorkflowRepairLoop`](browser_use/workflow_dsl/planner.py) 驱动下一轮修复
   - -> 最终由 [`WorkflowCompiler`](browser_use/workflow_dsl/compiler.py) 产出 `record_document` 与 `replay_document`
   - -> 由 [`WorkflowArtifacts`](browser_use/workflow_dsl/views.py) 对外暴露

2. **replay 模式**
   - [`Agent.run()`](browser_use/agent/service.py)
   - -> [`WorkflowRuntime.replay()`](browser_use/workflow_dsl/runtime.py)
   - -> 顺序消费 replay DSL
   - -> 生成 workflow-native history / artifact

3. **对外结果边界**
   - `react` 模式：[`Agent.run()`](browser_use/agent/service.py) 仍返回 [`AgentHistoryList`](browser_use/agent/views.py)
   - workflow mode：[`Agent.run()`](browser_use/agent/service.py) / [`Agent.run_sync()`](browser_use/agent/service.py:3951) 统一返回 [`WorkflowAgentRunResult`](browser_use/workflow_dsl/views.py:148)
   - 新架构正式结果：
     - [`WorkflowAgentRunResult`](browser_use/workflow_dsl/views.py:148)
     - [`agent.workflow_artifacts`](browser_use/agent/service.py)
     - [`agent.workflow_record_summary`](browser_use/agent/service.py)

---

## 4. 当前仍存在的问题

### 4.1 P0 仍未完全收尾

#### 4.1.1 Executor 的页面变化检测仍然不够强
当前 [`StepExecutor`](browser_use/workflow_dsl/executor.py) 还没有真正做：
- before / after browser state 对比
- URL / title / DOM 关键区域变化检测
- click 后页面是否推进的明确验证

这会导致：
- record 主链虽然能靠下一轮最新页面快照继续修正
- 但某些 **假成功 step** 仍有机会被记入 `successful_steps`
- 最终可能污染 replay 资产

#### 4.1.2 失败采样仍然偏弱
当前失败反馈 [`ExecutionErrorFeedback`](browser_use/workflow_dsl/views.py) 已有结构，但采样内容还不够丰富：
- 主要还是 `URL + Title`
- 缺少关键 DOM 摘要
- 缺少 before/after state 差异
- 缺少更细的执行上下文

#### 4.1.3 post-condition validation 还是基础版
当前只做了最小版校验：
- input/fill
- navigate/open
- output_variable

但还缺：
- click 成功的页面推进判断
- extract 成功的内容质量判断
- wait / scroll / select 之类动作的业务后置条件验证

---


---

### 4.2 workflow mode 的对话保存仍未完全切到 workflow-native artifacts
虽然当前已经完成：
- event
- log
- artifact bundle

但“对话保存”这一块，仍然没有专门的 workflow-native conversation artifact。
当前更多还是沿用旧 Agent 的 conversation/save_conversation 路径。

如果后续要彻底完成 workflow mode 的独立化，这部分还需要继续做。

---

## 5. 为什么接下来最值得继续做 P0

从设计文档目标看：
- 首跑成功率
- 可沉淀自动化资产
- 0-token 回放稳定性

真正决定资产质量的，不是 Planner 能不能继续出 step，
而是 Executor 是否能**严格区分“工具执行过”与“页面真的推进了”**。

所以接下来最重要的是：
1. 深化页面变化检测
2. 深化失败采样
3. 深化 post-condition validation

这是防止“假成功 step 被沉淀进 DSL 资产”的关键。

---

## 6. 后续计划

### 6.1 下一阶段优先级（建议继续）

#### P0（优先继续）
1. 强化 [`StepExecutor`](browser_use/workflow_dsl/executor.py) 的页面变化检测
   - 采集 action 前后的 browser state
   - 对 URL / title / DOM 摘要做差异判断
   - 把变化判断纳入 success 认定

2. 强化失败采样
   - 失败时采集：
     - before/after URL
     - title
     - DOM 摘要片段
     - 最近 action result
     - resolved params
   - 写入 [`ExecutionErrorFeedback`](browser_use/workflow_dsl/views.py)

3. 强化 post-condition validation
   - click / input / navigate / extract / scroll 等动作做更细粒度验证
   - 建立 action-specific validation 策略

#### P1（可继续加强）
1. workflow-native conversation artifact
2. workflow-native artifact bundle 中增加更多调试工件
3. 更完整的 workflow UI / timeline / replay 调试数据


---

## 7. 当前测试情况

核心测试文件：
- [`test-workflow.py`](test-workflow.py)

当前已覆盖：
- parser roundtrip
- parser file api
- control-flow parse
- compiler variable collection
- record/replay compiler 差异
- executor control-flow
- set_variable expression
- planner context / prompt / repair hint
- runtime replay
- runtime record
- workflow artifacts 暴露
- [`WorkflowAgentRunResult`](browser_use/workflow_dsl/views.py:148) 统一返回语义
- MCP 对统一 workflow facade 的格式化适配
- workflow artifacts bundle 保存
- workflow-native event 基础验证
- workflow 结果边界不再退回旧 [`AgentHistoryList`](browser_use/agent/views.py)

已执行通过：
- [`python test-workflow.py`](test-workflow.py)

---

## 8. 总结

当前重构已经完成了从“旧 Agent / ReAct 主导”到“workflow-first 架构主干”的关键跃迁：

- Planner 已在线生成 DSL
- Repair Loop 已结构化
- Executor 已接管执行层
- Compiler 已区分 record/replay
- Runtime 已统一 record/replay 主数据流
- Artifacts 已成为正式结果出口
- workflow-native history / event / log / artifact bundle 已成型

当前最主要未收尾点已经收敛到 Executor 质量层：
- 页面变化检测
- 失败采样
- post-condition validation

这三项完成后，整体实现会更接近 [`DSL_ARCHITECTURE_DESIGN.md`](DSL_ARCHITECTURE_DESIGN.md) 所期待的“执行器验证通过的 DSL 文档，而不是历史日志”。
