"""Sanitized interface errors; never include paths, credentials or raw exceptions."""


class InterfaceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
