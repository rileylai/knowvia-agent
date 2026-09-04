from __future__ import annotations


class InfrastructureExecutionTimeout(RuntimeError):
    """Content-free timeout signal shared across infrastructure boundaries."""

    def __init__(self) -> None:
        super().__init__("Infrastructure execution timed out")
