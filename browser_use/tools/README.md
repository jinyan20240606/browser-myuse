# Tools 模块

该模块管理 Agent 可以使用的所有工具（动作），是 LLM 决策与浏览器操作之间的桥梁。

## 目录结构

```
tools/
├── service.py          # Tools 服务主类
├── registry/           # 工具注册表相关
│   ├── views.py        # 工具模型定义
│   └── ...
└── ...
```

## 核心概念

### Tools Service
**核心类：** `Tools`
- **作用：** 管理所有可用工具的注册、验证和执行。
- **功能：** 
  - 注册默认工具（点击、输入、滚动等）
  - 支持自定义工具注册
  - 处理工具调用的参数验证和错误处理

### 默认工具集

Browser-Use 提供的默认工具包括：

#### 导航工具
- `navigate`: 导航到指定 URL
- `go_back`: 返回上一页
- `search`: 执行搜索（支持 Google、DuckDuckGo 等）

#### 交互工具
- `click`: 点击指定索引的元素
- `input`: 在表单字段中输入文本
- `select_dropdown`: 选择下拉菜单选项
- `scroll`: 上下滚动页面
- `send_keys`: 发送键盘按键（Enter、Escape 等）

#### 内容提取
- `extract`: 使用 LLM 从页面提取信息
- `screenshot`: 请求截图

#### 文件操作
- `write_file`: 写入文件
- `read_file`: 读取文件

#### 任务控制
- `done`: 标记任务完成
- `wait`: 等待指定秒数

## 自定义工具

用户可以通过装饰器添加自定义工具：

```python
from browser_use import Tools

tools = Tools()

@tools.action("获取当前时间")
def get_current_time():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 使用自定义工具的 Agent
agent = Agent(task="...", tools=tools)
```

## 调用链路

1. **注册阶段：**
   - Agent 初始化时，创建 `Tools` 实例
   - 自动注册所有默认工具
   - 用户可添加自定义工具

2. **执行阶段：**
   - LLM 输出工具调用请求（如 `{"name": "click", "parameters": {"index": 5}}`）
   - `Tools` 服务解析请求，查找对应的工具函数
   - 验证参数并执行工具
   - 返回执行结果（成功/失败/返回值）

3. **反馈阶段：**
   - 工具执行结果被添加到对话历史
   - Agent 根据结果决定下一步动作

## 工具执行流程

```
LLM 决策 → 工具调用请求 → Tools 解析 → 参数验证 → 
执行工具 → 返回结果 → 更新状态 → 下一轮决策
```

## 与其他模块的关系

- **与 Agent：** Agent 通过 Tools 执行具体操作
- **与 Browser：** 大部分工具最终调用 BrowserSession 的方法
- **与 DOM：** 某些工具（如 click）依赖 DOM 模块提供的元素索引
- **与 LLM：** 工具描述被包含在系统提示中，供 LLM 决策使用