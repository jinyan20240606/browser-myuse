import asyncio  # 异步 I/O 支持，用于协程和 async/await
import gc  # 垃圾回收模块，用于强制回收对象
import inspect  # 运行时检查工具，用于判断函数是否为协程等
import json  # JSON 编解码
import logging  # 日志记录
import re  # 正则表达式处理
import tempfile  # 临时文件/目录操作
import time  # 时间相关函数（时间戳、睡眠等）
from collections.abc import Awaitable, Callable  # 类型提示：可等待对象和可调用对象
from pathlib import Path  # 路径操作的面向对象接口
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast  # 类型提示工具
from urllib.parse import urlparse  # URL 解析

if TYPE_CHECKING:
	# 防止循环导入，仅在类型检查时导入 Skill 类型
	from browser_use.skills.views import Skill

from dotenv import load_dotenv  # 加载 .env 环境变量文件

from browser_use.agent.cloud_events import (
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentStepEvent,
	CreateAgentTaskEvent,
	UpdateAgentTaskEvent,
)  # 导入代理相关的云事件类，用于事件分发和记录
from browser_use.agent.message_manager.utils import save_conversation  # 保存对话到文件的工具
from browser_use.llm.base import BaseChatModel  # 抽象的聊天模型基类类型
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError  # LLM 异常类型
from browser_use.llm.messages import BaseMessage, ContentPartImageParam, ContentPartTextParam, UserMessage  # LLM 消息类型
from browser_use.tokens.service import TokenCost  # 令牌/费用统计服务

load_dotenv()  # 从 .env 文件加载环境变量（例如 API keys）

from bubus import EventBus  # 事件总线实现，用于进程内事件分发
from pydantic import BaseModel, ValidationError  # Pydantic 用于模型验证与错误类型
from uuid_extensions import uuid7str  # 生成可排序/短的 UUID 字符串

from browser_use import Browser, BrowserProfile, BrowserSession  # 浏览器相关类（会话、配置）
from browser_use.agent.judge import construct_judge_messages  # 构造用于 Judge 的消息工具

# 延迟导入 gif 以避免启动时引入过多依赖
# from browser_use.agent.gif import create_history_gif
from browser_use.agent.message_manager.service import (
	MessageManager,
)  # 消息管理器服务，负责构建发送给 LLM 的消息上下文
from browser_use.agent.prompts import SystemPrompt  # 系统提示构造器
from browser_use.agent.views import (
	ActionResult,
	AgentError,
	AgentHistory,
	AgentHistoryList,
	AgentOutput,
	AgentSettings,
	AgentState,
	AgentStepInfo,
	AgentStructuredOutput,
	BrowserStateHistory,
	DetectedVariable,
	JudgementResult,
	StepMetadata,
)  # 代理相关的视图/数据模型导入（历史、输出、设置等）
from browser_use.browser.session import DEFAULT_BROWSER_PROFILE  # 默认浏览器配置
from browser_use.browser.views import BrowserStateSummary  # 浏览器状态摘要模型
from browser_use.config import CONFIG  # 全局配置
from browser_use.dom.views import DOMInteractedElement, MatchLevel  # DOM 交互元素表示和匹配层级枚举
from browser_use.filesystem.file_system import FileSystem  # 文件系统抽象，用于持久化提取内容等
from browser_use.observability import observe, observe_debug  # 观察/埋点装饰器
from browser_use.telemetry.service import ProductTelemetry  # 产品遥测服务
from browser_use.telemetry.views import AgentTelemetryEvent  # 遥测事件模型
from browser_use.tools.registry.views import ActionModel  # 动态动作模型类型
from browser_use.tools.service import Tools  # 工具注册与执行服务
from browser_use.utils import (
	URL_PATTERN,
	_log_pretty_path,
	check_latest_browser_use_version,
	get_browser_use_version,
	time_execution_async,
	time_execution_sync,
)  # 若干工具函数与常量（URL 正则、版本检查、计时装饰器等）

logger = logging.getLogger(__name__)  # 获取模块级 logger，用于记录模块内部日志


def log_response(response: AgentOutput, registry=None, logger=None) -> None:
	"""Utility function to log the model's response."""

	# Use module logger if no logger provided
	if logger is None:
		logger = logging.getLogger(__name__)

	# Only log thinking if it's present
	if response.current_state.thinking:
		logger.debug(f'💡 Thinking:\n{response.current_state.thinking}')

	# Only log evaluation if it's not empty
	eval_goal = response.current_state.evaluation_previous_goal
	if eval_goal:
		if 'success' in eval_goal.lower():
			emoji = '👍'
			# Green color for success
			logger.info(f'  \033[32m{emoji} Eval: {eval_goal}\033[0m')
		elif 'failure' in eval_goal.lower():
			emoji = '⚠️'
			# Red color for failure
			logger.info(f'  \033[31m{emoji} Eval: {eval_goal}\033[0m')
		else:
			emoji = '❔'
			# No color for unknown/neutral
			logger.info(f'  {emoji} Eval: {eval_goal}')

	# Always log memory if present
	if response.current_state.memory:
		logger.info(f'  🧠 Memory: {response.current_state.memory}')

	# Only log next goal if it's not empty
	next_goal = response.current_state.next_goal
	if next_goal:
		# Blue color for next goal
		logger.info(f'  \033[34m🎯 Next goal: {next_goal}\033[0m')

# Context：是你给这个类型变量起的名字（通常大写，和变量名一致，便于识
Context = TypeVar('Context')


AgentHookFunc = Callable[['Agent'], Awaitable[None]]


class Agent(Generic[Context, AgentStructuredOutput]):
	"""
	Agent 类 - 浏览器自动化代理的核心类
	
	这是一个泛型类，接受两个类型参数：
	- Context: 上下文类型，用于传递自定义数据
	- AgentStructuredOutput: 结构化输出类型，定义 Agent 返回的数据格式
	
	Agent 通过 LLM 决策，自动执行浏览器操作来完成用户指定的任务
	"""
	# 给函数 / 方法添加 “执行时间统计” 的功能，且传入了 '--init' 作为标识参数 
	@time_execution_sync('--init')
	def __init__(
		self,
		task: str,  # 用户任务描述
		llm: BaseChatModel | None = None,  # 语言模型，用于决策
		# Optional parameters - 可选参数
		browser_profile: BrowserProfile | None = None,  # 浏览器配置文件
		browser_session: BrowserSession | None = None,  # 浏览器会话
		browser: Browser | None = None,  # browser_session 的别名（推荐使用）
		tools: Tools[Context] | None = None,  # 工具注册表
		controller: Tools[Context] | None = None,  # tools 的别名（已废弃）
		# Skills integration
		skill_ids: list[str | Literal['*']] | None = None,
		skills: list[str | Literal['*']] | None = None,  # Alias for skill_ids
		skill_service: Any | None = None,
		# Initial agent run parameters
		sensitive_data: dict[str, str | dict[str, str]] | None = None,
		initial_actions: list[dict[str, dict[str, Any]]] | None = None,
		# Cloud Callbacks
		register_new_step_callback: (
			Callable[['BrowserStateSummary', 'AgentOutput', int], None]  # Sync callback
			| Callable[['BrowserStateSummary', 'AgentOutput', int], Awaitable[None]]  # Async callback
			| None
		) = None,
		register_done_callback: (
			Callable[['AgentHistoryList'], Awaitable[None]]  # Async Callback
			| Callable[['AgentHistoryList'], None]  # Sync Callback
			| None
		) = None,
		register_external_agent_status_raise_error_callback: Callable[[], Awaitable[bool]] | None = None,
		register_should_stop_callback: Callable[[], Awaitable[bool]] | None = None,
		# Agent settings
		output_model_schema: type[AgentStructuredOutput] | None = None,
		use_vision: bool | Literal['auto'] = True,
		save_conversation_path: str | Path | None = None,
		save_conversation_path_encoding: str | None = 'utf-8',
		max_failures: int = 3,
		override_system_message: str | None = None,
		extend_system_message: str | None = None,
		generate_gif: bool | str = False,
		available_file_paths: list[str] | None = None,
		include_attributes: list[str] | None = None,
		max_actions_per_step: int = 3,
		use_thinking: bool = True,
		flash_mode: bool = False,
		demo_mode: bool | None = None,
		max_history_items: int | None = None,
		page_extraction_llm: BaseChatModel | None = None,
		fallback_llm: BaseChatModel | None = None,
		use_judge: bool = True,
		ground_truth: str | None = None,
		judge_llm: BaseChatModel | None = None,
		injected_agent_state: AgentState | None = None,
		source: str | None = None,
		file_system_path: str | None = None,
		task_id: str | None = None,
		calculate_cost: bool = False,
		display_files_in_done_text: bool = True,
		include_tool_call_examples: bool = False,
		vision_detail_level: Literal['auto', 'low', 'high'] = 'auto',
		llm_timeout: int | None = None,
		step_timeout: int = 120,
		directly_open_url: bool = True,
		include_recent_events: bool = False,
		# 开关参数：是否采样页面图片（视觉模式下减少图片数量，节省 Token）
		sample_images: list[ContentPartTextParam | ContentPartImageParam] | None = None,
		final_response_after_failure: bool = True,
		llm_screenshot_size: tuple[int, int] | None = None,
		_url_shortening_limit: int = 25,
		**kwargs,
	):
		# 验证 llm_screenshot_size 大小
		if llm_screenshot_size is not None:
			if not isinstance(llm_screenshot_size, tuple) or len(llm_screenshot_size) != 2:
				raise ValueError('llm_screenshot_size must be a tuple of (width, height)')
			width, height = llm_screenshot_size
			if not isinstance(width, int) or not isinstance(height, int):
				raise ValueError('llm_screenshot_size dimensions must be integers')
			if width < 100 or height < 100:
				raise ValueError('llm_screenshot_size dimensions must be at least 100 pixels')
			self.logger.info(f'🖼️  LLM screenshot resizing enabled: {width}x{height}')
		if llm is None:
			default_llm_name = CONFIG.DEFAULT_LLM
			if default_llm_name:
				from browser_use.llm.models import get_llm_by_name

				llm = get_llm_by_name(default_llm_name)
			else:
				# No default LLM specified, use the original default
				from browser_use import ChatBrowserUse

				llm = ChatBrowserUse()

		# 如果是 ChatBrowserUse 则设置快速模式： set flashmode = True if llm is ChatBrowserUse
		if llm.provider == 'browser-use':
			flash_mode = True
			# 该模式通常是 browser-use 优化的 “快速响应模式”—— 比如缓存常用指令、精简上下文、提升 LLM 决策速度，适配浏览器自动化的低延迟需求

		# 当未指定 LLM 截图尺寸时，针对 Claude Sonnet 模型自动配置专属的截图尺寸：Auto-configure llm_screenshot_size for Claude Sonnet models
		if llm_screenshot_size is None:
			model_name = getattr(llm, 'model', '')
			if isinstance(model_name, str) and model_name.startswith('claude-sonnet'):
				llm_screenshot_size = (1400, 850)
				logger.info('🖼️  Auto-configured LLM screenshot size for Claude Sonnet: 1400x850')
		# 设置页面提取 LLM 和 判断评估 LLM
		if page_extraction_llm is None:
			page_extraction_llm = llm
		if judge_llm is None:
			judge_llm = llm
		# 初始化可用文件路径
		if available_file_paths is None:
			available_file_paths = []

		# Set timeout based on model name if not explicitly provided
		if llm_timeout is None:

			def _get_model_timeout(llm_model: BaseChatModel) -> int:
				"""Determine timeout based on model name"""
				model_name = getattr(llm_model, 'model', '').lower()
				if 'gemini' in model_name:
					if '3-pro' in model_name:
						return 90
					return 45
				elif 'groq' in model_name:
					return 30
				elif 'o3' in model_name or 'claude' in model_name or 'sonnet' in model_name or 'deepseek' in model_name:
					return 90
				else:
					return 60  # Default timeout

			llm_timeout = _get_model_timeout(llm)

		# 创建任务 ID 和 会话ID
		self.id = task_id or uuid7str()
		self.task_id: str = self.id
		self.session_id: str = uuid7str()
		# 创建浏览器管理的配置参数
		base_profile = browser_profile or DEFAULT_BROWSER_PROFILE
		if base_profile is DEFAULT_BROWSER_PROFILE:
			base_profile = base_profile.model_copy()
		if demo_mode is not None and base_profile.demo_mode != demo_mode:
			base_profile = base_profile.model_copy(update={'demo_mode': demo_mode})
		browser_profile = base_profile

		# Handle browser vs browser_session parameter (browser takes precedence)
		if browser and browser_session:
			raise ValueError('Cannot specify both "browser" and "browser_session" parameters. Use "browser" for the cleaner API.')
		browser_session = browser or browser_session

		if browser_session is not None and demo_mode is not None and browser_session.browser_profile.demo_mode != demo_mode:
			browser_session.browser_profile = browser_session.browser_profile.model_copy(update={'demo_mode': demo_mode})

		self.browser_session = browser_session or BrowserSession(
			browser_profile=browser_profile,
			id=uuid7str()[:-4] + self.id[-4:],  # re-use the same 4-char suffix so they show up together in logs
		)

		self._demo_mode_enabled: bool = bool(self.browser_profile.demo_mode) if self.browser_session else False
		if self._demo_mode_enabled and getattr(self.browser_profile, 'headless', False):
			self.logger.warning(
				'Demo mode is enabled but the browser is headless=True; set headless=False to view the in-browser panel.'
			)

		# Initialize available file paths as direct attribute
		self.available_file_paths = available_file_paths

		# 设置默认Tools：Set up tools first (needed to detect output_model_schema)
		if tools is not None:
			self.tools = tools
		elif controller is not None:
			self.tools = controller
		else:
			# 当 LLM 的视觉模式（use_vision）不是 “自动” 模式时，从 Agent 的工具集中移除截图工具（避免 Agent 调用截图但 LLM 无法解析视觉内容）；如果是自动模式，则保留截图工具（让 Agent 自主决定是否截图）
			# Exclude screenshot tool when use_vision is not auto
			exclude_actions = ['screenshot'] if use_vision != 'auto' else []
			# display_files_in_done_text：Agent 完成任务后，是否在最终的结果文本中显示相关文件路径
			self.tools = Tools(exclude_actions=exclude_actions, display_files_in_done_text=display_files_in_done_text)

		# Enforce screenshot exclusion when use_vision != 'auto', even if user passed custom tools
		if use_vision != 'auto':
			self.tools.exclude_action('screenshot')

		# 自动检测 LLM 模型是否支持坐标点击，若支持则开启工具集的坐标点击功能 Enable coordinate clicking for models that support it
		# 一些高级大模型，能直接输出目标元素的坐标位置，如：Claude Sonnet 4
		model_name = getattr(llm, 'model', '').lower()
		supports_coordinate_clicking = any(
			pattern in model_name for pattern in ['claude-sonnet-4', 'claude-opus-4', 'gemini-3-pro', 'browser-use/']
		)
		if supports_coordinate_clicking:
			self.tools.set_coordinate_clicking(True)

		# 解决 skills 和 skill_ids 两个参数的冲突问题，且让 skills 参数拥有更高优先级 ---- Handle skills vs skill_ids parameter (skills takes precedence)
		if skills and skill_ids:
			raise ValueError('Cannot specify both "skills" and "skill_ids" parameters. Use "skills" for the cleaner API.')
		skill_ids = skills or skill_ids

		# Skill服务集成：优先使用外部注入的 skill_service 实例，若未注入则基于 skill_ids 自行创建 SkillService 实例 -- Skills integration - use injected service or create from skill_ids
		self.skill_service = None
		self._skills_registered = False
		if skill_service is not None:
			self.skill_service = skill_service
		elif skill_ids:
			from browser_use.skills import SkillService

			self.skill_service = SkillService(skill_ids=skill_ids)

		# 统一 Agent 和 Tools 层的结构化输出模型，优先使用 Agent 显式指定的模型，若无则复用 Tools 层的模型，同时处理模型不一致的警告------Structured output - use explicit param or detect from tools
		tools_output_model = self.tools.get_output_model()
		if output_model_schema is not None and tools_output_model is not None:
			# Both provided - warn if they differ
			if output_model_schema is not tools_output_model:
				logger.warning(
					f'output_model_schema ({output_model_schema.__name__}) differs from Tools output_model '
					f'({tools_output_model.__name__}). Using Agent output_model_schema.'
				)
		elif output_model_schema is None and tools_output_model is not None:
			# Only tools has it - use that (cast is safe: both are BaseModel subclasses)
			output_model_schema = cast(type[AgentStructuredOutput], tools_output_model)
		self.output_model_schema = output_model_schema
		if self.output_model_schema is not None:
			self.tools.use_structured_output_action(self.output_model_schema)

		# Core components - task enhancement now has access to output_model_schema from tools
		# 增强任务包装器：传入 task（原始任务）和 output_model_schema（前文统一的结构化输出模型），保证增强逻辑一定能获取到格式约束的依据
		self.task = self._enhance_task_with_schema(task, output_model_schema)
		self.llm = llm
		self.judge_llm = judge_llm

		# 兜底 LLM（Fallback LLM）配置 ---- Fallback LLM configuration
		# 当主 LLM（self._original_llm） 出现故障（超时、报错、返回格式异常）时，自动切换到 self._fallback_llm 继续执行任务
		self._fallback_llm: BaseChatModel | None = fallback_llm
		self._using_fallback_llm: bool = False
		self._original_llm: BaseChatModel = llm  # Store original for reference
		self.directly_open_url = directly_open_url
		self.include_recent_events = include_recent_events
		self._url_shortening_limit = _url_shortening_limit

		self.sensitive_data = sensitive_data

		self.sample_images = sample_images

		self.settings = AgentSettings(
			use_vision=use_vision,
			vision_detail_level=vision_detail_level,
			save_conversation_path=save_conversation_path,
			save_conversation_path_encoding=save_conversation_path_encoding,
			max_failures=max_failures,
			override_system_message=override_system_message,
			extend_system_message=extend_system_message,
			generate_gif=generate_gif,
			include_attributes=include_attributes,
			max_actions_per_step=max_actions_per_step,
			use_thinking=use_thinking,
			flash_mode=flash_mode,
			max_history_items=max_history_items,
			page_extraction_llm=page_extraction_llm,
			calculate_cost=calculate_cost,
			include_tool_call_examples=include_tool_call_examples,
			llm_timeout=llm_timeout,
			step_timeout=step_timeout,
			final_response_after_failure=final_response_after_failure,
			use_judge=use_judge,
			ground_truth=ground_truth,
		)

		# Token cost service
		self.token_cost_service = TokenCost(include_cost=calculate_cost)
		self.token_cost_service.register_llm(llm)
		self.token_cost_service.register_llm(page_extraction_llm)
		self.token_cost_service.register_llm(judge_llm)

		# 优先使用外部注入的 Agent 状态（保证状态复用 / 恢复），否则初始化全新状态；
		# 同时创建空的 Agent 交互历史列表，用于记录对话、操作、成本等信息
		# Initialize state
		self.state = injected_agent_state or AgentState()

		# Initialize history
		self.history = AgentHistoryList(history=[], usage=None)

		# Initialize agent directory
		import time

		timestamp = int(time.time())
		base_tmp = Path(tempfile.gettempdir())
		self.agent_directory = base_tmp / f'browser_use_agent_{self.id}_{timestamp}'

		# Initialize file system and screenshot service
		# 为每个 Agent 实例创建唯一的临时目录（基于系统临时目录 + Agent ID + 时间戳），用于存储该 Agent 运行过程中产生的临时文件（截图、录屏、对话记录等）”
		self._set_file_system(file_system_path)
		# 使用Agent的临时目录初始化截图服务
		self._set_screenshot_service()

		# 设置Action步骤执行的模型-进行初始化：Action setup
		self._setup_action_models()
		# 记录 Agent 的版本信息和来源标识（用于溯源、兼容性适配）
		self._set_browser_use_version_and_source(source)

		initial_url = None

		# only load url if no initial actions are provided
		# 当满足特定条件（开启直接打开 URL、无后续任务、无初始动作）时
		if self.directly_open_url and not self.state.follow_up_task and not initial_actions:
			initial_url = self._extract_start_url(self.task)
			if initial_url:
				self.logger.info(f'🔗 Found URL in task: {initial_url}, adding as initial action...')
				initial_actions = [{'navigate': {'url': initial_url, 'new_tab': False}}]

		self.initial_url = initial_url
		# 从任务中提取起始 URL 并生成对应的导航动作，之后处理初始动作格式并验证 LLM（大语言模型）的连接状态
		self.initial_actions = self._convert_initial_actions(initial_actions) if initial_actions else None
		# 验证APIkey的有效性Verify we can connect to the model
		self._verify_and_setup_llm()

		# TODO: move this logic to the LLMs
		# Handle users trying to use use_vision=True with DeepSeek models
		# 针对 DeepSeek 模型的兼容性处理逻辑，核心作用是：当检测到当前使用的大语言模型（LLM）是 DeepSeek 系列时，自动关闭视觉功能（use_vision），并给出警告日志，同时备注了后续需要将这个逻辑迁移到 LLM 相关模块中
		if 'deepseek' in self.llm.model.lower():
			self.logger.warning('⚠️ DeepSeek models do not support use_vision=True yet. Setting use_vision=False for now...')
			self.settings.use_vision = False

		# Handle users trying to use use_vision=True with XAI models that don't support it
		# grok-3 variants and grok-code don't support vision; grok-2 and grok-4 do
		# 针对 XAI 系列模型做视觉功能（use_vision）的兼容性处理 —— 明确区分哪些 XAI 模型支持视觉、哪些不支持（grok-3 变体和 grok-code 不支持，grok-2 和 grok-4 支持），从而避免用户错误开启不支持的视觉功能导致程序异常
		model_lower = self.llm.model.lower()
		if 'grok-3' in model_lower or 'grok-code' in model_lower:
			self.logger.warning('⚠️ This XAI model does not support use_vision=True yet. Setting use_vision=False for now...')
			self.settings.use_vision = False

		logger.debug(
			f'{" +vision" if self.settings.use_vision else ""}'
			f' extraction_model={self.settings.page_extraction_llm.model if self.settings.page_extraction_llm else "Unknown"}'
			f'{" +file_system" if self.file_system else ""}'
		)

		# Store llm_screenshot_size in browser_session so tools can access it
		self.browser_session.llm_screenshot_size = llm_screenshot_size

		# 一是检测当前使用的 LLM 是否是 Anthropic 系列模型的实例，
		# 二是检测模型是否为 browser-use 微调版本（这类模型使用简化的提示词格式），为后续适配不同模型的提示词逻辑做准备
		# Check if LLM is ChatAnthropic instance
		from browser_use.llm.anthropic.chat import ChatAnthropic

		is_anthropic = isinstance(self.llm, ChatAnthropic)

		# Check if model is a browser-use fine-tuned model (uses simplified prompts)
		is_browser_use_model = 'browser-use/' in self.llm.model.lower()

		# Initialize message manager with state
		# 包含所有动作的初始系统提示 —— 会在每个步骤中更新”，明确了 MessageManager 的初始化目的和系统提示的动态特性
		# 初始化一个 MessageManager（消息管理器）实例，它会整合任务、系统提示、配置项、状态等核心信息，为后续智能体和大语言模型的交互（如生成提示词、管理对话历史）提供统一的消息管理能力
		self._message_manager = MessageManager(
			# 任务描述
			task=self.task,
			# 调用 get_system_message() 方法，生成最终的系统提示词字符串，传入 MessageManager
			system_message=SystemPrompt(
				# 每一步允许执行的最大动作数（控制智能体单次输出的动作数量）；
				max_actions_per_step=self.settings.max_actions_per_step,
				# 分别用于 “覆盖默认系统提示” 和 “扩展默认系统提示”（灵活定制提示词）
				override_system_message=override_system_message,
				extend_system_message=extend_system_message,
				# 关联之前的运行模式（思考模式 / 快速模式），适配提示词格式；
				use_thinking=self.settings.use_thinking,
				flash_mode=self.settings.flash_mode,
				# 关联之前识别的模型特征，生成适配 Anthropic 模型 / 浏览器微调模型的提示词；
				is_anthropic=is_anthropic,
				is_browser_use_model=is_browser_use_model,
			).get_system_message(),
			# 传入文件系统实例：允许消息管理器访问 / 操作文件（如读取本地文件、保存交互日志）
			file_system=self.file_system,
			# 传入历史状态：恢复消息管理器的历史状态（如之前的对话历史、已执行动作），保证状态的连续性（比如智能体重启后能接续之前的交互）
			state=self.state.message_manager_state,
			# 传入思考模式配置：控制消息管理器是否在对话历史中保留 “思考过程”（开启后会记录智能体的推理逻辑）。
			use_thinking=self.settings.use_thinking,
			# Settings that were previously in MessageManagerSettings
			# 是否在提示词中包含智能体 / 任务的属性信息；
			include_attributes=self.settings.include_attributes,
			# 敏感数据（用于提示词中脱敏或过滤）
			sensitive_data=sensitive_data,
			# 对话历史的最大保存条数（避免历史过长导致提示词超限）；
			max_history_items=self.settings.max_history_items,
			# 视觉功能的细节级别（控制截图 / 图片的描述粒度）；
			vision_detail_level=self.settings.vision_detail_level,
			# 是否在提示词中包含工具调用示例（帮助模型正确调用工具）；
			include_tool_call_examples=self.settings.include_tool_call_examples,
			# 是否包含近期事件（如最近执行的动作、页面变化）；
			include_recent_events=self.include_recent_events,
			# 示例图片（用于视觉任务的参考）
			sample_images=self.sample_images,
			# 传给 LLM 的截图尺寸（平衡图片质量和传输效率）。
			llm_screenshot_size=llm_screenshot_size,
		)
		# 敏感数据（sensitive_data）的安全校验逻辑：当智能体配置了敏感数据（如账号密码）时，通过检查浏览器的允许域名（allowed_domains）配置，防范敏感数据因恶意网站、域名配置不当导致的泄露风险，同时对域名特定的凭证做精准的域名匹配校验。
		if self.sensitive_data:
			# Check if sensitive_data has domain-specific credentials
			has_domain_specific_credentials = any(isinstance(v, dict) for v in self.sensitive_data.values())

			# If no allowed_domains are configured, show a security warning
			if not self.browser_profile.allowed_domains:
				self.logger.warning(
					'⚠️ Agent(sensitive_data=••••••••) was provided but Browser(allowed_domains=[...]) is not locked down! ⚠️\n'
					'          ☠️ If the agent visits a malicious website and encounters a prompt-injection attack, your sensitive_data may be exposed!\n\n'
					'   \n'
				)

			# If we're using domain-specific credentials, validate domain patterns
			elif has_domain_specific_credentials:
				# For domain-specific format, ensure all domain patterns are included in allowed_domains
				domain_patterns = [k for k, v in self.sensitive_data.items() if isinstance(v, dict)]

				# Validate each domain pattern against allowed_domains
				for domain_pattern in domain_patterns:
					is_allowed = False
					for allowed_domain in self.browser_profile.allowed_domains:
						# Special cases that don't require URL matching
						if domain_pattern == allowed_domain or allowed_domain == '*':
							is_allowed = True
							break

						# Need to create example URLs to compare the patterns
						# Extract the domain parts, ignoring scheme
						pattern_domain = domain_pattern.split('://')[-1] if '://' in domain_pattern else domain_pattern
						allowed_domain_part = allowed_domain.split('://')[-1] if '://' in allowed_domain else allowed_domain

						# Check if pattern is covered by an allowed domain
						# Example: "google.com" is covered by "*.google.com"
						if pattern_domain == allowed_domain_part or (
							allowed_domain_part.startswith('*.')
							and (
								pattern_domain == allowed_domain_part[2:]
								or pattern_domain.endswith('.' + allowed_domain_part[2:])
							)
						):
							is_allowed = True
							break

					if not is_allowed:
						self.logger.warning(
							f'⚠️ Domain pattern "{domain_pattern}" in sensitive_data is not covered by any pattern in allowed_domains={self.browser_profile.allowed_domains}\n'
							f'   This may be a security risk as credentials could be used on unintended domains.'
						)

		# Callbacks
		# 注册回调函数：将外部传入的各类回调函数绑定为当前实例的属性，让智能体在执行过程中（如开始新步骤、任务完成、需要停止等关键节点）能触发对应的自定义逻辑
		self.register_new_step_callback = register_new_step_callback
		self.register_done_callback = register_done_callback
		self.register_should_stop_callback = register_should_stop_callback
		self.register_external_agent_status_raise_error_callback = register_external_agent_status_raise_error_callback

		# Telemetry
		# 初始化产品遥测（Telemetry）实例，将遥测功能绑定到当前智能体 / 类实例中，用于收集产品使用过程中的关键数据（如功能调用、性能指标、错误信息等），帮助开发者分析产品使用情况、定位问题、优化体验
		self.telemetry = ProductTelemetry()

		# Event bus with WAL persistence
		# Default to ~/.config/browseruse/events/{agent_session_id}.jsonl
		# wal_path = CONFIG.BROWSER_USE_CONFIG_DIR / 'events' / f'{self.session_id}.jsonl'
		# 初始化带 WAL（Write-Ahead Log，预写式日志）持久化的事件总线（EventBus）实例，为智能体的事件管理提供 “可靠存储 + 事件分发” 能力 —— 既保证事件不会丢失（WAL 持久化），又能让智能体内部模块 / 外部组件通过事件总线解耦通信，同时注释还明确了持久化日志的默认存储路径
		self.eventbus = EventBus(name=f'Agent_{str(self.id)[-4:]}')

		# 处理并验证对话保存路径：当配置了 save_conversation_path（对话保存路径）时，先将路径标准化（解析用户主目录、转换为绝对路径），再输出日志告知用户对话将保存到该路径，保证路径在不同环境下的正确性和可追溯性
		if self.settings.save_conversation_path:
			self.settings.save_conversation_path = Path(self.settings.save_conversation_path).expanduser().resolve()
			self.logger.info(f'💬 Saving conversation to {_log_pretty_path(self.settings.save_conversation_path)}')

		# Initialize download tracking
		# 初始化智能体的下载文件跟踪功能：先校验浏览器会话是否就绪，再判断是否配置了下载路径，若配置则初始化下载文件跟踪的变量并记录调试日志，为后续监控浏览器下载文件的变化（如新增下载、校验下载结果）打下基础
		assert self.browser_session is not None, 'BrowserSession is not set up'
		self.has_downloads_path = self.browser_session.browser_profile.downloads_path is not None
		if self.has_downloads_path:
			self._last_known_downloads: list[str] = []
			self.logger.debug('📁 Initialized download tracking for agent')

		# Event-based pause control (kept out of AgentState for serialization)
		# 初始化基于异步事件（asyncio.Event）的外部暂停控制机制，并且特意将这个控制事件放在 AgentState 之外，
		# 用于实现对智能体执行流程的 “无侵入式暂停 / 恢复” 控制 —— 比如外部系统可通过触发这个事件，让智能体暂停执行或恢复执行，且不影响状态序列化
		self._external_pause_event = asyncio.Event()
		self._external_pause_event.set()

	def _enhance_task_with_schema(self, task: str, output_model_schema: type[AgentStructuredOutput] | None) -> str:
		"""Enhance task description with output schema information if provided."""
		if output_model_schema is None:
			return task

		try:
			schema = output_model_schema.model_json_schema()
			import json

			schema_json = json.dumps(schema, indent=2)

			enhancement = f'\nExpected output format: {output_model_schema.__name__}\n{schema_json}'
			return task + enhancement
		except Exception as e:
			self.logger.debug(f'Could not parse output schema: {e}')

		return task

	@property
	def logger(self) -> logging.Logger:
		"""
		获取特定于实例的日志记录器，名称中包含任务 ID
		
		使用属性装饰器，可以像访问属性一样调用：self.logger
		日志名称格式：Agent🅰 {任务ID} ⇢ 🅑 {浏览器会话ID} 🅣 {当前目标ID}
		
		Returns:
		    logging.Logger: 带有任务标识的日志记录器
		"""
		# 可能在 __init__ 中调用 logger，所以不假设 self.* 属性已经初始化
		# 使用海象运算符 (:=) 在同一行进行赋值和判断
		_task_id = task_id[-4:] if (task_id := getattr(self, 'task_id', None)) else '----'
		_browser_session_id = browser_session.id[-4:] if (browser_session := getattr(self, 'browser_session', None)) else '----'
		_current_target_id = (
			browser_session.agent_focus_target_id[-2:]
			if (browser_session := getattr(self, 'browser_session', None)) and browser_session.agent_focus_target_id
			else '--'
		)
		return logging.getLogger(f'browser_use.Agent🅰 {_task_id} ⇢ 🅑 {_browser_session_id} 🅣 {_current_target_id}')

	@property
	def browser_profile(self) -> BrowserProfile:
		assert self.browser_session is not None, 'BrowserSession is not set up'
		return self.browser_session.browser_profile

	@property
	def is_using_fallback_llm(self) -> bool:
		"""Check if the agent is currently using the fallback LLM."""
		return self._using_fallback_llm

	@property
	def current_llm_model(self) -> str:
		"""Get the model name of the currently active LLM."""
		return self.llm.model if hasattr(self.llm, 'model') else 'unknown'

	async def _check_and_update_downloads(self, context: str = '') -> None:
		"""
		检查新下载并更新可用文件路径
		
		该方法会：
		1. 比较当前下载文件与上次已知的下载文件
		2. 如果有新文件，更新 available_file_paths
		3. 记录新下载的文件
		
		Args:
		    context: 上下文描述，用于日志记录（如 "after executing actions"）
		"""
		if not self.has_downloads_path:
			return

		assert self.browser_session is not None, 'BrowserSession is not set up'

		try:
			current_downloads = self.browser_session.downloaded_files
			if current_downloads != self._last_known_downloads:
				self._update_available_file_paths(current_downloads)
				self._last_known_downloads = current_downloads
				if context:
					self.logger.debug(f'📁 {context}: Updated available files')
		except Exception as e:
			error_context = f' {context}' if context else ''
			self.logger.debug(f'📁 Failed to check for downloads{error_context}: {type(e).__name__}: {e}')

	def _update_available_file_paths(self, downloads: list[str]) -> None:
		"""更新 available_file_paths，加入已下载的文件"""
		if not self.has_downloads_path:
			return

		current_files = set(self.available_file_paths or [])
		new_files = set(downloads) - current_files

		if new_files:
			self.available_file_paths = list(current_files | new_files)

			self.logger.info(
				f'📁 Added {len(new_files)} downloaded files to available_file_paths (total: {len(self.available_file_paths)} files)'
			)
			for file_path in new_files:
				self.logger.info(f'📄 New file available: {file_path}')
		else:
			self.logger.debug(f'📁 No new downloads detected (tracking {len(current_files)} files)')

	def _set_file_system(self, file_system_path: str | None = None) -> None:
		"""初始化或恢复文件系统"""
		# Check for conflicting parameters
		if self.state.file_system_state and file_system_path:
			raise ValueError(
				'Cannot provide both file_system_state (from agent state) and file_system_path. '
				'Either restore from existing state or create new file system at specified path, not both.'
			)

		# Check if we should restore from existing state first
		if self.state.file_system_state:
			try:
				# Restore file system from state at the exact same location
				self.file_system = FileSystem.from_state(self.state.file_system_state)
				# The parent directory of base_dir is the original file_system_path
				self.file_system_path = str(self.file_system.base_dir)
				self.logger.debug(f'💾 File system restored from state to: {self.file_system_path}')
				return
			except Exception as e:
				self.logger.error(f'💾 Failed to restore file system from state: {e}')
				raise e

		# Initialize new file system
		try:
			if file_system_path:
				self.file_system = FileSystem(file_system_path)
				self.file_system_path = file_system_path
			else:
				# Use the agent directory for file system
				self.file_system = FileSystem(self.agent_directory)
				self.file_system_path = str(self.agent_directory)
		except Exception as e:
			self.logger.error(f'💾 Failed to initialize file system: {e}.')
			raise e

		# Save file system state to agent state
		self.state.file_system_state = self.file_system.get_state()

		self.logger.debug(f'💾 File system path: {self.file_system_path}')

	def _set_screenshot_service(self) -> None:
		"""使用代理目录初始化截图服务"""
		try:
			from browser_use.screenshots.service import ScreenshotService

			self.screenshot_service = ScreenshotService(self.agent_directory)
			self.logger.debug(f'📸 Screenshot service initialized in: {self.agent_directory}/screenshots')
		except Exception as e:
			self.logger.error(f'📸 Failed to initialize screenshot service: {e}.')
			raise e

	def save_file_system_state(self) -> None:
		"""将当前文件系统状态保存到 Agent 状态中"""
		if self.file_system:
			self.state.file_system_state = self.file_system.get_state()
		else:
			self.logger.error('💾 File system is not set up. Cannot save state.')
			raise ValueError('File system is not set up. Cannot save state.')

	def _set_browser_use_version_and_source(self, source_override: str | None = None) -> None:
		"""获取 browser-use 版本并确定包来源（git 或 pip）"""
		# Use the helper function for version detection
		version = get_browser_use_version()

		# Determine source
		try:
			package_root = Path(__file__).parent.parent.parent
			repo_files = ['.git', 'README.md', 'docs', 'examples']
			if all(Path(package_root / file).exists() for file in repo_files):
				source = 'git'
			else:
				source = 'pip'
		except Exception as e:
			self.logger.debug(f'Error determining source: {e}')
			source = 'unknown'

		if source_override is not None:
			source = source_override
		# self.logger.debug(f'Version: {version}, Source: {source}')  # moved later to _log_agent_run so that people are more likely to include it in copy-pasted support ticket logs
		self.version = version
		self.source = source

	def _setup_action_models(self) -> None:
		"""从工具注册表中设置动态动作模型----初始化self.ActionModel值
		
		根据不同的配置（flash_mode、use_thinking），从工具注册表中动态创建动作模型，并为智能体（Agent）设置对应的输出模型，同时还单独创建了用于触发结束动作的 Done 相关模型
		"""
		# Initially only include actions with no filters
		self.ActionModel = self.tools.registry.create_action_model()
		# Create output model with the dynamic actions
		if self.settings.flash_mode:
			self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
		elif self.settings.use_thinking:
			self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
		else:
			self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)

		# used to force the done action when max_steps is reached
		self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'])
		if self.settings.flash_mode:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
		elif self.settings.use_thinking:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
		else:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

	def _get_skill_slug(self, skill: 'Skill', all_skills: list['Skill']) -> str:
		"""
		从技能标题生成清晰的 slug 用作动作名称
		
		将标题转换为小写，移除非字母数字字符，用下划线替换空格。
		如果有重复 slug，则添加 UUID 后缀。

		Args:
			skill: The skill to get slug for
			all_skills: List of all skills to check for duplicates

		Returns:
			Slug like "cloned_github_stars_tracker" or "get_weather_data_a1b2" if duplicate

		Examples:
			"[Cloned] Github Stars Tracker" -> "cloned_github_stars_tracker"
			"Get Weather Data" -> "get_weather_data"
		"""
		import re

		# Remove special characters and convert to lowercase
		slug = re.sub(r'[^\w\s]', '', skill.title.lower())
		# Replace whitespace and hyphens with underscores
		slug = re.sub(r'[\s\-]+', '_', slug)
		# Remove leading/trailing underscores
		slug = slug.strip('_')

		# Check for duplicates and add UUID suffix if needed
		same_slug_count = sum(
			1 for s in all_skills if re.sub(r'[\s\-]+', '_', re.sub(r'[^\w\s]', '', s.title.lower()).strip('_')) == slug
		)
		if same_slug_count > 1:
			return f'{slug}_{skill.id[:4]}'
		else:
			return slug

	async def _register_skills_as_actions(self) -> None:
		"""将每个技能注册为单独的动作，使用 slug 作为动作名称"""
		if not self.skill_service or self._skills_registered:
			return

		self.logger.info('🔧 Registering skill actions...')

		# Fetch all skills (auto-initializes if needed)
		skills = await self.skill_service.get_all_skills()

		if not skills:
			self.logger.warning('No skills loaded from SkillService')
			return

		# Register each skill as its own action
		for skill in skills:
			slug = self._get_skill_slug(skill, skills)
			param_model = skill.parameters_pydantic(exclude_cookies=True)

			# Create description with skill title in quotes
			description = f'{skill.description} (Skill: "{skill.title}")'

			# Create handler for this specific skill
			def make_skill_handler(skill_id: str):
				async def skill_handler(params: BaseModel) -> ActionResult:
					"""Execute a specific skill"""
					assert self.skill_service is not None, 'SkillService not initialized'

					# Convert parameters to dict
					if isinstance(params, BaseModel):
						skill_params = params.model_dump()
					elif isinstance(params, dict):
						skill_params = params
					else:
						return ActionResult(extracted_content=None, error=f'Invalid parameters type: {type(params)}')

					# Get cookies from browser
					_cookies = await self.browser_session.cookies()

					try:
						result = await self.skill_service.execute_skill(
							skill_id=skill_id, parameters=skill_params, cookies=_cookies
						)

						if result.success:
							return ActionResult(
								extracted_content=str(result.result) if result.result else None,
								error=None,
							)
						else:
							return ActionResult(extracted_content=None, error=result.error or 'Skill execution failed')
					except Exception as e:
						# Check if it's a MissingCookieException
						if type(e).__name__ == 'MissingCookieException':
							# Format: "Missing cookies (name): description"
							cookie_name = getattr(e, 'cookie_name', 'unknown')
							cookie_description = getattr(e, 'cookie_description', str(e))
							error_msg = f'Missing cookies ({cookie_name}): {cookie_description}'
							return ActionResult(extracted_content=None, error=error_msg)
						return ActionResult(extracted_content=None, error=f'Skill execution error: {type(e).__name__}: {e}')

				return skill_handler

			# Create the handler for this skill
			handler = make_skill_handler(skill.id)
			handler.__name__ = slug

			# Register the action with the slug as the action name
			self.tools.registry.action(description=description, param_model=param_model)(handler)

		# Mark as registered
		self._skills_registered = True

		# Rebuild action models to include the new skill actions
		self._setup_action_models()

		# Reconvert initial actions with the new ActionModel type if they exist
		if self.initial_actions:
			# Convert back to dict form first
			initial_actions_dict = []
			for action in self.initial_actions:
				action_dump = action.model_dump(exclude_unset=True)
				initial_actions_dict.append(action_dump)
			# Reconvert using new ActionModel
			self.initial_actions = self._convert_initial_actions(initial_actions_dict)

		self.logger.info(f'✓ Registered {len(skills)} skill actions')

	async def _get_unavailable_skills_info(self) -> str:
		"""
		获取因缺少 cookie 而不可用的技能信息
		
		Returns:
			str: 描述不可用技能及如何使其可用的格式化字符串
		"""
		if not self.skill_service:
			return ''

		try:
			# Get all skills
			skills = await self.skill_service.get_all_skills()
			if not skills:
				return ''

			# Get current cookies
			current_cookies = await self.browser_session.cookies()
			cookie_dict = {cookie['name']: cookie['value'] for cookie in current_cookies}

			# Check each skill for missing required cookies
			unavailable_skills: list[dict[str, Any]] = []

			for skill in skills:
				# Get cookie parameters for this skill
				cookie_params = [p for p in skill.parameters if p.type == 'cookie']

				if not cookie_params:
					# No cookies needed, skip
					continue

				# Check for missing required cookies
				missing_cookies: list[dict[str, str]] = []
				for cookie_param in cookie_params:
					is_required = cookie_param.required if cookie_param.required is not None else True

					if is_required and cookie_param.name not in cookie_dict:
						missing_cookies.append(
							{'name': cookie_param.name, 'description': cookie_param.description or 'No description provided'}
						)

				if missing_cookies:
					unavailable_skills.append(
						{
							'id': skill.id,
							'title': skill.title,
							'description': skill.description,
							'missing_cookies': missing_cookies,
						}
					)

			if not unavailable_skills:
				return ''

			# Format the unavailable skills info with slugs
			lines = ['Unavailable Skills (missing required cookies):']
			for skill_info in unavailable_skills:
				# Get the full skill object to use the slug helper
				skill_obj = next((s for s in skills if s.id == skill_info['id']), None)
				slug = self._get_skill_slug(skill_obj, skills) if skill_obj else skill_info['title']
				title = skill_info['title']

				lines.append(f'\n  • {slug} ("{title}")')
				lines.append(f'    Description: {skill_info["description"]}')
				lines.append('    Missing cookies:')
				for cookie in skill_info['missing_cookies']:
					lines.append(f'      - {cookie["name"]}: {cookie["description"]}')

			return '\n'.join(lines)

		except Exception as e:
			self.logger.error(f'Error getting unavailable skills info: {type(e).__name__}: {e}')
			return ''

	def add_new_task(self, new_task: str) -> None:
		"""Add a new task to the agent, keeping the same task_id as tasks are continuous"""
		# Simply delegate to message manager - no need for new task_id or events
		# The task continues with new instructions, it doesn't end and start a new one
		self.task = new_task
		self._message_manager.add_new_task(new_task)
		# Mark as follow-up task and recreate eventbus (gets shut down after each run)
		self.state.follow_up_task = True
		# Reset control flags so agent can continue
		self.state.stopped = False
		self.state.paused = False
		agent_id_suffix = str(self.id)[-4:].replace('-', '_')
		if agent_id_suffix and agent_id_suffix[0].isdigit():
			agent_id_suffix = 'a' + agent_id_suffix
		self.eventbus = EventBus(name=f'Agent_{agent_id_suffix}')

	async def _check_stop_or_pause(self) -> None:
		"""
		统一校验所有 “停止 / 暂停” 触发条件（外部回调 + 内部状态），一旦满足条件就抛出 InterruptedError，让上层流程（如 step()/_execute_step()）捕获并优雅终止当前步骤 / 任务
		"""

		# Check new should_stop_callback - sets stopped state cleanly without raising
		if self.register_should_stop_callback:
			if await self.register_should_stop_callback():
				self.logger.info('External callback requested stop')
				self.state.stopped = True
				raise InterruptedError

		if self.register_external_agent_status_raise_error_callback:
			if await self.register_external_agent_status_raise_error_callback():
				raise InterruptedError

		if self.state.stopped:
			raise InterruptedError

		if self.state.paused:
			raise InterruptedError

	@observe(name='agent.step', ignore_output=True, ignore_input=True)
	@time_execution_async('--step')
	async def step(self, step_info: AgentStepInfo | None = None) -> None:
		"""
		执行单步任务 - Agent 的核心执行单元

		完成智能体单步执行的全流程（上下文准备→LLM 决策→动作执行→后处理），添加监控 / 计时能力，统一处理异常，保证无论是否出错都能完成清理和记录
		
		每一步包含三个阶段：
		1. 准备上下文 - 获取浏览器状态、更新动作模型
		2. 获取并执行动作 - 调用 LLM 获取决策，执行浏览器操作
		3. 后处理 - 检查下载、记录结果
		
		Args:
		    step_info: 步骤信息，包含当前步数和最大步数
		"""
		# 首先初始化计时，在任何异常发生之前
		self.step_start_time = time.time()
		# 初始化浏览器状态摘要（后续赋值）
		browser_state_summary = None 

		try:
			# 阶段 1: 准备上下文和计时
			browser_state_summary = await self._prepare_context(step_info)

			# 阶段 2: 获取模型输出并执行动作（核心：LLM决策 + 浏览器操作）
			await self._get_next_action(browser_state_summary)
			# 执行动作（如点击、输入、导航）
			await self._execute_actions()

			# 阶段 3: 后处理（检查下载、记录结果、更新状态）
			await self._post_process()

		except Exception as e:
			# 在一个地方处理所有异常
			await self._handle_step_error(e)

		finally:
			# 无论是否异常，都要执行清理和记录
			await self._finalize(browser_state_summary)

	async def _prepare_context(self, step_info: AgentStepInfo | None = None) -> BrowserStateSummary:
		"""
		准备步骤的上下文：浏览器状态、动作模型、页面动作
		
		这是每步执行的第一阶段，负责：
		1. 获取当前浏览器状态（包括截图）
		2. 检查新下载的文件
		3. 更新动作模型（根据当前页面过滤可用动作）
		4. 创建状态消息供 LLM 使用
		
		Args:
		    step_info: 步骤信息
		    
		Returns:
		    BrowserStateSummary: 浏览器状态摘要，包含 URL、标题、DOM 状态、截图等
		"""
		# step_start_time 现在在 step() 方法中设置

		assert self.browser_session is not None, 'BrowserSession is not set up'

		self.logger.debug(f'🌐 Step {self.state.n_steps}: Getting browser state...')
		# 始终为所有步骤截图
		self.logger.debug('📸 Requesting browser state with include_screenshot=True')
		browser_state_summary = await self.browser_session.get_browser_state_summary(
			include_screenshot=True,  # 始终捕获截图，即使 use_vision=False，以便云同步有用（现在很快）
			include_recent_events=self.include_recent_events,
		)
		if browser_state_summary.screenshot:
			self.logger.debug(f'📸 Got browser state WITH screenshot, length: {len(browser_state_summary.screenshot)}')
		else:
			self.logger.debug('📸 Got browser state WITHOUT screenshot')

		# Check for new downloads after getting browser state (catches PDF auto-downloads and previous step downloads)
		await self._check_and_update_downloads(f'Step {self.state.n_steps}: after getting browser state')

		self._log_step_context(browser_state_summary)
		await self._check_stop_or_pause()

		# Update action models with page-specific actions
		self.logger.debug(f'📝 Step {self.state.n_steps}: Updating action models...')
		# 更新动作模型以反映当前页面的可用动作和输出模型
		await self._update_action_models_for_page(browser_state_summary.url)

		# Get page-specific filtered actions
		page_filtered_actions = self.tools.registry.get_prompt_description(browser_state_summary.url)

		# Page-specific actions will be included directly in the browser_state message
		self.logger.debug(f'💬 Step {self.state.n_steps}: Creating state messages for context...')

		# Get unavailable skills info if skills service is enabled
		unavailable_skills_info = None
		if self.skill_service is not None:
			unavailable_skills_info = await self._get_unavailable_skills_info()

		self._message_manager.create_state_messages(
			browser_state_summary=browser_state_summary,
			model_output=self.state.last_model_output,
			result=self.state.last_result,
			step_info=step_info,
			use_vision=self.settings.use_vision,
			page_filtered_actions=page_filtered_actions if page_filtered_actions else None,
			sensitive_data=self.sensitive_data,
			available_file_paths=self.available_file_paths,  # Always pass current available_file_paths
			unavailable_skills_info=unavailable_skills_info,
		)

		await self._force_done_after_last_step(step_info)
		await self._force_done_after_failure()
		return browser_state_summary

	@observe_debug(ignore_input=True, name='get_next_action')
	async def _get_next_action(self, browser_state_summary: BrowserStateSummary) -> None:
		"""
		执行 LLM 交互，获取下一个动作决策

		(组装 消息管理器中的LLM 输入消息、带超时 / 重试调用 LLM 获取动作决策、校验智能体状态（暂停 / 终止）、处理后续回调和对话保存，最终把 LLM 输出存入智能体状态，为后续执行动作做准备)
		
		这是每步执行的第二阶段，负责：
		1. 从消息管理器获取输入消息
		2. 调用 LLM 获取模型输出（带重试逻辑）
		3. 处理回调和保存对话
		
		Args:
		    browser_state_summary: 浏览器状态摘要
		"""
		# 1、从消息管理器获取组装好的输入消息（上下文+任务+浏览器状态）
		input_messages = self._message_manager.get_messages()
		self.logger.debug(
			f'🤖 Step {self.state.n_steps}: Calling LLM with {len(input_messages)} messages (model: {self.llm.model})...'
		)

		# 输入消息就是用户类型的提示词结构大概如下：
		# 1. 回顾历史（agent_history）
		# agent_history = input_messages[-1].content
		# 2. 理解当前任务（agent_state）
		# agent_state = self.state
		# 3. 感知环境（browser_state）
		# browser_state = browser_state_summary
		# 4. 决定下一步动作（工具调用、页面操作等）

		try:
			# 2、调用 LLM，带超时控制
			model_output = await asyncio.wait_for(
				self._get_model_output_with_retry(input_messages), timeout=self.settings.llm_timeout
			)
		except TimeoutError:

			@observe(name='_llm_call_timed_out_with_input')
			async def _log_model_input_to_lmnr(input_messages: list[BaseMessage]) -> None:
				"""Log the model input"""
				pass

			await _log_model_input_to_lmnr(input_messages)

			raise TimeoutError(
				f'LLM call timed out after {self.settings.llm_timeout} seconds. Keep your thinking and output short.'
			)
		# 把LLM输出存入智能体状态，供后续执行动作使用
		self.state.last_model_output = model_output

		# # 第一次校验：获取LLM输出后，检查是否被暂停/终止
		await self._check_stop_or_pause()

		# 处理LLM调用后的回调（如记录token消耗、更新对话历史）+ 保存对话
		await self._handle_post_llm_processing(browser_state_summary, input_messages)

		# 第二次校验：存入历史前再次检查暂停/终止（防止处理回调过程中触发停止）
		await self._check_stop_or_pause()

	async def _execute_actions(self) -> None:
		"""
		执行模型输出的动作：校验 LLM 输出是否存在 → 调用多动作执行器执行动作 → 保存执行结果到智能体状态
		
		这是每步执行的第二阶段，负责：
		1. 从模型输出中提取动作列表
		2. 调用 multi_act 执行多个动作
		3. 保存执行结果到状态
		
		Raises:
		    ValueError: 如果没有模型输出
		"""
		if self.state.last_model_output is None:
			raise ValueError('No model output to execute actions from')

		# 执行多个动作（最多 max_actions_per_step 个）
		result = await self.multi_act(self.state.last_model_output.action)
		self.state.last_result = result

	async def _post_process(self) -> None:
		"""
		动作执行后的后处理
		
		负责：
		1. 检查新下载的文件
		2. 更新连续失败计数
		3. 记录最终结果（如果任务完成）
		
		Raises:
		    AssertionError: 如果浏览器会话未设置
		"""
		assert self.browser_session is not None, 'BrowserSession is not set up'

		# 执行动作后检查新下载
		await self._check_and_update_downloads('after executing actions')

		# check for action errors  and len more than 1
		# self.state.last_result[-1].error 最后一个动作执行出错
		if self.state.last_result and len(self.state.last_result) == 1 and self.state.last_result[-1].error:
			self.state.consecutive_failures += 1
			self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures: {self.state.consecutive_failures}')
			return

		if self.state.consecutive_failures > 0:
			self.state.consecutive_failures = 0
			self.logger.debug(f'🔄 Step {self.state.n_steps}: Consecutive failures reset to: {self.state.consecutive_failures}')

		# Log completion results
		if self.state.last_result and len(self.state.last_result) > 0 and self.state.last_result[-1].is_done:
			success = self.state.last_result[-1].success
			if success:
				# Green color for success
				self.logger.info(f'\n📄 \033[32m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
			else:
				# Red color for failure
				self.logger.info(f'\n📄 \033[31m Final Result:\033[0m \n{self.state.last_result[-1].extracted_content}\n\n')
			if self.state.last_result[-1].attachments:
				total_attachments = len(self.state.last_result[-1].attachments)
				for i, file_path in enumerate(self.state.last_result[-1].attachments):
					self.logger.info(f'👉 Attachment {i + 1 if total_attachments > 1 else ""}: {file_path}')

	async def _handle_step_error(self, error: Exception) -> None:
		"""
		处理步骤执行中发生的所有异常
		
		该方法会：
		1. 特殊处理 InterruptedError（用户中断）
		2. 记录错误日志（包括是否需要调试堆栈）
		3. 更新连续失败计数
		4. 将错误作为 ActionResult 保存，以便后续处理
		
		Args:
		    error: 捕获到的异常对象
		"""

		# Handle InterruptedError specially
		if isinstance(error, InterruptedError):
			error_msg = 'The agent was interrupted mid-step' + (f' - {str(error)}' if str(error) else '')
			# NOTE: This is not an error, it's a normal part of the execution when the user interrupts the agent
			self.logger.warning(f'{error_msg}')
			return

		# Handle all other exceptions
		include_trace = self.logger.isEnabledFor(logging.DEBUG)
		error_msg = AgentError.format_error(error, include_trace=include_trace)
		max_total_failures = self.settings.max_failures + int(self.settings.final_response_after_failure)
		prefix = f'❌ Result failed {self.state.consecutive_failures + 1}/{max_total_failures} times: '
		self.state.consecutive_failures += 1

		# Use WARNING for partial failures, ERROR only when max failures reached
		is_final_failure = self.state.consecutive_failures >= max_total_failures
		log_level = logging.ERROR if is_final_failure else logging.WARNING

		if 'Could not parse response' in error_msg or 'tool_use_failed' in error_msg:
			# give model a hint how output should look like
			self.logger.log(log_level, f'Model: {self.llm.model} failed')
			self.logger.log(log_level, f'{prefix}{error_msg}')
		else:
			self.logger.log(log_level, f'{prefix}{error_msg}')

		await self._demo_mode_log(f'Step error: {error_msg}', 'error', {'step': self.state.n_steps})
		self.state.last_result = [ActionResult(error=error_msg)]
		return None

	async def _finalize(self, browser_state_summary: BrowserStateSummary | None) -> None:
		"""
		步骤的最终化处理：

		单个步骤完成后的最终收尾逻辑，核心负责整合步骤全量信息（执行时间、浏览器状态、动作结果等）、持久化数据、发送事件并推进步骤计数
		
		负责：
		1. 计算步骤执行时间
		2. 创建历史记录项
		3. 记录步骤完成摘要
		4. 保存文件系统状态
		5. 发送事件
		6. 增加步骤计数器
		
		Args:
		    browser_state_summary: 浏览器状态摘要
		"""
		step_end_time = time.time()
		if not self.state.last_result:
			return
		# 量化步骤执行耗时（开始 / 结束时间、与上一步的间隔）；
		if browser_state_summary:
			step_interval = None
			if len(self.history.history) > 0:
				last_history_item = self.history.history[-1]

				if last_history_item.metadata:
					previous_end_time = last_history_item.metadata.step_end_time
					previous_start_time = last_history_item.metadata.step_start_time
					step_interval = max(0, previous_end_time - previous_start_time)
			metadata = StepMetadata(
				step_number=self.state.n_steps,
				step_start_time=self.step_start_time,
				step_end_time=step_end_time,
				step_interval=step_interval,
			)

			# Use _make_history_item like main branch
			# 生成结构化的历史记录（便于回溯步骤执行过程）；
			# 将步骤的 “输入（模型指令）- 过程（浏览器状态）- 输出（执行结果）- 元数据（时间）” 完整记录，形成可回溯的操作日志
			await self._make_history_item(
				self.state.last_model_output,
				browser_state_summary,
				self.state.last_result,
				metadata,
				state_message=self._message_manager.last_state_message_text,
			)

		# Log step completion summary
		# 记录步骤完成摘要日志（可视化执行结果）；比如 “步骤 1 执行完成，耗时 2.5 秒，成功执行 click 动作
		summary_message = self._log_step_completion_summary(self.step_start_time, self.state.last_result)
		if summary_message:
			await self._demo_mode_log(summary_message, 'info', {'step': self.state.n_steps})

		# Save file system state after step completion
		# 持久化文件系统状态（避免下载文件 / 操作记录丢失）；
		# 作用：避免步骤执行中修改的文件状态丢失，保证后续步骤能获取最新的文件信息（比如下一个步骤需要读取本次下载的文件）。
		self.save_file_system_state()

		# Emit both step created and executed events
		# 发送事件（供外部系统监控 / 消费步骤执行数据）；
		if browser_state_summary and self.state.last_model_output:
			# Extract key step data for the event
			actions_data = []
			if self.state.last_model_output.action:
				for action in self.state.last_model_output.action:
					action_dict = action.model_dump() if hasattr(action, 'model_dump') else {}
					actions_data.append(action_dict)

			# Emit CreateAgentStepEvent
			step_event = CreateAgentStepEvent.from_agent_step(
				self,
				self.state.last_model_output,
				self.state.last_result,
				actions_data,
				browser_state_summary,
			)
			self.eventbus.dispatch(step_event)

		# Increment step counter after step is fully completed
		# 推进步骤计数器（准备执行下一个步骤）。
		# 注意：这里使用 “步骤完全完成后再增加计数器”，避免步骤未完成就推进编号导致混乱；
		self.state.n_steps += 1

	async def _force_done_after_last_step(self, step_info: AgentStepInfo | None = None) -> None:
		"""
		处理最后一步的特殊逻辑
		
		如果达到最大步数：
		1. 提示模型这是最后一步
		2. 强制模型只能使用 done 工具
		"""
		if step_info and step_info.is_last_step():
			# Add last step warning if needed
			msg = 'You reached max_steps - this is your last step. Your only tool available is the "done" tool. No other tool is available. All other tools which you see in history or examples are not available.'
			msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed. Else success to true.'
			msg += '\nInclude everything you found out for the ultimate task in the done text.'
			self.logger.debug('Last step finishing up')
			self._message_manager._add_context_message(UserMessage(content=msg))
			self.AgentOutput = self.DoneAgentOutput

	async def _force_done_after_failure(self) -> None:
		"""
		在多次失败后强制结束
		
		如果连续失败次数达到上限：
		1. 提示模型由于失败过多即将终止
		2. 强制模型只能使用 done 工具
		"""
		# Create recovery message
		if self.state.consecutive_failures >= self.settings.max_failures and self.settings.final_response_after_failure:
			msg = f'You failed {self.settings.max_failures} times. Therefore we terminate the agent.'
			msg += '\nYour only tool available is the "done" tool. No other tool is available. All other tools which you see in history or examples are not available.'
			msg += '\nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed. Else success to true.'
			msg += '\nInclude everything you found out for the ultimate task in the done text.'

			self.logger.debug('Force done action, because we reached max_failures.')
			self._message_manager._add_context_message(UserMessage(content=msg))
			self.AgentOutput = self.DoneAgentOutput

	@observe(ignore_input=True, ignore_output=False)
	async def _judge_trace(self) -> JudgementResult | None:
		"""
		评估 Agent 的执行轨迹
		
		使用 judge_llm 对任务完成情况进行评估
		
		Returns:
		    JudgementResult | None: 评估结果，如果失败则返回 None
		"""
		task = self.task
		final_result = self.history.final_result() or ''
		agent_steps = self.history.agent_steps()
		screenshot_paths = [p for p in self.history.screenshot_paths() if p is not None]

		# Construct input messages for judge evaluation
		input_messages = construct_judge_messages(
			task=task,
			final_result=final_result,
			agent_steps=agent_steps,
			screenshot_paths=screenshot_paths,
			max_images=10,
			ground_truth=self.settings.ground_truth,
		)

		# Call LLM with JudgementResult as output format
		kwargs: dict = {'output_format': JudgementResult}

		# Only pass request_type for ChatBrowserUse (other providers don't support it)
		if self.judge_llm.provider == 'browser-use':
			kwargs['request_type'] = 'judge'

		try:
			response = await self.judge_llm.ainvoke(input_messages, **kwargs)
			judgement: JudgementResult = response.completion  # type: ignore[assignment]
			return judgement
		except Exception as e:
			self.logger.error(f'Judge trace failed: {e}')
			# Return a default judgement on failure
			return None

	async def _judge_and_log(self) -> None:
		"""运行评估并记录结论"""
		judgement = await self._judge_trace()

		# Attach judgement to last action result
		if self.history.history[-1].result[-1].is_done:
			last_result = self.history.history[-1].result[-1]
			last_result.judgement = judgement

			# Get self-reported success
			self_reported_success = last_result.success

			# Log the verdict based on self-reported success and judge verdict
			if judgement:
				# If both self-reported and judge agree on success, don't log
				if self_reported_success is True and judgement.verdict is True:
					return

				judge_log = '\n'
				# If agent reported success but judge thinks it failed, show warning
				if self_reported_success is True and judgement.verdict is False:
					judge_log += '⚠️  \033[33mAgent reported success but judge thinks task failed\033[0m\n'

				# Otherwise, show full judge result
				verdict_color = '\033[32m' if judgement.verdict else '\033[31m'
				verdict_text = '✅ PASS' if judgement.verdict else '❌ FAIL'
				judge_log += f'⚖️  {verdict_color}Judge Verdict: {verdict_text}\033[0m\n'
				if judgement.failure_reason:
					judge_log += f'   Failure Reason: {judgement.failure_reason}\n'
				if judgement.reached_captcha:
					judge_log += '   🤖 Captcha Detected: Agent encountered captcha challenges\n'
					judge_log += '   👉 🥷 Use Browser Use Cloud for the most stealth browser infra: https://docs.browser-use.com/customize/browser/remote\n'
				judge_log += f'   {judgement.reasoning}\n'
				self.logger.info(judge_log)

	async def _get_model_output_with_retry(self, input_messages: list[BaseMessage]) -> AgentOutput:
		"""
		获取模型输出，带重试逻辑（针对空动作）
		
		如果模型返回空动作，会进行重试：
		1. 第一次：发送澄清消息要求返回有效动作
		2. 第二次：如果仍然为空，插入安全的 noop 动作
		
		Args:
		    input_messages: 输入消息列表
			
		Returns:
		    AgentOutput: 模型输出
		"""
		# 第一步：调用核心方法获取模型原始输出
		model_output = await self.get_model_output(input_messages)
		self.logger.debug(
			f'✅ Step {self.state.n_steps}: Got LLM response with {len(model_output.action) if model_output.action else 0} actions'
		)
		# 核心判断：检查动作是否为空/无效（三种情况）
		if (
			not model_output.action# 动作字段为空
			or not isinstance(model_output.action, list)# 动作不是列表类型
			or all(action.model_dump() == {} for action in model_output.action)# 动作列表里全是空字典
		):
			self.logger.warning('Model returned empty action. Retrying...')
			# 构造澄清消息：提醒模型返回符合格式的有效动作
			clarification_message = UserMessage(
				content='You forgot to return an action. Please respond with a valid JSON action according to the expected schema with your assessment and next actions.'
			)
			# 重试消息列表 = 原始消息 + 澄清消息
			retry_messages = input_messages + [clarification_message]
			# 第二次调用模型（重试）
			model_output = await self.get_model_output(retry_messages)
			# 二次检查：如果重试后动作仍然无效
			if not model_output.action or all(action.model_dump() == {} for action in model_output.action):
				self.logger.warning('Model still returned empty after retry. Inserting safe noop action.')
				# 创建一个空动作实例（ActionModel是自定义的动作模型类）
				action_instance = self.ActionModel()
				# 给空动作设置「done」属性：标记任务失败，说明原因
				setattr(
					action_instance,
					'done',
					{
						'success': False,
						'text': 'No next action returned by LLM!',
					},
				)
				# 将这个安全的空动作赋值给模型输出，避免程序中断
				model_output.action = [action_instance]
		# 返回最终的模型输出（要么是有效动作，要么是重试后的动作，要么是安全空动作）
		return model_output

	async def _handle_post_llm_processing(
		self,
		browser_state_summary: BrowserStateSummary,
		input_messages: list[BaseMessage],
	) -> None:
		"""处理 LLM 交互后的回调和对话保存
		
		在 LLM 返回动作决策后，完成两件事 —— 触发外部自定义的步骤回调（通知上层系统进度）、将 LLM 的输入输出保存为对话文件（便于复盘和调试）
		"""
		if self.register_new_step_callback and self.state.last_model_output:
			if inspect.iscoroutinefunction(self.register_new_step_callback):
				await self.register_new_step_callback(
					browser_state_summary,
					self.state.last_model_output,
					self.state.n_steps,
				)
			else:
				self.register_new_step_callback(
					browser_state_summary,
					self.state.last_model_output,
					self.state.n_steps,
				)

		if self.settings.save_conversation_path and self.state.last_model_output:
			# Treat save_conversation_path as a directory (consistent with other recording paths)
			conversation_dir = Path(self.settings.save_conversation_path)
			conversation_filename = f'conversation_{self.id}_{self.state.n_steps}.txt'
			target = conversation_dir / conversation_filename
			await save_conversation(
				input_messages,
				self.state.last_model_output,
				target,
				self.settings.save_conversation_path_encoding,
			)

	async def _make_history_item(
		self,
		model_output: AgentOutput | None,
		browser_state_summary: BrowserStateSummary,
		result: list[ActionResult],
		metadata: StepMetadata | None = None,
		state_message: str | None = None,
	) -> None:
		"""
		创建并存储历史记录项
		
		Args:
		    model_output: 模型输出
		    browser_state_summary: 浏览器状态摘要
		    result: 动作执行结果列表
		    metadata: 步骤元数据
		    state_message: 状态消息
		"""

		if model_output:
			interacted_elements = AgentHistory.get_interacted_element(model_output, browser_state_summary.dom_state.selector_map)
		else:
			interacted_elements = [None]

		# Store screenshot and get path
		screenshot_path = None
		if browser_state_summary.screenshot:
			self.logger.debug(
				f'📸 Storing screenshot for step {self.state.n_steps}, screenshot length: {len(browser_state_summary.screenshot)}'
			)
			screenshot_path = await self.screenshot_service.store_screenshot(browser_state_summary.screenshot, self.state.n_steps)
			self.logger.debug(f'📸 Screenshot stored at: {screenshot_path}')
		else:
			self.logger.debug(f'📸 No screenshot in browser_state_summary for step {self.state.n_steps}')

		state_history = BrowserStateHistory(
			url=browser_state_summary.url,
			title=browser_state_summary.title,
			tabs=browser_state_summary.tabs,
			interacted_element=interacted_elements,
			screenshot_path=screenshot_path,
		)

		history_item = AgentHistory(
			model_output=model_output,
			result=result,
			state=state_history,
			metadata=metadata,
			state_message=state_message,
		)

		self.history.add_item(history_item)

	def _remove_think_tags(self, text: str) -> str:
		THINK_TAGS = re.compile(r'<think>.*?</think>', re.DOTALL)
		STRAY_CLOSE_TAG = re.compile(r'.*?</think>', re.DOTALL)
		# Step 1: Remove well-formed <think>...</think>
		text = re.sub(THINK_TAGS, '', text)
		# Step 2: If there's an unmatched closing tag </think>,
		#         remove everything up to and including that.
		text = re.sub(STRAY_CLOSE_TAG, '', text)
		return text.strip()

	# region - URL replacement
	def _replace_urls_in_text(self, text: str) -> tuple[str, dict[str, str]]:
		"""Replace URLs in a text string"""

		replaced_urls: dict[str, str] = {}

		def replace_url(match: re.Match) -> str:
			"""Url can only have 1 query and 1 fragment"""
			import hashlib

			original_url = match.group(0)

			# Find where the query/fragment starts
			query_start = original_url.find('?')
			fragment_start = original_url.find('#')

			# Find the earliest position of query or fragment
			after_path_start = len(original_url)  # Default: no query/fragment
			if query_start != -1:
				after_path_start = min(after_path_start, query_start)
			if fragment_start != -1:
				after_path_start = min(after_path_start, fragment_start)

			# Split URL into base (up to path) and after_path (query + fragment)
			base_url = original_url[:after_path_start]
			after_path = original_url[after_path_start:]

			# If after_path is within the limit, don't shorten
			if len(after_path) <= self._url_shortening_limit:
				return original_url

			# If after_path is too long, truncate and add hash
			if after_path:
				truncated_after_path = after_path[: self._url_shortening_limit]
				# Create a short hash of the full after_path content
				hash_obj = hashlib.md5(after_path.encode('utf-8'))
				short_hash = hash_obj.hexdigest()[:7]
				# Create shortened URL
				shortened = f'{base_url}{truncated_after_path}...{short_hash}'
				# Only use shortened URL if it's actually shorter than the original
				if len(shortened) < len(original_url):
					replaced_urls[shortened] = original_url
					return shortened

			return original_url

		return URL_PATTERN.sub(replace_url, text), replaced_urls

	def _process_messsages_and_replace_long_urls_shorter_ones(self, input_messages: list[BaseMessage]) -> dict[str, str]:
		"""
		将长 URL 替换为短 URL（原地修改 input_messages）
		
		Args:
		    input_messages: 输入消息列表
			
		Returns:
		    dict[str, str]: URL 替换映射 {短URL: 原URL}
		"""
		from browser_use.llm.messages import AssistantMessage, UserMessage

		urls_replaced: dict[str, str] = {}

		# Process each message, in place
		for message in input_messages:
			# no need to process SystemMessage, we have control over that anyway
			if isinstance(message, (UserMessage, AssistantMessage)):
				if isinstance(message.content, str):
					# Simple string content
					message.content, replaced_urls = self._replace_urls_in_text(message.content)
					urls_replaced.update(replaced_urls)

				elif isinstance(message.content, list):
					# List of content parts
					for part in message.content:
						if isinstance(part, ContentPartTextParam):
							part.text, replaced_urls = self._replace_urls_in_text(part.text)
							urls_replaced.update(replaced_urls)

		return urls_replaced

	@staticmethod
	def _recursive_process_all_strings_inside_pydantic_model(model: BaseModel, url_replacements: dict[str, str]) -> None:
		"""
		递归处理 Pydantic 模型中的所有字符串，将短 URL 替换为原 URL（原地修改）
		
		Args:
		    model: Pydantic 模型实例
		    url_replacements: URL 替换映射
		"""
		for field_name, field_value in model.__dict__.items():
			if isinstance(field_value, str):
				# Replace shortened URLs with original URLs in string
				processed_string = Agent._replace_shortened_urls_in_string(field_value, url_replacements)
				setattr(model, field_name, processed_string)
			elif isinstance(field_value, BaseModel):
				# Recursively process nested Pydantic models
				Agent._recursive_process_all_strings_inside_pydantic_model(field_value, url_replacements)
			elif isinstance(field_value, dict):
				# Process dictionary values in place
				Agent._recursive_process_dict(field_value, url_replacements)
			elif isinstance(field_value, (list, tuple)):
				processed_value = Agent._recursive_process_list_or_tuple(field_value, url_replacements)
				setattr(model, field_name, processed_value)

	@staticmethod
	def _recursive_process_dict(dictionary: dict, url_replacements: dict[str, str]) -> None:
		"""
		递归处理字典中的所有字符串，将短 URL 替换为原 URL（原地修改）
		
		Args:
		    dictionary: 字典对象
		    url_replacements: URL 替换映射
		"""
		for k, v in dictionary.items():
			if isinstance(v, str):
				dictionary[k] = Agent._replace_shortened_urls_in_string(v, url_replacements)
			elif isinstance(v, BaseModel):
				Agent._recursive_process_all_strings_inside_pydantic_model(v, url_replacements)
			elif isinstance(v, dict):
				Agent._recursive_process_dict(v, url_replacements)
			elif isinstance(v, (list, tuple)):
				dictionary[k] = Agent._recursive_process_list_or_tuple(v, url_replacements)

	@staticmethod
	def _recursive_process_list_or_tuple(container: list | tuple, url_replacements: dict[str, str]) -> list | tuple:
		"""
		递归处理列表或元组中的所有字符串，将短 URL 替换为原 URL
		
		Args:
		    container: 列表或元组
		    url_replacements: URL 替换映射
			
		Returns:
		    list | tuple: 处理后的列表或元组
		"""
		if isinstance(container, tuple):
			# For tuples, create a new tuple with processed items
			processed_items = []
			for item in container:
				if isinstance(item, str):
					processed_items.append(Agent._replace_shortened_urls_in_string(item, url_replacements))
				elif isinstance(item, BaseModel):
					Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, dict):
					Agent._recursive_process_dict(item, url_replacements)
					processed_items.append(item)
				elif isinstance(item, (list, tuple)):
					processed_items.append(Agent._recursive_process_list_or_tuple(item, url_replacements))
				else:
					processed_items.append(item)
			return tuple(processed_items)
		else:
			# For lists, modify in place
			for i, item in enumerate(container):
				if isinstance(item, str):
					container[i] = Agent._replace_shortened_urls_in_string(item, url_replacements)
				elif isinstance(item, BaseModel):
					Agent._recursive_process_all_strings_inside_pydantic_model(item, url_replacements)
				elif isinstance(item, dict):
					Agent._recursive_process_dict(item, url_replacements)
				elif isinstance(item, (list, tuple)):
					container[i] = Agent._recursive_process_list_or_tuple(item, url_replacements)
			return container

	@staticmethod
	def _replace_shortened_urls_in_string(text: str, url_replacements: dict[str, str]) -> str:
		"""
		将字符串中的所有短 URL 替换为原 URL
		
		Args:
		    text: 包含短 URL 的文本
		    url_replacements: URL 替换映射
			
		Returns:
		    str: 替换后的文本
		"""
		result = text
		for shortened_url, original_url in url_replacements.items():
			result = result.replace(shortened_url, original_url)
		return result

	# endregion - URL replacement

	@time_execution_async('--get_next_action')
	@observe_debug(ignore_input=True, ignore_output=True, name='get_model_output')
	async def get_model_output(self, input_messages: list[BaseMessage]) -> AgentOutput:
		"""
		从 LLM 获取下一个动作决策
		
		这是 Agent 决策的核心方法，负责：
		1. 处理输入消息（包括 URL 缩短）
		2. 调用 LLM 获取响应
		3. 解析和验证模型输出
		4. 处理速率限制和回退逻辑
		
		Args:
		    input_messages: 发送给 LLM 的消息列表
			
		Returns:
		    AgentOutput: 模型输出，包含动作决策和状态信息
		"""

		urls_replaced = self._process_messsages_and_replace_long_urls_shorter_ones(input_messages)

		# Build kwargs for ainvoke
		# Note: ChatBrowserUse will automatically generate action descriptions from output_format schema
		kwargs: dict = {'output_format': self.AgentOutput, 'session_id': self.session_id}

		try:
			response = await self.llm.ainvoke(input_messages, **kwargs)
			parsed: AgentOutput = response.completion  # type: ignore[assignment]

			# 还原输出中的短URL为原始URL（保证动作执行时URL有效
			if urls_replaced:
				self._recursive_process_all_strings_inside_pydantic_model(parsed, urls_replaced)

			# cut the number of actions to max_actions_per_step if needed
			# 裁剪动作数量：避免LLM返回过多动作（超过每步最大限制）
			if len(parsed.action) > self.settings.max_actions_per_step:
				parsed.action = parsed.action[: self.settings.max_actions_per_step]
			# 非暂停/停止状态时，记录响应日志+广播模型状态
			if not (hasattr(self.state, 'paused') and (self.state.paused or self.state.stopped)):
				log_response(parsed, self.tools.registry.registry, self.logger)
				await self._broadcast_model_state(parsed)
			# 记录下一步动作摘要（简化日志，便于快速查看）
			self._log_next_action_summary(parsed)
			return parsed
		except ValidationError:
			# Just re-raise - Pydantic's validation errors are already descriptive
			raise
		except (ModelRateLimitError, ModelProviderError) as e:
			# Check if we can switch to a fallback LLM
			if not self._try_switch_to_fallback_llm(e):
				# No fallback available, re-raise the original error
				raise
			# Retry with the fallback LLM
			return await self.get_model_output(input_messages)

	def _try_switch_to_fallback_llm(self, error: ModelRateLimitError | ModelProviderError) -> bool:
		"""
		尝试在速率限制或提供者错误后切换到备用 LLM
		
		支持的错误代码：
		- 401: API 密钥无效/过期
		- 402: 余额不足/需要付费
		- 429: 速率限制
		- 500, 502, 503, 504: 服务器错误
		
		Args:
		    error: 模型错误（速率限制或提供者错误）
			
		Returns:
		    bool: 如果成功切换到备用 LLM 返回 True，否则返回 False
		    
		Note:
		    一旦切换，代理将在剩余运行中使用备用 LLM
		"""
		# Already using fallback - can't switch again
		if self._using_fallback_llm:
			self.logger.warning(
				f'⚠️ Fallback LLM also failed ({type(error).__name__}: {error.message}), no more fallbacks available'
			)
			return False

		# Check if error is retryable (rate limit, auth errors, or server errors)
		# 401: API key invalid/expired - fallback to different provider
		# 402: Insufficient credits/payment required - fallback to different provider
		# 429: Rate limit exceeded
		# 500, 502, 503, 504: Server errors
		retryable_status_codes = {401, 402, 429, 500, 502, 503, 504}
		is_retryable = isinstance(error, ModelRateLimitError) or (
			hasattr(error, 'status_code') and error.status_code in retryable_status_codes
		)

		if not is_retryable:
			return False

		# Check if we have a fallback LLM configured
		if self._fallback_llm is None:
			self.logger.warning(f'⚠️ LLM error ({type(error).__name__}: {error.message}) but no fallback_llm configured')
			return False

		self._log_fallback_switch(error, self._fallback_llm)

		# Switch to the fallback LLM
		self.llm = self._fallback_llm
		self._using_fallback_llm = True

		# Register the fallback LLM for token cost tracking
		self.token_cost_service.register_llm(self._fallback_llm)

		return True

	def _log_fallback_switch(self, error: ModelRateLimitError | ModelProviderError, fallback: BaseChatModel) -> None:
		"""Log when switching to a fallback LLM."""
		original_model = self._original_llm.model if hasattr(self._original_llm, 'model') else 'unknown'
		fallback_model = fallback.model if hasattr(fallback, 'model') else 'unknown'
		error_type = type(error).__name__
		status_code = getattr(error, 'status_code', 'N/A')

		self.logger.warning(
			f'⚠️ Primary LLM ({original_model}) failed with {error_type} (status={status_code}), '
			f'switching to fallback LLM ({fallback_model})'
		)

	async def _log_agent_run(self) -> None:
		"""Log the agent run"""
		# Blue color for task
		self.logger.info(f'\033[34m🎯 Task: {self.task}\033[0m')

		self.logger.debug(f'🤖 Browser-Use Library Version {self.version} ({self.source})')

		# Check for latest version and log upgrade message if needed
		if CONFIG.BROWSER_USE_VERSION_CHECK:
			latest_version = await check_latest_browser_use_version()
			if latest_version and latest_version != self.version:
				self.logger.info(
					f'📦 Newer version available: {latest_version} (current: {self.version}). Upgrade with: uv add browser-use=={latest_version}'
				)

	def _log_first_step_startup(self) -> None:
		"""仅在第一步时记录启动消息"""
		if len(self.history.history) == 0:
			self.logger.info(
				f'Starting a browser-use agent with version {self.version}, with provider={self.llm.provider} and model={self.llm.model}'
			)

	def _log_step_context(self, browser_state_summary: BrowserStateSummary) -> None:
		"""记录步骤上下文信息，包括URL和交互元素数量"""
		url = browser_state_summary.url if browser_state_summary else ''
		url_short = url[:50] + '...' if len(url) > 50 else url
		interactive_count = len(browser_state_summary.dom_state.selector_map) if browser_state_summary else 0
		self.logger.info('\n')
		self.logger.info(f'📍 Step {self.state.n_steps}:')
		self.logger.debug(f'Evaluating page with {interactive_count} interactive elements on: {url_short}')

	def _log_next_action_summary(self, parsed: 'AgentOutput') -> None:
		"""记录下一个动作的详细摘要"""
		if not (self.logger.isEnabledFor(logging.DEBUG) and parsed.action):
			return

		action_count = len(parsed.action)

		# Collect action details
		action_details = []
		for i, action in enumerate(parsed.action):
			action_data = action.model_dump(exclude_unset=True)
			action_name = next(iter(action_data.keys())) if action_data else 'unknown'
			action_params = action_data.get(action_name, {}) if action_data else {}

			# Format key parameters concisely
			param_summary = []
			if isinstance(action_params, dict):
				for key, value in action_params.items():
					if key == 'index':
						param_summary.append(f'#{value}')
					elif key == 'text' and isinstance(value, str):
						text_preview = value[:30] + '...' if len(value) > 30 else value
						param_summary.append(f'text="{text_preview}"')
					elif key == 'url':
						param_summary.append(f'url="{value}"')
					elif key == 'success':
						param_summary.append(f'success={value}')
					elif isinstance(value, (str, int, bool)):
						val_str = str(value)[:30] + '...' if len(str(value)) > 30 else str(value)
						param_summary.append(f'{key}={val_str}')

			param_str = f'({", ".join(param_summary)})' if param_summary else ''
			action_details.append(f'{action_name}{param_str}')

	def _prepare_demo_message(self, message: str, limit: int = 600) -> str:
		"""准备演示模式消息"""
		# 之前会截断长条目；现在保留完整文本以在演示面板中提供更好的上下文
		return message.strip()

	async def _demo_mode_log(self, message: str, level: str = 'info', metadata: dict[str, Any] | None = None) -> None:
		"""发送演示模式日志消息到浏览器叠加层"""
		if not self._demo_mode_enabled or not message or self.browser_session is None:
			return
		try:
			await self.browser_session.send_demo_mode_log(
				message=self._prepare_demo_message(message),
				level=level,
				metadata=metadata or {},
			)
		except Exception as exc:
			self.logger.debug(f'[DemoMode] Failed to send overlay log: {exc}')

	async def _broadcast_model_state(self, parsed: 'AgentOutput') -> None:
		"""广播模型状态到演示模式日志"""
		if not self._demo_mode_enabled:
			return

		state = parsed.current_state
		step_meta = {'step': self.state.n_steps}

		if state.thinking:
			await self._demo_mode_log(state.thinking, 'thought', step_meta)

		if state.evaluation_previous_goal:
			eval_text = state.evaluation_previous_goal
			level = 'success' if 'success' in eval_text.lower() else 'warning' if 'failure' in eval_text.lower() else 'info'
			await self._demo_mode_log(eval_text, level, step_meta)

		if state.memory:
			await self._demo_mode_log(f'Memory: {state.memory}', 'info', step_meta)

		if state.next_goal:
			await self._demo_mode_log(f'Next goal: {state.next_goal}', 'info', step_meta)

	def _log_step_completion_summary(self, step_start_time: float, result: list[ActionResult]) -> str | None:
		"""记录步骤完成摘要，包括动作计数、时间和成功/失败统计"""
		if not result:
			return None

		step_duration = time.time() - step_start_time
		action_count = len(result)

		# Count success and failures
		success_count = sum(1 for r in result if not r.error)
		failure_count = action_count - success_count

		# Format success/failure indicators
		success_indicator = f'✅ {success_count}' if success_count > 0 else ''
		failure_indicator = f'❌ {failure_count}' if failure_count > 0 else ''
		status_parts = [part for part in [success_indicator, failure_indicator] if part]
		status_str = ' | '.join(status_parts) if status_parts else '✅ 0'

		message = (
			f'📍 Step {self.state.n_steps}: Ran {action_count} action{"" if action_count == 1 else "s"} '
			f'in {step_duration:.2f}s: {status_str}'
		)
		self.logger.debug(message)
		return message

	def _log_final_outcome_messages(self) -> None:
		"""根据代理运行结果向用户记录有用的消息"""
		# 检查代理是否失败
		is_successful = self.history.is_successful()

		if is_successful is False or is_successful is None:
			# Get final result to check for specific failure reasons
			final_result = self.history.final_result()
			final_result_str = str(final_result).lower() if final_result else ''

			# Check for captcha/cloudflare related failures
			captcha_keywords = ['captcha', 'cloudflare', 'recaptcha', 'challenge', 'bot detection', 'access denied']
			has_captcha_issue = any(keyword in final_result_str for keyword in captcha_keywords)

			if has_captcha_issue:
				# Suggest use_cloud=True for captcha/cloudflare issues
				task_preview = self.task[:10] if len(self.task) > 10 else self.task
				self.logger.info('')
				self.logger.info('Failed because of CAPTCHA? For better browser stealth, try:')
				self.logger.info(f'   agent = Agent(task="{task_preview}...", browser=Browser(use_cloud=True))')

			# General failure message
			self.logger.info('')
			self.logger.info('Did the Agent not work as expected? Let us fix this!')
			self.logger.info('   Open a short issue on GitHub: https://github.com/browser-use/browser-use/issues')

	def _log_agent_event(self, max_steps: int, agent_run_error: str | None = None) -> None:
		"""发送此次运行的代理事件到遥测系统"""

		token_summary = self.token_cost_service.get_usage_tokens_for_model(self.llm.model)

		# Prepare action_history data correctly
		action_history_data = []
		for item in self.history.history:
			if item.model_output and item.model_output.action:
				# Convert each ActionModel in the step to its dictionary representation
				step_actions = [
					action.model_dump(exclude_unset=True)
					for action in item.model_output.action
					if action  # Ensure action is not None if list allows it
				]
				action_history_data.append(step_actions)
			else:
				# Append None or [] if a step had no actions or no model output
				action_history_data.append(None)

		final_res = self.history.final_result()
		final_result_str = json.dumps(final_res) if final_res is not None else None

		# Extract judgement data if available
		judgement_data = self.history.judgement()
		judge_verdict = judgement_data.get('verdict') if judgement_data else None
		judge_reasoning = judgement_data.get('reasoning') if judgement_data else None
		judge_failure_reason = judgement_data.get('failure_reason') if judgement_data else None
		judge_reached_captcha = judgement_data.get('reached_captcha') if judgement_data else None
		judge_impossible_task = judgement_data.get('impossible_task') if judgement_data else None

		self.telemetry.capture(
			AgentTelemetryEvent(
				task=self.task,
				model=self.llm.model,
				model_provider=self.llm.provider,
				max_steps=max_steps,
				max_actions_per_step=self.settings.max_actions_per_step,
				use_vision=self.settings.use_vision,
				version=self.version,
				source=self.source,
				cdp_url=urlparse(self.browser_session.cdp_url).hostname
				if self.browser_session and self.browser_session.cdp_url
				else None,
				agent_type=None,  # Regular Agent (not code-use)
				action_errors=self.history.errors(),
				action_history=action_history_data,
				urls_visited=self.history.urls(),
				steps=self.state.n_steps,
				total_input_tokens=token_summary.prompt_tokens,
				total_output_tokens=token_summary.completion_tokens,
				prompt_cached_tokens=token_summary.prompt_cached_tokens,
				total_tokens=token_summary.total_tokens,
				total_duration_seconds=self.history.total_duration_seconds(),
				success=self.history.is_successful(),
				final_result_response=final_result_str,
				error_message=agent_run_error,
				judge_verdict=judge_verdict,
				judge_reasoning=judge_reasoning,
				judge_failure_reason=judge_failure_reason,
				judge_reached_captcha=judge_reached_captcha,
				judge_impossible_task=judge_impossible_task,
			)
		)

	async def take_step(self, step_info: AgentStepInfo | None = None) -> tuple[bool, bool]:
		"""执行一个步骤

		Returns:
		        Tuple[bool, bool]: (is_done, is_valid) - 是否完成，是否有效
		"""
		if step_info is not None and step_info.step_number == 0:
			# First step
			self._log_first_step_startup()
			# Normally there was no try catch here but the callback can raise an InterruptedError which we skip
			try:
				await self._execute_initial_actions()
			except InterruptedError:
				pass
			except Exception as e:
				raise e

		await self.step(step_info)

		if self.history.is_done():
			await self.log_completion()

			# Run judge before done callback if enabled
			if self.settings.use_judge:
				await self._judge_and_log()

			if self.register_done_callback:
				if inspect.iscoroutinefunction(self.register_done_callback):
					await self.register_done_callback(self.history)
				else:
					self.register_done_callback(self.history)
			return True, True

		return False, False

	def _extract_start_url(self, task: str) -> str | None:
		"""Extract URL from task string using naive pattern matching.
		
		一个基于正则匹配的 URL 提取函数，核心目标是从用户的任务字符串中，筛选出唯一、可导航的起始 URL，同时通过多层过滤规则排除无效 / 无关的 URL，避免误识别：
			先移除任务中的邮箱地址（避免被误判为 URL）；
			用正则匹配识别两种常见 URL 格式；
			过滤掉带特定文件后缀、含否定语境的 URL；
			自动补全 HTTPS 协议；
			返回值：仅返回唯一匹配的 URL（多个则返回 None，避免歧义，不自动导航）。
		
		"""

		import re

		# Remove email addresses from task before looking for URLs
		task_without_emails = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '', task)

		# Look for common URL patterns
		patterns = [
			r'https?://[^\s<>"\']+',  # Full URLs with http/https
			r'(?:www\.)?[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}(?:/[^\s<>"\']*)?',  # Domain names with subdomains and optional paths
		]

		# File extensions that should be excluded from URL detection
		# These are likely files rather than web pages to navigate to
		excluded_extensions = {
			# Documents
			'pdf',
			'doc',
			'docx',
			'xls',
			'xlsx',
			'ppt',
			'pptx',
			'odt',
			'ods',
			'odp',
			# Text files
			'txt',
			'md',
			'csv',
			'json',
			'xml',
			'yaml',
			'yml',
			# Archives
			'zip',
			'rar',
			'7z',
			'tar',
			'gz',
			'bz2',
			'xz',
			# Images
			'jpg',
			'jpeg',
			'png',
			'gif',
			'bmp',
			'svg',
			'webp',
			'ico',
			# Audio/Video
			'mp3',
			'mp4',
			'avi',
			'mkv',
			'mov',
			'wav',
			'flac',
			'ogg',
			# Code/Data
			'py',
			'js',
			'css',
			'java',
			'cpp',
			# Academic/Research
			'bib',
			'bibtex',
			'tex',
			'latex',
			'cls',
			'sty',
			# Other common file types
			'exe',
			'msi',
			'dmg',
			'pkg',
			'deb',
			'rpm',
			'iso',
			# GitHub/Project paths
			'polynomial',
		}

		excluded_words = {
			'never',
			'dont',
			'not',
			"don't",
		}

		found_urls = []
		for pattern in patterns:
			matches = re.finditer(pattern, task_without_emails)
			for match in matches:
				url = match.group(0)
				original_position = match.start()  # Store original position before URL modification

				# Remove trailing punctuation that's not part of URLs
				url = re.sub(r'[.,;:!?()\[\]]+$', '', url)

				# Check if URL ends with a file extension that should be excluded
				url_lower = url.lower()
				should_exclude = False
				for ext in excluded_extensions:
					if f'.{ext}' in url_lower:
						should_exclude = True
						break

				if should_exclude:
					self.logger.debug(f'Excluding URL with file extension from auto-navigation: {url}')
					continue

				# If in the 20 characters before the url position is a word in excluded_words skip to avoid "Never go to this url"
				context_start = max(0, original_position - 20)
				context_text = task_without_emails[context_start:original_position]
				if any(word.lower() in context_text.lower() for word in excluded_words):
					self.logger.debug(
						f'Excluding URL with word in excluded words from auto-navigation: {url} (context: "{context_text.strip()}")'
					)
					continue

				# Add https:// if missing (after excluded words check to avoid position calculation issues)
				if not url.startswith(('http://', 'https://')):
					url = 'https://' + url

				found_urls.append(url)

		unique_urls = list(set(found_urls))
		# If multiple URLs found, skip directly_open_urling
		if len(unique_urls) > 1:
			self.logger.debug(f'Multiple URLs found ({len(found_urls)}), skipping directly_open_url to avoid ambiguity')
			return None

		# If exactly one URL found, return it
		if len(unique_urls) == 1:
			return unique_urls[0]

		return None

	async def _execute_step(
		self,
		step: int,
		max_steps: int,
		step_info: AgentStepInfo,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> bool:
		"""
		执行智能体的单个步骤，添加超时保护，处理步骤级回调和演示日志，判断任务是否完成，返回布尔值标记步骤结束后任务是否完成

		Returns:
			bool: 如果任务完成返回 True，否则返回 False
		"""
		# 1. 执行步骤开始前的回调函数（外部传入的钩子）
		if on_step_start is not None:
			await on_step_start(self)
		# 2. 演示模式下输出步骤开始日志（带步数/总步数元数据）
		await self._demo_mode_log(
			f'Starting step {step + 1}/{max_steps}',
			'info',
			{'step': step + 1, 'total_steps': max_steps},
		)

		self.logger.debug(f'🚶 Starting step {step + 1}/{max_steps}...')

		try:
			# 执行核心步骤逻辑，添加超时保护（超时时间由配置指定）
			await asyncio.wait_for(
				# 真正执行单步的核心逻辑（如调用LLM、操作浏览器）
				self.step(step_info),
				# 步骤超时时间（比如30秒）
				timeout=self.settings.step_timeout,
			)
			self.logger.debug(f'✅ Completed step {step + 1}/{max_steps}')
		except TimeoutError:
			# 处理步骤超时异常（优雅容错）
			error_msg = f'Step {step + 1} timed out after {self.settings.step_timeout} seconds'
			self.logger.error(f'⏰ {error_msg}')
			await self._demo_mode_log(error_msg, 'error', {'step': step + 1})
			# 更新状态：连续失败次数+1，记录超时错误结果
			self.state.consecutive_failures += 1
			self.state.last_result = [ActionResult(error=error_msg)]

		if on_step_end is not None:
			await on_step_end(self)

		if self.history.is_done():
			await self.log_completion()

			# Run judge before done callback if enabled
			if self.settings.use_judge:
				await self._judge_and_log()

			if self.register_done_callback:
				if inspect.iscoroutinefunction(self.register_done_callback):
					await self.register_done_callback(self.history)
				else:
					self.register_done_callback(self.history)

			return True

		return False

	@observe(name='agent.run', ignore_input=True, ignore_output=True)
	@time_execution_async('--run')
	async def run(
		self,
		max_steps: int = 100,  # 最大执行步数，默认100步
		on_step_start: AgentHookFunc | None = None,  # 每步开始前的回调函数
		on_step_end: AgentHookFunc | None = None,  # 每步结束后的回调函数
	) -> AgentHistoryList[AgentStructuredOutput]:
		"""
		执行任务的主要方法 - 运行 Agent 完成指定任务
		- 作用：按照指定的最大步数运行智能体完成任务，同时处理暂停 / 终止信号、记录遥测数据、分发事件、清理资源，最终返回执行历史
		Args:
		    max_steps: 最大执行步数，防止无限循环
		    on_step_start: 每步开始前的回调函数
		    on_step_end: 每步结束后的回调函数
			
		Returns:
		    AgentHistoryList: 执行历史记录列表，包含每一步的执行结果
		"""
		# 获取异步循环、初始化错误追踪、强制退出标记
		loop = asyncio.get_event_loop()
		agent_run_error: str | None = None  # Initialize error tracking variable
		self._force_exit_telemetry_logged = False  # ADDED: Flag for custom telemetry on force exit
		should_delay_close = False

		# Set up the  signal handler with callbacks specific to this agent
		# 注册信号处理器（处理Ctrl+C等退出信号）
		# 核心作用：处理用户手动终止（Ctrl+C），保证强制退出时也能记录遥测数据，避免数据丢失；
		from browser_use.utils import SignalHandler

		# Define the custom exit callback function for second CTRL+C
		# 强制退出时记录遥测、刷盘、标记已记录
		def on_force_exit_log_telemetry():
			self._log_agent_event(max_steps=max_steps, agent_run_error='SIGINT: Cancelled by user')
			# NEW: Call the flush method on the telemetry instance
			if hasattr(self, 'telemetry') and self.telemetry:
				self.telemetry.flush()
			self._force_exit_telemetry_logged = True  # Set the flag

		signal_handler = SignalHandler(
			loop=loop,
			pause_callback=self.pause,
			resume_callback=self.resume,
			custom_exit_callback=on_force_exit_log_telemetry,  # 强制退出回调
			exit_on_second_int=True, # 第一次 Ctrl+C 暂停，第二次Ctrl+C强制退出
		)
		signal_handler.register()

		try:
			# 记录启动日志、初始化计时
			await self._log_agent_run()

			self.logger.debug(
				f'🔧 Agent setup: Agent Session ID {self.session_id[-4:]}, Task ID {self.task_id[-4:]}, Browser Session ID {self.browser_session.id[-4:] if self.browser_session else "None"} {"(connecting via CDP)" if (self.browser_session and self.browser_session.cdp_url) else "(launching local browser)"}'
			)

			# Initialize timing for session and task
			self._session_start_time = time.time()
			self._task_start_time = self._session_start_time  # Initialize task start time

			# Only dispatch session events if this is the first run
			# 首次运行时分发「创建会话事件」
			if not self.state.session_initialized:
				self.logger.debug('📡 Dispatching CreateAgentSessionEvent...')
				# Emit CreateAgentSessionEvent at the START of run()
				self.eventbus.dispatch(CreateAgentSessionEvent.from_agent(self))

				self.state.session_initialized = True

			self.logger.debug('📡 Dispatching CreateAgentTaskEvent...')
			# Emit CreateAgentTaskEvent at the START of run()
			# 分发「创建任务事件」
			self.eventbus.dispatch(CreateAgentTaskEvent.from_agent(self))

			# Log startup message on first step (only if we haven't already done steps)
			
			self._log_first_step_startup()
			# Start browser session and attach watchdogs
			# 启动浏览器会话 ==> 执行完这句话后，浏览器才真正启动
			self.logger.debug('🌐 --2516---Starting browser session...')
			await self.browser_session.start()
			# 当启用演示模式时，向用户 / 前端面板输出友好的任务启动提示和模式说明，让使用者清晰感知任务状态和演示模式的交互方式
			if self._demo_mode_enabled:
				await self._demo_mode_log(f'Started task: {self.task}', 'info', {'tag': 'task'})
				await self._demo_mode_log(
					'Demo mode active - follow the side panel for live thoughts and actions.',
					'info',
					{'tag': 'status'},
				)

			# 注册技能：Register skills as actions if SkillService is configured
			await self._register_skills_as_actions()

			# 执行初始动作，并同步到agent状态中作为第0步----如果传了initial_actions参数：Normally there was no try catch here but the callback can raise an InterruptedErro
			try:
				await self._execute_initial_actions()
			except InterruptedError:
				pass
			except Exception as e:
				raise e

			self.logger.debug(
				f'🔄 Starting main execution loop with max {max_steps} steps (currently at step {self.state.n_steps})...'
			)

			# 核心执行循环（步骤控制）
			while self.state.n_steps <= max_steps:
				# 转为0索引
				current_step = self.state.n_steps - 1  # Convert to 0-indexed for step_info

				# # 暂停逻辑：若暂停则等待外部恢复信号 Use the consolidated pause state management
				if self.state.paused:
					self.logger.debug(f'⏸️ Step {self.state.n_steps}: Agent paused, waiting to resume...')
					await self._external_pause_event.wait()
					signal_handler.reset()

				# # 失败次数超限：终止循环：Check if we should stop due to too many failures, if final_response_after_failure is True, we try one last time
				if (self.state.consecutive_failures) >= self.settings.max_failures + int(
					self.settings.final_response_after_failure
				):
					self.logger.error(f'❌ Stopping due to {self.settings.max_failures} consecutive failures')
					agent_run_error = f'Stopped due to {self.settings.max_failures} consecutive failures'
					break

				# 外部终止：终止循环： Check control flags before each step
				if self.state.stopped:
					self.logger.info('🛑 Agent stopped')
					agent_run_error = 'Agent stopped programmatically'
					break

				step_info = AgentStepInfo(step_number=current_step, max_steps=max_steps)
				# 执行单步动作，返回是否完成任务
				is_done = await self._execute_step(current_step, max_steps, step_info, on_step_start, on_step_end)

				if is_done:
					# Agent has marked the task as done
					# 注释：Agent已标记任务为完成
					if self._demo_mode_enabled and self.history.history:
						# 获取最终结果（无结果则默认'Task completed'）
						final_result_text = self.history.final_result() or 'Task completed'
						await self._demo_mode_log(f'Final Result: {final_result_text}', 'success', {'tag': 'task'})

					# 标记需要延迟关闭资源（比如演示模式下给用户 30 秒看结果，再关闭浏览器 / 事件总线）
					should_delay_close = True
					break
			# 循环正常结束时的逻辑：break后不走这个else
			else:
				# 步数超限--导致的---循环正常结束（未break）
				agent_run_error = 'Failed to complete task in maximum steps'
				# 记录步数超限错误到历史
				self.history.add_item(
					AgentHistory(
						model_output=None,
						result=[ActionResult(error=agent_run_error, include_in_memory=True)],
						state=BrowserStateHistory(
							url='',
							title='',
							tabs=[],
							interacted_element=[],
							screenshot_path=None,
						),
						metadata=None,
					)
				)
				
				self.logger.info(f'❌ {agent_run_error}')

			self.history.usage = await self.token_cost_service.get_usage_summary()

			# set the model output schema and call it on the fly
			if self.history._output_model_schema is None and self.output_model_schema is not None:
				self.history._output_model_schema = self.output_model_schema

			return self.history

		except KeyboardInterrupt:
			# Already handled by our signal handler, but catch any direct KeyboardInterrupt as well
			self.logger.debug('Got KeyboardInterrupt during execution, returning current history')
			agent_run_error = 'KeyboardInterrupt'

			self.history.usage = await self.token_cost_service.get_usage_summary()

			return self.history

		except Exception as e:
			self.logger.error(f'Agent run failed with exception: {e}', exc_info=True)
			agent_run_error = str(e)
			raise e

		finally:
			if should_delay_close and self._demo_mode_enabled and agent_run_error is None:
				await asyncio.sleep(30)
			if agent_run_error:
				await self._demo_mode_log(f'Agent stopped: {agent_run_error}', 'error', {'tag': 'run'})
			# Log token usage summary
			await self.token_cost_service.log_usage_summary()

			# Unregister signal handlers before cleanup
			signal_handler.unregister()

			if not self._force_exit_telemetry_logged:  # MODIFIED: Check the flag
				try:
					self._log_agent_event(max_steps=max_steps, agent_run_error=agent_run_error)
				except Exception as log_e:  # Catch potential errors during logging itself
					self.logger.error(f'Failed to log telemetry event: {log_e}', exc_info=True)
			else:
				# ADDED: Info message when custom telemetry for SIGINT was already logged
				self.logger.debug('Telemetry for force exit (SIGINT) was logged by custom exit callback.')

			# NOTE: CreateAgentSessionEvent and CreateAgentTaskEvent are now emitted at the START of run()
			# to match backend requirements for CREATE events to be fired when entities are created,
			# not when they are completed

			# Emit UpdateAgentTaskEvent at the END of run() with final task state
			self.eventbus.dispatch(UpdateAgentTaskEvent.from_agent(self))

			# Generate GIF if needed before stopping event bus
			if self.settings.generate_gif:
				output_path: str = 'agent_history.gif'
				if isinstance(self.settings.generate_gif, str):
					output_path = self.settings.generate_gif

				# Lazy import gif module to avoid heavy startup cost
				from browser_use.agent.gif import create_history_gif

				create_history_gif(task=self.task, history=self.history, output_path=output_path)

				# Only emit output file event if GIF was actually created
				if Path(output_path).exists():
					output_event = await CreateAgentOutputFileEvent.from_agent_and_file(self, output_path)
					self.eventbus.dispatch(output_event)

			# Log final messages to user based on outcome
			self._log_final_outcome_messages()

			# Stop the event bus gracefully, waiting for all events to be processed
			# Use longer timeout to avoid deadlocks in tests with multiple agents
			await self.eventbus.stop(timeout=3.0)

			await self.close()

	@observe_debug(ignore_input=True, ignore_output=True)
	@time_execution_async('--multi_act')
	async def multi_act(self, actions: list[ActionModel]) -> list[ActionResult]:
		"""
		执行多个动作

		- 按顺序执行动作列表，处理动作间延迟、done 动作特殊规则、暂停 / 停止信号，记录执行日志和耗时，遇到 done / 错误 / 最后一个动作时终止执行，最终返回所有已执行动作的结果列表
		
		按顺序执行动作列表，支持：
		1. 动作之间的延迟（wait_between_actions）
		2. 在遇到 done 动作时停止
		3. 在遇到错误时停止
		4. 暂停/停止检查
		
		Args:
		    actions: 要执行的动作列表
			
		Returns:
		    list[ActionResult]: 每个动作的执行结果列表
		"""
		results: list[ActionResult] = []
		time_elapsed = 0
		total_actions = len(actions)
		# 校验浏览器会话是否存在（前置保障）
		assert self.browser_session is not None, 'BrowserSession is not set up'
		# 预处理浏览器缓存的DOM选择器映射（用于快速定位页面元素，提升动作执行效率）：加载缓存的 DOM 选择器映射（页面元素的哈希 / 选择器对应关系），减少动作执行时重新查询 DOM 的耗时，提升执行效率；
		try:
			if (
				self.browser_session._cached_browser_state_summary is not None
				and self.browser_session._cached_browser_state_summary.dom_state is not None
			):
				# 1. 深拷贝缓存中的选择器映射（哈希->选择器），避免修改原缓存
				cached_selector_map = dict(self.browser_session._cached_browser_state_summary.dom_state.selector_map)
				# 2. 提取所有缓存元素的父分支哈希，存入集合（方便快速判断元素是否已缓存）
				cached_element_hashes = {e.parent_branch_hash() for e in cached_selector_map.values()}
			else:
				# 缓存不存在时，初始化空字典和空集合
				cached_selector_map = {}
				cached_element_hashes = set()
		except Exception as e:
			# 异常捕获兜底：即使缓存读取失败，也不中断动作执行，仅初始化空缓存。
			self.logger.error(f'Error getting cached selector map: {e}')
			cached_selector_map = {}
			cached_element_hashes = set()
		# 遍历执行每个动作（核心循环）：按顺序执行一系列浏览器自动化动作（如 click/type/done 等），并包含了 4 条核心执行规则、异常处理、日志记录和耗时统计等完善的工程化逻辑
		for i, action in enumerate(actions):
			""" 主要逻辑：
			1. 遍历一个动作列表（actions），按顺序异步执行每个动作；
			2. 内置 4 条执行规则（done 动作限制、动作间延迟、暂停 / 停止检查、终止条件）；
			3. 记录每个动作的执行日志、耗时、结果（成功 / 失败 / 完成）；
			4. 捕获执行异常并友好处理，最终返回所有动作的执行结果。
			"""
			# 规则1：done动作仅允许作为单个动作执行（若done出现在非第一个位置，直接终止循环）
			if i > 0:
				# 限制done动作只能单独执行（比如整个动作列表只能是[done]，不能是[click, done]），避免逻辑混乱
				if action.model_dump(exclude_unset=True).get('done') is not None:
					msg = f'Done action is allowed only as a single action - stopped after action {i} / {total_actions}.'
					self.logger.debug(msg)
					break

			# 规则2：非第一个动作，执行动作间延迟（避免操作过快导致页面未加载）
			if i > 0:
				# 第 2 个及以后的动作执行前，先等待指定时长
				self.logger.debug(f'Waiting {self.browser_profile.wait_between_actions} seconds between actions')
				# wait_between_actions，比如 1 秒。避免操作过快导致页面元素未加载完成，减少元素定位失败的概率
				await asyncio.sleep(self.browser_profile.wait_between_actions)

			try:
				# 规则3：执行前检查暂停/停止信号（响应外部控制）
				await self._check_stop_or_pause()
				# 解析动作名称（如click/type/done）
				# action_data：动作对象转为的字典（比如{'click': {'selector': '#login'}}）；
				action_data = action.model_dump(exclude_unset=True)
				# 取字典第一个键作为动作名称（比如click）
				action_name = next(iter(action_data.keys())) if action_data else 'unknown'

				# 记录动作执行前日志（便于调试）
				await self._log_action(action, action_name, i + 1, total_actions)
				# 统计动作执行耗时
				time_start = time.time()

				# 核心：调用工具执行单个动作
				result = await self.tools.act(
					action=action,  # 当前要执行的动作对象
					browser_session=self.browser_session,  # 浏览器会话（包含缓存、页面等）
					# 其他参数：文件系统、LLM 配置、敏感数据、可用文件路径等，适配复杂业务场景；
					file_system=self.file_system,
					page_extraction_llm=self.settings.page_extraction_llm,
					sensitive_data=self.sensitive_data,
					available_file_paths=self.available_file_paths,
				)
				# 返回值result：动作执行结果对象，包含error（错误信息）、is_done（是否完成）、success（是否成功）等属性

				time_end = time.time()
				time_elapsed = time_end - time_start
				# 动作执行结果处理：错误/完成日志
				if result.error:
					# 记录错误日志（比如 “动作 click 失败：元素未找到”）；
					await self._demo_mode_log(
						f'Action "{action_name}" failed: {result.error}',
						'error',
						{'action': action_name, 'step': self.state.n_steps},
					)
				# 记录完成日志（成功 / 警告级别）；
				elif result.is_done:
					completion_text = result.long_term_memory or result.extracted_content or 'Task marked as done.'
					level = 'success' if result.success is not False else 'warning'
					await self._demo_mode_log(
						completion_text,
						level,
						{'action': action_name, 'step': self.state.n_steps},
					)
				# 保存当前动作结果
				results.append(result)
				# 规则4：遇到done/错误/最后一个动作，终止循环
				if results[-1].is_done or results[-1].error or i == total_actions - 1:
					break

			except Exception as e:
				# 捕获动作执行中的所有异常，记录日志并重新抛出
				self.logger.error(f'❌ Executing action {i + 1} failed -> {type(e).__name__}: {e}')
				await self._demo_mode_log(
					f'Action "{action_name}" raised {type(e).__name__}: {e}',
					'error',
					{'action': action_name, 'step': self.state.n_steps},
				)
				raise e

		return results

	async def _log_action(self, action, action_name: str, action_num: int, total_actions: int) -> None:
		"""在执行前记录动作（带彩色格式化）"""
		# 颜色定义
		blue = '\033[34m'  # 动作名称
		magenta = '\033[35m'  # 参数名称
		reset = '\033[0m'

		# Format action number and name
		if total_actions > 1:
			action_header = f'▶️  [{action_num}/{total_actions}] {blue}{action_name}{reset}:'
			plain_header = f'▶️  [{action_num}/{total_actions}] {action_name}:'
		else:
			action_header = f'▶️   {blue}{action_name}{reset}:'
			plain_header = f'▶️  {action_name}:'

		# Get action parameters
		action_data = action.model_dump(exclude_unset=True)
		params = action_data.get(action_name, {})

		# Build parameter parts with colored formatting
		param_parts = []
		plain_param_parts = []

		if params and isinstance(params, dict):
			for param_name, value in params.items():
				# Truncate long values for readability
				if isinstance(value, str) and len(value) > 150:
					display_value = value[:150] + '...'
				elif isinstance(value, list) and len(str(value)) > 200:
					display_value = str(value)[:200] + '...'
				else:
					display_value = value

				param_parts.append(f'{magenta}{param_name}{reset}: {display_value}')
				plain_param_parts.append(f'{param_name}: {display_value}')

		# Join all parts
		if param_parts:
			params_string = ', '.join(param_parts)
			self.logger.info(f'  {action_header} {params_string}')
		else:
			self.logger.info(f'  {action_header}')

		if self._demo_mode_enabled:
			panel_message = plain_header
			if plain_param_parts:
				panel_message = f'{panel_message} {", ".join(plain_param_parts)}'
			await self._demo_mode_log(panel_message.strip(), 'action', {'action': action_name, 'step': self.state.n_steps})

	async def log_completion(self) -> None:
		"""记录任务完成状态"""
		# self._task_end_time = time.time()
		# self._task_duration = self._task_end_time - self._task_start_time TODO: 使用 take_step 时不工作
		if self.history.is_successful():
			self.logger.info('✅ Task completed successfully')
			await self._demo_mode_log('Task completed successfully', 'success', {'tag': 'task'})

	async def _generate_rerun_summary(
		self, original_task: str, results: list[ActionResult], summary_llm: BaseChatModel | None = None
	) -> ActionResult:
		"""使用截图和最后步骤信息生成重新运行完成的 AI 摘要"""
		from browser_use.agent.views import RerunSummaryAction

		# Get current screenshot
		screenshot_b64 = None
		try:
			screenshot = await self.browser_session.take_screenshot(full_page=False)
			if screenshot:
				import base64

				screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
		except Exception as e:
			self.logger.warning(f'Failed to capture screenshot for rerun summary: {e}')

		# Build summary prompt and message
		error_count = sum(1 for r in results if r.error)
		success_count = len(results) - error_count

		from browser_use.agent.prompts import get_rerun_summary_message, get_rerun_summary_prompt

		prompt = get_rerun_summary_prompt(
			original_task=original_task,
			total_steps=len(results),
			success_count=success_count,
			error_count=error_count,
		)

		# Use provided LLM, agent's LLM, or fall back to OpenAI with structured output
		try:
			# Determine which LLM to use
			if summary_llm is None:
				# Try to use the agent's LLM first
				summary_llm = self.llm
				self.logger.debug('Using agent LLM for rerun summary')
			else:
				self.logger.debug(f'Using provided LLM for rerun summary: {summary_llm.model}')

			# Build message with prompt and optional screenshot
			from browser_use.llm.messages import BaseMessage

			message = get_rerun_summary_message(prompt, screenshot_b64)
			messages: list[BaseMessage] = [message]  # type: ignore[list-item]

			# Try calling with structured output first
			self.logger.debug(f'Calling LLM for rerun summary with {len(messages)} message(s)')
			try:
				kwargs: dict = {'output_format': RerunSummaryAction}
				response = await summary_llm.ainvoke(messages, **kwargs)
				summary: RerunSummaryAction = response.completion  # type: ignore[assignment]
				self.logger.debug(f'LLM response type: {type(summary)}')
				self.logger.debug(f'LLM response: {summary}')
			except Exception as structured_error:
				# If structured output fails (e.g., Browser-Use LLM doesn't support it for this type),
				# fall back to text response without parsing
				self.logger.debug(f'Structured output failed: {structured_error}, falling back to text response')

				response = await summary_llm.ainvoke(messages, None)
				response_text = response.completion
				self.logger.debug(f'LLM text response: {response_text}')

				# Use the text response directly as the summary
				summary = RerunSummaryAction(
					summary=response_text if isinstance(response_text, str) else str(response_text),
					success=error_count == 0,
					completion_status='complete' if error_count == 0 else ('partial' if success_count > 0 else 'failed'),
				)

			self.logger.info(f'📊 Rerun Summary: {summary.summary}')
			self.logger.info(f'📊 Status: {summary.completion_status} (success={summary.success})')

			return ActionResult(
				is_done=True,
				success=summary.success,
				extracted_content=summary.summary,
				long_term_memory=f'Rerun completed with status: {summary.completion_status}. {summary.summary[:100]}',
			)

		except Exception as e:
			self.logger.warning(f'Failed to generate AI summary: {e.__class__.__name__}: {e}')
			self.logger.debug('Full error traceback:', exc_info=True)
			# Fallback to simple summary
			return ActionResult(
				is_done=True,
				success=error_count == 0,
				extracted_content=f'Rerun completed: {success_count}/{len(results)} steps succeeded',
				long_term_memory=f'Rerun completed: {success_count} steps succeeded, {error_count} errors',
			)

	async def _execute_ai_step(
		self,
		query: str,
		include_screenshot: bool = False,
		extract_links: bool = False,
		ai_step_llm: BaseChatModel | None = None,
	) -> ActionResult:
		"""
		在重新运行期间执行 AI 步骤以重新评估提取动作
		分析完整的页面 DOM/markdown + 可选的截图

		Args:
			query: 要从当前页面分析或提取的内容
			include_screenshot: 是否在分析中包含截图
			extract_links: 是否在 markdown 提取中包含链接
			ai_step_llm: 可选的 LLM。如果未提供，使用代理的 LLM

		Returns:
			ActionResult 包含提取的内容
		"""
		from browser_use.agent.prompts import get_ai_step_system_prompt, get_ai_step_user_prompt, get_rerun_summary_message
		from browser_use.llm.messages import SystemMessage, UserMessage
		from browser_use.utils import sanitize_surrogates

		# Use provided LLM or agent's LLM
		llm = ai_step_llm or self.llm
		self.logger.debug(f'Using LLM for AI step: {llm.model}')

		# Extract clean markdown
		try:
			from browser_use.dom.markdown_extractor import extract_clean_markdown

			content, content_stats = await extract_clean_markdown(
				browser_session=self.browser_session, extract_links=extract_links
			)
		except Exception as e:
			return ActionResult(error=f'Could not extract clean markdown: {type(e).__name__}: {e}')

		# Get screenshot if requested
		screenshot_b64 = None
		if include_screenshot:
			try:
				screenshot = await self.browser_session.take_screenshot(full_page=False)
				if screenshot:
					import base64

					screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
			except Exception as e:
				self.logger.warning(f'Failed to capture screenshot for ai_step: {e}')

		# Build prompt with content stats
		original_html_length = content_stats['original_html_chars']
		initial_markdown_length = content_stats['initial_markdown_chars']
		final_filtered_length = content_stats['final_filtered_chars']
		chars_filtered = content_stats['filtered_chars_removed']

		stats_summary = f"""Content processed: {original_html_length:,} HTML chars → {initial_markdown_length:,} initial markdown → {final_filtered_length:,} filtered markdown"""
		if chars_filtered > 0:
			stats_summary += f' (filtered {chars_filtered:,} chars of noise)'

		# Sanitize content
		content = sanitize_surrogates(content)
		query = sanitize_surrogates(query)

		# Get prompts from prompts.py
		system_prompt = get_ai_step_system_prompt()
		prompt_text = get_ai_step_user_prompt(query, stats_summary, content)

		# Build user message with optional screenshot
		if screenshot_b64:
			user_message = get_rerun_summary_message(prompt_text, screenshot_b64)
		else:
			user_message = UserMessage(content=prompt_text)

		try:
			import asyncio

			response = await asyncio.wait_for(llm.ainvoke([SystemMessage(content=system_prompt), user_message]), timeout=120.0)

			current_url = await self.browser_session.get_current_page_url()
			extracted_content = (
				f'<url>\n{current_url}\n</url>\n<query>\n{query}\n</query>\n<result>\n{response.completion}\n</result>'
			)

			# Simple memory handling
			MAX_MEMORY_LENGTH = 1000
			if len(extracted_content) < MAX_MEMORY_LENGTH:
				memory = extracted_content
				include_extracted_content_only_once = False
			else:
				file_name = await self.file_system.save_extracted_content(extracted_content)
				memory = f'Query: {query}\nContent in {file_name} and once in <read_state>.'
				include_extracted_content_only_once = True

			self.logger.info(f'🤖 AI Step: {memory}')
			return ActionResult(
				extracted_content=extracted_content,
				include_extracted_content_only_once=include_extracted_content_only_once,
				long_term_memory=memory,
			)
		except Exception as e:
			self.logger.warning(f'Failed to execute AI step: {e.__class__.__name__}: {e}')
			self.logger.debug('Full error traceback:', exc_info=True)
			return ActionResult(error=f'AI step failed: {e}')

	async def rerun_history(
		self,
		history: AgentHistoryList,
		max_retries: int = 3,
		skip_failures: bool = False,
		delay_between_actions: float = 2.0,
		max_step_interval: float = 45.0,
		summary_llm: BaseChatModel | None = None,
		ai_step_llm: BaseChatModel | None = None,
		wait_for_elements: bool = False,
	) -> list[ActionResult]:
		"""
		重新运行保存的动作历史，带有错误处理和重试逻辑

		Args:
		                history: 要重放的历史记录
		                max_retries: 每个动作的最大重试次数
		                skip_failures: 是否跳过失败的动作或停止执行。当为 True 时，也会跳过
		                               原始运行中有错误的步骤（例如自动关闭的模态框关闭按钮，
		                               或变得不可交互的元素）
		                delay_between_actions: 动作之间的延迟（秒）（当没有保存的间隔时使用）
		                max_step_interval: 保存的 step_interval 的最大延迟（限制原始运行的 LLM 时间）
		                summary_llm: 可选的 LLM，用于生成最终摘要。如果未提供，使用代理的 LLM
		                ai_step_llm: 可选的 LLM，用于 AI 步骤（提取动作）。如果未提供，使用代理的 LLM
		                wait_for_elements: 如果为 True，在尝试元素匹配之前等待最小数量的元素。
		                               对于 shadow DOM 内容动态加载的 SPA 页面很有用。
		                               默认为 False。

		Returns:
		                动作结果列表（包括 AI 摘要作为最终结果）
		"""
		# Skip cloud sync session events for rerunning (we're replaying, not starting new)
		self.state.session_initialized = True

		# Initialize browser session
		await self.browser_session.start()

		results = []

		# Track previous step for redundant retry detection
		previous_item: AgentHistory | None = None
		previous_step_succeeded: bool = False

		try:
			for i, history_item in enumerate(history.history):
				goal = history_item.model_output.current_state.next_goal if history_item.model_output else ''
				step_num = history_item.metadata.step_number if history_item.metadata else i
				step_name = 'Initial actions' if step_num == 0 else f'Step {step_num}'

				# Determine step delay
				if history_item.metadata and history_item.metadata.step_interval is not None:
					# Cap the saved interval to max_step_interval (saved interval includes LLM time)
					step_delay = min(history_item.metadata.step_interval, max_step_interval)
					# Format delay nicely - show ms for values < 1s, otherwise show seconds
					if step_delay < 1.0:
						delay_str = f'{step_delay * 1000:.0f}ms'
					else:
						delay_str = f'{step_delay:.1f}s'
					if history_item.metadata.step_interval > max_step_interval:
						delay_source = f'capped to {delay_str} (saved was {history_item.metadata.step_interval:.1f}s)'
					else:
						delay_source = f'using saved step_interval={delay_str}'
				else:
					step_delay = delay_between_actions
					if step_delay < 1.0:
						delay_str = f'{step_delay * 1000:.0f}ms'
					else:
						delay_str = f'{step_delay:.1f}s'
					delay_source = f'using default delay={delay_str}'

				self.logger.info(f'Replaying {step_name} ({i + 1}/{len(history.history)}) [{delay_source}]: {goal}')

				if (
					not history_item.model_output
					or not history_item.model_output.action
					or history_item.model_output.action == [None]
				):
					self.logger.warning(f'{step_name}: No action to replay, skipping')
					results.append(ActionResult(error='No action to replay'))
					continue

				# Check if the original step had errors - skip if skip_failures is enabled
				original_had_error = any(r.error for r in history_item.result if r.error)
				if original_had_error and skip_failures:
					error_msgs = [r.error for r in history_item.result if r.error]
					self.logger.warning(
						f'{step_name}: Original step had error(s), skipping (skip_failures=True): {error_msgs[0][:100] if error_msgs else "unknown"}'
					)
					results.append(
						ActionResult(
							error=f'Skipped - original step had error: {error_msgs[0][:100] if error_msgs else "unknown"}'
						)
					)
					continue

				# Check if this step is a redundant retry of the previous step
				# This handles cases where original run needed to click same element multiple times
				# due to slow page response, but during replay the first click already worked
				if self._is_redundant_retry_step(history_item, previous_item, previous_step_succeeded):
					self.logger.info(f'{step_name}: Skipping redundant retry (previous step already succeeded with same element)')
					results.append(
						ActionResult(
							extracted_content='Skipped - redundant retry of previous step',
							include_in_memory=False,
						)
					)
					# Don't update previous_item/previous_step_succeeded - keep tracking the original step
					continue

				retry_count = 0
				step_succeeded = False
				# Exponential backoff: 5s base, doubling each retry, capped at 30s
				base_retry_delay = 5.0
				max_retry_delay = 30.0
				while retry_count < max_retries:
					try:
						result = await self._execute_history_step(history_item, step_delay, ai_step_llm, wait_for_elements)
						results.extend(result)
						step_succeeded = True
						break

					except Exception as e:
						retry_count += 1
						if retry_count == max_retries:
							error_msg = f'{step_name} failed after {max_retries} attempts: {str(e)}'
							self.logger.error(error_msg)
							# Always record the error in results so AI summary counts it correctly
							results.append(ActionResult(error=error_msg))
							if not skip_failures:
								raise RuntimeError(error_msg)
							# With skip_failures=True, continue to next step
						else:
							# Exponential backoff: 5s, 10s, 20s, ... capped at 30s
							retry_delay = min(base_retry_delay * (2 ** (retry_count - 1)), max_retry_delay)
							self.logger.warning(
								f'{step_name} failed (attempt {retry_count}/{max_retries}), retrying in {retry_delay}s...'
							)
							await asyncio.sleep(retry_delay)

				# Update tracking for redundant retry detection
				previous_item = history_item
				previous_step_succeeded = step_succeeded

			# Generate AI summary of rerun completion
			self.logger.info('🤖 Generating AI summary of rerun completion...')
			summary_result = await self._generate_rerun_summary(self.task, results, summary_llm)
			results.append(summary_result)

			return results
		finally:
			# Always close resources, even on failure
			await self.close()

	async def _execute_initial_actions(self) -> None:
		"""执行初始动作（如果提供）
		
		self.initial_actions：预设的初始动作列表（如 [{"action": "navigate", "url": "https://xxx.com"}]），非空时才执行；
		
		"""
		if self.initial_actions and not self.state.follow_up_task:
			self.logger.debug(f'⚡ Executing {len(self.initial_actions)} initial actions...')
			result = await self.multi_act(self.initial_actions)
			# 更新结果 1 以提及它是自动加载的
			if result and self.initial_url and result[0].long_term_memory:
				result[0].long_term_memory = f'Found initial url and automatically loaded it. {result[0].long_term_memory}'
			self.state.last_result = result

			# Save initial actions to history as step 0 for rerun capability
			# Skip browser state capture for initial actions (usually just URL navigation)
			if self.settings.flash_mode:
				model_output = self.AgentOutput(
					evaluation_previous_goal=None,
					memory='Initial navigation',
					next_goal=None,
					action=self.initial_actions,
				)
			else:
				model_output = self.AgentOutput(
					evaluation_previous_goal='Start',
					memory=None,
					next_goal='Initial navigation',
					action=self.initial_actions,
				)

			metadata = StepMetadata(step_number=0, step_start_time=time.time(), step_end_time=time.time(), step_interval=None)

			# Create minimal browser state history for initial actions
			state_history = BrowserStateHistory(
				url=self.initial_url or '',
				title='Initial Actions',
				tabs=[],
				interacted_element=[None] * len(self.initial_actions),  # No DOM elements needed
				screenshot_path=None,
			)

			history_item = AgentHistory(
				model_output=model_output,
				result=result,
				state=state_history,
				metadata=metadata,
			)

			self.history.add_item(history_item)
			self.logger.debug('📝 Saved initial actions to history as step 0')
			self.logger.debug('Initial actions completed')

	async def _wait_for_minimum_elements(
		self,
		min_elements: int,
		timeout: float = 30.0,
		poll_interval: float = 1.0,
	) -> BrowserStateSummary | None:
		"""等待页面至少有 min_elements 个交互元素

		这有助于处理 SPA 页面，其中 shadow DOM 和动态内容
		即使在 document.readyState 为 'complete' 时也可能立即可用

		Args:
			min_elements: 要等待的最小交互元素数量
			timeout: 最大等待时间（秒）
			poll_interval: 轮询尝试之间的时间（秒）

		Returns:
			如果找到最小元素则返回 BrowserStateSummary，超时则返回 None
		"""
		assert self.browser_session is not None, 'BrowserSession is not set up'

		start_time = time.time()
		last_count = 0

		while (time.time() - start_time) < timeout:
			state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
			if state and state.dom_state.selector_map:
				current_count = len(state.dom_state.selector_map)
				if current_count >= min_elements:
					self.logger.debug(f'✅ Page has {current_count} elements (needed {min_elements}), proceeding with action')
					return state
				if current_count != last_count:
					self.logger.debug(
						f'⏳ Waiting for elements: {current_count}/{min_elements} '
						f'(timeout in {timeout - (time.time() - start_time):.1f}s)'
					)
					last_count = current_count
			await asyncio.sleep(poll_interval)

		# Return last state even if we didn't reach min_elements
		self.logger.warning(f'⚠️ Timeout waiting for {min_elements} elements, proceeding with {last_count} elements')
		return await self.browser_session.get_browser_state_summary(include_screenshot=False)

	def _count_expected_elements_from_history(self, history_item: AgentHistory) -> int:
		"""根据历史记录估计预期的最小元素数量

		使用历史记录中的动作索引来确定页面应该具有的最小
		元素数量。如果动作针对索引 N，页面需要在 selector_map 中至少有 N+1 个元素
		"""
		if not history_item.model_output or not history_item.model_output.action:
			return 0

		max_index = -1  # Use -1 to indicate no index found yet
		for action in history_item.model_output.action:
			# Get the element index this action targets
			index = action.get_index()
			if index is not None:
				max_index = max(max_index, index)

		# Need at least max_index + 1 elements (indices are 0-based)
		# Cap at 50 to avoid waiting forever for very high indices
		# max_index >= 0 means we found at least one action with an index
		return min(max_index + 1, 50) if max_index >= 0 else 0

	async def _execute_history_step(
		self,
		history_item: AgentHistory,
		delay: float,
		ai_step_llm: BaseChatModel | None = None,
		wait_for_elements: bool = False,
	) -> list[ActionResult]:
		"""执行历史记录中的单个步骤，带有元素验证

		对于提取动作，使用 AI 重新评估内容，因为页面内容可能已更改

		Args:
			history_item: 要执行的历史步骤
			delay: 执行步骤前的延迟
			ai_step_llm: 可选的 LLM，用于 AI 步骤
			wait_for_elements: 如果为 True，在元素匹配之前等待最小元素
		"""
		assert self.browser_session is not None, 'BrowserSession is not set up'

		await asyncio.sleep(delay)

		# Optionally wait for minimum elements before element matching (useful for SPAs)
		if wait_for_elements:
			# Determine if we need to wait for elements (actions that interact with DOM elements)
			needs_element_matching = False
			if history_item.model_output:
				for i, action in enumerate(history_item.model_output.action):
					action_data = action.model_dump(exclude_unset=True)
					action_name = next(iter(action_data.keys()), None)
					# Actions that need element matching
					if action_name in ('click', 'input', 'hover', 'select_option', 'drag_and_drop'):
						historical_elem = (
							history_item.state.interacted_element[i] if i < len(history_item.state.interacted_element) else None
						)
						if historical_elem is not None:
							needs_element_matching = True
							break

			# If we need element matching, wait for minimum elements before proceeding
			if needs_element_matching:
				min_elements = self._count_expected_elements_from_history(history_item)
				if min_elements > 0:
					state = await self._wait_for_minimum_elements(min_elements, timeout=15.0, poll_interval=1.0)
				else:
					state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
			else:
				state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
		else:
			state = await self.browser_session.get_browser_state_summary(include_screenshot=False)
		if not state or not history_item.model_output:
			raise ValueError('Invalid state or model output')

		results = []
		pending_actions = []

		for i, action in enumerate(history_item.model_output.action):
			# Check if this is an extract action - use AI step instead
			action_data = action.model_dump(exclude_unset=True)
			action_name = next(iter(action_data.keys()), None)

			if action_name == 'extract':
				# Execute any pending actions first to maintain correct order
				# (e.g., if step is [click, extract], click must happen before extract)
				if pending_actions:
					batch_results = await self.multi_act(pending_actions)
					results.extend(batch_results)
					pending_actions = []

				# Now execute AI step for extract action
				extract_params = action_data['extract']
				query = extract_params.get('query', '')
				extract_links = extract_params.get('extract_links', False)

				self.logger.info(f'🤖 Using AI step for extract action: {query[:50]}...')
				ai_result = await self._execute_ai_step(
					query=query,
					include_screenshot=False,  # Match original extract behavior
					extract_links=extract_links,
					ai_step_llm=ai_step_llm,
				)
				results.append(ai_result)
			else:
				# For non-extract actions, update indices and collect for batch execution
				historical_elem = history_item.state.interacted_element[i]
				updated_action = await self._update_action_indices(
					historical_elem,
					action,
					state,
				)
				if updated_action is None:
					# Build informative error message with diagnostic info
					elem_info = self._format_element_for_error(historical_elem)
					selector_map = state.dom_state.selector_map or {}
					selector_count = len(selector_map)

					# Find elements with same node_name for diagnostics
					hist_node = historical_elem.node_name.lower() if historical_elem else ''
					similar_elements = []
					if historical_elem and historical_elem.attributes:
						hist_aria = historical_elem.attributes.get('aria-label', '')
						for idx, elem in selector_map.items():
							if elem.node_name.lower() == hist_node and elem.attributes:
								elem_aria = elem.attributes.get('aria-label', '')
								if elem_aria:
									similar_elements.append(f'{idx}:{elem_aria[:30]}')
									if len(similar_elements) >= 5:
										break

					diagnostic = ''
					if similar_elements:
						diagnostic = f'\n  Available <{hist_node.upper()}> with aria-label: {similar_elements}'
					elif hist_node:
						same_node_count = sum(1 for e in selector_map.values() if e.node_name.lower() == hist_node)
						diagnostic = (
							f'\n  Found {same_node_count} <{hist_node.upper()}> elements (none with matching identifiers)'
						)

					raise ValueError(
						f'Could not find matching element for action {i} in current page.\n'
						f'  Looking for: {elem_info}\n'
						f'  Page has {selector_count} interactive elements.{diagnostic}\n'
						f'  Tried: EXACT hash → STABLE hash → XPATH → ATTRIBUTE matching'
					)
				pending_actions.append(updated_action)

		# Execute any remaining pending actions
		if pending_actions:
			batch_results = await self.multi_act(pending_actions)
			results.extend(batch_results)

		return results

	async def _update_action_indices(
		self,
		historical_element: DOMInteractedElement | None,
		action: ActionModel,  # 根据你的动作模型正确输入类型
		browser_state_summary: BrowserStateSummary,
	) -> ActionModel | None:
		"""
		根据当前页面状态更新动作索引
		返回更新的动作，如果找不到元素则返回 None

		级联匹配策略（按顺序尝试每个级别）：
		1. EXACT: 完整的 element_hash 匹配（包括所有属性 + ax_name）
		2. STABLE: 过滤掉动态 CSS 类的哈希（focus、hover、animation 等）
		3. XPATH: XPath 字符串匹配（DOM 中的结构位置）
		4. ATTRIBUTE: 唯一属性匹配（name、id、aria-label），用于旧的历史文件
		"""
		if not historical_element or not browser_state_summary.dom_state.selector_map:
			return action

		selector_map = browser_state_summary.dom_state.selector_map
		highlight_index: int | None = None
		match_level: MatchLevel | None = None

		# Debug: log what we're looking for and what's available
		self.logger.info(
			f'🔍 Searching for element: <{historical_element.node_name}> '
			f'hash={historical_element.element_hash} stable_hash={historical_element.stable_hash}'
		)
		# Log what elements are in selector_map for debugging
		if historical_element.node_name:
			hist_name = historical_element.node_name.lower()
			matching_nodes = [
				(idx, elem.node_name, elem.attributes.get('name') if elem.attributes else None)
				for idx, elem in selector_map.items()
				if elem.node_name.lower() == hist_name
			]
			self.logger.info(
				f'🔍 Selector map has {len(selector_map)} elements, '
				f'{len(matching_nodes)} are <{hist_name.upper()}>: {matching_nodes}'
			)

		# Level 1: EXACT hash match
		for idx, elem in selector_map.items():
			if elem.element_hash == historical_element.element_hash:
				highlight_index = idx
				match_level = MatchLevel.EXACT
				break

		if highlight_index is None:
			self.logger.debug(f'EXACT hash match failed (checked {len(selector_map)} elements)')

		# Level 2: STABLE hash match (dynamic classes filtered)
		# Use stored stable_hash (computed at save time from EnhancedDOMTreeNode - single source of truth)
		if highlight_index is None and historical_element.stable_hash is not None:
			for idx, elem in selector_map.items():
				if elem.compute_stable_hash() == historical_element.stable_hash:
					highlight_index = idx
					match_level = MatchLevel.STABLE
					self.logger.info('Element matched at STABLE level (dynamic classes filtered)')
					break
			if highlight_index is None:
				self.logger.debug('STABLE hash match failed')
		elif highlight_index is None:
			self.logger.debug('STABLE hash match skipped (no stable_hash in history)')

		# Level 3: XPATH match
		if highlight_index is None and historical_element.x_path:
			for idx, elem in selector_map.items():
				if elem.xpath == historical_element.x_path:
					highlight_index = idx
					match_level = MatchLevel.XPATH
					self.logger.info(f'Element matched at XPATH level: {historical_element.x_path}')
					break
			if highlight_index is None:
				self.logger.debug(f'XPATH match failed for: {historical_element.x_path[-60:]}')

		# Level 4: Unique attribute fallback (for old history files without stable_hash)
		if highlight_index is None and historical_element.attributes:
			hist_attrs = historical_element.attributes
			hist_name = historical_element.node_name.lower()

			# Try matching by unique identifiers: name, id, or aria-label
			for attr_key in ['name', 'id', 'aria-label']:
				if attr_key in hist_attrs and hist_attrs[attr_key]:
					for idx, elem in selector_map.items():
						if (
							elem.node_name.lower() == hist_name
							and elem.attributes
							and elem.attributes.get(attr_key) == hist_attrs[attr_key]
						):
							highlight_index = idx
							match_level = MatchLevel.XPATH  # Reuse XPATH level for logging
							self.logger.info(f'Element matched via {attr_key} attribute: {hist_attrs[attr_key]}')
							break
					if highlight_index is not None:
						break

			if highlight_index is None:
				tried_attrs = [k for k in ['name', 'id', 'aria-label'] if k in hist_attrs and hist_attrs[k]]
				# Log what was tried and what's available on the page for debugging
				same_node_elements = [
					(idx, elem.attributes.get('aria-label') or elem.attributes.get('id') or elem.attributes.get('name'))
					for idx, elem in selector_map.items()
					if elem.node_name.lower() == hist_name and elem.attributes
				]
				self.logger.info(
					f'🔍 ATTRIBUTE match failed for <{hist_name.upper()}> '
					f'(tried: {tried_attrs}, looking for: {[hist_attrs.get(k) for k in tried_attrs]}). '
					f'Page has {len(same_node_elements)} <{hist_name.upper()}> elements with identifiers: '
					f'{same_node_elements[:5]}{"..." if len(same_node_elements) > 5 else ""}'
				)

		if highlight_index is None:
			return None

		old_index = action.get_index()
		if old_index != highlight_index:
			action.set_index(highlight_index)
			level_name = match_level.name if match_level else 'UNKNOWN'
			self.logger.info(f'Element index updated {old_index} → {highlight_index} (matched at {level_name} level)')

		return action

	def _format_element_for_error(self, elem: DOMInteractedElement | None) -> str:
		"""为历史重新运行期间的错误消息格式化元素信息"""
		if elem is None:
			return '<no element recorded>'

		parts = [f'<{elem.node_name}>']

		# Add key identifying attributes
		if elem.attributes:
			for key in ['name', 'id', 'aria-label', 'type']:
				if key in elem.attributes and elem.attributes[key]:
					parts.append(f'{key}="{elem.attributes[key]}"')

		# Add hash info
		parts.append(f'hash={elem.element_hash}')
		if elem.stable_hash:
			parts.append(f'stable_hash={elem.stable_hash}')

		# Add xpath (truncated)
		if elem.x_path:
			xpath_short = elem.x_path if len(elem.x_path) <= 60 else f'...{elem.x_path[-57:]}'
			parts.append(f'xpath="{xpath_short}"')

		return ' '.join(parts)

	def _is_redundant_retry_step(
		self,
		current_item: AgentHistory,
		previous_item: AgentHistory | None,
		previous_step_succeeded: bool,
	) -> bool:
		"""
		检测当前步骤是否是前一步骤的冗余重试

		这处理了原始运行由于页面响应慢而需要多次单击同一元素的情况，
		但在重放期间第一次单击已经成功。
		当页面已经导航时，对同一元素的后续重试单击将失败，因为该元素不再存在

		如果满足以下条件则返回 True：
		- 前一步骤成功
		- 两个步骤针对同一元素（通过 element_hash、stable_hash 或 xpath）
		- 两个步骤执行相同的动作类型（例如，都是单击）
		"""
		if not previous_item or not previous_step_succeeded:
			return False

		# Get interacted elements from both steps (first action in each)
		curr_elements = current_item.state.interacted_element
		prev_elements = previous_item.state.interacted_element

		if not curr_elements or not prev_elements:
			return False

		curr_elem = curr_elements[0] if curr_elements else None
		prev_elem = prev_elements[0] if prev_elements else None

		if not curr_elem or not prev_elem:
			return False

		# Check if same element by various matching strategies
		same_by_hash = curr_elem.element_hash == prev_elem.element_hash
		same_by_stable_hash = (
			curr_elem.stable_hash is not None
			and prev_elem.stable_hash is not None
			and curr_elem.stable_hash == prev_elem.stable_hash
		)
		same_by_xpath = curr_elem.x_path == prev_elem.x_path

		if not (same_by_hash or same_by_stable_hash or same_by_xpath):
			return False

		# Check if same action type
		curr_actions = current_item.model_output.action if current_item.model_output else []
		prev_actions = previous_item.model_output.action if previous_item.model_output else []

		if not curr_actions or not prev_actions:
			return False

		# Get the action type (first key in the action dict)
		curr_action_data = curr_actions[0].model_dump(exclude_unset=True)
		prev_action_data = prev_actions[0].model_dump(exclude_unset=True)

		curr_action_type = next(iter(curr_action_data.keys()), None)
		prev_action_type = next(iter(prev_action_data.keys()), None)

		if curr_action_type != prev_action_type:
			return False

		self.logger.debug(
			f'🔄 Detected redundant retry: both steps target same element '
			f'<{curr_elem.node_name}> with action "{curr_action_type}"'
		)

		return True

	async def load_and_rerun(
		self,
		history_file: str | Path | None = None,
		variables: dict[str, str] | None = None,
		**kwargs,
	) -> list[ActionResult]:
		"""
		从文件加载历史记录并重新运行，可选择替换变量

		Args:
			history_file: 历史文件的路径
			variables: 可选的字典，将变量名称映射到新值（例如 {'email': 'new@example.com'}）
			**kwargs: 传递给 rerun_history 的其他参数：
				- max_retries: 每个动作的最大重试次数（默认：3）
				- skip_failures: 失败时继续（默认：True）
				- delay_between_actions: 没有保存间隔时的延迟（默认：2.0s）
				- max_step_interval: 保存的 step_interval 的上限（默认：45.0s）
				- summary_llm: 用于最终摘要的自定义 LLM
				- ai_step_llm: 用于提取重新评估的自定义 LLM
		"""
		if not history_file:
			history_file = 'AgentHistory.json'
		history = AgentHistoryList.load_from_file(history_file, self.AgentOutput)

		# Substitute variables if provided
		if variables:
			history = self._substitute_variables_in_history(history, variables)

		return await self.rerun_history(history, **kwargs)

	def save_history(self, file_path: str | Path | None = None) -> None:
		"""将历史记录保存到文件，带有敏感数据过滤"""
		if not file_path:
			file_path = 'AgentHistory.json'
		self.history.save_to_file(file_path, sensitive_data=self.sensitive_data)

	def pause(self) -> None:
		"""在下一步之前暂停代理"""
		print('\n\n⏸️ Paused the agent and left the browser open.\n\tPress [Enter] to resume or [Ctrl+C] again to quit.')
		self.state.paused = True
		self._external_pause_event.clear()

	def resume(self) -> None:
		"""恢复代理执行"""
		# TODO: 本地浏览器已关闭
		print('----------------------------------------------------------------------')
		print('▶️  Resuming agent execution where it left off...\n')
		self.state.paused = False
		self._external_pause_event.set()

	def stop(self) -> None:
		"""停止代理"""
		self.logger.info('⏹️ Agent stopping')
		self.state.stopped = True

		# 发出暂停事件信号以解除任何等待代码的阻塞，以便它可以检查停止状态
		self._external_pause_event.set()

		# 任务已停止

	def _convert_initial_actions(self, actions: list[dict[str, dict[str, Any]]]) -> list[ActionModel]:
		"""将基于字典的动作转换为 ActionModel 实例
		
		输入示例：
		[{"click": {"x": 100, "y": 200}}, {"input_text": {"text": "hello"}}]

		返回示例：
		[ActionModel(click=ClickParams(x=100, y=200)), ActionModel(input_text=InputTextParams(text="hello"))]
		"""
		converted_actions = []
		action_model = self.ActionModel
		for action_dict in actions:
			# Each action_dict should have a single key-value pair
			action_name = next(iter(action_dict))
			params = action_dict[action_name]

			# Get the parameter model for this action from registry
			action_info = self.tools.registry.registry.actions[action_name]
			param_model = action_info.param_model

			# Create validated parameters using the appropriate param model
			validated_params = param_model(**params)

			# Create ActionModel instance with the validated parameters
			action_model = self.ActionModel(**{action_name: validated_params})
			converted_actions.append(action_model)

		return converted_actions

	def _verify_and_setup_llm(self):
		"""
		验证 LLM API 密钥是否已设置，并且 LLM API 正确响应
		如果在自动模式下，还处理工具调用方法检测
		"""

		# 如果已经完成验证则跳过
		if getattr(self.llm, '_verified_api_keys', None) is True or CONFIG.SKIP_LLM_API_KEY_VERIFICATION:
			setattr(self.llm, '_verified_api_keys', True)
			return True

	@property
	def message_manager(self) -> MessageManager:
		return self._message_manager

	async def close(self):
		"""关闭所有资源"""
		try:
			# 仅在 keep_alive 为 False（或未设置）时关闭浏览器
			if self.browser_session is not None:
				if not self.browser_session.browser_profile.keep_alive:
					# 终止浏览器会话 - 这会分发 BrowserStopEvent，
					# 使用 clear=True 停止 EventBus，并重新创建一个新的 EventBus
					await self.browser_session.kill()

			# Close skill service if configured
			if self.skill_service is not None:
				await self.skill_service.close()

			# Force garbage collection
			gc.collect()

			# Debug: Log remaining threads and asyncio tasks
			import threading

			threads = threading.enumerate()
			self.logger.debug(f'🧵 Remaining threads ({len(threads)}): {[t.name for t in threads]}')

			# Get all asyncio tasks
			tasks = asyncio.all_tasks(asyncio.get_event_loop())
			# Filter out the current task (this close() coroutine)
			other_tasks = [t for t in tasks if t != asyncio.current_task()]
			if other_tasks:
				self.logger.debug(f'⚡ Remaining asyncio tasks ({len(other_tasks)}):')
				for task in other_tasks[:10]:  # Limit to first 10 to avoid spam
					self.logger.debug(f'  - {task.get_name()}: {task}')

		except Exception as e:
			self.logger.error(f'Error during cleanup: {e}')

	async def _update_action_models_for_page(self, page_url: str) -> None:
		"""使用页面特定的动作更新动作模型

		基于当前页面 URL 动态更新智能体的动作模型和输出模型，让 LLM 生成的动作严格匹配当前页面的可操作范围，同时适配不同的执行模式。

		- 更新ActionModel： 首先会根据pageUrl过滤出当前页面仅有的可执行动作，如当前url页面只能使用click，和导航动作，不能使用下单动作
		- 更新self.AgentOutput 输出格式规范的模型
		"""
		# Create new action model with current page's filtered actions
		self.ActionModel = self.tools.registry.create_action_model(page_url=page_url)
		# Update output model with the new actions
		if self.settings.flash_mode:
			self.AgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.ActionModel)
		elif self.settings.use_thinking:
			self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)
		else:
			self.AgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.ActionModel)

		# Update done action model too
		self.DoneActionModel = self.tools.registry.create_action_model(include_actions=['done'], page_url=page_url)
		if self.settings.flash_mode:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_flash_mode(self.DoneActionModel)
		elif self.settings.use_thinking:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions(self.DoneActionModel)
		else:
			self.DoneAgentOutput = AgentOutput.type_with_custom_actions_no_thinking(self.DoneActionModel)

	async def authenticate_cloud_sync(self, show_instructions: bool = True) -> bool:
		"""
		与云服务进行身份验证以供将来运行

		这在用户想要在任务完成后进行身份验证时很有用，
		以便将来的运行将同步到云

		Args:
			show_instructions: 是否向用户显示身份验证说明

		Returns:
			bool: 如果身份验证成功则返回 True
		"""
		self.logger.warning('Cloud sync has been removed and is no longer available')
		return False

	def run_sync(
		self,
		max_steps: int = 100,
		on_step_start: AgentHookFunc | None = None,
		on_step_end: AgentHookFunc | None = None,
	) -> AgentHistoryList[AgentStructuredOutput]:
		"""异步 run 方法的同步包装器，以便在没有 asyncio 的情况下更容易使用"""
		import asyncio

		return asyncio.run(self.run(max_steps=max_steps, on_step_start=on_step_start, on_step_end=on_step_end))

	def detect_variables(self) -> dict[str, DetectedVariable]:
		"""检测代理历史记录中的可重用变量"""
		from browser_use.agent.variable_detector import detect_variables_in_history

		return detect_variables_in_history(self.history)

	def _substitute_variables_in_history(self, history: AgentHistoryList, variables: dict[str, str]) -> AgentHistoryList:
		"""用新值替换历史记录中的变量，以便使用不同的数据重新运行"""
		from browser_use.agent.variable_detector import detect_variables_in_history

		# Detect variables in the history
		detected_vars = detect_variables_in_history(history)

		# Build a mapping of original values to new values
		value_replacements: dict[str, str] = {}
		for var_name, new_value in variables.items():
			if var_name in detected_vars:
				old_value = detected_vars[var_name].original_value
				value_replacements[old_value] = new_value
			else:
				self.logger.warning(f'Variable "{var_name}" not found in history, skipping substitution')

		if not value_replacements:
			self.logger.info('No variables to substitute')
			return history

		# Create a deep copy of history to avoid modifying the original
		import copy

		modified_history = copy.deepcopy(history)

		# Substitute values in all actions
		substitution_count = 0
		for history_item in modified_history.history:
			if not history_item.model_output or not history_item.model_output.action:
				continue

			for action in history_item.model_output.action:
				# Handle both Pydantic models and dicts
				if hasattr(action, 'model_dump'):
					action_dict = action.model_dump()
				elif isinstance(action, dict):
					action_dict = action
				else:
					action_dict = vars(action) if hasattr(action, '__dict__') else {}

				# Substitute in all string fields
				substitution_count += self._substitute_in_dict(action_dict, value_replacements)

				# Update the action with modified values
				if hasattr(action, 'model_dump'):
					# For Pydantic RootModel, we need to recreate from the modified dict
					if hasattr(action, 'root'):
						# This is a RootModel - recreate it from the modified dict
						new_action = type(action).model_validate(action_dict)
						# Replace the root field in-place using object.__setattr__ to bypass Pydantic's immutability
						object.__setattr__(action, 'root', getattr(new_action, 'root'))
					else:
						# Regular Pydantic model - update fields in-place
						for key, val in action_dict.items():
							if hasattr(action, key):
								setattr(action, key, val)
				elif isinstance(action, dict):
					action.update(action_dict)

		self.logger.info(f'Substituted {substitution_count} value(s) in {len(value_replacements)} variable type(s) in history')
		return modified_history

	def _substitute_in_dict(self, data: dict, replacements: dict[str, str]) -> int:
		"""递归替换字典中的值，返回所做的替换计数"""
		count = 0
		for key, value in data.items():
			if isinstance(value, str):
				# Replace if exact match
				if value in replacements:
					data[key] = replacements[value]
					count += 1
			elif isinstance(value, dict):
				# Recurse into nested dicts
				count += self._substitute_in_dict(value, replacements)
			elif isinstance(value, list):
				# Handle lists
				for i, item in enumerate(value):
					if isinstance(item, str) and item in replacements:
						value[i] = replacements[item]
						count += 1
					elif isinstance(item, dict):
						count += self._substitute_in_dict(item, replacements)
		return count
