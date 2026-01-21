# Browser-Use 核心架构与逻辑关系

Browser-Use 是一个使 LLM 能够与浏览器交互的 Python 库。它采用高度模块化的设计，将任务执行、环境感官、执行机构和语言模型进行解耦。

## 核心架构概览

Browser-Use 的核心流程如下：
1. **感知（Perception）**：通过浏览器获取页面的视觉和 DOM 结构。
2. **决策（Reasoning）**：将感知的状态传递给 LLM，由 LLM 决定下一步动作。
3. **行动（Action）**：执行 LLM 决定的动作（如点击、输入、导航）。
4. **循环（Loop）**：重复上述过程，直到任务完成。

## 模块调用链路逻辑

### 1. 初始化阶段 (Initialization)
- 用户实例化 `Browser` (位于 `browser/`) 和 `BaseChatModel` (位于 `llm/`)。
- 用户实例化 `Agent` (位于 `agent/`)，将浏览器、语言模型以及可选的 `Tools` 传入。
- `Agent` 内部初始化 `MessageManager` (管理对话上下文) 和 `FileSystem` (管理文件访问)。

### 2. 执行循环 (Main Loop - `Agent.run()`)
- **获取状态**：`Agent` 调用 `BrowserSession.get_state()`。
    - `BrowserSession` 内部调用 `DomService` (位于 `dom/`) 提取页面元素。
    - `DomService` 使用 `serializer` 处理 DOM 并在页面上生成 `index` 高亮。
    - 结果被包装成 `BrowserStateSummary` (包含截图和 Markdown 格式的 DOM)。
- **决策决策**：`Agent` 将状态通过 `MessageManager` 构建成提示词，发送给 `LLM`。
    - `LLM` 模块将通用请求序列化为特定厂商（OpenAI/Claude 等）的 API 请求。
    - `LLM` 返回包含 "思考（Thinking）" 和 "工具调用（Tool Calls）" 的响应。
- **动作执行**：`Agent` 解析 LLM 响应，通过 `Controller/Tools` 执行动作。
    - 工具函数（如 `click`）通过索引在页面中找到对应元素。
    - 调用 `BrowserSession` 操作底层 Playwright 实例执行物理动作。
- **反馈记录**：动作执行的结果返回给 `Agent`，并存储在 `AgentHistory` 中。

### 3. 完成阶段 (Completion)
- 当 LLM 调用 `done` 工具或达到最大步数时，循环终止。
- `Agent` 汇总历史记录并返回 `AgentHistoryList`。

## 目录指南

| 模块 | 职责 | 核心类/文件 |
| :--- | :--- | :--- |
| [**`agent/`**](agent/README.md) | **大脑/协调者**。控制整个任务生命周期和逻辑循环。 | `Agent`, `service.py` |
| [**`browser/`**](browser/README.md) | **环境/窗口**。管理浏览器进程、页面和视觉状态。 | `BrowserSession`, `session.py` |
| [**`dom/`**](dom/README.md) | **眼睛/感知**。解析网页结构，将其转化为 LLM 可读格式。 | `DomService`, `service.py` |
| [**`llm/`**](llm/README.md) | **智力**。适配不同的语言模型提供商。 | `BaseChatModel`, `models.py` |
| [**`tools/`**](tools/README.md) | **手/执行**。定义和执行所有原子化的浏览器操作。 | `Tools`, `service.py` |
| [**`controller/`**](controller/README.md) | **动作映射**。将 LLM 决策映射到 Python 函数。 | `Controller` |
| [**`filesystem/`**](filesystem/README.md) | **记忆/存储**。管理 Agent 对本地文件的读写。 | `FileSystem` |
| **`telemetry/`** | **监控**。匿名收集使用数据用于改进。 | `telemetry.py` |
| **`utils/`** | **辅助工具**。通用的实用函数、日志配置等。 | `utils.py`, `logging_config.py` |

## 调用关系图 (简化)

```text
    用户任务 (Task)
        ↓
    [ Agent ] ←-------→ [ LLM (GPT/Claude) ]
        |                   ↑ (提示词/状态)
        ↓                   |
    [ Controller ]     [ DomService ]
        |                   ↑ (解析)
        ↓                   |
    [ Tools ] ---------→ [ Browser (Playwright) ]
        |                   |
        └-------------------┘
             (执行物理操作)
```

## 关键流程节点
- **`Agent._run_step()`**: 这是最核心的单步执行函数。
- **`DomService.get_clickable_elements()`**: 决定了 Agent "能看到什么"。
- **`BrowserSession.take_screenshot()`**: 提供视觉反馈。
- **`MessageManager.get_messages()`**: 决定了 LLM 接收到什么上下文。
