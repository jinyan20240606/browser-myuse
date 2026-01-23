# Browser-Use DSL 协议设计分析报告（方案 B）

## 一、设计目标

**核心目标**：设计一套平台无关的 DSL 协议，能从 Agent 执行历史中提取，直接丢给执行器执行，**完全跳过 LLM 推理**，严格保证执行成功。

---

## 二、现有架构分析

### 2.1 动作执行链路

```
AgentOutput.action (ActionModel列表)
    ↓
Registry.execute_action(action_name, params, ...)
    ↓
RegisteredAction.function(params, browser_session, ...)
    ↓
EventBus.dispatch(XXXEvent)
    ↓
BrowserSession 执行具体操作
```

**关键发现**：[`Registry.execute_action()`](../browser_use/tools/registry/service.py:327) 是执行入口，只需要 `action_name` + `params` 字典即可执行。

### 2.2 现有 Action 参数模型（来自 [`browser_use/tools/views.py`](../browser_use/tools/views.py:1)）

| 动作 | 参数模型 | 关键字段 |
|------|----------|----------|
| `navigate` | [`NavigateAction`](../browser_use/tools/views.py:28) | `url: str`, `new_tab: bool` |
| `search` | [`SearchAction`](../browser_use/tools/views.py:17) | `query: str`, `engine: str` |
| `click` | [`ClickElementAction`](../browser_use/tools/views.py:37) | `index: int`, `coordinate_x/y: int` |
| `input` | [`InputTextAction`](../browser_use/tools/views.py:51) | `index: int`, `text: str`, `clear: bool` |
| `scroll` | [`ScrollAction`](../browser_use/tools/views.py:79) | `down: bool`, `pages: float`, `index: int` |
| `send_keys` | [`SendKeysAction`](../browser_use/tools/views.py:85) | `keys: str` |
| `switch` | [`SwitchTabAction`](../browser_use/tools/views.py:71) | `tab_id: str` |
| `close` | [`CloseTabAction`](../browser_use/tools/views.py:75) | `tab_id: str` |
| `extract` | [`ExtractAction`](../browser_use/tools/views.py:7) | `query: str` |
| `wait` | 内置 | `seconds: int` |
| `go_back` | [`NoParamsAction`](../browser_use/tools/views.py:94) | 无参数 |
| `done` | [`DoneAction`](../browser_use/tools/views.py:57) | `text: str`, `success: bool` |

### 2.3 元素定位问题

**核心挑战**：当前使用 `index` 定位元素，但页面变化后索引失效。

分析 [`EnhancedDOMTreeNode`](../browser_use/dom/views.py:365) 发现可用的稳定定位信息：

```python
# 可用于稳定选择器的属性
node.xpath  # 生成的 XPath 路径
node.attributes  # 包含 id, class, name, data-testid 等
node.backend_node_id  # CDP 后端节点 ID
node.tag_name  # 标签名
```

**[`MatchLevel`](../browser_use/dom/views.py:161) 枚举已定义**：
```python
class MatchLevel(Enum):
    EXACT = 1   # 完整 hash（当前行为）
    STABLE = 2  # 过滤动态 class 的 hash
    XPATH = 3   # XPath 字符串比较
```

---

## 三、DSL 协议设计

### 3.1 协议格式（JSON Schema）

```json
{
  "$schema": "https://browser-use.com/dsl/v1.0/schema.json",
  "version": "1.0",
  "metadata": {
    "created_at": "2025-01-23T12:00:00Z",
    "source_task": "原始任务描述",
    "agent_id": "uuid",
    "total_steps": 10,
    "success": true
  },
  "variables": {
    "USERNAME": "${env:USERNAME}",
    "PASSWORD": "${env:PASSWORD}",
    "SEARCH_QUERY": "browser automation"
  },
  "steps": [
    {
      "step_id": 1,
      "action": "navigate",
      "params": {
        "url": "https://example.com",
        "new_tab": false
      },
      "wait_after": 1000,
      "retry": 3
    },
    {
      "step_id": 2,
      "action": "click",
      "params": {
        "index": 5
      },
      "selectors": {
        "primary": {
          "type": "xpath",
          "value": "//button[@id='login']"
        },
        "fallback": [
          {"type": "css", "value": "#login-button"},
          {"type": "text", "value": "Login"},
          {"type": "attributes", "value": {"data-testid": "login-btn"}}
        ]
      },
      "element_snapshot": {
        "tag": "button",
        "text": "Login",
        "attributes": {"id": "login", "class": "btn primary"}
      },
      "wait_after": 500
    },
    {
      "step_id": 3,
      "action": "input",
      "params": {
        "index": 10,
        "text": "${USERNAME}",
        "clear": true
      },
      "selectors": {
        "primary": {"type": "xpath", "value": "//input[@name='username']"}
      }
    },
    {
      "step_id": 4,
      "action": "evaluate",
      "params": {
        "expression": "document.querySelector('.result').textContent"
      }
    },
    {
      "step_id": 5,
      "action": "done",
      "params": {
        "text": "Task completed",
        "success": true
      }
    }
  ]
}
```

### 3.2 选择器策略（核心创新）

**多级回退选择器**：

```json
"selectors": {
  "primary": {
    "type": "xpath",
    "value": "//button[@id='submit']"
  },
  "fallback": [
    {"type": "css", "value": "button[data-testid='submit-btn']"},
    {"type": "text", "value": "Submit", "tag": "button"},
    {"type": "attributes", "value": {"name": "submit", "type": "submit"}},
    {"type": "index", "value": 5, "context_hash": "abc123"}
  ]
}
```

**选择器类型**：

| 类型 | 说明 | 稳定性 |
|------|------|--------|
| `xpath` | 从 [`EnhancedDOMTreeNode.xpath`](../browser_use/dom/views.py:464) 提取 | ⭐⭐⭐⭐ |
| `css` | 基于 id/class/attributes 生成 | ⭐⭐⭐⭐⭐ |
| `text` | 文本内容 + 标签名匹配 | ⭐⭐⭐ |
| `attributes` | 多属性组合匹配 | ⭐⭐⭐⭐ |
| `index` | 原始索引 + 上下文 hash | ⭐⭐（仅作最后回退） |

### 3.3 变量系统

```json
"variables": {
  "USERNAME": "${env:LOGIN_USER}",
  "PASSWORD": "${env:LOGIN_PASS}",
  "SEARCH_TERM": "${arg:0}",
  "CONFIG_VALUE": "${file:config.json:$.api.key}"
}
```

**变量来源**：
- `${env:NAME}` - 环境变量
- `${arg:N}` - 命令行参数
- `${file:path:jsonpath}` - 配置文件
- `${prompt:message}` - 运行时提示输入
- `${secret:KEY}` - 敏感数据（与现有 [`sensitive_data`](../browser_use/tools/registry/service.py:334) 机制兼容）

---

## 四、执行器设计

### 4.1 DSL 执行器核心接口

```python
# browser_use/dsl/executor.py

class DSLExecutor:
    """DSL 协议执行器 - 完全跳过 LLM"""
    
    def __init__(
        self,
        browser_session: BrowserSession,
        tools: Tools | None = None,
        variables: dict[str, str] | None = None,
    ):
        self.browser = browser_session
        self.tools = tools or Tools()
        self.variables = variables or {}
        
    async def execute(
        self,
        dsl: dict | str | Path,  # DSL 对象/JSON字符串/文件路径
        on_step_complete: Callable | None = None,
        on_error: Callable | None = None,
    ) -> DSLExecutionResult:
        """执行 DSL 协议"""
        
    async def _resolve_element_selector(
        self,
        selectors: dict,
        timeout: float = 5.0,
    ) -> int | None:
        """
        多级选择器解析 -> 返回可用的 element index
        
        1. 尝试 primary selector
        2. 依次尝试 fallback selectors
        3. 全部失败则返回 None
        """
        
    async def _execute_step(self, step: dict) -> StepResult:
        """执行单个步骤"""
        action_name = step["action"]
        params = self._resolve_variables(step["params"])
        
        # 元素类动作需要先解析选择器
        if action_name in ["click", "input", "scroll"] and "selectors" in step:
            resolved_index = await self._resolve_element_selector(step["selectors"])
            if resolved_index is None:
                raise ElementNotFoundError(step)
            params["index"] = resolved_index
        
        # 调用现有的 execute_action
        result = await self.tools.registry.execute_action(
            action_name=action_name,
            params=params,
            browser_session=self.browser,
        )
        return result
```

### 4.2 与现有系统集成

**复用现有组件**：

| 组件 | 复用方式 |
|------|----------|
| [`Registry.execute_action()`](../browser_use/tools/registry/service.py:327) | 直接调用 |
| [`Tools`](../browser_use/tools/service.py:105) | 复用所有注册的 action |
| [`BrowserSession`](../browser_use/browser/session.py) | 浏览器控制 |
| [`EnhancedDOMTreeNode`](../browser_use/dom/views.py:365) | 元素定位与选择器生成 |
| [`sensitive_data`](../browser_use/tools/registry/service.py:412) 机制 | 变量脱敏 |

---

## 五、DSL 生成器设计

### 5.1 从 AgentHistory 导出 DSL

```python
# browser_use/dsl/generator.py

class DSLGenerator:
    """从 Agent 执行历史生成可复用的 DSL"""
    
    @staticmethod
    def from_agent_history(
        history: AgentHistoryList,
        task: str,
        include_selectors: bool = True,
        selector_strategy: str = "all",  # "primary_only" | "all"
    ) -> dict:
        """
        将 AgentHistory 转换为 DSL 协议
        
        关键步骤：
        1. 遍历 history.history
        2. 提取 model_output.action 中的动作和参数
        3. 从 state.selector_map 提取元素选择器
        4. 生成多级回退选择器
        """
        
    @staticmethod
    def _generate_selectors(
        action: ActionModel,
        selector_map: DOMSelectorMap,
        browser_state: BrowserStateHistory,
    ) -> dict:
        """
        为元素动作生成多级选择器
        
        数据来源：
        - selector_map[index] -> EnhancedDOMTreeNode
        - node.xpath -> XPath 选择器
        - node.attributes -> CSS/属性选择器
        - node.get_meaningful_text_for_llm() -> 文本选择器
        """
```

### 5.2 选择器生成算法

```python
def _generate_selectors_for_element(node: EnhancedDOMTreeNode) -> dict:
    selectors = {"fallback": []}
    
    # 1. 优先使用唯一标识符
    if node.attributes.get("id"):
        selectors["primary"] = {
            "type": "css",
            "value": f"#{node.attributes['id']}"
        }
    elif node.attributes.get("data-testid"):
        selectors["primary"] = {
            "type": "css", 
            "value": f"[data-testid='{node.attributes['data-testid']}']"
        }
    else:
        # XPath 作为 primary（已在 node.xpath 中生成）
        selectors["primary"] = {
            "type": "xpath",
            "value": node.xpath
        }
    
    # 2. 添加回退选择器
    # 文本选择器
    text = node.get_meaningful_text_for_llm()
    if text:
        selectors["fallback"].append({
            "type": "text",
            "value": text[:50],
            "tag": node.tag_name
        })
    
    # 属性组合选择器
    stable_attrs = {}
    for attr in ["name", "class", "type", "role", "aria-label"]:
        if attr in node.attributes and node.attributes[attr]:
            stable_attrs[attr] = node.attributes[attr]
    if stable_attrs:
        selectors["fallback"].append({
            "type": "attributes",
            "value": stable_attrs
        })
    
    # 3. 元素快照（用于调试和可视化）
    selectors["element_snapshot"] = {
        "tag": node.tag_name,
        "text": text[:100] if text else "",
        "attributes": dict(node.attributes)
    }
    
    return selectors
```

---

## 六、执行保证机制

### 6.1 元素定位容错

```python
async def _resolve_element_selector(self, selectors: dict, timeout: float = 5.0) -> int | None:
    """多级选择器解析"""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # 获取当前 DOM 状态
        dom_state = await self.browser.get_browser_state_summary()
        selector_map = dom_state.selector_map
        
        # 1. 尝试 primary selector
        primary = selectors.get("primary")
        if primary:
            index = await self._match_selector(primary, selector_map)
            if index is not None:
                return index
        
        # 2. 依次尝试 fallback
        for fallback in selectors.get("fallback", []):
            index = await self._match_selector(fallback, selector_map)
            if index is not None:
                return index
        
        # 3. 等待后重试
        await asyncio.sleep(0.5)
    
    return None

async def _match_selector(self, selector: dict, selector_map: DOMSelectorMap) -> int | None:
    """匹配单个选择器"""
    selector_type = selector["type"]
    value = selector["value"]
    
    for index, node in selector_map.items():
        if selector_type == "xpath":
            if node.xpath == value:
                return index
        elif selector_type == "css":
            if self._css_matches(node, value):
                return index
        elif selector_type == "text":
            if selector.get("tag", "").lower() == node.tag_name.lower():
                node_text = node.get_meaningful_text_for_llm()
                if value.lower() in node_text.lower():
                    return index
        elif selector_type == "attributes":
            if self._attributes_match(node, value):
                return index
    
    return None
```

### 6.2 执行结果验证

```python
@dataclass
class DSLExecutionResult:
    success: bool
    total_steps: int
    completed_steps: int
    failed_step: int | None
    error_message: str | None
    step_results: list[StepResult]
    duration_seconds: float
    
    def to_agent_history(self) -> AgentHistoryList:
        """转换为 AgentHistoryList 格式，便于分析"""
```

---

## 七、使用示例

### 7.1 从 Agent 执行中导出 DSL

```python
from browser_use import Agent, Browser
from browser_use.dsl import DSLGenerator

# 1. 正常执行 Agent
agent = Agent(task="在 Google 搜索 browser-use", browser=Browser())
history = await agent.run()

# 2. 导出为 DSL
dsl = DSLGenerator.from_agent_history(
    history=history,
    task=agent.task,
    include_selectors=True
)

# 3. 保存 DSL
import json
with open("search_google.dsl.json", "w") as f:
    json.dump(dsl, f, indent=2, ensure_ascii=False)
```

### 7.2 执行 DSL（跳过 LLM）

```python
from browser_use import Browser
from browser_use.dsl import DSLExecutor

# 1. 加载 DSL
with open("search_google.dsl.json") as f:
    dsl = json.load(f)

# 2. 创建执行器
browser = Browser()
await browser.start()

executor = DSLExecutor(
    browser_session=browser,
    variables={"SEARCH_QUERY": "new search term"}
)

# 3. 执行（完全不需要 LLM）
result = await executor.execute(dsl)
print(f"执行结果: {result.success}, 耗时: {result.duration_seconds}s")

await browser.stop()
```

### 7.3 命令行工具

```bash
# 执行 DSL 文件
browser-use run workflow.dsl.json --var SEARCH_QUERY="test"

# 从 Python 脚本导出 DSL
browser-use export script.py --output workflow.dsl.json

# 验证 DSL 语法
browser-use validate workflow.dsl.json
```

---

## 八、与现有模块的兼容性

| 现有模块 | 兼容方式 |
|----------|----------|
| [`CodeAgent`](../browser_use/code_use/service.py:54) | DSL 可作为 `initial_actions` 导入 |
| [`notebook_export`](../browser_use/code_use/notebook_export.py:1) | DSL 可转换为 Notebook |
| [`Skills`](../browser_use/skills/README.md:1) | 技能可封装为 DSL 模板 |
| [`sensitive_data`](../browser_use/tools/registry/service.py:334) | 变量系统直接复用 |
| [`AgentHistoryList`](../browser_use/agent/views.py:731) | DSL 结果可转换为 History |

---

## 九、实现优先级

1. **P0（核心）**：DSL Schema 定义 + 基础执行器
2. **P1（关键）**：多级选择器解析 + 从 AgentHistory 导出
3. **P2（增强）**：变量系统 + 命令行工具
4. **P3（扩展）**：可视化编辑器 + 与 CI/CD 集成

---

## 十、总结

本方案设计了一套完整的 DSL 协议，**严格保证执行成功**的关键在于：

1. **多级回退选择器**：从 XPath/CSS/Text/Attributes 多维度定位元素
2. **复用现有执行链路**：直接调用 [`Registry.execute_action()`](../browser_use/tools/registry/service.py:327)
3. **变量系统**：支持敏感数据和动态参数
4. **元素快照**：保存执行时的元素状态，便于调试

DSL 协议与现有 browser-use 架构**完全兼容**，无需修改核心代码，只需新增 `browser_use/dsl/` 模块。