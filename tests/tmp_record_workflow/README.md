# `tmp_record_workflow` 产物目录说明

本文档说明 [`tmp_record_workflow/`](tmp_record_workflow) 目录下各个文件的作用，以及它们之间的关系。

这个目录来自 [`WorkflowRuntime._save_record_artifacts()`](browser_use/workflow_dsl/runtime.py:431) 在 record 模式下的保存逻辑。

保存入口逻辑：
- [`bundle_dir = Path(output_path).with_suffix('')`](browser_use/workflow_dsl/runtime.py:447)
- 当测试里传入 `output_path='tmp_record_workflow.md'` 时，最终 bundle 目录就是 [`tmp_record_workflow/`](tmp_record_workflow)

生成的文件包括：
- [`record.md`](tmp_record_workflow/record.md)
- [`replay.md`](tmp_record_workflow/replay.md)
- [`history.json`](tmp_record_workflow/history.json)
- [`planner_turns.json`](tmp_record_workflow/planner_turns.json)
- [`manifest.json`](tmp_record_workflow/manifest.json)

---

## 1. [`manifest.json`](tmp_record_workflow/manifest.json)

作用：
- 这是整个 bundle 的“索引文件” / “入口文件”。
- 它本身不保存执行细节，只负责告诉你：
  - 录制 DSL 在哪
  - 回放 DSL 在哪
  - history 在哪
  - planner turn 历史在哪

当前内容：
- `record_document` -> [`tmp_record_workflow/record.md`](tmp_record_workflow/record.md)
- `replay_document` -> [`tmp_record_workflow/replay.md`](tmp_record_workflow/replay.md)
- `history` -> [`tmp_record_workflow/history.json`](tmp_record_workflow/history.json)
- `planner_turns` -> [`tmp_record_workflow/planner_turns.json`](tmp_record_workflow/planner_turns.json)

适合使用场景：
- 外部系统拿到 bundle 后，先读 [`manifest.json`](tmp_record_workflow/manifest.json)
- 再按索引去读其它文件

---

## 2. [`record.md`](tmp_record_workflow/record.md)

作用：
- 保存 **record 模式下的 DSL 文档**。
- 这个文档尽量保留录制时的原始步骤顺序与重复动作。

当前内容特点：
- 包含 `id/name/version/task`
- 包含 `variables`
- 包含 `steps`

当前样例里只有一个 step：
- `step_1`
- `action: click`
- `index: 1`

适合使用场景：
- 查看“录制阶段到底沉淀了什么步骤”
- 调试录制资产质量
- 对比 replay 版本是否做了压缩

---

## 3. [`replay.md`](tmp_record_workflow/replay.md)

作用：
- 保存 **replay 模式使用的 DSL 文档**。
- 这个文档是更偏执行/回放侧的资产。

和 [`record.md`](tmp_record_workflow/record.md) 的区别：
- `record.md` 偏录制原貌
- `replay.md` 偏回放使用
- 在复杂场景下，[`WorkflowCompiler.compile()`](browser_use/workflow_dsl/compiler.py:15) 会对 replay 模式做相邻重复步骤压缩

当前测试样例中：
- 由于只有一个 step
- 所以 [`record.md`](tmp_record_workflow/record.md) 和 [`replay.md`](tmp_record_workflow/replay.md) 内容相同

适合使用场景：
- 直接拿去做 replay
- 作为“最终可执行 DSL 资产”查看

---

## 4. [`history.json`](tmp_record_workflow/history.json)

作用：
- 保存 **workflow-native 执行历史**。
- 它记录的是“record 主链每一轮发生了什么”，而不是只看最终 DSL。

当前结构：
- `task`: `Record test`
- `mode`: `record`
- `entries`: 每轮一条 [`WorkflowHistoryEntry`](browser_use/workflow_dsl/views.py:109)

当前样例中有两轮：

### 第 1 轮
- `turn_number = 1`
- `planned_steps` 里有 `step_1`
- `executed_steps` 里也有 `step_1`
- `outputs` 里是 action result 的字符串化结果
- `done = false`

这表示：
- 第 1 轮 planner 规划了一个 click
- executor 成功执行了这个 click
- 这一轮不是结束轮

### 第 2 轮
- `turn_number = 2`
- `planned_steps = []`
- `executed_steps = []`
- `outputs = ['Workflow completed']`
- `done = true`

这表示：
- 第 2 轮 planner 直接返回 `is_done=True`
- 没再生成新的步骤
- 本轮只是“完成收尾轮”

适合使用场景：
- 排查 planner/executor/runtime 每轮的行为
- 看清哪些 step 是计划的、哪些是真正执行成功的
- 分析 workflow 录制过程中每一轮的状态变化

---

## 5. [`planner_turns.json`](tmp_record_workflow/planner_turns.json)

作用：
- 保存 **planner 视角的轮次摘要**。
- 这个文件比 [`history.json`](tmp_record_workflow/history.json) 更偏“规划层调试”。

当前内容：
- `step_number = 1`
- `plan` 中包含一个 `click` step
- `result_summary = '- step_1: success'`
- `error = null`

它和 [`history.json`](tmp_record_workflow/history.json) 的区别：
- [`history.json`](tmp_record_workflow/history.json) 更完整，包含执行历史、输出、done 状态
- [`planner_turns.json`](tmp_record_workflow/planner_turns.json) 更聚焦 planner 每轮给了什么计划，以及这轮结果如何

适合使用场景：
- 调试 planner 行为
- 分析某一轮 planner 输出 DSL 是否合理
- 对比 repair loop 前后 planner 的变化

---

## 6. 整个目录的关系

你可以把这个 bundle 理解成 3 层：

### A. 结果 DSL 层
- [`record.md`](tmp_record_workflow/record.md)
- [`replay.md`](tmp_record_workflow/replay.md)

回答的问题是：
- 最终沉淀了什么 workflow DSL？

### B. 历史 / 调试层
- [`history.json`](tmp_record_workflow/history.json)
- [`planner_turns.json`](tmp_record_workflow/planner_turns.json)

回答的问题是：
- 这些 DSL 是怎么一步步生成出来的？
- 每一轮 planner / executor 做了什么？

### C. 索引层
- [`manifest.json`](tmp_record_workflow/manifest.json)

回答的问题是：
- 其它文件分别在哪里？

---

## 7. 如果只想快速看这个 bundle，推荐阅读顺序

推荐顺序：
1. 先看 [`manifest.json`](tmp_record_workflow/manifest.json)
2. 再看 [`record.md`](tmp_record_workflow/record.md) / [`replay.md`](tmp_record_workflow/replay.md)
3. 然后看 [`history.json`](tmp_record_workflow/history.json)
4. 最后看 [`planner_turns.json`](tmp_record_workflow/planner_turns.json)

这样能快速建立认知：
- 最终产物是什么
- 生成过程是什么
- planner 每轮做了什么

---

## 8. 当前这个测试样例为什么文件内容很简单

因为 [`build_record_runtime()`](test-workflow.py:430) 里预设的 planner 输出很简单：
- 第 1 轮只规划一个 `click`
- 第 2 轮直接 `done`

所以这个 bundle 是一个最小样例，用来验证：
- record 主链可跑通
- artifacts 能正确保存
- history/planner_turns/manifest 结构完整

真实复杂任务下：
- [`record.md`](tmp_record_workflow/record.md) / [`replay.md`](tmp_record_workflow/replay.md) 会更长
- [`history.json`](tmp_record_workflow/history.json) 会有更多 turn
- [`planner_turns.json`](tmp_record_workflow/planner_turns.json) 会出现更多 planner 规划记录
