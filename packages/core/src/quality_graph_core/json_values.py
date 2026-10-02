"""Validate JSON shapes with an explicit caller-owned error contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue


@dataclass(frozen=True)
class JsonValidator:
    """Narrow JSON values without coercion or loss of diagnostic context."""

    error_type: type[Exception] = TypeError
    verb: str = "be"

    def object(self, value: JsonValue, context: str) -> dict[str, JsonValue]:
        """Require an object with string keys."""
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            message = f"{context} must {self.verb} an object"
            raise self.error_type(message)
        return value

    def array(self, value: JsonValue, context: str) -> list[JsonValue]:
        """Require an array without coercing other sequences."""
        if not isinstance(value, list):
            message = f"{context} must {self.verb} an array"
            raise self.error_type(message)
        return value

    def string(self, value: JsonValue, context: str) -> str:
        """Require a string while allowing empty values for caller validation."""
        if not isinstance(value, str):
            message = f"{context} must be a string"
            raise self.error_type(message)
        return value

    def integer(self, value: JsonValue, context: str) -> int:
        """Require an integer and reject booleans."""
        if not isinstance(value, int) or isinstance(value, bool):
            message = f"{context} must be an integer"
            raise self.error_type(message)
        return value

    def boolean(self, value: JsonValue, context: str) -> bool:
        """Require a boolean without accepting numeric stand-ins."""
        if not isinstance(value, bool):
            message = f"{context} must be a boolean"
            raise self.error_type(message)
        return value

    def optional_object(self, value: JsonValue, context: str) -> dict[str, JsonValue] | None:
        """Allow null or validate an object."""
        return None if value is None else self.object(value, context)

    def optional_string(self, value: JsonValue, context: str) -> str | None:
        """Allow null or validate a string."""
        return None if value is None else self.string(value, context)

    def optional_integer(self, value: JsonValue, context: str) -> int | None:
        """Allow null or validate an integer."""
        return None if value is None else self.integer(value, context)


JSON = JsonValidator()
