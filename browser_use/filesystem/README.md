# Filesystem 模块

该模块提供文件系统操作功能，允许 Agent 读取、写入和管理本地文件。

## 目录结构

```
filesystem/
├── file_system.py      # 文件系统服务主类
└── __init__.py         # 模块导出
```

## 核心文件详解

### [`file_system.py`](file_system.py)
**核心类：** `FileSystem`
- **作用：** 提供安全的文件系统访问接口，限制 Agent 只能访问指定的文件路径。
- **功能：**
  - 读取文件内容
  - 写入文件内容
  - 列出目录内容
  - 检查文件是否存在
  - 获取文件信息（大小、修改时间等）

## 安全机制

为了防止 Agent 意外或恶意访问系统文件，FileSystem 模块实现了以下安全机制：

1. **路径限制：** Agent 只能访问通过 `available_file_paths` 参数明确指定的文件和目录。
2. **路径验证：** 所有文件操作都会验证路径是否在允许的范围内。
3. **权限控制：** 可以配置只读或读写权限。

## 使用方式

### 在 Agent 中启用文件系统

```python
from browser_use import Agent

agent = Agent(
    task="读取 data.txt 文件并总结内容",
    available_file_paths=["./data.txt", "./output/"]  # 指定允许访问的文件路径
)
```

### 工具集成

FileSystem 与以下工具集成：
- `read_file`: 读取指定文件的内容
- `write_file`: 将内容写入指定文件
- `replace_file`: 在文件中查找并替换文本

## 调用链路

1. **初始化：** Agent 初始化时，如果指定了 `available_file_paths`，会创建 FileSystem 实例。
2. **工具调用：** 当 Agent 调用 `read_file` 或 `write_file` 工具时，工具函数会访问 FileSystem 实例。
3. **路径验证：** FileSystem 验证请求的文件路径是否在允许的范围内。
4. **文件操作：** 如果路径有效，执行实际的文件读写操作。
5. **结果返回：** 将操作结果返回给工具，再传递给 Agent。

## 与其他模块的关系

- **与 Agent：** Agent 通过 `available_file_paths` 参数配置 FileSystem
- **与 Tools：** 文件操作工具（read_file, write_file）依赖 FileSystem 实现
- **与 Controller：** 作为工具注册的一部分，FileSystem 功能通过 Controller 提供给 Agent