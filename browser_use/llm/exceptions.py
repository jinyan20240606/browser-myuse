class ModelError(Exception):
	pass


class ModelProviderError(ModelError):
	"""Exception raised when a model provider returns an error."""

	def __init__(
		self,
		message: str,
		status_code: int = 502,
		model: str | None = None,
		user_facing_message: str | None = None,
		error_summary: str | None = None,
		dump_file: str | None = None,
		finish_reason: str | None = None,
	):
		super().__init__(message)
		self.message = message
		self.status_code = status_code
		self.model = model
		self.user_facing_message = user_facing_message
		self.error_summary = error_summary
		self.dump_file = dump_file
		self.finish_reason = finish_reason


class ModelRateLimitError(ModelProviderError):
	"""Exception raised when a model provider returns a rate limit error."""

	def __init__(
		self,
		message: str,
		status_code: int = 429,
		model: str | None = None,
	):
		super().__init__(message, status_code, model)
