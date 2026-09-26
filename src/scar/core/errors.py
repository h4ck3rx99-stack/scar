"""Explicit error taxonomy for SCAR."""

from __future__ import annotations


class ScarError(Exception):
    """Base class for all SCAR errors."""


class ConfigError(ScarError):
    pass


class CapabilityUnavailable(ScarError):
    """A real integration whose external prerequisite is missing."""

    def __init__(self, prerequisite: str, setup_doc: str, detail: str = "") -> None:
        self.prerequisite = prerequisite
        self.setup_doc = setup_doc
        self.detail = detail
        msg = f"{prerequisite} (see {setup_doc})"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


class PolicyDenied(ScarError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ApprovalDenied(ScarError):
    def __init__(self, reason: str = "denied by user") -> None:
        self.reason = reason
        super().__init__(reason)


class ToolError(ScarError):
    """A tool failed in a way the agent can observe and recover from."""

    def __init__(self, message: str, error_type: str = "ToolError") -> None:
        self.error_type = error_type
        super().__init__(message)


class ToolInputError(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "InvalidInput")


class PathViolation(ScarError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class BudgetExceeded(ScarError):
    def __init__(self, budget: str, limit: float) -> None:
        self.budget = budget
        self.limit = limit
        super().__init__(f"budget exceeded: {budget} (limit {limit})")


class Cancelled(ScarError):
    def __init__(self, reason: str = "cancelled") -> None:
        self.reason = reason
        super().__init__(reason)


class VerificationFailed(ScarError):
    pass


class FocusLost(ScarError):
    """The expected target window lost foreground focus before input."""


class ResourceDenied(ScarError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StorageError(ScarError):
    pass
