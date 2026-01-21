# Browser 模块

该模块负责管理底层的 Playwright 浏览器实例、会话生命周期以及页面级操作。

## 目录结构

```
browser/
├── session.py          # 浏览器会话管理 (BrowserSession)
├── profile.py          # 浏览器配置文件 (BrowserProfile)
├── views.py            # 状态和视图模型
├── video_recorder.py   # 视频录制功能
├── python_highlights.py # 页面元素高亮逻辑
├── session_manager.py  # 会话池管理
├── cloud/              # 云端浏览器对接逻辑
└── watchdogs/          # 浏览器状态监控（防止崩溃等）
```

## 核心文件详解

### [`session.py`](session.py)
**核心类：** `BrowserSession` (也通过别名 `Browser` 暴露)。
- **作用：** 它是与浏览器交互的顶层入口。负责启动 Playwright、管理上下文（Context）和页面（Page）。
- **功能：** 页面导航、获取页面状态、截图、执行脚本、资源清理。

### [`profile.py`](profile.py)
**核心类：** `BrowserProfile`。
- **作用：** 定义浏览器的启动配置，如是否无头（headless）、窗口大小、代理设置、用户数据目录等。

### [`views.py`](views.py)
**内容：** 定义了 `BrowserStateSummary` 等数据结构，用于向 Agent 传递当前页面的结构化信息（如 URL、标题、截图、DOM 树摘要）。

## 调用链路与逻辑关系

1. **初始化：** `Agent` 初始化时会创建一个 `BrowserSession` 实例。
2. **启动：** 当调用 `agent.run()` 时，`BrowserSession` 会调用 Playwright 启动浏览器进程。
3. **状态获取：** 在每个 Agent 步骤中，Agent 会通过 `BrowserSession.get_state()` 获取当前页面的视觉和 DOM 状态。
4. **交互执行：** Agent 决定动作后，通过 `controller` 调用 `BrowserSession` 的方法（如 `navigate`, `click`, `input`）来操作页面。
5. **清理：** 任务完成后，`BrowserSession` 负责关闭页面和浏览器连接。