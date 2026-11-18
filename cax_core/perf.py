"""Lightweight performance tracing shared by local and LLM operations."""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional

SpanSink = Callable[[dict[str, Any]], None]

_collector: ContextVar[Optional["PerformanceCollector"]] = ContextVar(
    "cax_performance_collector",
    default=None,
)
_span_id: ContextVar[Optional[str]] = ContextVar("cax_performance_span", default=None)
_record_id: ContextVar[Optional[str]] = ContextVar("cax_performance_record", default=None)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)


def extract_token_usage(payload: Any) -> tuple[dict[str, int], bool]:
    """Extract provider-reported token usage without inventing missing counts."""
    if not isinstance(payload, dict):
        return {}, False
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return {}, False

    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    candidates = {
        "prompt_tokens": raw.get("prompt_tokens"),
        "completion_tokens": raw.get("completion_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "cached_prompt_tokens": (
            raw.get("prompt_cache_hit_tokens")
            or raw.get("cached_prompt_tokens")
            or prompt_details.get("cached_tokens")
        ),
        "reasoning_tokens": (
            raw.get("reasoning_tokens")
            or completion_details.get("reasoning_tokens")
        ),
    }
    usage: dict[str, int] = {}
    for key, value in candidates.items():
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            usage[key] = number
    complete = "total_tokens" in usage or (
        "prompt_tokens" in usage and "completion_tokens" in usage
    )
    return usage, complete


class PerfSpan:
    """Mutable span that is emitted when its context exits."""

    def __init__(
        self,
        collector: "PerformanceCollector",
        operation: str,
        *,
        kind: str = "non_llm",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        record_id: Optional[str] = None,
        inputs: Optional[dict[str, Any]] = None,
    ):
        self.collector = collector
        self.operation = operation
        self.kind = kind
        self.provider = provider
        self.model = model
        self.record_id = record_id or _record_id.get()
        self.inputs = dict(inputs or {})
        self.outputs: dict[str, Any] = {}
        self.token_usage: dict[str, int] = {}
        self.rates: dict[str, float] = {}
        self.attributes: dict[str, Any] = {}
        self.status = "ok"
        self.error: Optional[str] = None
        self.retries = 0
        self.usage_complete = False
        self.span_id = uuid.uuid4().hex
        self.parent_span_id: Optional[str] = None
        self.started_at = _now()
        self.started_ns = time.perf_counter_ns()
        self._span_token: Any = None
        self._record_token: Any = None

    def __enter__(self) -> "PerfSpan":
        self.parent_span_id = _span_id.get()
        self._span_token = _span_id.set(self.span_id)
        if self.record_id:
            self._record_token = _record_id.set(self.record_id)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc is not None:
            self.status = "error"
            self.error = str(exc)
        if self._record_token is not None:
            _record_id.reset(self._record_token)
        if self._span_token is not None:
            _span_id.reset(self._span_token)

        elapsed_ns = max(0, time.perf_counter_ns() - self.started_ns)
        event = {
            "span_id": self.span_id,
            "trace_id": self.collector.trace_id,
            "parent_span_id": self.parent_span_id,
            "run_id": self.collector.run_id,
            "record_id": self.record_id,
            "operation": self.operation,
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "started_at": self.started_at,
            "wall_ms": round(elapsed_ns / 1_000_000, 3),
            "inputs": _jsonable(self.inputs),
            "outputs": _jsonable(self.outputs),
            "token_usage": _jsonable(self.token_usage),
            "rates": _jsonable(self.rates),
            "attributes": _jsonable(self.attributes),
            "retries": int(self.retries),
            "usage_complete": bool(self.usage_complete),
            "error": self.error,
        }
        try:
            self.collector.emit(event)
        except Exception:
            # Telemetry must never break the parsing workflow.
            pass
        return False

    def set_inputs(self, **values: Any) -> "PerfSpan":
        self.inputs.update(values)
        return self

    def set_outputs(self, **values: Any) -> "PerfSpan":
        self.outputs.update(values)
        return self

    def set_rates(self, **values: Any) -> "PerfSpan":
        for name, value in values.items():
            try:
                self.rates[name] = round(float(value), 6)
            except (TypeError, ValueError):
                continue
        return self

    def set_token_usage(self, usage: dict[str, int], *, complete: bool) -> "PerfSpan":
        self.token_usage.update(usage)
        self.usage_complete = bool(complete)
        elapsed_s = max((time.perf_counter_ns() - self.started_ns) / 1_000_000_000, 1e-9)
        if usage.get("completion_tokens") is not None:
            self.set_rates(completion_tokens_per_second=usage["completion_tokens"] / elapsed_s)
        return self

    def set_status(self, status: str, *, error: Optional[str] = None) -> "PerfSpan":
        self.status = status
        if error is not None:
            self.error = error
        return self

    def set_attributes(self, **values: Any) -> "PerfSpan":
        self.attributes.update(values)
        return self


class _NullSpan:
    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False

    def set_inputs(self, **values: Any) -> "_NullSpan":
        return self

    def set_outputs(self, **values: Any) -> "_NullSpan":
        return self

    def set_rates(self, **values: Any) -> "_NullSpan":
        return self

    def set_token_usage(self, usage: dict[str, int], *, complete: bool) -> "_NullSpan":
        return self

    def set_status(self, status: str, *, error: Optional[str] = None) -> "_NullSpan":
        return self

    def set_attributes(self, **values: Any) -> "_NullSpan":
        return self


class PerformanceCollector:
    def __init__(
        self,
        run_id: Optional[str] = None,
        *,
        trace_id: Optional[str] = None,
        sink: Optional[SpanSink] = None,
    ):
        self.run_id = run_id
        self.trace_id = trace_id or uuid.uuid4().hex
        self.sink = sink
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.sink is not None:
            self.sink(event)

    def span(
        self,
        operation: str,
        *,
        kind: str = "non_llm",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        record_id: Optional[str] = None,
        inputs: Optional[dict[str, Any]] = None,
    ) -> PerfSpan:
        return PerfSpan(
            self,
            operation,
            kind=kind,
            provider=provider,
            model=model,
            record_id=record_id,
            inputs=inputs,
        )


def current_collector() -> Optional[PerformanceCollector]:
    return _collector.get()


def span(
    operation: str,
    *,
    kind: str = "non_llm",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    record_id: Optional[str] = None,
    inputs: Optional[dict[str, Any]] = None,
) -> PerfSpan | _NullSpan:
    collector = current_collector()
    if collector is None:
        return _NullSpan()
    return collector.span(
        operation,
        kind=kind,
        provider=provider,
        model=model,
        record_id=record_id,
        inputs=inputs,
    )


@contextmanager
def activate(collector: PerformanceCollector) -> Iterator[PerformanceCollector]:
    collector_token = _collector.set(collector)
    span_token = _span_id.set(None)
    record_token = _record_id.set(None)
    try:
        yield collector
    finally:
        _record_id.reset(record_token)
        _span_id.reset(span_token)
        _collector.reset(collector_token)
