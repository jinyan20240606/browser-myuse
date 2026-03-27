# Browser-Use 项目快速启动指南

本文档记录了启动 browser-use 项目的完整步骤，帮助新成员快速配置开发环境。
## 环境要求

- Python 3.11 或更高版本
- macOS, Linux 或 Windows
- 网络连接（用于下载依赖和 Chromium）
- [uv](https://github.com/astral-sh/uv) 包管理器

## 快速开始

如果你熟悉 Python 开发，可以使用以下命令快速启动：

```bash
# 1. 克隆项目（如果还没有）
git clone https://github.com/browser-use/browser-use
cd browser-use

# 2. 创建并激活虚拟环境
uv venv --python 3.11
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
uv sync

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填写 BROWSER_USE_API_KEY
# 获取 API key: https://cloud.browser-use.com/new-api-key

# 5. 运行示例
uv run examples/simple.py
```