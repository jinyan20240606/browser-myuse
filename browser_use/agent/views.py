"""
Agent 视图模块 (Agent Views Module)

此模块定义了 Browser-Use Agent 的核心数据结构和视图模型。
包含了 Agent 的配置、状态、输出格式、历史记录等关键类。

主要类:
- AgentSettings: Agent 的配置选项
- AgentState: Agent 的运行状态
- AgentOutput: Agent 的输出格式
- AgentHistory: Agent 的单步历史记录
- AgentHistoryList: Agent 的完整历史记录列表
- ActionResult: 动作执行结果
"""

from __future__ import annotations  # 启用延迟类型注解，支持前向引用

# ============================================================================
# 标准库导入 (Standard Library Imports)
# ============================================================================
import json  # JSON 序列化和反序列化
import logging  # 日志记录
import traceback  # 异常堆栈追踪
from dataclasses import dataclass  # 数据类装饰器，用于创建简单的数据容器
from pathlib import Path  # 面向对象的文件系统路径
from typing import Any, Generic, Literal  # 类型提示工具

# ============================================================================
# 第三方库导入 (Third-party Library Imports)
# ============================================================================
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator
# BaseModel: Pydantic 基础模型类，提供数据验证和序列化
# ConfigDict: 模型配置字典
# Field: 字段定义器，用于添加元数据和验证规则
# ValidationError: 验证错误异常
# create_model: 动态创建 Pydantic 模型
# model_validator: 模型级别的验证装饰器

from typing_extensions import TypeVar  # 扩展的类型变量，支持更多类型特性
from uuid_extensions import uuid7str  # UUID7 字符串生成器，基于时间戳的有序 UUID

# ============================================================================
# 内部模块导入 (Internal Module Imports)
# ============================================================================
from browser_use.agent.message_manager.views import MessageManagerState
# MessageManagerState: 消息管理器状态，管理 Agent 与 LLM 之间的消息历史

from browser_use.browser.views import BrowserStateHistory
# BrowserStateHistory: 浏览器状态历史，记录浏览器的快照信息

from browser_use.dom.views import DEFAULT_INCLUDE_ATTRIBUTES, DOMInteractedElement, DOMSelectorMap
# DEFAULT_INCLUDE_ATTRIBUTES: 默认包含的 DOM 属性列表
# DOMInteractedElement: 交互过的 DOM 元素信息
# DOMSelectorMap: DOM 选择器映射，索引到元素的映射

# 以下导入已注释，保留供将来使用
# from browser_use.dom.history_tree_processor.service import (
# 	DOMElementNode,
# 	DOMHistoryElement,
# 	HistoryTreeProcessor,
# )
# from browser_use.dom.views import SelectorMap

from browser_use.filesystem.file_system import FileSystemState
# FileSystemState: 文件系统状态，管理 Agent 可访问的文件

from browser_use.llm.base import BaseChatModel
# BaseChatModel: LLM 聊天模型的基类，定义了与语言模型交互的接口

from browser_use.tokens.views import UsageSummary
# UsageSummary: Token 使用摘要，记录 API 调用的 token 消耗

from browser_use.tools.registry.views import ActionModel
# ActionModel: 动作模型，定义了 Agent 可执行的动作结构

# ============================================================================
# 模块级别配置 (Module-level Configuration)
# ============================================================================
logger = logging.getLogger(__name__)  # 获取当前模块的日志记录器


# ============================================================================
# Agent 配置类 (Agent Configuration Classes)
# ============================================================================


class AgentSettings(BaseModel):
	"""
	Agent 配置选项类 (Agent Configuration Options)
	
	此类定义了 Agent 的所有可配置参数，包括视觉处理、错误处理、
	系统提示、性能优化等方面的设置。
	
	Attributes:
		use_vision: 是否使用视觉功能。True 启用，False 禁用，'auto' 自动检测
		vision_detail_level: 视觉分析的详细程度，可选 'auto'、'low'、'high'
		save_conversation_path: 保存对话历史的文件路径
		save_conversation_path_encoding: 保存文件的编码格式
		max_failures: 最大连续失败次数，超过后停止执行
		generate_gif: 是否生成执行过程的 GIF 动画，可以是布尔值或文件路径
		override_system_message: 完全覆盖默认系统提示词
		extend_system_message: 在默认系统提示词后追加内容
		include_attributes: 要包含在 DOM 分析中的 HTML 属性列表
		max_actions_per_step: 每步最多执行的动作数量
		use_thinking: 是否启用思考过程（Chain of Thought）
		flash_mode: 快速模式，禁用评估和目标设定，提高执行速度
		use_judge: 是否使用 Judge 模型验证执行结果
		ground_truth: Judge 验证的标准答案或评判标准
		max_history_items: 保留在上下文中的最大历史条目数
		page_extraction_llm: 用于页面内容提取的专用 LLM 模型
		calculate_cost: 是否计算 API 调用成本
		include_tool_call_examples: 是否在提示中包含工具调用示例
		llm_timeout: LLM 调用超时时间（秒）
		step_timeout: 单步执行超时时间（秒）
		final_response_after_failure: 失败后是否尝试最后一次恢复调用
	"""

	use_vision: bool | Literal['auto'] = True  # 视觉功能开关
	vision_detail_level: Literal['auto', 'low', 'high'] = 'auto'  # 视觉详细程度
	save_conversation_path: str | Path | None = None  # 对话保存路径
	save_conversation_path_encoding: str | None = 'utf-8'  # 编码格式
	max_failures: int = 3  # 最大失败次数
	generate_gif: bool | str = False  # GIF 生成开关
	override_system_message: str | None = None  # 覆盖系统提示
	extend_system_message: str | None = None  # 扩展系统提示
	include_attributes: list[str] | None = DEFAULT_INCLUDE_ATTRIBUTES  # DOM 属性列表
	max_actions_per_step: int = 3  # 每步最大动作数
	use_thinking: bool = True  # 思考模式开关
	flash_mode: bool = False  # 快速模式：禁用 evaluation_previous_goal 和 next_goal，设置 use_thinking = False
	use_judge: bool = True  # Judge 验证开关
	ground_truth: str | None = None  # Judge 验证的标准答案
	max_history_items: int | None = None  # 最大历史条目数

	page_extraction_llm: BaseChatModel | None = None  # 页面提取专用 LLM
	calculate_cost: bool = False  # 成本计算开关
	include_tool_call_examples: bool = False  # 包含工具调用示例
	llm_timeout: int = 60  # LLM 超时（秒）：Gemini 30s，O3 90s，默认 60s
	step_timeout: int = 180  # 单步超时（秒）
	final_response_after_failure: bool = True  # 失败后尝试最后恢复


# ============================================================================
# Agent 状态类 (Agent State Classes)
# ============================================================================


class AgentState(BaseModel):
	"""
	Agent 状态类 (Agent State Container)
	
	此类保存 Agent 运行时的所有状态信息，包括执行进度、
	错误计数、暂停/恢复状态、消息历史等。支持序列化以便检查点恢复。
	
	Attributes:
		agent_id: Agent 的唯一标识符（UUID7 格式，基于时间戳）
		n_steps: 当前执行到的步骤数
		consecutive_failures: 连续失败次数
		last_result: 上一步的执行结果列表
		last_plan: 上一步的计划/目标
		last_model_output: 上一步的模型输出
		paused: 是否处于暂停状态
		stopped: 是否已停止
		session_initialized: 会话事件是否已分发
		follow_up_task: 是否为后续任务
		message_manager_state: 消息管理器状态
		file_system_state: 文件系统状态
	"""

	model_config = ConfigDict(arbitrary_types_allowed=True)  # 允许任意类型

	agent_id: str = Field(default_factory=uuid7str)  # Agent 唯一 ID
	n_steps: int = 1  # 当前步骤数
	consecutive_failures: int = 0  # 连续失败计数
	last_result: list[ActionResult] | None = None  # 上一步结果
	last_plan: str | None = None  # 上一步计划
	last_model_output: AgentOutput | None = None  # 上一步模型输出

	# 暂停/恢复状态（保持可序列化以支持检查点）
	paused: bool = False  # 暂停标志
	stopped: bool = False  # 停止标志
	session_initialized: bool = False  # 会话是否已初始化
	follow_up_task: bool = False  # 是否为后续任务

	message_manager_state: MessageManagerState = Field(default_factory=MessageManagerState)  # 消息状态
	file_system_state: FileSystemState | None = None  # 文件系统状态


@dataclass
class AgentStepInfo:
	"""
	Agent 步骤信息类 (Agent Step Information)
	
	用于跟踪 Agent 当前执行进度的轻量级数据类。
	
	Attributes:
		step_number: 当前步骤编号
		max_steps: 最大允许步骤数
	"""
	step_number: int  # 当前步骤号
	max_steps: int  # 最大步骤数

	def is_last_step(self) -> bool:
		"""
		检查是否为最后一步
		
		Returns:
			bool: 如果当前是最后一步返回 True
		"""
		return self.step_number >= self.max_steps - 1


# ============================================================================
# 结果和判断类 (Result and Judgement Classes)
# ============================================================================


class JudgementResult(BaseModel):
	"""
	Judge 判断结果类 (LLM Judgement Result)
	
	此类存储 Judge 模型对 Agent 执行轨迹的评判结果。
	Judge 会分析 Agent 的执行历史，判断任务是否成功完成。
	
	Attributes:
		reasoning: 判断的推理过程说明
		verdict: 判断结果，True 表示成功，False 表示失败
		failure_reason: 失败原因说明（最多 5 句话）
		impossible_task: 是否为不可能完成的任务
		reached_captcha: 是否遇到了验证码
	"""

	reasoning: str | None = Field(
		default=None,
		description='判断的推理过程说明'
	)
	verdict: bool = Field(
		description='执行是否成功'
	)
	failure_reason: str | None = Field(
		default=None,
		description='失败原因（最多5句话）。如果成功则为空字符串',
	)
	impossible_task: bool = Field(
		default=False,
		description='任务是否不可能完成（模糊指令、网站故障、链接不可访问、缺少登录凭据等）',
	)
	reached_captcha: bool = Field(
		default=False,
		description='执行过程中是否遇到验证码',
	)


class ActionResult(BaseModel):
	"""
	动作执行结果类 (Action Execution Result)
	
	此类封装了单个动作执行后的结果信息，包括成功/失败状态、
	提取的内容、错误信息、附件等。是 Agent 与工具交互的核心数据结构。
	
	Attributes:
		is_done: 任务是否完成（用于 done 动作）
		success: 任务是否成功（仅当 is_done=True 时有效）
		judgement: Judge 的判断结果
		error: 错误信息（总是添加到长期记忆）
		attachments: 要在完成消息中显示的文件列表
		images: Base64 编码的图片列表
		long_term_memory: 要存入长期记忆的内容
		extracted_content: 从页面提取的内容
		include_extracted_content_only_once: 提取内容是否只使用一次
		metadata: 可观测性元数据（如点击坐标）
		include_in_memory: [已弃用] 是否包含在长期记忆中
	"""

	# 完成状态字段 (Completion Status Fields)
	is_done: bool | None = False  # 任务是否完成
	success: bool | None = None  # 任务是否成功

	# Judge 判断结果 (Judge Evaluation)
	judgement: JudgementResult | None = None  # 轨迹判断结果

	# 错误处理 - 总是包含在长期记忆中 (Error Handling)
	error: str | None = None  # 错误信息

	# 文件相关 (File Related)
	attachments: list[str] | None = None  # 要显示的附件文件
	images: list[dict[str, Any]] | None = None  # 图片列表 [{"name": "file.jpg", "data": "base64_string"}]

	# 记忆相关 (Memory Related)
	long_term_memory: str | None = None  # 长期记忆内容
	extracted_content: str | None = None  # 提取的内容
	include_extracted_content_only_once: bool = False  # 提取内容是否只用一次

	# 元数据 (Metadata)
	metadata: dict | None = None  # 可观测性元数据（如点击坐标）

	# 已弃用字段 (Deprecated)
	include_in_memory: bool = False  # [已弃用] 是否包含在记忆中

	@model_validator(mode='after')
	def validate_success_requires_done(self):
		"""
		验证 success 字段的使用规则
		
		确保 success=True 只能在 is_done=True 时设置。
		普通成功的动作应保持 success 为 None。
		
		Raises:
			ValueError: 当 success=True 但 is_done!=True 时
		"""
		if self.success is True and self.is_done is not True:
			raise ValueError(
				'success=True 只能在 is_done=True 时设置。'
				'普通成功的动作应保持 success 为 None。'
				'失败的动作使用 success=False。'
			)
		return self


class RerunSummaryAction(BaseModel):
	"""
	重新运行摘要类 (Rerun Summary Action)
	
	AI 生成的重新运行完成摘要，用于记录重跑执行的结果。
	
	Attributes:
		summary: 重新运行过程的摘要说明
		success: 基于视觉检查判断重新运行是否成功
		completion_status: 完成状态 - complete(全部成功)/partial(部分成功)/failed(失败)
	"""

	summary: str = Field(description='重新运行过程的摘要')
	success: bool = Field(description='重新运行是否成功（基于视觉检查）')
	completion_status: Literal['complete', 'partial', 'failed'] = Field(
		description='完成状态：complete(全部成功)、partial(部分成功)、failed(失败)'
	)


class StepMetadata(BaseModel):
	"""
	步骤元数据类 (Step Metadata)
	
	记录单个步骤的执行元数据，包括时间信息和步骤编号。
	用于性能分析和调试。
	
	Attributes:
		step_start_time: 步骤开始时间（Unix 时间戳）
		step_end_time: 步骤结束时间（Unix 时间戳）
		step_number: 步骤编号
		step_interval: 步骤间隔时间
	"""

	step_start_time: float  # 开始时间戳
	step_end_time: float  # 结束时间戳
	step_number: int  # 步骤编号
	step_interval: float | None = None  # 步骤间隔

	@property
	def duration_seconds(self) -> float:
		"""
		计算步骤执行时长
		
		Returns:
			float: 步骤执行时长（秒）
		"""
		return self.step_end_time - self.step_start_time


# ============================================================================
# Agent 输出类 (Agent Output Classes)
# ============================================================================


class AgentBrain(BaseModel):
	"""
	Agent 思维状态类 (Agent Brain State)
	
	表示 Agent 的思维过程，包括思考、评估、记忆和目标。
	这是 Agent 决策过程的核心数据结构。
	
	Attributes:
		thinking: 思考过程（Chain of Thought）
		evaluation_previous_goal: 对上一个目标完成情况的评估
		memory: 当前记忆/上下文摘要
		next_goal: 下一步要达成的目标
	"""
	thinking: str | None = None  # 思考过程
	evaluation_previous_goal: str  # 上一目标评估
	memory: str  # 记忆摘要
	next_goal: str  # 下一目标


class AgentOutput(BaseModel):
	"""
	Agent 输出类 (Agent Output Model)
	
	此类定义了 Agent 每一步的完整输出格式，包括思维过程和要执行的动作列表。
	这是 LLM 返回的结构化数据格式，Agent 根据此输出执行操作。
	
	Attributes:
		thinking: 思考过程（可选，在 flash_mode 下禁用）
		evaluation_previous_goal: 对上一目标的评估
		memory: 当前记忆/上下文
		next_goal: 下一步目标
		action: 要执行的动作列表（至少一个）
	
	Methods:
		current_state: 获取当前思维状态（AgentBrain）
		type_with_custom_actions: 创建包含自定义动作的输出类型
		type_with_custom_actions_no_thinking: 创建无思考字段的输出类型
		type_with_custom_actions_flash_mode: 创建快速模式的输出类型
	"""
	model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')  # 禁止额外字段

	thinking: str | None = None  # 思考过程
	evaluation_previous_goal: str | None = None  # 上一目标评估
	memory: str | None = None  # 记忆
	next_goal: str | None = None  # 下一目标
	action: list[ActionModel] = Field(
		...,
		json_schema_extra={'min_items': 1},  # 确保至少有一个动作
	)

	@classmethod
	def model_json_schema(cls, **kwargs):
		"""生成 JSON Schema，标记必需字段"""
		schema = super().model_json_schema(**kwargs)
		schema['required'] = ['evaluation_previous_goal', 'memory', 'next_goal', 'action']
		return schema

	@property
	def current_state(self) -> AgentBrain:
		"""
		获取当前思维状态
		
		为向后兼容提供的属性，返回包含扁平化属性的 AgentBrain 对象。
		
		Returns:
			AgentBrain: 当前思维状态
		"""
		return AgentBrain(
			thinking=self.thinking,
			evaluation_previous_goal=self.evaluation_previous_goal if self.evaluation_previous_goal else '',
			memory=self.memory if self.memory else '',
			next_goal=self.next_goal if self.next_goal else '',
		)

	@staticmethod
	def type_with_custom_actions(custom_actions: type[ActionModel]) -> type[AgentOutput]:
		"""
		创建包含自定义动作的 AgentOutput 类型
		
		使用 Pydantic 的 create_model 动态创建新的模型类，
		将自定义动作类型注入到 action 字段中。
		
		Args:
			custom_actions: 自定义动作的类型（ActionModel 的子类）
			
		Returns:
			type[AgentOutput]: 新的 AgentOutput 子类
		"""
		model_ = create_model(
			'AgentOutput',
			__base__=AgentOutput,
			action=(
				list[custom_actions],  # type: ignore
				Field(..., description='要执行的动作列表', json_schema_extra={'min_items': 1}),
			),
			__module__=AgentOutput.__module__,
		)
		return model_

	@staticmethod
	def type_with_custom_actions_no_thinking(custom_actions: type[ActionModel]) -> type[AgentOutput]:
		"""
		创建无思考字段的 AgentOutput 类型
		
		与 type_with_custom_actions 类似，但从 JSON Schema 中移除 thinking 字段。
		适用于不需要 Chain of Thought 的场景。
		
		Args:
			custom_actions: 自定义动作的类型
			
		Returns:
			type[AgentOutput]: 无 thinking 字段的 AgentOutput 子类
		"""

		class AgentOutputNoThinking(AgentOutput):
			"""无思考字段的 Agent 输出"""
			@classmethod
			def model_json_schema(cls, **kwargs):
				schema = super().model_json_schema(**kwargs)
				del schema['properties']['thinking']
				schema['required'] = ['evaluation_previous_goal', 'memory', 'next_goal', 'action']
				return schema

		model = create_model(
			'AgentOutput',
			__base__=AgentOutputNoThinking,
			action=(
				list[custom_actions],  # type: ignore
				Field(..., json_schema_extra={'min_items': 1}),
			),
			__module__=AgentOutputNoThinking.__module__,
		)

		return model

	@staticmethod
	def type_with_custom_actions_flash_mode(custom_actions: type[ActionModel]) -> type[AgentOutput]:
		"""
		创建快速模式的 AgentOutput 类型
		
		仅保留 memory 和 action 字段，移除 thinking、evaluation_previous_goal 和 next_goal。
		适用于需要最快响应速度的场景。
		
		Args:
			custom_actions: 自定义动作的类型
			
		Returns:
			type[AgentOutput]: 快速模式的 AgentOutput 子类
		"""

		class AgentOutputFlashMode(AgentOutput):
			"""快速模式的 Agent 输出"""
			@classmethod
			def model_json_schema(cls, **kwargs):
				schema = super().model_json_schema(**kwargs)
				# 移除 thinking、evaluation_previous_goal 和 next_goal 字段
				del schema['properties']['thinking']
				del schema['properties']['evaluation_previous_goal']
				del schema['properties']['next_goal']
				# 更新必需字段为仅保留的属性
				schema['required'] = ['memory', 'action']
				return schema

		model = create_model(
			'AgentOutput',
			__base__=AgentOutputFlashMode,
			action=(
				list[custom_actions],  # type: ignore
				Field(..., json_schema_extra={'min_items': 1}),
			),
			__module__=AgentOutputFlashMode.__module__,
		)

		return model


# ============================================================================
# Agent 历史记录类 (Agent History Classes)
# ============================================================================


class AgentHistory(BaseModel):
	"""
	Agent 历史记录项类 (Agent History Item)
	
	此类记录 Agent 单个步骤的完整信息，包括模型输出、执行结果、
	浏览器状态和元数据。是构建完整执行历史的基本单元。
	
	Attributes:
		model_output: 该步骤的模型输出（可能为 None）
		result: 动作执行结果列表
		state: 浏览器状态历史快照
		metadata: 步骤元数据（时间等）
		state_message: 状态消息
	
	Methods:
		get_interacted_element: 获取交互过的 DOM 元素
		model_dump: 自定义序列化，支持敏感数据过滤
	"""

	model_output: AgentOutput | None  # 模型输出
	result: list[ActionResult]  # 执行结果列表
	state: BrowserStateHistory  # 浏览器状态快照
	metadata: StepMetadata | None = None  # 步骤元数据
	state_message: str | None = None  # 状态消息

	model_config = ConfigDict(arbitrary_types_allowed=True, protected_namespaces=())

	@staticmethod
	def get_interacted_element(model_output: AgentOutput, selector_map: DOMSelectorMap) -> list[DOMInteractedElement | None]:
		"""
		获取交互过的 DOM 元素列表
		
		根据动作中的索引从选择器映射中查找对应的 DOM 元素。
		
		Args:
			model_output: Agent 的模型输出
			selector_map: DOM 选择器映射
			
		Returns:
			list[DOMInteractedElement | None]: 交互元素列表，未找到的为 None
		"""
		elements = []
		for action in model_output.action:
			index = action.get_index()
			if index is not None and index in selector_map:
				el = selector_map[index]
				elements.append(DOMInteractedElement.load_from_enhanced_dom_tree(el))
			else:
				elements.append(None)
		return elements

	def _filter_sensitive_data_from_string(self, value: str, sensitive_data: dict[str, str | dict[str, str]] | None) -> str:
		"""
		从字符串中过滤敏感数据
		
		将敏感数据替换为 <secret>key</secret> 格式的占位符。
		
		Args:
			value: 要过滤的字符串
			sensitive_data: 敏感数据字典
			
		Returns:
			str: 过滤后的字符串
		"""
		if not sensitive_data:
			return value

		# 收集所有敏感值，将旧格式转换为新格式
		sensitive_values: dict[str, str] = {}

		# 处理所有敏感数据条目
		for key_or_domain, content in sensitive_data.items():
			if isinstance(content, dict):
				# 新格式: {domain: {key: value}}
				for key, val in content.items():
					if val:  # 跳过空值
						sensitive_values[key] = val
			elif content:  # 旧格式: {key: value} - 内部转换为新格式
				sensitive_values[key_or_domain] = content

		# 如果没有有效的敏感数据条目，直接返回原值
		if not sensitive_values:
			return value

		# 用占位符标签替换所有敏感数据值
		for key, val in sensitive_values.items():
			value = value.replace(val, f'<secret>{key}</secret>')

		return value

	def _filter_sensitive_data_from_dict(
		self, data: dict[str, Any], sensitive_data: dict[str, str | dict[str, str]] | None
	) -> dict[str, Any]:
		"""
		递归过滤字典中的敏感数据
		
		Args:
			data: 要过滤的字典
			sensitive_data: 敏感数据字典
			
		Returns:
			dict[str, Any]: 过滤后的字典
		"""
		if not sensitive_data:
			return data

		filtered_data = {}
		for key, value in data.items():
			if isinstance(value, str):
				filtered_data[key] = self._filter_sensitive_data_from_string(value, sensitive_data)
			elif isinstance(value, dict):
				filtered_data[key] = self._filter_sensitive_data_from_dict(value, sensitive_data)
			elif isinstance(value, list):
				filtered_data[key] = [
					self._filter_sensitive_data_from_string(item, sensitive_data)
					if isinstance(item, str)
					else self._filter_sensitive_data_from_dict(item, sensitive_data)
					if isinstance(item, dict)
					else item
					for item in value
				]
			else:
				filtered_data[key] = value
		return filtered_data

	def model_dump(self, sensitive_data: dict[str, str | dict[str, str]] | None = None, **kwargs) -> dict[str, Any]:
		"""
		自定义序列化方法
		
		处理循环引用并过滤敏感数据。
		
		Args:
			sensitive_data: 要过滤的敏感数据字典
			**kwargs: 其他序列化参数
			
		Returns:
			dict[str, Any]: 序列化后的字典
		"""
		# 处理动作序列化
		model_output_dump = None
		if self.model_output:
			action_dump = [action.model_dump(exclude_none=True, mode='json') for action in self.model_output.action]

			# 仅从包含 input 的动作参数中过滤敏感数据
			if sensitive_data:
				action_dump = [
					self._filter_sensitive_data_from_dict(action, sensitive_data) if 'input' in action else action
					for action in action_dump
				]

			model_output_dump = {
				'evaluation_previous_goal': self.model_output.evaluation_previous_goal,
				'memory': self.model_output.memory,
				'next_goal': self.model_output.next_goal,
				'action': action_dump,  # 保留实际动作数据
			}
			# 仅在存在时包含 thinking
			if self.model_output.thinking is not None:
				model_output_dump['thinking'] = self.model_output.thinking

		# 处理结果序列化 - 不过滤 ActionResult 数据
		# 因为它应该包含对 Agent 有意义的信息
		result_dump = [r.model_dump(exclude_none=True, mode='json') for r in self.result]

		return {
			'model_output': model_output_dump,
			'result': result_dump,
			'state': self.state.to_dict(),
			'metadata': self.metadata.model_dump() if self.metadata else None,
			'state_message': self.state_message,
		}


# 结构化输出的泛型类型变量
AgentStructuredOutput = TypeVar('AgentStructuredOutput', bound=BaseModel)


class AgentHistoryList(BaseModel, Generic[AgentStructuredOutput]):
	"""
	Agent 历史记录列表类 (Agent History List)
	
	存储 Agent 完整执行历史的容器类。提供了丰富的查询方法
	用于分析执行结果、提取内容、检查状态等。
	
	泛型参数:
		AgentStructuredOutput: 结构化输出的 Pydantic 模型类型
	
	Attributes:
		history: 历史记录项列表
		usage: Token 使用摘要
		_output_model_schema: 结构化输出的模型 Schema
	
	主要方法:
		- total_duration_seconds(): 获取总执行时长
		- save_to_file(): 保存历史到文件
		- load_from_file(): 从文件加载历史
		- final_result(): 获取最终结果
		- is_done(): 检查是否完成
		- is_successful(): 检查是否成功
		- errors(): 获取所有错误
		- urls(): 获取所有访问的 URL
		- screenshots(): 获取所有截图
		- model_actions(): 获取所有动作
		- structured_output: 获取结构化输出
	"""

	history: list[AgentHistory]  # 历史记录列表
	usage: UsageSummary | None = None  # Token 使用摘要

	_output_model_schema: type[AgentStructuredOutput] | None = None  # 结构化输出 Schema

	def total_duration_seconds(self) -> float:
		"""
		获取所有步骤的总执行时长
		
		Returns:
			float: 总时长（秒）
		"""
		total = 0.0
		for h in self.history:
			if h.metadata:
				total += h.metadata.duration_seconds
		return total

	def __len__(self) -> int:
		"""返回历史记录项数量"""
		return len(self.history)

	def __str__(self) -> str:
		"""对象的字符串表示"""
		return f'AgentHistoryList(all_results={self.action_results()}, all_model_outputs={self.model_actions()})'

	def add_item(self, history_item: AgentHistory) -> None:
		"""
		添加历史记录项
		
		Args:
			history_item: 要添加的历史记录项
		"""
		self.history.append(history_item)

	def __repr__(self) -> str:
		"""对象的 repr 表示"""
		return self.__str__()

	def save_to_file(self, filepath: str | Path, sensitive_data: dict[str, str | dict[str, str]] | None = None) -> None:
		"""
		保存历史到 JSON 文件
		
		支持敏感数据过滤。
		
		Args:
			filepath: 文件路径
			sensitive_data: 要过滤的敏感数据
		"""
		try:
			Path(filepath).parent.mkdir(parents=True, exist_ok=True)
			data = self.model_dump(sensitive_data=sensitive_data)
			with open(filepath, 'w', encoding='utf-8') as f:
				json.dump(data, f, indent=2)
		except Exception as e:
			raise e

	# def save_as_playwright_script(
	# 	self,
	# 	output_path: str | Path,
	# 	sensitive_data_keys: list[str] | None = None,
	# 	browser_config: BrowserConfig | None = None,
	# 	context_config: BrowserContextConfig | None = None,
	# ) -> None:
	# 	"""
	# 	Generates a Playwright script based on the agent's history and saves it to a file.
	# 	Args:
	# 		output_path: The path where the generated Python script will be saved.
	# 		sensitive_data_keys: A list of keys used as placeholders for sensitive data
	# 							 (e.g., ['username_placeholder', 'password_placeholder']).
	# 							 These will be loaded from environment variables in the
	# 							 generated script.
	# 		browser_config: Configuration of the original Browser instance.
	# 		context_config: Configuration of the original BrowserContext instance.
	# 	"""
	# 	from browser_use.agent.playwright_script_generator import PlaywrightScriptGenerator

	# 	try:
	# 		serialized_history = self.model_dump()['history']
	# 		generator = PlaywrightScriptGenerator(serialized_history, sensitive_data_keys, browser_config, context_config)

	# 		script_content = generator.generate_script_content()
	# 		path_obj = Path(output_path)
	# 		path_obj.parent.mkdir(parents=True, exist_ok=True)
	# 		with open(path_obj, 'w', encoding='utf-8') as f:
	# 			f.write(script_content)
	# 	except Exception as e:
	# 		raise e

	def model_dump(self, **kwargs) -> dict[str, Any]:
		"""
		自定义序列化方法
		
		使用 AgentHistory 的 model_dump 进行正确序列化。
		
		Returns:
			dict[str, Any]: 序列化后的字典
		"""
		return {
			'history': [h.model_dump(**kwargs) for h in self.history],
		}

	@classmethod
	def load_from_dict(cls, data: dict[str, Any], output_model: type[AgentOutput]) -> AgentHistoryList:
		"""
		从字典加载历史记录
		
		遍历历史并验证 output_model 动作，以丰富自定义动作。
		
		Args:
			data: 包含历史数据的字典
			output_model: 用于验证的输出模型类型
			
		Returns:
			AgentHistoryList: 加载的历史记录列表
		"""
		for h in data['history']:
			if h['model_output']:
				if isinstance(h['model_output'], dict):
					h['model_output'] = output_model.model_validate(h['model_output'])
				else:
					h['model_output'] = None
			if 'interacted_element' not in h['state']:
				h['state']['interacted_element'] = None

		history = cls.model_validate(data)
		return history

	@classmethod
	def load_from_file(cls, filepath: str | Path, output_model: type[AgentOutput]) -> AgentHistoryList:
		"""
		从 JSON 文件加载历史记录
		
		Args:
			filepath: 文件路径
			output_model: 用于验证的输出模型类型
			
		Returns:
			AgentHistoryList: 加载的历史记录列表
		"""
		with open(filepath, encoding='utf-8') as f:
			data = json.load(f)
		return cls.load_from_dict(data, output_model)

	def last_action(self) -> None | dict:
		"""
		获取历史中的最后一个动作
		
		Returns:
			dict | None: 最后一个动作的字典，如果没有则返回 None
		"""
		if self.history and self.history[-1].model_output:
			return self.history[-1].model_output.action[-1].model_dump(exclude_none=True, mode='json')
		return None

	def errors(self) -> list[str | None]:
		"""
		获取历史中的所有错误
		
		对于没有错误的步骤返回 None。
		
		Returns:
			list[str | None]: 错误列表
		"""
		errors = []
		for h in self.history:
			step_errors = [r.error for r in h.result if r.error]
			# 每个步骤只能有一个错误
			errors.append(step_errors[0] if step_errors else None)
		return errors

	def final_result(self) -> None | str:
		"""
		获取历史中的最终结果
		
		Returns:
			str | None: 最终提取的内容
		"""
		if self.history and self.history[-1].result[-1].extracted_content:
			return self.history[-1].result[-1].extracted_content
		return None

	def is_done(self) -> bool:
		"""
		检查 Agent 是否已完成
		
		Returns:
			bool: 如果已完成返回 True
		"""
		if self.history and len(self.history[-1].result) > 0:
			last_result = self.history[-1].result[-1]
			return last_result.is_done is True
		return False

	def is_successful(self) -> bool | None:
		"""
		检查 Agent 是否成功完成
		
		Agent 在最后一步决定是否成功。如果尚未完成则返回 None。
		
		Returns:
			bool | None: 成功返回 True，失败返回 False，未完成返回 None
		"""
		if self.history and len(self.history[-1].result) > 0:
			last_result = self.history[-1].result[-1]
			if last_result.is_done is True:
				return last_result.success
		return None

	def has_errors(self) -> bool:
		"""
		检查是否有任何非 None 的错误
		
		Returns:
			bool: 如果有错误返回 True
		"""
		return any(error is not None for error in self.errors())

	def judgement(self) -> dict | None:
		"""
		获取 Judge 判断结果的字典形式
		
		Returns:
			dict | None: 判断结果字典，如果不存在则返回 None
		"""
		if self.history and len(self.history[-1].result) > 0:
			last_result = self.history[-1].result[-1]
			if last_result.judgement:
				return last_result.judgement.model_dump()
		return None

	def is_judged(self) -> bool:
		"""
		检查 Agent 轨迹是否已被 Judge 评判
		
		Returns:
			bool: 如果已评判返回 True
		"""
		if self.history and len(self.history[-1].result) > 0:
			last_result = self.history[-1].result[-1]
			return last_result.judgement is not None
		return False

	def is_validated(self) -> bool | None:
		"""
		检查 Judge 是否验证了执行（verdict 为 True）
		
		Returns:
			bool | None: 验证通过返回 True，失败返回 False，未评判返回 None
		"""
		if self.history and len(self.history[-1].result) > 0:
			last_result = self.history[-1].result[-1]
			if last_result.judgement:
				return last_result.judgement.verdict
		return None

	def urls(self) -> list[str | None]:
		"""
		获取历史中的所有 URL
		
		Returns:
			list[str | None]: URL 列表
		"""
		return [h.state.url if h.state.url is not None else None for h in self.history]

	def screenshot_paths(self, n_last: int | None = None, return_none_if_not_screenshot: bool = True) -> list[str | None]:
		"""
		获取历史中的所有截图路径
		
		Args:
			n_last: 只返回最后 n 个截图路径
			return_none_if_not_screenshot: 如果没有截图是否返回 None
			
		Returns:
			list[str | None]: 截图路径列表
		"""
		if n_last == 0:
			return []
		if n_last is None:
			if return_none_if_not_screenshot:
				return [h.state.screenshot_path if h.state.screenshot_path is not None else None for h in self.history]
			else:
				return [h.state.screenshot_path for h in self.history if h.state.screenshot_path is not None]
		else:
			if return_none_if_not_screenshot:
				return [h.state.screenshot_path if h.state.screenshot_path is not None else None for h in self.history[-n_last:]]
			else:
				return [h.state.screenshot_path for h in self.history[-n_last:] if h.state.screenshot_path is not None]

	def screenshots(self, n_last: int | None = None, return_none_if_not_screenshot: bool = True) -> list[str | None]:
		"""
		获取历史中的所有截图（base64 字符串）
		
		Args:
			n_last: 只返回最后 n 个截图
			return_none_if_not_screenshot: 如果没有截图是否返回 None
			
		Returns:
			list[str | None]: 截图的 base64 字符串列表
		"""
		if n_last == 0:
			return []

		history_items = self.history if n_last is None else self.history[-n_last:]
		screenshots = []

		for item in history_items:
			screenshot_b64 = item.state.get_screenshot()
			if screenshot_b64:
				screenshots.append(screenshot_b64)
			else:
				if return_none_if_not_screenshot:
					screenshots.append(None)
				# 如果 return_none_if_not_screenshot 为 False，则跳过 None 值

		return screenshots

	def action_names(self) -> list[str]:
		"""
		获取历史中的所有动作名称
		
		Returns:
			list[str]: 动作名称列表
		"""
		action_names = []
		for action in self.model_actions():
			actions = list(action.keys())
			if actions:
				action_names.append(actions[0])
		return action_names

	def model_thoughts(self) -> list[AgentBrain]:
		"""
		获取历史中的所有思考内容
		
		Returns:
			list[AgentBrain]: Agent 思考状态列表
		"""
		return [h.model_output.current_state for h in self.history if h.model_output]

	def model_outputs(self) -> list[AgentOutput]:
		"""
		获取历史中的所有模型输出
		
		Returns:
			list[AgentOutput]: 模型输出列表
		"""
		return [h.model_output for h in self.history if h.model_output]

	def model_actions(self) -> list[dict]:
		"""
		获取历史中的所有动作及其参数
		
		Returns:
			list[dict]: 动作字典列表，包含交互元素信息
		"""
		outputs = []

		for h in self.history:
			if h.model_output:
				# 在 zip 之前防止 interacted_element 为 None
				interacted_elements = h.state.interacted_element or [None] * len(h.model_output.action)
				for action, interacted_element in zip(h.model_output.action, interacted_elements):
					output = action.model_dump(exclude_none=True, mode='json')
					output['interacted_element'] = interacted_element
					outputs.append(output)
		return outputs

	def action_history(self) -> list[list[dict]]:
		"""
		获取截断的动作历史，只包含必要字段
		
		Returns:
			list[list[dict]]: 按步骤分组的动作列表
		"""
		step_outputs = []

		for h in self.history:
			step_actions = []
			if h.model_output:
				# 在 zip 之前防止 interacted_element 为 None
				interacted_elements = h.state.interacted_element or [None] * len(h.model_output.action)
				# 将动作与交互元素和结果配对
				for action, interacted_element, result in zip(h.model_output.action, interacted_elements, h.result):
					action_output = action.model_dump(exclude_none=True, mode='json')
					action_output['interacted_element'] = interacted_element
					# 只保留结果中的 long_term_memory
					action_output['result'] = result.long_term_memory if result and result.long_term_memory else None
					step_actions.append(action_output)
			step_outputs.append(step_actions)

		return step_outputs

	def action_results(self) -> list[ActionResult]:
		"""
		获取历史中的所有结果
		
		Returns:
			list[ActionResult]: 动作结果列表
		"""
		results = []
		for h in self.history:
			results.extend([r for r in h.result if r])
		return results

	def extracted_content(self) -> list[str]:
		"""
		获取历史中的所有提取内容
		
		Returns:
			list[str]: 提取内容列表
		"""
		content = []
		for h in self.history:
			content.extend([r.extracted_content for r in h.result if r.extracted_content])
		return content

	def model_actions_filtered(self, include: list[str] | None = None) -> list[dict]:
		"""
		获取过滤后的模型动作（JSON 格式）
		
		Args:
			include: 要包含的动作名称列表
			
		Returns:
			list[dict]: 过滤后的动作列表
		"""
		if include is None:
			include = []
		outputs = self.model_actions()
		result = []
		for o in outputs:
			for i in include:
				if i == list(o.keys())[0]:
					result.append(o)
		return result

	def number_of_steps(self) -> int:
		"""
		获取历史中的步骤数量
		
		Returns:
			int: 步骤数量
		"""
		return len(self.history)

	def agent_steps(self) -> list[str]:
		"""
		将 Agent 历史格式化为可读的步骤描述
		
		用于 Judge 评估。
		
		Returns:
			list[str]: 格式化的步骤描述列表
		"""
		steps = []

		# 遍历历史项（每个是一个 AgentHistory）
		for i, h in enumerate(self.history):
			step_text = f'Step {i + 1}:\n'

			# 从 model_output 获取动作
			if h.model_output and h.model_output.action:
				# 使用 mode='json' 的 model_dump 来正确序列化枚举
				actions_list = [action.model_dump(exclude_none=True, mode='json') for action in h.model_output.action]
				action_json = json.dumps(actions_list, indent=1)
				step_text += f'Actions: {action_json}\n'

			# 获取结果（h.result 中已经是 list[ActionResult]）
			if h.result:
				for j, result in enumerate(h.result):
					if result.extracted_content:
						content = str(result.extracted_content)
						step_text += f'Result {j + 1}: {content}\n'

					if result.error:
						error = str(result.error)
						step_text += f'Error {j + 1}: {error}\n'

			steps.append(step_text)

		return steps

	@property
	def structured_output(self) -> AgentStructuredOutput | None:
		"""
		获取历史中的结构化输出
		
		属性方法。
		
		Returns:
			AgentStructuredOutput | None: 如果 final_result 和 _output_model_schema 都可用则返回结构化输出，否则返回 None
		"""
		final_result = self.final_result()
		if final_result is not None and self._output_model_schema is not None:
			return self._output_model_schema.model_validate_json(final_result)

		return None

	def get_structured_output(self, output_model: type[AgentStructuredOutput]) -> AgentStructuredOutput | None:
		"""
		使用提供的模式从历史中获取结构化输出
		
		当从沙箱执行访问结构化输出时使用此方法，
		因为 _output_model_schema 私有属性在序列化过程中不会被保留。
		
		Args:
			output_model: 用于解析输出的 Pydantic 模型类
			
		Returns:
			AgentStructuredOutput | None: 解析后的结构化输出，如果没有最终结果则返回 None
		"""
		final_result = self.final_result()
		if final_result is not None:
			return output_model.model_validate_json(final_result)
		return None


# ============================================================================
# 错误处理类 (Error Handling Classes)
# ============================================================================


class AgentError:
	"""
	Agent 错误处理类 (Agent Error Container)
	
	提供错误消息常量和错误格式化方法。
	用于统一处理 Agent 执行过程中的各种错误。
	
	Class Attributes:
		VALIDATION_ERROR: 验证错误消息
		RATE_LIMIT_ERROR: 速率限制错误消息
		NO_VALID_ACTION: 无有效动作错误消息
	
	Methods:
		format_error: 根据错误类型格式化错误消息
	"""

	VALIDATION_ERROR = 'Invalid model output format. Please follow the correct schema.'  # 验证错误
	RATE_LIMIT_ERROR = 'Rate limit reached. Waiting before retry.'  # 速率限制错误
	NO_VALID_ACTION = 'No valid action found'  # 无有效动作

	@staticmethod
	def format_error(error: Exception, include_trace: bool = False) -> str:
		"""
		根据错误类型格式化错误消息
		
		可选择包含堆栈追踪信息。
		
		Args:
			error: 异常对象
			include_trace: 是否包含堆栈追踪
			
		Returns:
			str: 格式化后的错误消息
		"""
		message = ''
		if isinstance(error, ValidationError):
			return f'{AgentError.VALIDATION_ERROR}\nDetails: {str(error)}'
		# 延迟导入以避免在模块级别加载 openai SDK（约 800ms）
		from openai import RateLimitError

		if isinstance(error, RateLimitError):
			return AgentError.RATE_LIMIT_ERROR

		# 处理来自 llm_use 的 LLM 响应验证错误
		error_str = str(error)
		if 'LLM response missing required fields' in error_str or 'Expected format: AgentOutput' in error_str:
			# 提取主要错误消息，不包含巨大的堆栈追踪
			lines = error_str.split('\n')
			main_error = lines[0] if lines else error_str

			# 提供更清晰的错误消息
			helpful_msg = f'{main_error}\n\n上一个响应的输出结构无效。请遵循所需的输出格式。\n\n'

			if include_trace:
				helpful_msg += f'\n\n完整堆栈追踪:\n{traceback.format_exc()}'

			return helpful_msg

		if include_trace:
			return f'{str(error)}\n堆栈追踪:\n{traceback.format_exc()}'
		return f'{str(error)}'


# ============================================================================
# 变量检测类 (Variable Detection Classes)
# ============================================================================


class DetectedVariable(BaseModel):
	"""
	检测到的变量类 (Detected Variable)
	
	表示在 Agent 历史中检测到的变量信息。
	用于变量化和参数化重放功能。
	
	Attributes:
		name: 变量名称
		original_value: 原始值
		type: 变量类型（默认为 'string'）
		format: 变量格式（可选）
	"""

	name: str  # 变量名称
	original_value: str  # 原始值
	type: str = 'string'  # 变量类型
	format: str | None = None  # 变量格式


class VariableMetadata(BaseModel):
	"""
	变量元数据类 (Variable Metadata)
	
	存储历史中检测到的所有变量的元数据。
	
	Attributes:
		detected_variables: 检测到的变量字典，键为变量标识符
	"""

	detected_variables: dict[str, DetectedVariable] = Field(default_factory=dict)  # 变量字典
