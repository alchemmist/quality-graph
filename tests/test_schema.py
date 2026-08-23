import json

from quality_graph.schema import result_schema_json, result_schema_value


def test_result_schema_is_deterministic_and_describes_protocol_contract() -> None:
    serialized = result_schema_json()
    schema = json.loads(serialized)

    assert serialized == result_schema_json()
    assert schema == result_schema_value()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schemaVersion"] == {"const": 0}
    assert schema["additionalProperties"] is False
    assert "failureKind" not in schema["required"]
    assert schema["allOf"][0]["then"] == {"required": ["failureKind"]}
