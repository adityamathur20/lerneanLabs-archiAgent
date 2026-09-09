"""Test doubles. No network, ever."""

from __future__ import annotations


class FakeLLMClient:
    """Returns a canned reply, or raises a canned error.

    Records every call so tests can assert on the request shape without a
    live provider.
    """

    def __init__(self, reply: dict | None = None,
                 error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error
        self.calls: list[dict] = []

    def classify_json(self, *, system: str, user: str, schema: dict,
                      max_tokens: int = 2048) -> dict:
        self.calls.append({"system": system, "user": user,
                           "schema": schema, "max_tokens": max_tokens})
        if self._error is not None:
            raise self._error
        assert self._reply is not None, "FakeLLMClient needs a reply or an error"
        return self._reply
