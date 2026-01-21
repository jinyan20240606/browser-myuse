# Sandbox 模块

Sandbox 模块提供了安全隔离的执行环境，使 Agent 可以在受控的沙箱中运行，特别是在云端部署或多租户环境中。

## 目录结构

```
sandbox/
├── __init__.py         # 模块导出
└── service.py          # 沙箱服务实现
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `Sandbox`
- **作用：** 提供沙箱环境管理
- **功能：**
  - 创建隔离的浏览器会话
  - 管理资源配额
  - 限制网络访问
  - 监控执行状态

## 核心概念

### 隔离执行
沙箱确保每个 Agent 实例在独立的环境中运行，互不干扰，并且与宿主系统隔离。

### 资源限制
可以配置沙箱的资源使用限制，如内存、CPU、网络带宽等，防止单个任务耗尽系统资源。

### 安全控制
限制 Agent 可以访问的文件路径、网络域等，防止恶意操作。

## 使用场景

- **SaaS 服务：** 为多个用户提供隔离的 Browser-Use 服务
- **安全执行：** 运行不受信任的任务或代码
- **开发测试：** 在干净的环境中测试 Agent 行为

## 使用示例

```python
from browser_use.sandbox import Sandbox

# 使用沙箱装饰器
@Sandbox(
    cloud_profile_id='your-profile-id',
    cloud_proxy_country_code='us'
)
async def task(browser):
    agent = Agent(task="...", browser=browser)
    await agent.run()
```

## 与其他模块的关系

- **与 Browser 模块：** 沙箱管理 BrowserSession 的创建和生命周期
- **与 Agent 模块：** Agent 在沙箱提供的环境中运行
- **与 LLM 模块：** LLM 调用不受沙箱限制，但其影响范围受限于沙箱

## 调用链路

```
用户请求 → Sandbox 初始化 → 创建隔离环境 → 启动 BrowserSession → 运行 Agent