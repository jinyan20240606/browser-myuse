# Browser-Use 项目快速启动指南

本文档记录了启动 browser-use 项目的完整步骤，帮助新成员快速配置开发环境。

## 📋 目录

- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [详细步骤](#详细步骤)
- [运行示例](#运行示例)
- [常见问题](#常见问题)

## 环境要求

- Python 3.11 或更高版本
- macOS, Linux 或 Windows
- 网络连接（用于下载依赖和 Chromium）

## 快速开始

如果你熟悉 Python 开发，可以使用以下命令快速启动：

```bash
# 1. 克隆项目（如果还没有）
git clone https://github.com/browser-use/browser-use
cd browser-use

# 2. 创建并激活虚拟环境（如果不存在）
uv venv --python 3.11
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv sync

# 3. 安装 Chromium 浏览器
uvx browser-use install

# 4. 配置环境变量（复制 .env.example 到 .env 并填写 API key）
cp .env.example .env

# 5. 运行示例
python examples/simple.py
```