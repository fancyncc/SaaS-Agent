import re

from backend.observability import trace_identifier


def test_traceparent_preserves_trace_id_not_span_suffix():
    trace_id = "a" * 32
    assert trace_identifier(f"00-{trace_id}-{'b' * 16}-01") == trace_id
    for invalid in (None, "untrusted-text", f"00-{'0' * 32}-{'b' * 16}-01", f"00-{trace_id}-{'0' * 16}-01"):
        result = trace_identifier(invalid)
        assert re.fullmatch(r"[0-9a-f]{32}", result)
        assert int(result, 16)


async def test_api_returns_valid_trace_id(client):
    trace_id = "c" * 32
    result = await client.get("/api/auth/me", headers={"traceparent": f"00-{trace_id}-{'d' * 16}-01"})
    assert result.headers["X-Trace-ID"] == trace_id
