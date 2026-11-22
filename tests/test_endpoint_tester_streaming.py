"""CLI endpoint tester recognizes SSE completion and failure semantics."""

import json

import httpx
import pytest

from scripts.test_api_endpoints import EndpointTester


@pytest.mark.parametrize("terminal", ["complete", "error", None])
def test_cli_requires_completion_not_only_http_200(terminal, capsys):
    frames = 'event: delta\ndata: {"content":"first"}\n\n'
    if terminal:
        payload = (
            {"status": 503, "detail": "Unavailable"}
            if terminal == "error"
            else {
                "assistant_message": {"content": "first"},
                "refresh_token": "DO_NOT_PRINT",
            }
        )
        frames += f"event: {terminal}\ndata: {json.dumps(payload)}\n\n"
    tester = EndpointTester("http://test", 5)
    tester.client.close()
    tester.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, text=frames, headers={"Content-Type": "text/event-stream"}
            )
        )
    )
    try:
        assert tester.test(
            "POST",
            "/v1/conversations/test/messages/stream",
            200,
            {"content": "question"},
        ) is (terminal == "complete")
        output = capsys.readouterr().out
        assert "first" in output
        assert "DO_NOT_PRINT" not in output
    finally:
        tester.close()
