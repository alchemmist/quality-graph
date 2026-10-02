from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qg_gitlab.api import integer, string
from quality_graph_core.json_values import JSON, JsonValidator

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue


@pytest.mark.parametrize("error", [TypeError, ValueError])
@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("object", []),
        ("array", {}),
        ("string", 1),
        ("integer", True),
        ("integer", 1.5),
        ("boolean", 1),
        ("optional_integer", False),
        ("optional_object", []),
        ("optional_string", 3),
    ],
)
def test_json_shapes_preserve_caller_exception_and_context(
    error: type[Exception], method: str, value: JsonValue
) -> None:
    validator = JsonValidator(error)
    with pytest.raises(error, match="probe"):
        getattr(validator, method)(value, "probe")


def test_json_optional_values_do_not_coerce_and_identity_bounds_stay_explicit() -> None:
    assert JSON.optional_object(None, "object") is None
    assert JSON.optional_string(None, "string") is None
    assert JSON.optional_integer(None, "integer") is None
    assert JSON.integer(0, "count") == 0
    assert JSON.string("", "text") == ""
    with pytest.raises(ValueError, match="positive"):
        integer(0, "identity")
    with pytest.raises(TypeError, match="integer"):
        integer(value=True, context="identity")
    with pytest.raises(ValueError, match="empty"):
        string("", "name")
    with pytest.raises(TypeError, match="must return an object"):
        JsonValidator(verb="return").object([], "response")
