# MCP (Model Context Protocol) 模块

MCP 模块实现了 Model Context Protocol，允许 Browser-Use 作为 MCP 服务器运行，为其他应用提供浏览器自动化能力。

## 目录结构

```
mcp/
├── __init__.py         # 模块导出
├── __main__.py         # MCP 服务器入口
├── client.py           # MCP 客户端实现
├── controller.py       # MCP 控制器
└── README.md           # 本文件
```

## 核心文件详解

### [`__main__.py`](__main__.py)
**作用：** MCP 服务器的入口点
- 启动 MCP 服务器
- 注册可用的工具和资源
- 处理客户端请求

### [`client.py`](client.py)
**核心类：** `MCPClient`
- **作用：** 实现 MCP 客户端，可以连接到其他 MCP 服务器
- **功能：**
  - 发现可用工具
  - 调用远程工具
  - 处理响应

### [`controller.py`](controller.py)
**核心类：** `MCPController`
- **作用：** 管理 MCP 服务的工具注册和调用
- **功能：**
  - 将 Browser-Use 的工具暴露为 MCP 工具
  - 处理工具调用请求
  - 管理会话状态

## MCP 协议概述

Model Context Protocol 是一个标准化的协议，用于 AI 模型与外部工具之间的通信。Browser-Use 通过 MCP 可以：

1. **作为服务器：** 为其他 AI 应用提供浏览器自动化能力
2. **作为客户端：** 调用其他 MCP 服务提供的工具

## 使用场景

### 作为 MCP 服务器

```bash
# 启动 Browser-Use MCP 服务器
python -m browser_use.mcp
```

其他应用可以通过 MCP 协议调用 Browser-Use 的功能。

### 作为 MCP 客户端

```python
from browser_use.mcp import MCPClient

client = MCPClient("mcp://other-service")
tools = await client.list_tools()
result = await client.call_tool("tool_name", params)
```

## 与其他模块的关系

- **与 Tools 模块：** MCP 将 Tools 模块的工具通过标准协议暴露
- **与 Agent 模块：** MCP 可以创建和管理 Agent 实例
- **与 Browser 模块：** 通过 MCP 管理浏览器会话

## 调用链路

```
MCP 客户端 → MCP 协议 → MCP 服务器 → Browser-Use Tools → 执行操作