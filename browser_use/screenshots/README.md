# Screenshots 模块

Screenshots 模块负责管理和存储 Agent 执行过程中的截图，提供截图的保存、检索和优化功能。

## 目录结构

```
screenshots/
├── service.py          # 截图服务实现
├── __init__.py         # 模块导出
└── README.md           # 本文件
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `ScreenshotService`
- **作用：** 管理截图的生成、存储和检索
- **功能：**
  - 截图生成和保存
  - 截图格式转换和压缩
  - 按步骤组织截图
  - 清理过期截图

## 截图存储策略

### 文件组织
截图按以下结构组织：
```
agent_directory/
└── screenshots/
    ├── step_001.png
    ├── step_002.png
    └── ...
```

### 命名规则
- 按步骤顺序命名：`step_XXX.png`
- 包含时间戳信息
- 支持自定义命名

## 使用场景

### 调试和分析
- 查看 Agent 每一步的视觉状态
- 分析执行失败的原因
- 优化任务执行策略

### 生成报告
- 创建执行过程的可视化报告
- 生成 GIF 动画展示执行流程

### 质量保证
- 验证 Agent 的执行结果
- 比较不同执行的差异

## 与其他模块的关系

- **与 Browser 模块：** 调用 Browser 的截图功能
- **与 Agent 模块：** Agent 在每步执行后保存截图
- **与 GIF 模块：** 提供截图供 GIF 生成使用

## 调用链路

```
Agent 执行步骤 → Browser 截图 → ScreenshotService 保存 → 文件系统
```

## 性能优化

- **懒加载：** 只在需要时生成截图
- **压缩：** 自动压缩 PNG 图片以节省空间
- **清理：** 自动清理超过保留期限的截图
- **并行处理：** 异步保存截图，不阻塞主流程

## 配置选项

- `screenshot_enabled`: 是否启用截图功能
- `screenshot_quality`: 截图质量 (1-100)
- `screenshot_format`: 截图格式 (png/jpeg)
- `max_screenshots`: 最大保留截图数量