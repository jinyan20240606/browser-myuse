# Browser-Use DSL 协议设计（方案 B）

## 一、设计目标

### 1.1 核心目标

设计一套**平台无关的 DSL 协议**，实现以下工作流：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           首次执行（需要 LLM）                           │
├─────────────────────────────────────────────────────────────────────────┤
│  用户任务 → Agent + LLM 推理 → 执行成功 → 导出 DSL 文件                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
                            保存 workflow.dsl.json
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                        后续执行（完全跳过 LLM）                          │
├─────────────────────────────────────────────────────────────────────────┤
│  加载 DSL 文件 → DSLExecutor 直接执行 → 完成任务                         │
│  • 零 LLM 调用                                                          │
│  • 毫秒级启动                                                            │
│  • 100% 可复现                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **完全自包含** | DSL 文件包含执行所需的全部信息，不依赖外部上下文 |
| **确定性执行** | 相同 DSL + 相同页面状态 = 相同执行结果 |
| **容错性强** | 多级选择器回退，适应页面轻微变化 |
| **人类可读** | JSON 格式，可手动编辑和调试 |
| **零 LLM 依赖** | 执行阶段完全不调用任何 LLM |

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

### 3.1 完整 DSL 示例

以下是一个完整的、可直接执行的 DSL 文件示例（登录并搜索）：

```json
{
  "$schema": "https://browser-use.com/dsl/v1.0/schema.json",
  "version": "1.0",
  "metadata": {
    "name": "登录并搜索商品",
    "description": "自动登录电商网站并搜索指定商品",
    "created_at": "2025-01-23T12:00:00Z",
    "source_task": "登录 example.com 并搜索 'browser automation'",
    "source_agent_id": "agent-uuid-12345",
    "total_steps": 6,
    "estimated_duration_seconds": 15,
    "success": true
  },
  "variables": {
    "USERNAME": {
      "source": "env",
      "key": "LOGIN_USER",
      "description": "登录用户名"
    },
    "PASSWORD": {
      "source": "secret",
      "key": "LOGIN_PASS",
      "description": "登录密码（敏感数据）"
    },
    "SEARCH_QUERY": {
      "source": "arg",
      "index": 0,
      "default": "browser automation",
      "description": "搜索关键词"
    }
  },
  "steps": [
    {
      "step_id": 1,
      "name": "打开登录页面",
      "action": "navigate",
      "params": {
        "url": "https://example.com/login",
        "new_tab": false
      },
      "assertions": {
        "url_contains": "/login",
        "title_contains": "登录"
      },
      "wait_after_ms": 1000,
      "timeout_ms": 10000,
      "retry": {
        "max_attempts": 3,
        "delay_ms": 1000,
        "on_fail": "abort"
      }
    },
    {
      "step_id": 2,
      "name": "输入用户名",
      "action": "input",
      "params": {
        "text": "${USERNAME}",
        "clear": true
      },
      "selectors": {
        "primary": {
          "type": "css",
          "value": "#username"
        },
        "fallback": [
          {"type": "xpath", "value": "//input[@name='username']"},
          {"type": "xpath", "value": "//input[@placeholder='用户名']"},
          {"type": "attributes", "value": {"type": "text", "autocomplete": "username"}}
        ]
      },
      "element_snapshot": {
        "tag": "input",
        "text": "",
        "attributes": {"id": "username", "name": "username", "type": "text"}
      }
    },
    {
      "step_id": 3,
      "name": "输入密码",
      "action": "input",
      "params": {
        "text": "${PASSWORD}",
        "clear": true
      },
      "selectors": {
        "primary": {
          "type": "css",
          "value": "#password"
        },
        "fallback": [
          {"type": "xpath", "value": "//input[@type='password']"},
          {"type": "attributes", "value": {"type": "password"}}
        ]
      },
      "element_snapshot": {
        "tag": "input",
        "attributes": {"id": "password", "type": "password"}
      }
    },
    {
      "step_id": 4,
      "name": "点击登录按钮",
      "action": "click",
      "selectors": {
        "primary": {
          "type": "css",
          "value": "button[type='submit']"
        },
        "fallback": [
          {"type": "text", "value": "登录", "tag": "button"},
          {"type": "text", "value": "Login", "tag": "button"},
          {"type": "xpath", "value": "//button[contains(@class, 'login')]"}
        ]
      },
      "element_snapshot": {
        "tag": "button",
        "text": "登录",
        "attributes": {"type": "submit", "class": "btn btn-primary"}
      },
      "wait_after_ms": 2000,
      "assertions": {
        "url_not_contains": "/login"
      }
    },
    {
      "step_id": 5,
      "name": "搜索商品",
      "action": "input",
      "params": {
        "text": "${SEARCH_QUERY}",
        "clear": true
      },
      "selectors": {
        "primary": {
          "type": "css",
          "value": "input[name='search']"
        },
        "fallback": [
          {"type": "xpath", "value": "//input[@placeholder='搜索']"},
          {"type": "attributes", "value": {"type": "search"}}
        ]
      },
      "post_action": {
        "action": "send_keys",
        "params": {"keys": "Enter"}
      },
      "wait_after_ms": 1500
    },
    {
      "step_id": 6,
      "name": "完成任务",
      "action": "done",
      "params": {
        "text": "已成功登录并搜索 '${SEARCH_QUERY}'",
        "success": true
      }
    }
  ]
}
```

### 3.2 DSL Schema 定义

```typescript
// DSL 根结构
interface BrowserUseDSL {
  $schema: string;                    // JSON Schema URL
  version: "1.0";                     // 协议版本
  metadata: DSLMetadata;              // 元数据
  variables?: Record<string, Variable>; // 变量定义
  steps: Step[];                      // 执行步骤
}

// 元数据
interface DSLMetadata {
  name: string;                       // 工作流名称
  description?: string;               // 描述
  created_at: string;                 // 创建时间 (ISO 8601)
  source_task: string;                // 原始任务描述
  source_agent_id?: string;           // 生成此 DSL 的 Agent ID
  total_steps: number;                // 总步骤数
  estimated_duration_seconds?: number; // 预估执行时间
  success: boolean;                   // 原始执行是否成功
}

// 变量定义
interface Variable {
  source: "env" | "secret" | "arg" | "file" | "prompt"; // 变量来源
  key?: string;                       // 环境变量名/密钥名
  index?: number;                     // 命令行参数索引
  path?: string;                      // 文件路径
  jsonpath?: string;                  // JSON 路径
  default?: string;                   // 默认值
  description?: string;               // 描述
}

// 执行步骤
interface Step {
  step_id: number;                    // 步骤 ID
  name?: string;                      // 步骤名称（人类可读）
  action: ActionName;                 // 动作类型
  params?: Record<string, any>;       // 动作参数
  selectors?: SelectorConfig;         // 元素选择器（仅元素类动作）
  element_snapshot?: ElementSnapshot; // 元素快照（调试用）
  assertions?: Assertions;            // 执行后断言
  wait_after_ms?: number;             // 执行后等待时间
  timeout_ms?: number;                // 超时时间
  retry?: RetryConfig;                // 重试配置
  post_action?: Step;                 // 后置动作（如 input 后按 Enter）
  condition?: Condition;              // 条件执行（可选）
}

// 动作类型
type ActionName =
  | "navigate" | "search" | "click" | "input"
  | "scroll" | "send_keys" | "switch" | "close"
  | "extract" | "evaluate" | "wait" | "go_back" | "done";

// 选择器配置
interface SelectorConfig {
  primary: Selector;                  // 主选择器
  fallback?: Selector[];              // 回退选择器列表
}

// 单个选择器
interface Selector {
  type: "css" | "xpath" | "text" | "attributes" | "index";
  value: string | Record<string, string> | number;
  tag?: string;                       // 仅 text 类型需要
  context_hash?: string;              // 仅 index 类型需要
}

// 元素快照
interface ElementSnapshot {
  tag: string;
  text?: string;
  attributes: Record<string, string>;
}

// 断言配置
interface Assertions {
  url_contains?: string;
  url_not_contains?: string;
  title_contains?: string;
  element_exists?: Selector;
  element_not_exists?: Selector;
}

// 重试配置
interface RetryConfig {
  max_attempts: number;               // 最大重试次数
  delay_ms: number;                   // 重试间隔
  on_fail: "abort" | "skip" | "continue"; // 失败后行为
}

// 条件执行（v1.1 扩展）
interface Condition {
  type: "element_exists" | "url_matches" | "variable_equals";
  selector?: Selector;
  pattern?: string;
  variable?: string;
  value?: string;
  then?: Step[];                      // 条件满足时执行
  else?: Step[];                      // 条件不满足时执行
}
```

### 3.3 选择器策略（核心创新）

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

### 3.4 选择器匹配算法

执行器使用 CDP `evaluate()` 直接在浏览器中查询元素，而非在 Python 中遍历 DOM：

```python
async def _resolve_selector_via_cdp(
    self,
    selector: Selector,
    cdp_client: CDPClient
) -> int | None:
    """使用 CDP 直接查询元素，返回 selector_map 中的 index"""
    
    selector_type = selector["type"]
    value = selector["value"]
    
    if selector_type == "css":
        # 直接使用 CSS 选择器
        js_code = f"document.querySelector('{value}')"
    
    elif selector_type == "xpath":
        # XPath 查询
        js_code = f"""
        document.evaluate(
            '{value}',
            document,
            null,
            XPathResult.FIRST_ORDERED_NODE_TYPE,
            null
        ).singleNodeValue
        """
    
    elif selector_type == "text":
        # 文本内容匹配
        tag = selector.get("tag", "*")
        js_code = f"""
        Array.from(document.querySelectorAll('{tag}')).find(
            el => el.textContent.includes('{value}')
        )
        """
    
    elif selector_type == "attributes":
        # 多属性组合匹配
        conditions = " and ".join([
            f"@{k}='{v}'" for k, v in value.items()
        ])
        js_code = f"""
        document.evaluate(
            '//*[{conditions}]',
            document,
            null,
            XPathResult.FIRST_ORDERED_NODE_TYPE,
            null
        ).singleNodeValue
        """
    
    # 执行 JS 并获取 backend_node_id
    result = await cdp_client.execute(
        "Runtime.evaluate",
        expression=js_code,
        returnByValue=False
    )
    
    if result.get("result", {}).get("objectId"):
        # 获取 backend_node_id
        node_info = await cdp_client.execute(
            "DOM.describeNode",
            objectId=result["result"]["objectId"]
        )
        backend_node_id = node_info["node"]["backendNodeId"]
        
        # 在当前 selector_map 中查找对应的 index
        return self._find_index_by_backend_node_id(backend_node_id)
    
    return None
```

### 3.5 变量系统

变量在执行时动态解析，支持多种来源：

| 来源类型 | 语法 | 说明 | 示例 |
|----------|------|------|------|
| `env` | `${VAR_NAME}` | 环境变量 | `${LOGIN_USER}` |
| `secret` | `<secret>KEY</secret>` | 敏感数据（与现有机制兼容） | `<secret>password</secret>` |
| `arg` | 通过 `variables` 定义 | 命令行参数 | 见 DSL 示例 |
| `file` | 通过 `variables` 定义 | 配置文件 | `{"source": "file", "path": "config.json", "jsonpath": "$.api.key"}` |
| `prompt` | 通过 `variables` 定义 | 运行时提示输入 | `{"source": "prompt", "message": "请输入验证码"}` |

```python
# 变量解析示例
def _resolve_variables(self, params: dict) -> dict:
    """解析参数中的变量引用"""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str):
            # 替换 ${VAR_NAME} 格式
            for var_name, var_def in self.dsl["variables"].items():
                placeholder = f"${{{var_name}}}"
                if placeholder in value:
                    actual_value = self._get_variable_value(var_name, var_def)
                    value = value.replace(placeholder, actual_value)
            resolved[key] = value
        else:
            resolved[key] = value
    return resolved

def _get_variable_value(self, name: str, var_def: dict) -> str:
    """根据变量定义获取实际值"""
    source = var_def["source"]
    
    if source == "env":
        return os.environ.get(var_def["key"], var_def.get("default", ""))
    
    elif source == "secret":
        # 复用现有 sensitive_data 机制
        return self.sensitive_data.get(var_def["key"], "")
    
    elif source == "arg":
        return self.args[var_def["index"]] if var_def["index"] < len(self.args) else var_def.get("default", "")
    
    elif source == "file":
        with open(var_def["path"]) as f:
            data = json.load(f)
            return jsonpath.parse(var_def["jsonpath"]).find(data)[0].value
    
    elif source == "prompt":
        return input(var_def.get("message", f"请输入 {name}: "))
    
    return var_def.get("default", "")
```

---

## 四、执行器完整设计

### 4.1 DSL 执行器完整实现

```python
# browser_use/dsl/executor.py

from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Any
import asyncio
import time
import json
import logging
import os

from browser_use.browser.session import BrowserSession
from browser_use.tools.service import Tools
from browser_use.tools.views import ActionResult

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """单个步骤的执行结果"""
    step_id: int
    action: str
    success: bool
    duration_ms: float
    error: str | None = None
    extracted_content: str | None = None
    selector_used: str | None = None  # 记录实际使用的选择器


@dataclass
class DSLExecutionResult:
    """DSL 执行的完整结果"""
    success: bool
    total_steps: int
    completed_steps: int
    failed_step: int | None
    error_message: str | None
    step_results: list[StepResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    
    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "success": self.success,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_step": self.failed_step,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "step_results": [
                {
                    "step_id": r.step_id,
                    "action": r.action,
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                    "selector_used": r.selector_used,
                }
                for r in self.step_results
            ]
        }


class ElementNotFoundError(Exception):
    """元素未找到异常"""
    pass


class DSLExecutor:
    """
    DSL 协议执行器 - 完全跳过 LLM
    
    核心特性：
    - 零 LLM 调用：直接解析 DSL 并执行
    - 多级选择器回退：确保元素定位的稳定性
    - 变量动态解析：支持环境变量、密钥、运行时参数
    - 断言验证：执行后验证页面状态
    - 详细日志：记录每个步骤的执行情况
    """
    
    def __init__(
        self,
        browser: BrowserSession,
        tools: Tools | None = None,
        sensitive_data: dict[str, str] | None = None,
        args: list[str] | None = None,
    ):
        """
        初始化 DSL 执行器
        
        Args:
            browser: 浏览器会话
            tools: 工具注册表（可选，默认使用标准工具）
            sensitive_data: 敏感数据字典（用于替换 ${PASSWORD} 等）
            args: 命令行参数列表（用于替换 ${arg:0} 等）
        """
        self.browser = browser
        self.tools = tools or Tools()
        self.sensitive_data = sensitive_data or {}
        self.args = args or []
        self.dsl: dict = {}
        
    async def execute(
        self,
        dsl: dict | str | Path,
        on_step_complete: Callable[[StepResult], None] | None = None,
        on_error: Callable[[Exception, int], None] | None = None,
    ) -> DSLExecutionResult:
        """
        执行 DSL 协议 - 完全不需要 LLM
        
        Args:
            dsl: DSL 对象、JSON 字符串或文件路径
            on_step_complete: 每个步骤完成后的回调
            on_error: 发生错误时的回调
            
        Returns:
            DSLExecutionResult: 执行结果
        """
        start_time = time.time()
        
        # 1. 加载 DSL
        self.dsl = self._load_dsl(dsl)
        steps = self.dsl.get("steps", [])
        total_steps = len(steps)
        
        metadata = self.dsl.get("metadata", {})
        logger.info(f"🚀 开始执行 DSL: {metadata.get('name', 'Unknown')}")
        logger.info(f"   描述: {metadata.get('description', 'N/A')}")
        logger.info(f"   总步骤数: {total_steps}")
        
        step_results: list[StepResult] = []
        completed_steps = 0
        failed_step = None
        error_message = None
        
        # 2. 逐步执行
        for step in steps:
            step_id = step.get("step_id", completed_steps + 1)
            step_name = step.get("name", f"Step {step_id}")
            action = step.get("action", "unknown")
            
            logger.info(f"▶️  [{step_id}/{total_steps}] {step_name} ({action})")
            
            step_start = time.time()
            
            try:
                result, selector_used = await self._execute_step(step)
                step_duration = (time.time() - step_start) * 1000
                
                step_result = StepResult(
                    step_id=step_id,
                    action=action,
                    success=True,
                    duration_ms=step_duration,
                    extracted_content=getattr(result, 'extracted_content', None),
                    selector_used=selector_used,
                )
                step_results.append(step_result)
                completed_steps += 1
                
                logger.info(f"   ✅ 完成 ({step_duration:.0f}ms)")
                
                if on_step_complete:
                    on_step_complete(step_result)
                
                # 执行后等待
                wait_after = step.get("wait_after_ms", 0)
                if wait_after > 0:
                    await asyncio.sleep(wait_after / 1000)
                
                # 执行断言
                assertions = step.get("assertions")
                if assertions:
                    await self._verify_assertions(assertions)
                    
            except Exception as e:
                step_duration = (time.time() - step_start) * 1000
                error_msg = str(e)
                
                step_result = StepResult(
                    step_id=step_id,
                    action=action,
                    success=False,
                    duration_ms=step_duration,
                    error=error_msg,
                )
                step_results.append(step_result)
                
                logger.error(f"   ❌ 失败: {error_msg}")
                
                if on_error:
                    on_error(e, step_id)
                
                # 处理重试逻辑
                retry_config = step.get("retry", {})
                if await self._handle_retry(step, retry_config, e):
                    # 重试成功，继续
                    completed_steps += 1
                    step_results[-1].success = True
                    step_results[-1].error = None
                    continue
                
                # 根据 on_fail 策略决定是否继续
                on_fail = retry_config.get("on_fail", "abort")
                if on_fail == "abort":
                    failed_step = step_id
                    error_message = error_msg
                    break
                elif on_fail == "skip":
                    logger.warning(f"   ⏭️  跳过步骤 {step_id}")
                    continue
                # on_fail == "continue" 时继续执行
        
        duration = time.time() - start_time
        success = failed_step is None and completed_steps == total_steps
        
        result = DSLExecutionResult(
            success=success,
            total_steps=total_steps,
            completed_steps=completed_steps,
            failed_step=failed_step,
            error_message=error_message,
            step_results=step_results,
            duration_seconds=duration,
        )
        
        if success:
            logger.info(f"🎉 DSL 执行成功! 耗时: {duration:.2f}s")
        else:
            logger.error(f"💥 DSL 执行失败于步骤 {failed_step}: {error_message}")
        
        return result
    
    def _load_dsl(self, dsl: dict | str | Path) -> dict:
        """加载 DSL 配置"""
        if isinstance(dsl, dict):
            return dsl
        elif isinstance(dsl, Path):
            with open(dsl, 'r', encoding='utf-8') as f:
                return json.load(f)
        elif isinstance(dsl, str):
            # 尝试作为 JSON 解析，失败则作为文件路径
            try:
                return json.loads(dsl)
            except json.JSONDecodeError:
                with open(dsl, 'r', encoding='utf-8') as f:
                    return json.load(f)
        else:
            raise ValueError(f"不支持的 DSL 类型: {type(dsl)}")
    
    async def _execute_step(self, step: dict) -> tuple[ActionResult, str | None]:
        """
        执行单个步骤
        
        Returns:
            tuple: (ActionResult, selector_used_description)
        """
        action_name = step.get("action")
        params = step.get("params", {})
        selector_used = None
        
        # 解析变量
        params = self._resolve_variables(params)
        
        # 元素类动作需要先解析选择器
        if action_name in ["click", "input", "scroll", "upload_file"] and "selectors" in step:
            resolved_index, selector_used = await self._resolve_element_selector(
                step["selectors"],
                timeout=step.get("timeout_ms", 5000) / 1000
            )
            if resolved_index is None:
                raise ElementNotFoundError(
                    f"无法找到元素: {step.get('name', step['step_id'])}\n"
                    f"尝试的选择器: {json.dumps(step['selectors'], ensure_ascii=False)}"
                )
            params["index"] = resolved_index
        
        # 调用现有的 execute_action
        result = await self.tools.registry.execute_action(
            action_name=action_name,
            params=params,
            browser_session=self.browser,
            sensitive_data=self.sensitive_data,
        )
        
        # 执行后置动作（如 input 后按 Enter）
        post_action = step.get("post_action")
        if post_action:
            await self._execute_step(post_action)
        
        return result, selector_used
    
    async def _resolve_element_selector(
        self,
        selectors: dict,
        timeout: float = 5.0,
    ) -> tuple[int | None, str | None]:
        """
        多级选择器解析 - 使用 CDP 直接查询
        
        Returns:
            tuple: (element_index, selector_used_description)
        """
        start_time = time.time()
        cdp_client = self.browser.cdp_client
        
        while time.time() - start_time < timeout:
            # 1. 尝试 primary selector
            primary = selectors.get("primary")
            if primary:
                index = await self._resolve_selector_via_cdp(primary, cdp_client)
                if index is not None:
                    desc = f"primary:{primary['type']}:{primary.get('value', '')[:50]}"
                    return index, desc
            
            # 2. 依次尝试 fallback
            for i, fallback in enumerate(selectors.get("fallback", [])):
                index = await self._resolve_selector_via_cdp(fallback, cdp_client)
                if index is not None:
                    desc = f"fallback[{i}]:{fallback['type']}:{str(fallback.get('value', ''))[:50]}"
                    return index, desc
            
            # 3. 等待后重试
            await asyncio.sleep(0.5)
        
        return None, None
    
    async def _resolve_selector_via_cdp(
        self,
        selector: dict,
        cdp_client: Any,
    ) -> int | None:
        """使用 CDP 直接查询元素，返回 selector_map 中的 index"""
        selector_type = selector["type"]
        value = selector["value"]
        
        # 构建 JS 查询代码
        if selector_type == "css":
            js_code = f"document.querySelector('{value}')"
        
        elif selector_type == "xpath":
            js_code = f"""
            document.evaluate(
                '{value}',
                document,
                null,
                XPathResult.FIRST_ORDERED_NODE_TYPE,
                null
            ).singleNodeValue
            """
        
        elif selector_type == "text":
            tag = selector.get("tag", "*")
            escaped_value = str(value).replace("'", "\\'")
            js_code = f"""
            Array.from(document.querySelectorAll('{tag}')).find(
                el => el.textContent.includes('{escaped_value}')
            )
            """
        
        elif selector_type == "attributes":
            conditions = " and ".join([
                f"@{k}='{v}'" for k, v in value.items()
            ])
            js_code = f"""
            document.evaluate(
                '//*[{conditions}]',
                document,
                null,
                XPathResult.FIRST_ORDERED_NODE_TYPE,
                null
            ).singleNodeValue
            """
        
        elif selector_type == "index":
            # 直接返回索引（仅作最后回退）
            return value
        
        else:
            return None
        
        try:
            # 执行 JS 并获取 backend_node_id
            result = await cdp_client.execute(
                "Runtime.evaluate",
                expression=js_code,
                returnByValue=False
            )
            
            if result.get("result", {}).get("objectId"):
                # 获取 backend_node_id
                node_info = await cdp_client.execute(
                    "DOM.describeNode",
                    objectId=result["result"]["objectId"]
                )
                backend_node_id = node_info["node"]["backendNodeId"]
                
                # 在当前 selector_map 中查找对应的 index
                return await self._find_index_by_backend_node_id(backend_node_id)
        except Exception as e:
            logger.debug(f"选择器解析失败: {selector_type}={value}, 错误: {e}")
        
        return None
    
    async def _find_index_by_backend_node_id(self, backend_node_id: int) -> int | None:
        """在 selector_map 中查找 backend_node_id 对应的 index"""
        state = await self.browser.get_browser_state_summary()
        selector_map = state.selector_map
        
        for index, node in selector_map.items():
            if node.backend_node_id == backend_node_id:
                return index
        
        return None
    
    def _resolve_variables(self, params: dict) -> dict:
        """解析参数中的变量引用"""
        resolved = {}
        variables = self.dsl.get("variables", {})
        
        for key, value in params.items():
            if isinstance(value, str):
                # 替换 ${VAR_NAME} 格式的变量
                for var_name, var_def in variables.items():
                    placeholder = f"${{{var_name}}}"
                    if placeholder in value:
                        actual_value = self._get_variable_value(var_name, var_def)
                        value = value.replace(placeholder, actual_value)
                resolved[key] = value
            else:
                resolved[key] = value
        
        return resolved
    
    def _get_variable_value(self, name: str, var_def: dict) -> str:
        """根据变量定义获取实际值"""
        source = var_def.get("source", "env")
        
        if source == "env":
            return os.environ.get(var_def.get("key", name), var_def.get("default", ""))
        
        elif source == "secret":
            return self.sensitive_data.get(var_def.get("key", name), var_def.get("default", ""))
        
        elif source == "arg":
            index = var_def.get("index", 0)
            return self.args[index] if index < len(self.args) else var_def.get("default", "")
        
        elif source == "file":
            try:
                import jsonpath_ng
                with open(var_def["path"]) as f:
                    data = json.load(f)
                    matches = jsonpath_ng.parse(var_def["jsonpath"]).find(data)
                    return str(matches[0].value) if matches else var_def.get("default", "")
            except Exception:
                return var_def.get("default", "")
        
        elif source == "prompt":
            return input(var_def.get("message", f"请输入 {name}: "))
        
        return var_def.get("default", "")
    
    async def _verify_assertions(self, assertions: dict) -> None:
        """验证断言"""
        current_url = await self.browser.get_current_page_url()
        
        if "url_contains" in assertions:
            expected = assertions["url_contains"]
            if expected not in current_url:
                raise AssertionError(f"URL 断言失败: 期望包含 '{expected}', 实际: '{current_url}'")
        
        if "url_not_contains" in assertions:
            unexpected = assertions["url_not_contains"]
            if unexpected in current_url:
                raise AssertionError(f"URL 断言失败: 不应包含 '{unexpected}', 实际: '{current_url}'")
        
        if "title_contains" in assertions:
            expected = assertions["title_contains"]
            title = await self.browser.get_current_page_title()
            if expected not in title:
                raise AssertionError(f"标题断言失败: 期望包含 '{expected}', 实际: '{title}'")
    
    async def _handle_retry(
        self,
        step: dict,
        retry_config: dict,
        error: Exception
    ) -> bool:
        """处理重试逻辑，返回是否重试成功"""
        max_attempts = retry_config.get("max_attempts", 0)
        delay_ms = retry_config.get("delay_ms", 1000)
        
        for attempt in range(max_attempts):
            logger.info(f"   🔄 重试 {attempt + 1}/{max_attempts}...")
            await asyncio.sleep(delay_ms / 1000)
            
            try:
                await self._execute_step(step)
                return True
            except Exception as e:
                logger.warning(f"   重试失败: {e}")
        
        return False
```

### 4.2 与现有系统集成

**复用现有组件**：

| 组件 | 复用方式 |
|------|----------|
| [`Registry.execute_action()`](../browser_use/tools/registry/service.py:327) | 直接调用，零修改 |
| [`Tools`](../browser_use/tools/service.py:105) | 复用所有注册的 action |
| [`BrowserSession`](../browser_use/browser/session.py:94) | 浏览器控制 |
| [`EnhancedDOMTreeNode`](../browser_use/dom/views.py:365) | 元素定位与选择器生成 |
| [`sensitive_data`](../browser_use/tools/registry/service.py:412) 机制 | 变量脱敏 |
| CDP Client | 直接使用 CDP 查询元素 |

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

## 七、端到端工作流示例

### 7.1 核心价值

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        复用效果：LLM 执行一次，无限复用                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   首次执行（录制）           后续执行（回放）                                 │
│   ┌─────────────────┐       ┌─────────────────┐                             │
│   │ Agent + LLM     │       │ DSLExecutor     │                             │
│   │ • 消耗 Token    │  →→→  │ • 零 Token      │                             │
│   │ • 推理 2-5s     │       │ • 启动 <100ms   │                             │
│   │ • 单次使用      │       │ • 无限复用      │                             │
│   └────────┬────────┘       └────────┬────────┘                             │
│            │                         │                                      │
│            ▼                         ▼                                      │
│     执行成功 + 导出 DSL        直接执行 DSL                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 录制阶段 - 从 Agent 执行中导出 DSL

```python
import asyncio
import json
from browser_use import Agent, Browser, ChatBrowserUse
from browser_use.dsl import DSLGenerator

async def record_workflow():
    """
    录制工作流 - 首次执行，需要 LLM
    
    执行完成后导出完整的 DSL 文件，后续可无限复用
    """
    
    # 1. 创建 Agent 并执行任务
    browser = Browser()
    agent = Agent(
        task="登录 example.com 并搜索 'browser automation'",
        browser=browser,
        llm=ChatBrowserUse(),  # 需要 LLM
        sensitive_data={
            "LOGIN_USER": "my_username",
            "LOGIN_PASS": "my_password",
        }
    )
    
    # 2. 执行任务（消耗 LLM tokens）
    print("🎬 开始录制工作流...")
    history = await agent.run()
    
    # 3. 检查执行是否成功
    if not history.is_successful():
        print("❌ 任务执行失败，无法生成 DSL")
        await browser.stop()
        return None
    
    # 4. 从执行历史生成 DSL
    dsl = DSLGenerator.from_agent_history(
        history=history,
        task=agent.task,
        name="登录并搜索",           # 工作流名称
        include_selectors=True,      # 包含元素选择器
        selector_strategy="all",     # 生成所有回退选择器
    )
    
    # 5. 保存 DSL 文件
    output_path = "login_and_search.dsl.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dsl, f, indent=2, ensure_ascii=False)
    
    print(f"✅ DSL 已保存到: {output_path}")
    print(f"   包含 {len(dsl['steps'])} 个步骤")
    print(f"   后续执行将完全跳过 LLM")
    
    await browser.stop()
    return dsl

# 执行录制（仅需一次）
asyncio.run(record_workflow())
```

### 7.3 回放阶段 - 执行 DSL（完全跳过 LLM）

```python
import asyncio
from browser_use import Browser
from browser_use.dsl import DSLExecutor

async def replay_workflow():
    """
    回放工作流 - 完全不需要 LLM
    
    直接加载 DSL 文件并执行，零 API 费用
    """
    
    # 1. 创建浏览器
    browser = Browser()
    await browser.start()
    
    # 2. 创建 DSL 执行器（无需 LLM！）
    executor = DSLExecutor(
        browser=browser,
        sensitive_data={
            "LOGIN_PASS": "my_password",  # 敏感数据在运行时提供
        },
        args=["new search term"],  # 可选：覆盖搜索词
    )
    
    # 3. 执行 DSL 文件（零 LLM 调用！）
    print("▶️  开始回放工作流...")
    result = await executor.execute(
        "login_and_search.dsl.json",
        on_step_complete=lambda r: print(f"  ✓ 步骤 {r.step_id}: {r.action}"),
    )
    
    # 4. 输出结果
    print("\n" + "="*50)
    if result.success:
        print(f"🎉 执行成功!")
        print(f"   完成步骤: {result.completed_steps}/{result.total_steps}")
        print(f"   总耗时: {result.duration_seconds:.2f}s")
        print(f"   LLM 调用: 0 次 ✨")
    else:
        print(f"💥 执行失败于步骤 {result.failed_step}")
        print(f"   错误: {result.error_message}")
    
    await browser.stop()
    return result

# 执行回放（可无限次重复）
asyncio.run(replay_workflow())
```

### 7.4 批量执行与定时任务

```python
import asyncio
from browser_use import Browser
from browser_use.dsl import DSLExecutor

async def batch_execute(dsl_files: list[str]):
    """批量执行多个 DSL 工作流 - 适合定时任务"""
    
    results = []
    
    for dsl_file in dsl_files:
        browser = Browser(headless=True)  # 无头模式
        await browser.start()
        
        executor = DSLExecutor(browser=browser)
        result = await executor.execute(dsl_file)
        
        results.append({
            "file": dsl_file,
            "success": result.success,
            "duration": result.duration_seconds,
        })
        
        await browser.stop()
    
    # 汇总结果
    print("\n📊 批量执行结果:")
    total_llm_calls = 0  # 始终为 0！
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"  {status} {r['file']} ({r['duration']:.2f}s)")
    
    success_count = sum(1 for r in results if r["success"])
    print(f"\n总计: {success_count}/{len(results)} 成功")
    print(f"LLM 调用: {total_llm_calls} 次")

# 示例：每天定时执行
asyncio.run(batch_execute([
    "daily_report.dsl.json",
    "data_scraping.dsl.json",
    "auto_checkin.dsl.json",
]))
```

### 7.5 命令行工具

```bash
# 执行 DSL 文件（带变量）
browser-use dsl run login_and_search.dsl.json \
    --var SEARCH_QUERY="new term" \
    --secret LOGIN_PASS="password123"

# 验证 DSL 语法
browser-use dsl validate workflow.dsl.json

# 查看 DSL 信息
browser-use dsl info workflow.dsl.json
# 输出：
# 名称: 登录并搜索
# 步骤数: 6
# 预估耗时: 15s
# 变量: USERNAME, PASSWORD, SEARCH_QUERY

# 批量执行（并行）
browser-use dsl batch *.dsl.json --parallel 4 --headless
```

### 7.6 使用场景对比

| 场景 | 首次执行（Agent + LLM） | 后续执行（DSL） |
|------|-------------------------|-----------------|
| **LLM 调用** | ✅ 需要（多次推理） | ❌ 完全不需要 |
| **API 费用** | ~$0.01-0.10/次 | $0.00 |
| **启动延迟** | 2-5s（LLM 推理） | <100ms |
| **执行稳定性** | 可能有随机性 | 100% 确定性 |
| **适用场景** | 首次探索、开发调试 | 生产环境、批量任务、定时任务 |
| **错误处理** | LLM 可自适应 | 依赖多级选择器回退 |
| **可编辑性** | 需重新执行 | 可手动编辑 JSON |

### 7.7 典型应用场景

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DSL 典型应用场景                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 定时任务自动化                                                          │
│     ├─ 每日签到                                                             │
│     ├─ 定时数据抓取                                                          │
│     └─ 自动化报表生成                                                        │
│                                                                             │
│  2. 批量操作                                                                │
│     ├─ 批量账号注册                                                          │
│     ├─ 批量数据导入                                                          │
│     └─ 批量内容发布                                                          │
│                                                                             │
│  3. CI/CD 集成                                                              │
│     ├─ E2E 测试用例                                                          │
│     ├─ 部署后验证                                                            │
│     └─ 回归测试                                                              │
│                                                                             │
│  4. 企业级应用                                                              │
│     ├─ RPA 流程自动化                                                        │
│     ├─ 跨系统数据同步                                                        │
│     └─ 工作流程标准化                                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 八、与现有模块的兼容性

| 现有模块 | 兼容方式 |
|----------|----------|
| [`Registry.execute_action()`](../browser_use/tools/registry/service.py:327) | 直接调用，DSL 执行器的核心入口 |
| [`Tools`](../browser_use/tools/service.py:105) | 复用所有注册的 action |
| [`BrowserSession`](../browser_use/browser/session.py:94) | 浏览器控制 |
| [`sensitive_data`](../browser_use/tools/registry/service.py:334) | 变量系统直接复用 |
| [`AgentHistoryList`](../browser_use/agent/views.py:731) | DSL 可从 History 导出，也可转换回 History |
| [`EnhancedDOMTreeNode`](../browser_use/dom/views.py:365) | 元素定位与选择器生成 |
| [`CodeAgent`](../browser_use/code_use/service.py:54) | DSL 可作为 `initial_actions` 导入 |

---

## 九、实现优先级

| 优先级 | 功能 | 说明 |
|--------|------|------|
| **P0（核心）** | DSL Schema 定义 + 基础执行器 | 最小可用版本 |
| **P1（关键）** | 多级选择器解析 + 从 AgentHistory 导出 | 确保可复用性 |
| **P2（增强）** | 变量系统 + 断言验证 | 增强灵活性 |
| **P3（扩展）** | 命令行工具 + CI/CD 集成 | 提升易用性 |
| **P4（未来）** | 可视化编辑器 + 条件分支 | 高级功能 |

---

## 十、总结

### 10.1 核心价值

本方案实现了 **"LLM 执行一次，无限复用"** 的目标：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DSL 复用方案核心价值                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ✅ 零 LLM 依赖     - 执行阶段完全不调用 LLM，零 Token 消耗                │
│   ✅ 确定性执行     - 相同 DSL + 相同页面 = 相同结果                        │
│   ✅ 毫秒级启动     - 无 LLM 推理延迟，启动时间 <100ms                      │
│   ✅ 完全自包含     - DSL 文件包含执行所需的全部信息                         │
│   ✅ 人类可读       - JSON 格式，可手动编辑和调试                           │
│   ✅ 架构兼容       - 复用现有执行链路，无需修改核心代码                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 技术保障

**严格保证执行成功**的关键技术：

1. **多级回退选择器**：CSS → XPath → Text → Attributes → Index
2. **CDP 直接查询**：使用 `Runtime.evaluate()` 在浏览器中直接定位元素
3. **复用现有执行链路**：直接调用 [`Registry.execute_action()`](../browser_use/tools/registry/service.py:327)
4. **变量系统**：支持环境变量、敏感数据、命令行参数、配置文件
5. **断言验证**：执行后验证 URL、标题、元素状态
6. **元素快照**：保存执行时的元素状态，便于调试

### 10.3 实现路径

```
Phase 1: 核心功能（P0 + P1）
├─ DSL Schema 定义
├─ DSLExecutor 基础执行
├─ 多级选择器解析
└─ DSLGenerator.from_agent_history()

Phase 2: 增强功能（P2 + P3）
├─ 变量系统完善
├─ 断言验证
├─ 命令行工具
└─ 批量执行支持

Phase 3: 高级功能（P4）
├─ 条件分支 if/else
├─ 循环结构 while/for
└─ 可视化编辑器
```

### 10.4 目录结构

```
browser_use/dsl/
├── __init__.py
├── executor.py       # DSLExecutor 执行器
├── generator.py      # DSLGenerator 生成器
├── schema.py         # DSL Schema 定义
├── selectors.py      # 选择器解析逻辑
├── variables.py      # 变量系统
└── cli.py            # 命令行工具
```

DSL 协议与现有 browser-use 架构**完全兼容**，无需修改核心代码，只需新增 `browser_use/dsl/` 模块

DSL 协议与现有 browser-use 架构**完全兼容**，无需修改核心代码，只需新增 `browser_use/dsl/` 模块。