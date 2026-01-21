# Telemetry 模块

Telemetry 模块负责收集匿名使用数据，帮助改进 Browser-Use。所有遥测数据都是匿名的，并且可以完全禁用。

- 产品遥测服务（收集 Agent 运行数据、用户行为、错误日志等）

## 目录结构

```
telemetry/
├── service.py          # 遥测服务实现
├── views.py            # 遥测数据模型
├── __init__.py         # 模块导出
└── README.md           # 本文件
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `ProductTelemetry`
- **作用：** 管理遥测数据的收集和发送
- **功能：**
  - 收集匿名使用统计
  - 批量发送数据到服务器
  - 处理错误和重试

### [`views.py`](views.py)
**核心模型：**
- `AgentTelemetryEvent`: Agent 事件数据
- `TelemetryData`: 遥测数据结构

## 收集的数据

遥测系统收集以下**匿名**数据：
- Agent 执行次数
- 平均执行时间
- 使用的 LLM 类型
- 操作系统和 Python 版本
- 错误统计（不包含敏感信息）

**不会收集：**
- 任务内容
- 网页内容
- 个人身份信息
- API 密钥

## 禁用遥测

您可以通过以下方式禁用遥测：

### 环境变量
```bash
export ANONYMIZED_TELEMETRY=false
```

### Python 代码
```python
import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
```

## 隐私保护

- 所有数据都经过匿名处理
- 使用 PostHog 进行数据收集和分析
- 遵循 GDPR 等隐私法规
- 源代码公开，可审查收集的内容

## 与其他模块的关系

- **与 Agent 模块：** Agent 在关键事件时触发遥测
- **与 Browser 模块：** 收集浏览器会话统计
- **与 LLM 模块：** 记录 LLM 使用情况

## 调用链路

```
Agent 事件 → Telemetry 收集 → 批量处理 → 异步发送 → PostHog
```

## 性能影响

遥测系统设计为零性能影响：
- 异步收集和发送
- 不阻塞主要操作
- 失败时静默处理