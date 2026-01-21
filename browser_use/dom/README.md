# DOM 模块

该模块负责解析复杂的 HTML DOM 树，并将其转化为 LLM 能够理解的简化结构（通常是 Markdown 或简化的 JSON）。

## 目录结构

```
dom/
├── service.py          # DOM 解析主服务
├── views.py            # DOM 节点和状态模型
├── utils.py            # DOM 处理工具函数
├── markdown_extractor.py # 将 DOM 转换为 Markdown 格式
├── enhanced_snapshot.py  # 增强型页面快照逻辑
└── serializer/         # 各种序列化处理器（HTML, Paint Order 等）
```

## 核心文件详解

### [`service.py`](service.py)
**核心类：** `DomService`。
- **作用：** 核心引擎，通过执行 JavaScript 脚本从浏览器页面中提取交互式元素。
- **逻辑：** 过滤不可见元素、计算元素位置、构建元素树、为每个元素分配唯一的 `index`（供 Agent 点击使用）。

### [`views.py`](views.py)
**核心模型：** `DOMElementNode`。
- **作用：** 表示一个简化的 DOM 节点，包含标签名、属性、文本内容、边界框坐标等。

### [`markdown_extractor.py`](markdown_extractor.py)
**核心功能：** 将 `DOMElementNode` 树转换为结构化的 Markdown 文本。
- **用途：** 作为 LLM 的输入，使其能理解页面结构。

### [`serializer/`](serializer/)
**作用：** 提供多种序列化策略，将 DOM 转换为不同格式。
- **关键文件：**
  - `clickable_elements.py`: 生成带索引的可点击元素列表。
  - `html_serializer.py`: 生成简化版 HTML。
  - `paint_order.py`: 按绘制顺序过滤元素，移除被遮挡的元素。

## 调用链路与逻辑关系

1. **触发：** `BrowserSession.get_state()` 被调用时，会实例化 `DomService`。
2. **数据获取：** `DomService` 通过 `page.evaluate()` 执行 JavaScript 脚本，从浏览器获取原始 DOM 信息。
3. **处理：** 使用 `serializer` 中的逻辑对原始 DOM 进行过滤、排序和结构化。
4. **输出：** 生成 `DOMElementNode` 树，并通过 `markdown_extractor` 转换为 Markdown。
5. **传递：** 最终的结构化数据被包装在 `BrowserStateSummary` 中，传递给 `Agent`。