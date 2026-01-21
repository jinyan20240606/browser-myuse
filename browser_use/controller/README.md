# Controller 模块

该模块负责动作（Actions）的注册、管理和执行。它是 LLM 决策与浏览器执行之间的桥梁。

## 目录结构

```
controller/
├── __init__.py         # 导出 Controller 类
└── service.py          # (通常在此处实现动作注册逻辑，有时合并在 tools 中)
```

*(注意：在最新版本中，Controller 的主要逻辑可能已经迁移并合并到 `browser_use/tools` 模块，`Controller` 类常作为 `Tools` 的别名存在。)*

## 核心概念

### Action Registry (动作注册表)
Controller 维护一个注册表，记录了所有可供 Agent 使用的工具函数（如 `click`, `type`, `scroll`）。

### Action Execution (动作执行)
当 LLM 输出一个工具调用请求（如 `{"name": "click", "parameters": {"index": 1}}`）时，Controller 负责：
1. 解析请求。
2. 查找对应的 Python 函数。
3. 执行函数并传入参数。
4. 返回执行结果给 Agent。

## 调用链路

1. **注册：** 在 `Agent` 初始化时，会加载默认的动作集（来自 `browser_use.tools.service`）。用户也可以注册自定义动作。
2. **决策：** LLM 根据 `SystemPrompt` 中提供的工具描述，决定调用哪个工具。
3. **调用：** `Agent` 接收到 LLM 的响应后，通过 `Controller` (或 `Tools` 服务) 执行具体的动作。
4. **反馈：** 动作执行的结果（成功、失败或返回值）被记录并作为下一轮对话的输入。