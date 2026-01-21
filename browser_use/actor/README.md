# Actor 模块

Actor 模块提供了底层的浏览器自动化操作接口，是 Browser-Use 的基础构建块。它直接与 Playwright 交互，执行具体的浏览器操作。

## 目录结构

```
actor/
├── page.py             # 页面级操作（导航、执行脚本等）
├── element.py          # 元素级操作（点击、输入等）
├── mouse.py            # 鼠标操作（移动、悬停等）
├── utils.py            # 工具函数
├── README.md           # 本文件
└── playground/         # 演练场示例
```

## 核心文件详解

### [`page.py`](page.py)
**核心类：** `PageActions`
- **作用：** 提供页面级别的操作接口
- **功能：**
  - 导航到指定 URL
  - 页面刷新、后退、前进
  - 执行 JavaScript 脚本
  - 获取页面标题、URL 等信息
  - 页面截图

### [`element.py`](element.py)
**核心类：** `ElementActions`
- **作用：** 提供元素级别的操作接口
- **功能：**
  - 点击元素
  - 在元素中输入文本
  - 获取元素文本内容
  - 检查元素是否存在、可见性等状态
  - 选择下拉框选项

### [`mouse.py`](mouse.py)
**核心类：** `MouseActions`
- **作用：** 提供鼠标级别的精细操作
- **功能：**
  - 鼠标移动到指定坐标
  - 鼠标悬停
  - 鼠标点击（左键、右键、中键）
  - 鼠标拖拽

### [`utils.py`](utils.py)
**作用：** 提供 Actor 模块的工具函数
- **功能：**
  - 坐标转换
  - 元素定位辅助函数
  - 等待机制

## 与其他模块的关系

1. **与 Browser 模块：** Actor 是 Browser 模块的底层实现，BrowserSession 通过 Actor 执行具体的浏览器操作。
2. **与 DOM 模块：** DOM 模块解析出的元素信息会被 Actor 用来执行具体操作。
3. **与 Tools 模块：** Tools 模块中的工具函数最终会调用 Actor 提供的接口来执行操作。

## 调用链路

```
Agent 决策 → Tools 执行 → BrowserSession 调用 → Actor 操作 → Playwright 执行
```

## 使用示例

```python
from browser_use import Browser
from browser_use.actor.page import PageActions

browser = Browser()
page_actions = PageActions(browser.page)
await page_actions.navigate("https://example.com")
