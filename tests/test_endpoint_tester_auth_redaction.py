import pytest

from scripts.test_api_endpoints import redact


def test_auth_values_are_redacted_from_nested_payloads():
    assert redact({"code": "000042", "nested": {"new_password": "private"}}) == {
        "code": "<redacted>", "nested": {"new_password": "<redacted>"}
    }


@pytest.mark.parametrize("field", ["code", "new_password", "password", "refresh_token"])
def test_validation_input_is_redacted_using_field_location(field):
    error = {"detail": [{
        "loc": ["body", field], "input": "private value",
        "ctx": {"secret": "private value"}, "msg": "Invalid value",
    }]}
    safe = redact(error)
    assert safe["detail"][0]["input"] == "<redacted>"
    assert safe["detail"][0]["ctx"] == "<redacted>"
    assert safe["detail"][0]["loc"] == ["body", field]
    assert safe["detail"][0]["msg"] == "Invalid value"
    assert error["detail"][0]["input"] == "private value"
