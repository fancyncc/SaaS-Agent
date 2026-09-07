import re
import secrets
from time import monotonic

from opentelemetry import trace
from prometheus_client import Counter, Histogram

from backend.config import get_settings

REQUESTS = Counter("saas_api_requests_total", "API requests", ["method", "route", "status"])
LATENCY = Histogram("saas_api_seconds", "API latency", ["method", "route"])


def trace_identifier(traceparent: str | None) -> str:
    """Accept valid version-00 parent IDs; never persist malformed client text."""
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", traceparent or "")
    if match and int(match[1], 16) and int(match[2], 16):
        return match[1]
    return secrets.token_hex(16)


def setup():
    endpoint = get_settings().otel_exporter_otlp_endpoint
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": "saas-implementation"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)


async def instrument(request, call_next):
    started = monotonic()
    with trace.get_tracer("implementation.api").start_as_current_span(request.method):
        response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    REQUESTS.labels(request.method, route, str(response.status_code)).inc()
    LATENCY.labels(request.method, route).observe(monotonic() - started)
    return response
