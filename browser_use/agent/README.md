# Browser-Use Agent 模块

- [Browser-Use Agent 模块](#browser-use-agent-模块)
  - [目录结构](#目录结构)
  - [service.py 关键入口API入手分析](#servicepy-关键入口api入手分析)
    - [使用示例](#使用示例)
    - [1. 入口点深度解析 (`Agent` 类)](#1-入口点深度解析-agent-类)
      - [**初始化阶段 (`Agent.__init__`)**](#初始化阶段-agent__init__)
    - [2. 核心执行循环 (`Agent.run`)](#2-核心执行循环-agentrun)
    - [3. 单步执行深度解析 (`Agent.step`) --- 最核心的方法](#3-单步执行深度解析-agentstep-----最核心的方法)
      - [**第一阶段：准备上下文 (`_prepare_context`)**](#第一阶段准备上下文-_prepare_context)
      - [**第二阶段：获取决策 (`_get_next_action`)**](#第二阶段获取决策-_get_next_action)
      - [**第三阶段：动作执行器 (`_execute_actions` -\> `multi_act`)** ----- ***【就是所谓的执行器】***](#第三阶段动作执行器-_execute_actions---multi_act-------就是所谓的执行器)
      - [**第四阶段：动作执行后的后处理 (`self._post_process`)**](#第四阶段动作执行后的后处理-self_post_process)
    - [4. 关键代码位置备忘 (Quick Reference)](#4-关键代码位置备忘-quick-reference)
  - [文件详解](#文件详解)
    - [`service.py`](#servicepy)
    - [`views.py`](#viewspy)
    - [`prompts.py`](#promptspy)
    - [`judge.py`](#judgepy)
    - [`variable_detector.py`](#variable_detectorpy)
    - [`cloud_events.py`](#cloud_eventspy)
    - [`gif.py`](#gifpy)
    - [Message Manager (消息管理器)](#message-manager-消息管理器)
      - [`message_manager/service.py`](#message_managerservicepy)
      - [`message_manager/views.py`](#message_managerviewspy)
      - [`message_manager/utils.py`](#message_managerutilspy)
    - [System Prompts (系统提示词模板)](#system-prompts-系统提示词模板)
  - [架构说明](#架构说明)
  - [python代码积累](#python代码积累)
    - [异步事件（asyncio.Event）的外部暂停控制机制](#异步事件asyncioevent的外部暂停控制机制)


Browser-Use Agent 是整个库的核心组件，负责协调浏览器操作、LLM 交互和任务执行。

## 目录结构

```
agent/
├── service.py                 # Agent 核心实现
├── views.py                   # 数据模型和视图类
├── prompts.py                 # 系统提示词生成
├── judge.py                   # 任务评估和判断器
├── variable_detector.py       # 变量检测器
├── cloud_events.py            # 云事件定义
├── gif.py                     # GIF 生成工具
├── message_manager/           # 消息管理器
│   ├── service.py             # 消息管理服务
│   ├── views.py               # 消息相关数据模型
│   └── utils.py               # 消息工具函数
├── system_prompts/            # 系统提示词模板
│   ├── system_prompt.md       # 基础系统提示词
│   ├── system_prompt_browser_use.md       # BrowserUse 系统提示词
│   ├── system_prompt_browser_use_flash.md # BrowserUse 快速模式提示词
│   ├── system_prompt_flash.md             # 快速模式提示词
│   ├── system_prompt_no_thinking.md       # 无思考模式提示词
│   └── system_prompt_browser_use_no_thinking.md # BrowserUse 无思考模式提示词
└── __init__.py                # 模块初始化文件
```

## service.py 关键入口API入手分析

1. python的类中，下划线开头的都是约定的私有方法，非带下划线的都是公有方法

### 使用示例

```python
from browser_use import Agent, ChatBrowserUse

# 创建 Agent 实例
agent = Agent(
    task="搜索最新的 AI 新闻并总结前 3 条",
    llm=ChatBrowserUse()
)

# 运行 Agent
history = await agent.run()

# 获取结果
result = history.final_result()
```

### 1. 入口点深度解析 (`Agent` 类)

当你在代码中运行 `agent.run()` 时，项目的执行引擎正式启动。

#### **初始化阶段 (`Agent.__init__`)** 
（`def __init__ 方法`）
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

run 方法的详细逻辑如下：
1. 初始化阶段
   1. 完成智能体启动前的准备工作，包括：
   2. 信号处理（防强制退出观测不到数据）
   3. 标记会话初始化状态（避免重复创建）；
   4. 分发事件（供外部模块监听会话 / 任务创建）；
   5. 启动浏览器、加载技能、执行前置动作（如登录、导航到初始页面）。
2. 核心执行while步骤循环（步骤控制-self.state.n_steps）
   1. 循环执行步骤，直到「步数超限」「任务完成」「失败超限」「外部终止」；
   2. 每步先检查暂停状态（依赖_external_pause_event 异步事件）；
   3. `_execute_step()` 是真正执行单步动作（调用 LLM、操作浏览器）的核心方法；----- 执行单个步骤（带超时控制），任务最终完成返回True
      1. 内部调用 `self.step(step_info)`
      2. 核心亮点：超时控制：
         1. asyncio.wait_for() 是异步场景的超时保护神器，作用是：如果 self.step() 执行时间超过 step_timeout，会主动抛出 TimeoutError，避免单个步骤卡死导致整个智能体挂起；
      3. 容错处理：
         1. 步骤超时后不直接终止整个任务，而是：记录超时日志（演示模式也会输出错误提示）；
         2. 更新智能体状态（连续失败次数 + 1，记录错误结果）；
         3. 继续执行后续逻辑（而非抛异常），保证主循环能继续判断是否终止
      4. finally块执行逻辑：`self._finalize(browser_state_summary)`单个步骤完成后的最终收尾逻辑，核心负责整合步骤全量信息（执行时间、浏览器状态、动作结果等）、持久化数据、发送事件并推进步骤计数：
          1. 计算步骤执行时间
          2. 创建历史记录项
          3. 记录步骤完成摘要
          4. 保存文件系统状态
          5. 发送事件
          6. 增加步骤计数器-----更新while步骤循环的变量
   4. else 分支是 Python 特有的 “循环正常结束（未触发 break）” 逻辑，处理步数超限场景
3. 异常处理（保证鲁棒性）
   1. 单独捕获 KeyboardInterrupt（用户 Ctrl+C），保证优雅返回执行历史；
   2. 其他异常记录详细堆栈（exc_info=True），便于生产环境排查问题。
4. 最终清理（finally 块，必执行）
   1. 保证智能体无论正常结束还是异常终止，都能：
   2. 完成遥测数据记录（关键生产指标不丢失）；
   3. 分发最终事件（外部系统感知任务状态）；
   4. 清理资源（浏览器进程、事件总线、技能服务等，避免内存泄漏）；
   5. 生成可视化结果（GIF），便于调试 / 用户查看。

---

### 3. 单步执行深度解析 (`Agent.step`) --- 最核心的方法

- `step` 是智能体完成 “思考 - 行动” 闭环的核心方法
- 具体执行操作前开始请求大模型的核心方法入口，完成智能体单步执行的全流程（上下文准备→LLM 决策→动作执行→后处理），添加监控 / 计时能力，统一处理异常，保证无论是否出错都能完成清理和记录


#### **第一阶段：准备上下文 (`_prepare_context`)**

> 目的：获取赋值browser_state_summary；浏览器状态摘要

```js
// browser_state_summary 的部分结构如下
page_info =
pagination_buttons =[]
pending_network_requests =
screenshot = None
tabs = [TabInfo(url='about:blank', title='', target_id='01221C861558347092885F5FC3E4441C',parent_target_id=None)]
title ='Empty Tab'
url ='about:blank'
```
- **状态快照**: 调用 `browser_session.get_browser_state_summary()`。
  - **DOM 转换**: `DomService` 将复杂的 HTML 转化为精简的 `EnhancedDOMTreeNode` 树。
  - **截图**: 即使不开启视觉模式，也会捕获截图用于调试。
- **动作模型更新**: 根据当前 URL 动态更新可用的工具（某些工具可能只在特定域名可用）。
- **消息构建**: `MessageManager` 将最终的上下文（当前 DOM 状态、截图、历史记录和错误信息）拼接成 LLM 的输入存到消息管理器中。
  - 通过`self._message_manager.create_state_messages`

#### **第二阶段：获取决策 (`_get_next_action`)**
- 从消息管理器中获取组装好的输入消息--（_prepare_context阶段存到消息管理器中的最终上下文），包含：
  - 系统提示（告知 LLM 任务目标、动作规则）；
  - 历史对话（之前的思考和动作记录）；
  - 当前浏览器状态摘要（browser_state_summary，比如当前激活的页面 URL、浏览器打开了哪些标签，哪些url、已执行动作和缓存的DOM选择器映射）；
  - 用户指令（原始任务要求）；
- **LLM 调用**: `_get_model_output_with_retry`方法：把拿到的消息传入大模型
  - 内部实际调用 `await self.llm.ainvoke(input_messages, **kwargs)`。
  - 返回model_output，更新到把LLM输出存入智能体状态，供后续执行动作使用
    - 模型输出，包含动作action属性和状态信息
- **第一次校验：获取LLM输出后，检查是否被暂停/终止**
		await self._check_stop_or_pause()
- **处理LLM调用后的回调（如记录token消耗、更新对话历史）+ 保存对话**
		await self._handle_post_llm_processing(browser_state_summary, input_messages)
- **第二次校验：存入历史前再次检查暂停/终止（防止处理回调过程中触发停止）**
		await self._check_stop_or_pause()

#### **第三阶段：动作执行器 (`_execute_actions` -> `multi_act`)** ----- ***【就是所谓的执行器】***
1. **动作列表获取**: 从 LLM 输出中获取动作列表`self.state.last_model_output.action`。
2. **获取预处理浏览器缓存的DOM选择器映射**:（用于快速定位页面元素，提升动作执行效率）：加载缓存的 DOM 选择器映射（页面元素的哈希 / 选择器对应关系），减少动作执行时重新查询 DOM 的耗时，提升执行效率；
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
3. **for循环遍历执行每个动作（核心循环）**
   1. 作用：按顺序执行一系列浏览器自动化动作（如 click/type/done 等），并包含了 4 条核心执行规则、异常处理、日志记录和耗时统计等完善的工程化逻辑
   2. 主要逻辑：
      1. 遍历一个动作列表（actions），按顺序异步执行每个动作；
         1. 核心：调用工具执行单个动作
         2. **await self.tools.act()**: 调用工具执行单个动作
         3. **底层操作**:
            1. - `click` -> `Element.click()` -> `CDP.Input.dispatchMouseEvent`。
            2. - `input` -> `Element.type()` -> `CDP.Input.dispatchKeyEvent`。
         4. **结果捕获**: 每个动作返回 `ActionResult`，包含执行结果或错误。
      2. 每次遍历时校验检查
         1. 内置 4 条执行规则（done 动作限制、动作间延迟、暂停 / 停止检查、终止条件）；
         2. 记录每个动作的执行日志、耗时、结果（成功 / 失败 / 完成）；
         3. 捕获执行异常并友好处理，最终返回所有动作的执行结果。
---


#### **第四阶段：动作执行后的后处理 (`self._post_process`)**
1. 检查新下载的文件
2. 更新连续失败计数
3. 记录最终结果（如果任务完成）
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


## 文件详解

### [`service.py`](service.py)
这是 Agent 模块的核心实现文件，包含了 `Agent` 类的完整定义。

**主要功能：**
- Agent 初始化和配置
- 任务执行主循环 (`run` 方法)
- 浏览器会话管理
- LLM 交互协调
- 工具注册和调用
- 错误处理和重试机制
- 遥测和日志记录

**关键类和方法：**
- `Agent` 类：核心 Agent 实现
  - `__init__`：初始化 Agent 实例
  - `run`：执行任务的主要异步方法
  - `_execute_step`：执行单个步骤

### [`views.py`](views.py)
定义了 Agent 相关的数据模型和视图类，使用 Pydantic 进行数据验证。

**主要类：**
- `AgentSettings`：Agent 配置设置
- `AgentState`：Agent 状态
- `AgentHistory`：Agent 历史记录
- `AgentHistoryList`：Agent 历史记录列表
- `AgentOutput`：Agent 输出
- `ActionResult`：动作执行结果
- `AgentError`：Agent 错误信息

### [`prompts.py`](prompts.py)
负责生成系统提示词，根据不同的配置和模式生成相应的提示词内容。

**主要类：**
- `SystemPrompt`：系统提示词生成器
  - `build_system_message`：构建系统消息
  - `build_example_messages`：构建示例消息

### [`judge.py`](judge.py)
实现任务评估和判断器功能，用于评估 Agent 执行步骤的成功与否。

**主要功能：**
- 评估 Agent 执行步骤
- 判断任务是否完成
- 提供反馈和改进建议

### [`variable_detector.py`](variable_detector.py)
实现变量检测功能，用于从文本中提取和识别变量。

**主要功能：**
- 检测文本中的变量
- 变量类型识别
- 变量值提取

### [`cloud_events.py`](cloud_events.py)
定义云事件相关的数据模型，用于遥测和监控。

**主要类：**
- 各种事件类，如 `CreateAgentSessionEvent`、`CreateAgentTaskEvent` 等

### [`gif.py`](gif.py)
实现 GIF 生成功能，用于创建 Agent 执行过程的动画。

**主要功能：**
- 从历史记录创建 GIF
- 图片处理和动画生成

### Message Manager (消息管理器)

#### [`message_manager/service.py`](message_manager/service.py)
实现消息管理服务，负责管理 Agent 与 LLM 之间的消息交互。

**主要类：**
- `MessageManager`：消息管理器
  - 管理消息历史
  - 构建消息上下文
  - 处理消息截断和优化

#### [`message_manager/views.py`](message_manager/views.py)
定义消息管理相关的数据模型。

#### [`message_manager/utils.py`](message_manager/utils.py)
实现消息管理相关的工具函数。

### System Prompts (系统提示词模板)

系统提示词模板目录包含各种预定义的系统提示词，用于不同模式和场景：

- `system_prompt.md`：基础系统提示词
- `system_prompt_browser_use.md`：BrowserUse 系统提示词
- `system_prompt_browser_use_flash.md`：BrowserUse 快速模式提示词
- `system_prompt_flash.md`：快速模式提示词
- `system_prompt_no_thinking.md`：无思考模式提示词
- `system_prompt_browser_use_no_thinking.md`：BrowserUse 无思考模式提示词

## 架构说明

Agent 模块采用模块化设计，各组件职责明确：

1. **核心执行**：由 `service.py` 中的 `Agent` 类负责
2. **数据模型**：由 `views.py` 定义
3. **提示词管理**：由 `prompts.py` 和 `system_prompts/` 目录负责
4. **消息管理**：由 `message_manager/` 目录负责
5. **评估和判断**：由 `judge.py` 负责
6. **变量检测**：由 `variable_detector.py` 负责
7. **云事件**：由 `cloud_events.py` 定义
8. **GIF 生成**：由 `gif.py` 负责

这种设计使得各组件可以独立开发和测试，同时又能很好地协同工作。


## python代码积累

### 异步事件（asyncio.Event）的外部暂停控制机制

```python
# 初始化基于异步事件（asyncio.Event）的外部暂停控制机制，并且特意将这个控制事件放在 AgentState 之外，
# 用于实现对智能体执行流程的 “无侵入式暂停 / 恢复” 控制 —— 比如外部系统可通过触发这个事件，让智能体暂停执行或恢复执行，且不影响状态序列化
		self._external_pause_event = asyncio.Event()
		self._external_pause_event.set()

上面是Agent类的初始化代码，用于初始化基于异步事件（asyncio.Event）的外部暂停控制机制，并且特意将这个控制事件放在 AgentState 之外，下面是异步事件的使用方式伪代码
===============

async def run(self):
    """智能体主执行循环（异步）"""
    self.logger.info("🚀 智能体启动，默认可执行（暂停事件已就绪）")
    
    while not self.state.is_done:
        # 关键：每次执行步骤前，先等待暂停事件（若事件未就绪则阻塞）
        await self._external_pause_event.wait()
        
        # 执行单步动作（如调用LLM、操作浏览器）
        await self._execute_single_step()
        
        # 检查是否达到停止条件（如最大步骤、任务完成）
        if self._should_stop():
            break

# 外部控制暂停/恢复的接口（供外部系统调用）
async def pause(self):
    """暂停智能体执行"""
    self._external_pause_event.clear()  # 清除事件，触发阻塞
    self.logger.info("⏸️ 智能体已暂停执行")

async def resume(self):
    """恢复智能体执行"""
    self._external_pause_event.set()  # 设置事件，解除阻塞
    self.logger.info("▶️ 智能体已恢复执行")
```