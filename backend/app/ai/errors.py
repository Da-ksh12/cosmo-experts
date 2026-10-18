"""Errors of the unified AI engine. Every error carries a status the engine maps to the response envelope and a message that is safe to show to the person who
made the request (no paths, no exception text, no internal state)."""
from __future__ import annotations


class AIError(Exception):
    status = "error"
    default_message = "Something went wrong while processing this request."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.public_message = message or self.default_message


class Unsupported(AIError):
    status = "unsupported"
    default_message = "This kind of request is not supported."


class Unavailable(AIError):
    status = "unavailable"
    default_message = "This feature is not available right now."


class RejectedInput(AIError):
    status = "rejected_input"
    default_message = "The input could not be used."


class Forbidden(AIError):
    status = "forbidden"
    default_message = "You do not have permission for this action."


class ResourceExhausted(AIError):
    status = "resource_exhausted"
    default_message = "The service is busy. Please try again in a moment."


class TimeoutExceeded(AIError):
    status = "timeout"
    default_message = "This took too long and was stopped. Please try again."


class Cancelled(AIError):
    status = "cancelled"
    default_message = "This request was cancelled."
