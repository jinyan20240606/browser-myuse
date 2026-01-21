# Integrations 模块

Integrations 模块提供了与第三方服务的集成功能，使 Agent 能够与外部服务进行交互。

## 目录结构

```
integrations/
├── gmail/              # Gmail 集成
│   ├── service.py      # Gmail 服务实现
│   ├── actions.py      # Gmail 相关动作
│   └── __init__.py
└── README.md           # 本文件
```

## 核心集成

### Gmail 集成

**功能：**
- 读取邮件
- 发送邮件
- 搜索邮件
- 管理标签和文件夹

**使用示例：**

```python
from browser_use import Agent
from browser_use.integrations.gmail import GmailIntegration

# 创建 Gmail 集成实例
gmail = GmailIntegration(
    credentials_path="path/to/credentials.json"
)

# 创建带有 Gmail 集成的 Agent
agent = Agent(
    task="读取最新的 5 封邮件并总结",
    tools=gmail.get_tools()
)
```

## 扩展新集成

要添加新的集成：

1. 在 `integrations/` 下创建新目录（如 `integrations/slack/`）
2. 实现 `service.py`，提供集成服务类
3. 实现 `actions.py`，定义可用的工具动作
4. 在 `__init__.py` 中导出主要类

## 与其他模块的关系

- **与 Tools 模块：** 集成的动作通过 Tools 模块注册，供 Agent 使用
- **与 Agent 模块：** Agent 通过调用集成提供的工具来与外部服务交互
- **与 LLM 模块：** LLM 决定何时调用集成的工具

## 调用链路

```
Agent 决策 → Tools 调用 → Integration Service → 外部 API → 返回结果
```

## 安全考虑

- 敏感信息（如 API 密钥）应通过环境变量或配置文件管理
- 遵循各服务的 API 使用限制和最佳实践
- 实现适当的错误处理和重试机制