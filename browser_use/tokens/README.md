# Tokens 模块

Tokens 模块负责跟踪和管理 LLM API 调用的 Token 使用情况，帮助用户监控和控制成本。

## 目录结构

```
tokens/
├── service.py          # Token 成本服务实现
├── __init__.py         # 模块导出
└── README.md           # 本文件
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `TokenCost`
- **作用：** 跟踪 Token 使用和计算成本
- **功能：**
  - 记录输入和输出 Token 数量
  - 根据模型定价计算成本
  - 提供成本统计和报告

## 支持的模型

Token 成本服务支持以下模型提供商的成本计算：
- OpenAI (GPT-3.5, GPT-4 系列)
- Anthropic (Claude 系列)
- Google (Gemini 系列)
- 其他兼容模型

## 使用方式

### 启用 Token 跟踪

```python
from browser_use import Agent

# 在 Agent 中启用 Token 成本计算
agent = Agent(
    task="...",
    calculate_cost=True  # 启用成本计算
)

history = await agent.run()
print(f"Total cost: ${history.usage.total_cost}")
```

### 手动跟踪

```python
from browser_use.tokens import TokenCost

# 创建 Token 成本跟踪器
token_cost = TokenCost(include_cost=True)

# 注册使用的模型
token_cost.register_llm(your_llm_instance)

# 获取成本信息
cost_info = token_cost.get_cost_info()
```

## 成本计算

成本计算基于各模型提供商的官方定价：
- 输入 Token 和输出 Token 分别计费
- 不同模型有不同的单价
- 支持批量折扣和企业定价

## 与其他模块的关系

- **与 LLM 模块：** 从 LLM 响应中提取 Token 使用信息
- **与 Agent 模块：** Agent 使用 TokenCost 跟踪整个任务的成本
- **与 History 模块：** 成本信息存储在 AgentHistory 中

## 调用链路

```
LLM 调用 → 提取 Token 信息 → TokenCost 记录 → 累计成本 → 返回统计
```

## 性能影响

Token 跟踪对性能影响极小：
- 仅在启用时工作
- 异步记录和计算
- 不影响 LLM 调用性能