# Skills 模块

Skills 模块提供了预定义的任务技能集合，使 Agent 能够执行更复杂的任务。技能是封装好的工具组合，针对特定场景优化。

## 目录结构

```
skills/
├── service.py          # 技能服务实现
├── views.py            # 技能数据模型
├── registry.py         # 技能注册表
├── __init__.py         # 模块导出
└── README.md           # 本文件
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `SkillService`
- **作用：** 管理技能的加载、注册和执行
- **功能：**
  - 从技能库加载技能
  - 注册技能为可用工具
  - 执行技能定义的操作序列

### [`views.py`](views.py)
**核心模型：**
- `Skill`: 技能定义模型
- `SkillParameter`: 技能参数定义
- `SkillResult`: 技能执行结果

### [`registry.py`](registry.py)
**作用：** 维护可用技能的注册表
- **功能：**
  - 注册内置技能
  - 支持自定义技能注册
  - 提供技能发现机制

## 内置技能

### 数据提取技能
- `extract_table`: 从网页提取表格数据
- `extract_list`: 提取列表项
- `extract_key_value`: 提取键值对信息

### 表单处理技能
- `fill_form`: 自动填写表单
- `submit_form`: 提交表单

### 文件处理技能
- `download_file`: 下载文件
- `upload_file`: 上传文件

## 使用示例

```python
from browser_use import Agent

# 使用特定技能
agent = Agent(
    task="从网站提取产品价格信息",
    skills=["extract_table", "extract_key_value"]
)

result = await agent.run()
```

## 自定义技能

```python
from browser_use.skills import Skill

# 定义自定义技能
custom_skill = Skill(
    name="my_custom_skill",
    description="执行自定义操作",
    parameters=[...],
    implementation=my_function
)

# 注册技能
agent = Agent(
    task="使用自定义技能",
    skills=[custom_skill]
)
```

## 与其他模块的关系

- **与 Tools 模块：** 技能最终通过 Tools 模块注册为可用工具
- **与 Agent 模块：** Agent 通过技能服务加载和使用技能
- **与 LLM 模块：** LLM 根据技能描述决定是否调用技能

## 调用链路

```
Agent 决策 → 技能调用 → SkillService → 工具执行 → 返回结果
