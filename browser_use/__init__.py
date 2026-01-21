import os
from typing import TYPE_CHECKING

from browser_use.logging_config import setup_logging

# 只有在非 MCP 模式下或明确请求时才设置日志记录 (Only set up logging if not in MCP mode or if explicitly requested)
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
	from browser_use.config import CONFIG

	# Get log file paths from config/environment
	debug_log_file = getattr(CONFIG, 'BROWSER_USE_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'BROWSER_USE_INFO_LOG_FILE', None)

	# Set up logging with file handlers if specified
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

	logger = logging.getLogger('browser_use')

# Monkeypatch BaseSubprocessTransport.__del__ 以优雅地处理已关闭的事件循环
# 这修复了 Python asyncio 中的一个已知问题：当事件循环关闭时，子进程传输对象的析构函数可能会抛出错误
# Monkeypatch BaseSubprocessTransport.__del__ to handle closed event loops gracefully
from asyncio import base_subprocess

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""
	修补后的 __del__ 方法，用于处理已关闭的事件循环，
	避免抛出像 RuntimeError: Event loop is closed 这样的干扰性错误。
	"""
	"""Patched __del__ that handles closed event loops without throwing noisy red-herring errors like RuntimeError: Event loop is closed"""
	try:
		# Check if the event loop is closed before calling the original
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			# Event loop is closed, skip cleanup that requires the loop
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' in str(e):
			# Silently ignore this specific error
			pass
		else:
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del


# 延迟导入的类型存根 - 修复 linter 警告 (Type stubs for lazy imports - fixes linter warnings)
# 这些导入仅在类型检查期间执行，运行时不会加载，从而提高启动速度
if TYPE_CHECKING:
	from browser_use.agent.prompts import SystemPrompt
	from browser_use.agent.service import Agent

	# from browser_use.agent.service import Agent
	from browser_use.agent.views import ActionModel, ActionResult, AgentHistoryList
	from browser_use.browser import BrowserProfile, BrowserSession
	from browser_use.browser import BrowserSession as Browser
	from browser_use.code_use.service import CodeAgent
	from browser_use.dom.service import DomService
	from browser_use.llm import models
	from browser_use.llm.anthropic.chat import ChatAnthropic
	from browser_use.llm.azure.chat import ChatAzureOpenAI
	from browser_use.llm.browser_use.chat import ChatBrowserUse
	from browser_use.llm.google.chat import ChatGoogle
	from browser_use.llm.groq.chat import ChatGroq
	from browser_use.llm.mistral.chat import ChatMistral
	from browser_use.llm.oci_raw.chat import ChatOCIRaw
	from browser_use.llm.ollama.chat import ChatOllama
	from browser_use.llm.openai.chat import ChatOpenAI
	from browser_use.llm.vercel.chat import ChatVercel
	from browser_use.sandbox import sandbox
	from browser_use.tools.service import Controller, Tools

	# 延迟导入映射表 - 仅在实际访问时导入 (Lazy imports mapping - only import when actually accessed)
	# 定义了所有可用的导入项，包括 Agent、浏览器、工具、LLM 模型等
	# 这种机制避免了一次性加载所有模块，显著减少了初始导入时间
_LAZY_IMPORTS = {
	# Agent service (heavy due to dependencies)
	# 'Agent': ('browser_use.agent.service', 'Agent'),
	# Code-use agent (Jupyter notebook-like execution)
	'CodeAgent': ('browser_use.code_use.service', 'CodeAgent'),
	'Agent': ('browser_use.agent.service', 'Agent'),
	# System prompt (moderate weight due to agent.views imports)
	'SystemPrompt': ('browser_use.agent.prompts', 'SystemPrompt'),
	# Agent views (very heavy - over 1 second!)
	'ActionModel': ('browser_use.agent.views', 'ActionModel'),
	'ActionResult': ('browser_use.agent.views', 'ActionResult'),
	'AgentHistoryList': ('browser_use.agent.views', 'AgentHistoryList'),
	'BrowserSession': ('browser_use.browser', 'BrowserSession'),
	'Browser': ('browser_use.browser', 'BrowserSession'),  # Alias for BrowserSession
	'BrowserProfile': ('browser_use.browser', 'BrowserProfile'),
	# Tools (moderate weight)
	'Tools': ('browser_use.tools.service', 'Tools'),
	'Controller': ('browser_use.tools.service', 'Controller'),  # alias
	# DOM service (moderate weight)
	'DomService': ('browser_use.dom.service', 'DomService'),
	# Chat models (very heavy imports)
	'ChatOpenAI': ('browser_use.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('browser_use.llm.google.chat', 'ChatGoogle'),
	'ChatAnthropic': ('browser_use.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatBrowserUse': ('browser_use.llm.browser_use.chat', 'ChatBrowserUse'),
	'ChatGroq': ('browser_use.llm.groq.chat', 'ChatGroq'),
	'ChatMistral': ('browser_use.llm.mistral.chat', 'ChatMistral'),
	'ChatAzureOpenAI': ('browser_use.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatOCIRaw': ('browser_use.llm.oci_raw.chat', 'ChatOCIRaw'),
	'ChatOllama': ('browser_use.llm.ollama.chat', 'ChatOllama'),
	'ChatVercel': ('browser_use.llm.vercel.chat', 'ChatVercel'),
	# LLM models module
	'models': ('browser_use.llm.models', None),
	# Sandbox execution
	'sandbox': ('browser_use.sandbox', 'sandbox'),
}


def __getattr__(name: str):
	"""
	延迟导入机制 - 仅在实际访问模块时导入它们。
	当访问模块属性时触发，检查属性是否在延迟导入映射中。
	如果存在，动态导入相应模块并缓存到全局命名空间，
	这样第二次访问同一属性时就不需要重新导入。
	
	例如，当用户执行以下代码时会调用此函数：
	```python
	# 这是浅路径写法，只导入顶层模块，依赖 browser_use/__init__.py 中的 __getattr__ 魔术方法才能生效；
	from browser_use import Agent  # 此时会触发 __getattr__('Agent')
	agent = Agent(...)  # 实际上是调用了延迟导入的 Agent 类
	# 顶部仅导入模块，不导入具体类，首次使用时即Agent()时才真正导入
	```
	
	这个函数是 Python 的魔术方法，在访问模块中不存在的属性时自动调用。
	通过这种方式，我们实现了按需加载，而不是在模块导入时加载所有内容。

	如果使用传统的静态导入（Static Import），通常会写在文件顶部，例如：
	```python
	from browser_use.agent.service import Agent
	from browser_use.browser import BrowserSession as Browser
	```
	静态导入会在模块初始化时立即加载所有依赖，导致启动变慢。
	"""
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			if attr_name is None:
				# For modules like 'models', return the module itself
				attr = module
			else:
				attr = getattr(module, attr_name)
			# Cache the imported attribute in the module's globals
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# 定义模块的公共 API (Define the module's public API)
# __all__ 指定了当使用 `from browser_use import *` 时应该导入哪些名称
# 它也帮助 IDE 和文档工具识别哪些是模块的公共接口
# 这个列表必须与 _LAZY_IMPORTS 映射表中的键保持一致
__all__ = [
	'Agent',
	'CodeAgent',
	# 'CodeAgent',
	'BrowserSession',
	'Browser',  # Alias for BrowserSession
	'BrowserProfile',
	'Controller',
	'DomService',
	'SystemPrompt',
	'ActionResult',
	'ActionModel',
	'AgentHistoryList',
	# Chat models
	'ChatOpenAI',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatBrowserUse',
	'ChatGroq',
	'ChatMistral',
	'ChatAzureOpenAI',
	'ChatOCIRaw',
	'ChatOllama',
	'ChatVercel',
	'Tools',
	'Controller',
	# LLM models module
	'models',
	# Sandbox execution
	'sandbox',
]
