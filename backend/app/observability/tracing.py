"""OpenTelemetry tracing. Spans are always created (cheap no-op without an exporter);
set OTEL_ENDPOINT to export OTLP/HTTP to a collector (Jaeger, Tempo, Honeycomb, ...)."""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

log = logging.getLogger(__name__)
_configured = False


def configure_tracing(service_name: str, endpoint: str | None = None, exporter: SpanExporter | None = None) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
        log.info("OTLP trace export enabled")
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("mindguard")
