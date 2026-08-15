"""Stable, user-safe product failures."""

from __future__ import annotations


class ProductError(RuntimeError):
    """An expected failure that may be returned without leaking internals."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def conflict(code: str, message: str) -> ProductError:
    """Create a non-retryable product state conflict."""

    return ProductError(409, code, message)
