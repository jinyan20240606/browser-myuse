# LLM 模块

该模块提供了对不同大语言模型（LLM）提供商的统一接口封装。

## 目录结构

```
llm/
├── base.py             # LLM 基类定义
├── models.py           # 模型工厂和注册表
├── schema.py           # 通用数据结构
├── messages.py         # 消息类型定义
├── exceptions.py       # LLM 相关异常
├── views.py            # 视图和响应模型
├── openai/             # OpenAI 接口实现
├── anthropic/          # Anthropic (Claude) 接口实现
├── google/             # Google (Gemini) 接口实现
├── azure/              # Azure OpenAI 接口实现
├── ollama/             # Ollama (本地模型) 接口实现
├── browser_use/        # Browser-Use 专用 LLM
├── groq/               # Groq 接口实现
├── mistral/            # Mistral 接口实现
├── deepseek/           # DeepSeek 接口实现
└── ...                 # 其他提供商
```

## 核心文件详解

### [`base.py`](base.py)
**核心类：** `BaseChatModel`。
- **作用：** 定义所有 LLM 类必须实现的抽象基类。确保所有模型提供统一的接口。
- **关键方法：** `chat()`, `supports_vision()`, `supports_tools()`, `supports_structured_output()`。

### [`models.py`](models.py)
**核心功能：** 模型工厂和注册表。
- **作用：** 提供 `get_llm_by_name()` 等工厂方法，根据名称创建特定的 LLM 实例。

### [`messages.py`](messages.py)
**核心类：** `BaseMessage`, `SystemMessage`, `UserMessage`, `AssistantMessage`。
- **作用：** 定义与 LLM 交互的消息格式，支持文本、图像等多种内容类型。

### [`schema.py`](schema.py)
**内容：** 定义 LLM 相关的数据结构，如工具调用格式、响应格式等。

## 各提供商实现

每个 LLM 提供商的子目录通常包含：
- `chat.py`: 实现 `BaseChatModel` 的具体类（如 `ChatOpenAI`）。
- `serializer.py`: 负责将通用消息格式转换为该提供商的特定 API 格式。

## 调用链路与逻辑关系

1. **初始化：** `Agent` 初始化时接收一个 LLM 实例（如 `ChatOpenAI()`）。
2. **消息构建：** `MessageManager` 构建对话历史，包括系统提示、用户任务、之前的交互记录等。
3. **API 调用：** `Agent` 调用 `llm.chat(messages)` 发送消息到 LLM。
4. **格式转换：** 各 LLM 实现类使用其 `serializer` 将通用消息格式转换为特定 API 格式。
5. **响应解析：** LLM 响应被解析为 `LLMResponse` 对象，包含文本内容和工具调用信息。
6. **错误处理：** 处理 API 错误、速率限制、超时等异常情况。

## 扩展新模型

要添加新的 LLM 提供商：
1. 在 `llm/` 下创建新目录（如 `llm/newprovider/`）。
2. 实现 `ChatNewProvider` 类，继承自 `BaseChatModel`。
3. 实现必要的序列化器。
4. 在 `models.py` 中注册新模型。
