"""Tool framework (section 23): JSON schemas, input/output validation, permissions,
consent enforcement, execution logging and error handling.

Every tool is scoped to `ctx.user_id`; no input model accepts a user id, so a tool
call can never reach another user's data. Tools reachable by a language model are an
explicit, read-only allow-list.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import ConflictError, MindGuardError, NotFoundError, ValidationFailedError
from app.observability import metrics
from app.schemas.common import ConsentScope, ToolCallStatus
from app.security.tickets import TicketError

if TYPE_CHECKING:
    from app.agents.runtime import RunContext

log = logging.getLogger(__name__)
Caller = Literal["agent", "llm", "user"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolPermission(StrEnum):
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    DEVICE_ACTION = "device_action"


Handler = Callable[["RunContext", Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: ToolPermission
    handler: Handler
    required_consents: frozenset[ConsentScope] = field(default_factory=frozenset)
    llm_callable: bool = False
    timeout_seconds: float = 8.0

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "llm_callable": self.llm_callable,
            "required_consents": sorted(c.value for c in self.required_consents),
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


@dataclass
class ToolResult:
    tool: str
    status: ToolCallStatus
    output: BaseModel | None = None
    error: str | None = None
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is ToolCallStatus.OK and self.output is not None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name} already registered")
        if spec.llm_callable and spec.permission is not ToolPermission.READ:
            raise ValueError("only read-only tools may be exposed to language models")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, *, llm_callable: bool | None = None) -> list[str]:
        return sorted(n for n, s in self._tools.items() if llm_callable is None or s.llm_callable == llm_callable)

    def describe(self) -> list[dict[str, Any]]:
        return [self._tools[n].describe() for n in sorted(self._tools)]

    async def invoke(self, name: str, arguments: dict[str, Any] | BaseModel, ctx: RunContext, *,
                     caller: Caller = "agent") -> ToolResult:
        started = time.perf_counter()
        spec = self._tools.get(name)
        raw_args = arguments.model_dump(mode="json") if isinstance(arguments, BaseModel) else arguments
        result = await self._run(spec, name, arguments, ctx, caller)
        result.latency_ms = (time.perf_counter() - started) * 1000
        metrics.TOOL_CALLS.labels(name if spec else "unknown", result.status.value).inc()
        if ctx.recorder is not None:
            try:
                await ctx.recorder.tool_call(
                    run_id=ctx.run_id, user_id=ctx.user_id, tool_name=name[:64],
                    arguments={"caller": caller, **(raw_args if isinstance(raw_args, dict) else {"raw": str(raw_args)[:200]})},
                    result=result.output.model_dump(mode="json") if result.output is not None else None,
                    status=result.status.value, error=result.error, latency_ms=result.latency_ms,
                )
            except Exception:
                log.exception("failed to record tool call")
        return result

    async def _run(self, spec: ToolSpec | None, name: str, arguments: dict[str, Any] | BaseModel, ctx: RunContext,
                   caller: Caller) -> ToolResult:
        if spec is None:
            return ToolResult(name, ToolCallStatus.DENIED, error="unknown tool")
        if caller == "llm" and not spec.llm_callable:
            return ToolResult(name, ToolCallStatus.DENIED, error="tool is not available to language models")
        try:
            args = arguments if isinstance(arguments, spec.input_model) else spec.input_model.model_validate(
                arguments.model_dump() if isinstance(arguments, BaseModel) else arguments)
        except ValidationError as exc:
            return ToolResult(name, ToolCallStatus.INVALID_INPUT, error=f"{exc.error_count()} validation error(s): "
                              + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()[:5]))
        missing = spec.required_consents - ctx.consents
        if missing:
            return ToolResult(name, ToolCallStatus.DENIED, error="consent required: " + ", ".join(sorted(m.value for m in missing)))
        try:
            async with ctx.session.begin_nested():
                output = await asyncio.wait_for(spec.handler(ctx, args), spec.timeout_seconds)
        except TicketError as exc:
            return ToolResult(name, ToolCallStatus.DENIED, error=f"authorization failed: {exc}")
        except ValidationFailedError as exc:
            return ToolResult(name, ToolCallStatus.INVALID_INPUT, error=exc.message)
        except (NotFoundError, ConflictError) as exc:
            return ToolResult(name, ToolCallStatus.DENIED if isinstance(exc, NotFoundError) else ToolCallStatus.ERROR,
                              error=exc.message)
        except MindGuardError as exc:
            return ToolResult(name, ToolCallStatus.ERROR, error=exc.message)
        except TimeoutError:
            return ToolResult(name, ToolCallStatus.ERROR, error="tool timed out")
        except Exception as exc:
            log.exception("tool %s crashed", name)
            return ToolResult(name, ToolCallStatus.ERROR, error=f"tool failed: {type(exc).__name__}")
        try:
            validated = spec.output_model.model_validate(output.model_dump() if isinstance(output, BaseModel) else output)
        except ValidationError as exc:
            return ToolResult(name, ToolCallStatus.INVALID_OUTPUT, error=f"{exc.error_count()} output validation error(s)")
        return ToolResult(name, ToolCallStatus.OK, output=validated)
