"""Shared control-flow exceptions for built-in action modules."""


class TaskNoop(BaseException):
    """Raised when an action found no work and should not record a run."""


class TaskDeferred(BaseException):
    """Raised when a task should run later without recording a skipped run."""

    def __init__(self, reason: str, delay_seconds: int = 20 * 60):
        super().__init__(reason)
        self.reason = reason
        self.delay_seconds = delay_seconds
