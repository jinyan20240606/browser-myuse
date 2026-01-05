# Browser-Use 架构原理

## 核心架构

Browser-Use 是一个基于 LLM 的浏览器自动化框架，采用分层架构设计，通过 CDP (Chrome DevTools Protocol) 控制浏览器。

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent 层                             │
│  (智能决策、任务规划、状态管理、消息管理)                      │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                      Tools 层                                │
│  (工具注册、动作执行、结果返回)                               │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                    Browser 层                                │
│  (会话管理、事件总线、CDP 客户端、Watchdog 监控)             │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                    Actor 层                                  │
│  (Page 操作、Element 交互、Mouse 控制)                       │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                     DOM 层                                   │
│  (DOM 解析、序列化、可访问性树、元素定位)                     │
└─────────────────────────────────────────────────────────────┘
```

## 各模块职责

### 1. Agent 层 (`browser_use/agent/`)

**核心职责**: 智能决策和任务执行

**关键组件**:
- **Agent** (`service.py`): 主智能体类，负责任务执行循环
  - 接收任务描述
  - 调用 LLM 生成动作
  - 执行动作并更新状态
  - 判断任务是否完成
- **MessageManager**: 消息历史管理
- **SystemPrompt**: 系统提示词生成
- **Judge**: 任务完成度评估

**执行流程**:
```
1. 初始化 Agent (task, llm, browser)
2. 进入主循环:
   - 获取浏览器状态 (DOM + 截图)
   - 构建消息 (系统提示 + 历史 + 当前状态)
   - 调用 LLM 生成动作
   - 执行动作 (通过 Tools)
   - 更新历史和状态
   - 判断是否完成
3. 返回结果
```

### 2. Browser 层 (`browser_use/browser/`)

**核心职责**: 浏览器会话管理和 CDP 通信

**关键组件**:
- **BrowserSession** (`session.py`): 浏览器会话核心
  - 管理 CDP 连接
  - 事件总线 (EventBus)
  - Tab/Target 管理
  - 生命周期管理
- **SessionManager**: 多会话管理
- **Watchdogs**: 监控器集合
  - `CrashWatchdog`: 崩溃检测
  - `PopupWatchdog`: 弹窗处理
  - `DownloadWatchdog`: 下载监控
  - `SecurityWatchdog`: 安全警告处理
- **CloudBrowserClient**: 云浏览器服务集成

**事件驱动架构**:
```python
# 事件示例
BrowserLaunchEvent
NavigationStartedEvent
NavigationCompleteEvent
ClickElementEvent
FileDownloadedEvent
```

### 3. Actor 层 (`browser_use/actor/`)

**核心职责**: 底层浏览器操作接口

**关键组件**:
- **Page** (`page.py`): 页面级操作
  - 导航、刷新
  - 截图
  - JavaScript 执行
  - 元素查询
- **Element** (`element.py`): 元素级操作
  - 点击、输入
  - 属性获取
  - 可见性检查
- **Mouse** (`mouse.py`): 鼠标操作
  - 移动、点击
  - 拖拽

**设计模式**: Actor 模式，封装 CDP 命令为高级操作

### 4. DOM 层 (`browser_use/dom/`)

**核心职责**: DOM 解析和元素定位

**关键组件**:
- **DomService** (`service.py`): DOM 服务
  - 获取 DOM 树
  - 获取可访问性树 (AX Tree)
  - 元素定位
- **DOMTreeSerializer**: DOM 序列化器
  - 将 DOM 转换为 LLM 可理解的格式
  - 过滤无关元素
  - 提取交互元素
- **EnhancedSnapshot**: 增强快照
  - 合并 DOM 和 AX Tree
  - 计算元素位置
  - Paint Order 过滤

**关键算法**:
- Paint Order Filtering: 移除被遮挡的元素
- Clickable Elements Detection: 识别可点击元素
- Element Indexing: 为元素分配索引供 LLM 引用

### 5. LLM 层 (`browser_use/llm/`)

**核心职责**: LLM 抽象和统一接口

**关键组件**:
- **BaseChatModel** (`base.py`): LLM 协议接口
  - `ainvoke()`: 异步调用
  - 结构化输出支持
- **ChatOpenAI**: OpenAI 模型封装
- **ChatAnthropic**: Anthropic 模型封装
- **ChatGoogle**: Google Gemini 模型封装
- **ChatBrowserUse**: Browser-Use 优化模型

**消息序列化**:
- 统一消息格式 (`BaseMessage`)
- 支持文本、图片、工具调用
- 自动处理不同提供商的差异

### 6. Tools 层 (`browser_use/tools/`)

**核心职责**: 工具注册和执行

**内置工具**:
- `search`: 搜索
- `navigate`: 导航
- `click`: 点击元素
- `input`: 输入文本
- `scroll`: 滚动
- `extract`: 提取内容
- `done`: 完成任务

**自定义工具**:
```python
from browser_use import Tools

tools = Tools()

@tools.action('自定义工具描述')
def my_tool(param: str) -> ActionResult:
    # 实现逻辑
    return ActionResult(extracted_content='结果')
```

**工具注册表** (`Registry`):
- 动态注册工具
- 生成工具描述
- 参数验证

### 7. Cloud 层 (`browser_use/browser/cloud/`)

**核心职责**: 云浏览器服务

**功能**:
- 自动创建云浏览器实例
- 代理支持 (绕过验证码)
- 认证同步
- 远程流式查看

**使用方式**:
```python
browser = Browser(use_cloud=True)
```

## API入手分析


### 1. 入口点深度解析 (`Agent` 类)

当你在代码中运行 `agent.run()` 时，项目的执行引擎正式启动。

#### **初始化阶段 (`Agent.__init__`)**
- **LLM 设置**: 默认推荐 `ChatBrowserUse`，也会根据模型名称（如 Claude Sonnet）自动优化截图尺寸。
- **BrowserSession 绑定**: 如果未提供，会自动创建一个基于 `cdp-use` 的 `BrowserSession`。
- **MessageManager 初始化**: 这是 Agent 的"大脑存储"，它加载 `SystemPrompt`（系统提示词），包含 LLM 需要遵循的所有规则和可用的 `Tools` 列表。
- **ActionModels 设置**: 自动根据 `Tools` 注册表生成 Pydantic 模型，用于约束 LLM 的结构化输出。

---

### 2. 核心执行循环 (`Agent.run`)

`run` 方法是整个任务的生命周期管理器：

1. **信号处理**: 注册 `SignalHandler`，捕获 `Ctrl+C` 以实现优雅停机或暂停。
2. **事件发布**: 通过 `EventBus` 发布 `CreateAgentTaskEvent`，允许外部监控任务状态。
3. **主步进循环**:
   - 调用 `_execute_step()`，这实际上是一个 `asyncio.wait_for` 包装器，受 `step_timeout` 限制。
   - 循环执行直到满足结束条件（LLM 发出 `done` 动作、达到 `max_steps` 或发生不可恢复错误）。

---

### 3. 单步执行深度解析 (`Agent.step`)

这是项目最核心的代码位置，定义了 AI 如何与浏览器交互：

#### **第一阶段：准备上下文 (`_prepare_context`)**
- **状态快照**: 调用 `browser_session.get_browser_state_summary()`。
  - **DOM 转换**: `DomService` 将复杂的 HTML 转化为精简的 `EnhancedDOMTreeNode` 树。
  - **截图**: 即使不开启视觉模式，也会捕获截图用于调试。
- **动作空间更新**: 根据当前 URL 动态更新可用的工具（某些工具可能只在特定域名可用）。
- **消息构建**: `MessageManager` 将当前 DOM 状态、截图、历史记录和错误信息拼接成 LLM 的输入。

#### **第二阶段：获取决策 (`_get_next_action`)**
- **LLM 调用**: 调用 `self.llm.ainvoke`。
- **结构化解析**: 强制 LLM 返回 `AgentOutput` 格式，包含 `thinking`（思考过程）和 `action`（动作列表）。
- **Fallback 机制**: 如果主 LLM 发生 429（限流）或 5xx 错误，会自动切换到备份 LLM（如果已配置）。

#### **第三阶段：动作执行 (`_execute_actions` -> `multi_act`)**
- **工具分发**: 遍历动作列表，通过 `Tools` 注册表找到对应的处理函数。
- **底层操作**:
  - `click` -> `Element.click()` -> `CDP.Input.dispatchMouseEvent`。
  - `input` -> `Element.type()` -> `CDP.Input.dispatchKeyEvent`。
- **结果捕获**: 每个动作返回 `ActionResult`，包含执行结果或错误。

#### **第四阶段：收尾与评估 (`_finalize`)**
- **历史归档**: 将动作、结果和新的状态存入 `AgentHistory`。
- **Judge 评估**: 如果开启了 `use_judge`，在 `done` 动作发出后，会另起一个 LLM 调用来客观评估任务是否真的按要求完成了。

---

### 4. 关键代码位置备忘 (Quick Reference)

逻辑环节 | 文件路径 | 关键函数/类 |
:--- | :--- | :--- |
**任务循环入口** | `browser_use/agent/service.py` | `Agent.run()` |
**AI 决策逻辑** | `browser_use/agent/service.py` | `Agent.step()` |
**DOM 简化算法** | `browser_use/dom/service.py` | `DomService.get_dom_tree()` |
**元素点击/输入** | `browser_use/actor/element.py` | `Element.click()`, `Element.type()` |
**提示词生成** | `browser_use/agent/prompts.py` | `SystemPrompt.get_system_message()` |
**工具定义** | `browser_use/tools/service.py` | `@tools.action` 装饰的各种函数 |
**浏览器驱动** | `browser_use/browser/session.py` | `BrowserSession` (CDP 封装) |

## 数据流转

### 1. 初始化流程
```
用户代码
  ↓
Agent(task, llm, browser)
  ↓
BrowserSession (创建 CDP 连接)
  ↓
启动浏览器 (本地或云)
  ↓
初始化 Watchdogs
```

### 2. 执行循环流程
```
Agent.run()
  ↓
获取浏览器状态
  ├─ DomService.get_dom_tree()
  ├─ Page.screenshot()
  └─ 构建状态摘要
  ↓
构建消息
  ├─ SystemPrompt
  ├─ 历史消息
  └─ 当前状态 (DOM + 截图)
  ↓
LLM.ainvoke(messages)
  ↓
解析动作 (ActionModel)
  ↓
Tools.execute(action)
  ├─ Actor 操作 (Page/Element/Mouse)
  └─ 返回 ActionResult
  ↓
更新历史
  ↓
判断是否完成
  ├─ Judge 评估
  └─ 继续或结束
```

### 3. 事件流转
```
BrowserSession (事件发布者)
  ↓
EventBus (事件总线)
  ↓
Watchdogs (事件订阅者)
  ├─ 处理弹窗
  ├─ 处理下载
  └─ 处理崩溃
```

## 关键设计模式

### 1. 事件驱动
- 使用 `bubus.EventBus` 实现松耦合
- Watchdogs 监听并处理浏览器事件

### 2. 协议抽象
- `BaseChatModel` 统一 LLM 接口
- 支持多种 LLM 提供商

### 3. 懒加载
- `__getattr__` 实现模块懒加载
- 减少启动时间

### 4. Actor 模式
- 封装 CDP 命令为高级操作
- 提供类型安全的接口

### 5. 注册表模式
- Tools 动态注册
- 支持自定义工具扩展

## 扩展点

### 1. 自定义 LLM
```python
from browser_use.llm.base import BaseChatModel

class MyLLM(BaseChatModel):
    async def ainvoke(self, messages, output_format=None):
        # 实现调用逻辑
        pass
```

### 2. 自定义工具
```python
@tools.action('工具描述')
def my_tool(param: str) -> ActionResult:
    return ActionResult(extracted_content='结果')
```

### 3. 自定义 Watchdog
```python
class MyWatchdog:
    async def handle_event(self, event):
        # 处理逻辑
        pass
```

## 性能优化

### 1. DOM 优化
- Paint Order Filtering: 减少无关元素
- 可访问性树: 更准确的元素语义
- 增量更新: 只更新变化部分

### 2. LLM 优化
- 结构化输出: 减少 token 消耗
- 消息压缩: 限制历史长度
- Flash Mode: 跳过思考步骤

### 3. 浏览器优化
- CDP 连接复用
- 异步操作
- 并行请求

## 总结

Browser-Use 的核心思想是:
1. **分层架构**: 清晰的职责划分
2. **事件驱动**: 松耦合的组件通信
3. **协议抽象**: 统一的接口设计
4. **可扩展性**: 支持自定义 LLM、工具、Watchdog

通过这种架构，Browser-Use 实现了:
- 灵活的 LLM 集成
- 可靠的浏览器控制
- 高效的 DOM 处理
- 强大的扩展能力