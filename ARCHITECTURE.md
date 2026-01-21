# Browser-Use 架构原理

## 前置概念

1. 可访问性树（AX Tree）：浏览器基于 DOM 树生成的语义化子集树状结构，仅保留有语义的元素，过滤装饰性 / 隐藏元素，专为辅助技术设计
   1. AX Tree 节点包含 3 类核心信息 —— 角色（role，如 button/link）、名称（name，可读文本）、状态（states，如 disabled/hidden）；
   2. 获取完整 AX Tree —— 借助浏览器 CDP 协议（推荐），原生js获取不到完整的，只能单个节点获取属性进行手动拼接
   3. 优势：
      1. 精准定位元素：传统 DOM 定位可能依赖 id/class（易变），而 AX Tree 按 “语义” 定位（比如 “找到角色为 button、名称为‘提交’且状态为 disabled 的元素”），更适配 AI/Agent 自动操控浏览器的场景；
      2. 理解页面意图：Agent 不仅能 “看到” DOM 标签，还能 “理解” 元素的实际功能（比如区分 “装饰性 div” 和 “真正的按钮”），提升任务执行的准确性（比如你的 Agent 要点击 “提交” 按钮，不会误点装饰元素）。

2. DOM 树：网页的完整结构树，包含所有标签、属性、文本
   1. 包含所有元素，无语义过滤
3. 无障碍属性（aria-*）：手动为元素补充的可访问性属性（如 aria-label、aria-disabled）

### 执行器相关策略
> 动作执行器 (`_execute_actions` -> `multi_act中的概念
1. browser_state_summary字段含义：浏览器状态摘要对象（包含 DOM 缓存、窗口状态、Cookie 等），为None时跳过历史记录创建
1. 预处理浏览器缓存的DOM选择器映射
   1. 作用：缓存的DOM选择器映射本质是一个 **“元素唯一标识 ↔ 定位该元素的选择器”** 的键值对字典，核心是把 “找元素的结果” 存起来，下次不用再重新找
   2. browser_session._cached_browser_state_summary：当前浏览器状态摘要（browser_state_summary，比如页面 URL、关键元素、已执行动作和缓存的DOM选择器映射）；
      1. dom_state.selector_map：缓存的DOM选择器映射
      2. cached_element_hashes：缓存的DOM选择器映射的元素hash值集合
         1. hash的生成方式：元素：`<button id="login" class="btn">登录</button>` ，组合字符串：button-id=login-class=btn-text=登录，哈希：md5(组合字符串) == 8f9b9c8a7d6e5f4g3h2j1k0l
```python
   dom_state.selector_map = {
    "goods_title_10086": "#J_ItemTitle",
    "goods_price_10086": ".price.J_price",
    "buy_button_10086": "#J_LinkBuy",
    "add_cart_button_10086": "#J_LinkBasket"
} 
# 对应的cached_element_hashes就是这些标识的集合：
cached_element_hashes = {"goods_title_10086", "goods_price_10086", ...}
```

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