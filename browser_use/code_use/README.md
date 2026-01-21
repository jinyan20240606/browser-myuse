# Code-Use 模块

Code-Use 模块提供了一个类似 Jupyter Notebook 的代码执行环境，允许 Agent 执行 Python 代码并获取结果。

## 目录结构

```
code_use/
├── service.py          # CodeAgent 核心实现
├── views.py            # 数据模型和视图
├── utils.py            # 工具函数
├── formatting.py       # 代码格式化
├── namespace.py        # 命名空间管理
├── notebook_export.py  # Notebook 导出功能
├── system_prompt.md    # 系统提示词
└── README.md           # 本文件
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `CodeAgent`
- **作用：** 提供代码执行环境，类似于 Jupyter Notebook
- **功能：**
  - 执行 Python 代码
  - 管理变量状态
  - 捕获输出和错误
  - 支持多步代码执行

### [`views.py`](views.py)
**核心模型：**
- `CodeExecutionResult`: 代码执行结果
- `CodeAgentState`: CodeAgent 状态

### [`namespace.py`](namespace.py)
**作用：** 管理代码执行的命名空间
- **功能：**
  - 保存和恢复变量状态
  - 提供安全的执行环境

### [`notebook_export.py`](notebook_export.py)
**作用：** 将代码执行历史导出为 Jupyter Notebook 格式

## 使用场景

Code-Use 适用于以下场景：
- 数据分析和处理
- 代码生成和测试
- 数学计算
- 文件处理
- API 调用和数据处理

## 使用示例

```python
from browser_use import CodeAgent

agent = CodeAgent(
    task="计算斐波那契数列的前 10 项"
)

result = await agent.run()
print(result)
```

## 与 Agent 的区别

| 特性 | Agent | CodeAgent |
|------|-------|-----------|
| 主要用途 | 浏览器自动化 | 代码执行 |
| 交互对象 | 网页 | Python 解释器 |
| 输出 | 浏览器操作结果 | 代码执行结果 |
| 视觉能力 | 支持 | 不需要 |

## 调用链路

1. **初始化：** 创建 CodeAgent 实例
2. **代码生成：** LLM 生成 Python 代码
3. **代码执行：** 在隔离的命名空间中执行代码
4. **结果捕获：** 捕获输出、错误和变量状态
5. **状态更新：** 更新命名空间，供后续执行使用
