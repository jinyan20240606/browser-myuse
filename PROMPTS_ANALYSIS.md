# `browser_use/agent/prompts.py` 提示词类型分析

该文件是 Browser Use 框架中负责构建和管理提示词（Prompts）的核心模块，包含 **2 个核心类** 和 **4 个辅助函数**。

## 1. [`SystemPrompt`](browser_use/agent/prompts.py:27) 类

**用途**：生成发送给 LLM 的**系统消息（角色设定）**。

**使用场景**：Agent 初始化时，用于定义 LLM 的身份、能力边界和行为准则。

**核心逻辑**：
通过 [`_load_prompt_template()`](browser_use/agent/prompts.py:59) 方法，根据当前配置（模型类型、是否开启 Flash 模式、是否开启 Thinking 模式等）动态加载对应的 Markdown 模板文件。

**模板选择逻辑表**：

| 条件组合 | 对应模板文件 | 说明 |
|----------|--------------|------|
| **Browser Use 模型** + Flash 模式 | `system_prompt_browser_use_flash.md` | 针对 Browser Use 微调模型的极速模式 |
| **Browser Use 模型** + Thinking | `system_prompt_browser_use.md` | 针对 Browser Use 微调模型的标准思考模式 |
| **Browser Use 模型** (无 Thinking) | `system_prompt_browser_use_no_thinking.md` | 针对 Browser Use 微调模型的直接输出模式 |
| **Anthropic 4.5** + Flash | `system_prompt_anthropic_flash.md` | 针对 Claude 4.5 系列的长 Context 缓存优化 (Flash) |
| **Anthropic 4.5** + Thinking | `system_prompt_anthropic.md` | 针对 Claude 4.5 系列的长 Context 缓存优化 (Thinking) |
| Anthropic + Flash | `system_prompt_flash_anthropic.md` | 针对其他 Claude 模型的 Flash 模式 |
| Flash 模式 (通用) | `system_prompt_flash.md` | 通用模型的极速模式 |
| Thinking 模式 (通用) | `system_prompt.md` | 通用模型的思维链模式 |
| 无 Thinking (通用) | `system_prompt_no_thinking.md` | 通用模型的直接输出模式 |

---

## 2. [`AgentMessagePrompt`](browser_use/agent/prompts.py:108) 类

**用途**：构建 Agent 运行每一步发送给 LLM 的**用户消息（当前状态）**。

**使用场景**：每次调用 LLM 进行决策前，告诉它“现在发生了什么”、“看到了什么”、“任务进度如何”。

**核心组件方法**：

*   **[`_get_browser_state_description()`](browser_use/agent/prompts.py:222)**：生成浏览器状态描述
    *   **页面统计**：链接数、输入框数、图片数、iframe 数、Shadow DOM 状态等。
    *   **标签页信息**：所有标签页列表、当前选中的标签页、当前 URL。
    *   **DOM 树**：最核心的部分，页面的可交互元素列表（简化版 DOM）。
    *   **其他**：滚动位置、最近的浏览器事件、自动关闭的弹窗信息、PDF 查看器警告等。

*   **[`_get_agent_state_description()`](browser_use/agent/prompts.py:323)**：生成 Agent 自身状态描述
    *   **`<user_request>`**：用户的原始任务目标。
    *   **`<file_system>`**：文件系统状态描述。
    *   **`<todo_contents>`**：Todo 列表的内容（用于复杂任务规划）。
    *   **`<step_info>`**：当前步骤数 / 最大步骤数。
    *   **`<sensitive_data>`**：敏感数据提示（如果有）。

*   **[`get_user_message(use_vision)`](browser_use/agent/prompts.py:385)**：组合最终消息
    *   将上述历史记录 (`agent_history`)、Agent 状态 (`agent_state`)、浏览器状态 (`browser_state`) 拼接。
    *   如果启用了视觉功能 (`use_vision=True`)，会将当前截图 (`screenshots`) 和之前步骤的截图作为图片输入发送给多模态模型。
    *   还会处理通过 `read_file` 动作读取到的图片文件。

---

## 3. 辅助功能函数

这些函数主要服务于特定的子功能场景。

### 任务回放 (Rerun) 相关

*   **[`get_rerun_summary_prompt(...)`](browser_use/agent/prompts.py:489)**
    *   **用途**：生成用于总结任务回放结果的提示词。
    *   **场景**：当一个 Rerun 任务执行完毕后，调用此 Prompt 让 LLM 根据截图和执行统计（成功/失败步数）来判断任务是否最终成功。
    
*   **[`get_rerun_summary_message(...)`](browser_use/agent/prompts.py:510)**
    *   **用途**：构建回放总结的 UserMessage 对象。
    *   **场景**：将上述提示词文本和（可选的）最终截图包装成发给 LLM 的消息结构。

### 数据提取 (AI Step) 相关

*   **[`get_ai_step_system_prompt()`](browser_use/agent/prompts.py:536)**
    *   **用途**：定义“网页数据提取专家”的 System Prompt。
    *   **场景**：当 Agent 执行 `extract` 动作或进行纯数据提取子任务时使用。
    *   **内容**：设定 LLM 为提取专家，强调“只从网页提取，不捏造数据，格式要简洁”。

*   **[`get_ai_step_user_prompt(...)`](browser_use/agent/prompts.py:568)**
    *   **用途**：构建数据提取任务的 User Prompt。
    *   **场景**：配合上面的 System Prompt 使用。
    *   **结构**：
        ```xml
        <query>用户查询内容</query>
        <content_stats>内容统计摘要</content_stats>
        <webpage_content>清洗后的网页 Markdown 内容</webpage_content>
        ```

---

## 总结表

| 类型 | Python 对象 | 核心用途 | 关键使用时机 |
|------|------------|----------|--------------|
| **System Prompt** | `SystemPrompt` 类 | **设定身份**：定义 LLM 是谁，该怎么思考 | Agent 启动初始化时 |
| **User Prompt** | `AgentMessagePrompt` 类 | **描述现状**：告诉 LLM 现在看到了什么，要做什么 | Agent 每一步决策前 |
| **Rerun Prompt** | `get_rerun_summary_*` 函数 | **结果验证**：让 LLM 判断回放是否成功 | 任务回放结束后 |
| **Extraction Prompt** | `get_ai_step_*` 函数 | **数据提取**：专注从网页提取特定信息 | 执行提取类动作时 |