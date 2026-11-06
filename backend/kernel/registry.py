"""Capability-scoped tool registry.

Every tool returns a structured result, never raises into the loop, and declares which
capabilities it needs. The agent only ever receives the subset of the feed its
capabilities allow (see clamp.ClampedFeed), so "no future events" is enforced by the
registry and the clamp, not by a prompt instruction.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

ALL_CAPABILITIES = {
    "match_state",
    "past_events",
    "speech_memory",
    "predictions",
    "verify",
    "telemetry",
    "video_evidence",
    "ask_user",
    "permission",
    "surface",
}


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[[dict], Any]
    capabilities: tuple[str, ...] = ()
    args_model: Optional[type[BaseModel]] = None
    timeout_s: float = 8.0
    deterministic: bool = True
    read_only: bool = True

    def schema_hint(self) -> str:
        if self.args_model is None:
            return "{}"
        props = self.args_model.model_json_schema().get("properties", {})
        req = set(self.args_model.model_json_schema().get("required", []))
        return ", ".join(
            f"{k}:{v.get('type', 'any')}{'' if k in req else '?'}" for k, v in props.items()
        ) or "{}"


@dataclass
class ToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: str = ""
    ms: float = 0.0
    blocked: bool = False

    def preview(self, limit: int = 240) -> str:
        payload = self.data if self.ok else self.error
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        return text[:limit]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.call_log: list[dict[str, Any]] = []

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def tool(self, name: str, description: str, fn: Callable[[dict], Any],
             capabilities: tuple[str, ...] = (), args_model: Optional[type[BaseModel]] = None,
             deterministic: bool = True, read_only: bool = True) -> None:
        self.register(Tool(name, description, fn, capabilities, args_model,
                           deterministic=deterministic, read_only=read_only))

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self, allowed: Optional[set[str]] = None) -> str:
        lines = []
        for name in self.names():
            t = self._tools[name]
            if allowed is not None and name not in allowed:
                continue
            lines.append(f"- {name}({t.schema_hint()}) -> {t.description}"
                         f" [caps={','.join(t.capabilities) or 'none'}]")
        return "\n".join(lines)

    def call(self, name: str, args: dict[str, Any], allowed_tools: Optional[set[str]] = None,
             granted_caps: Optional[set[str]] = None) -> ToolResult:
        start = time.perf_counter()
        if allowed_tools is not None and name not in allowed_tools:
            return self._log(ToolResult(False, name, error=f"tool_not_granted:{name}",
                                         blocked=True, ms=self._ms(start)), name, args)
        t = self._tools.get(name)
        if t is None:
            return self._log(ToolResult(False, name, error=f"unknown_tool",
                                         ms=self._ms(start)), name, args)
        if granted_caps is not None:
            missing = [c for c in t.capabilities if c not in granted_caps]
            if missing:
                return self._log(ToolResult(False, name, blocked=True,
                                            error=f"capability_denied:{','.join(missing)}",
                                            ms=self._ms(start)), name, args)
        if t.args_model is not None:
            try:
                # keep the validated model: tool functions read attributes, not dict keys
                args_obj = t.args_model.model_validate(args or {})
            except ValidationError as exc:
                return self._log(ToolResult(False, name,
                                            error=f"bad_args:{exc.errors()[0].get('loc')}: "
                                                  f"{exc.errors()[0].get('msg')}",
                                            ms=self._ms(start)), name, args)
            try:
                data = t.fn(args_obj)
                res = ToolResult(True, name, data=data, ms=self._ms(start))
            except TimeoutError:
                res = ToolResult(False, name, error=f"timeout:{t.timeout_s}", ms=self._ms(start))
            except Exception as exc:
                res = ToolResult(False, name, error=f"tool_error:{type(exc).__name__}:{str(exc)[:180]}",
                                 ms=self._ms(start))
            return self._log(res, name, args)
        try:
            data = t.fn(args)
            res = ToolResult(True, name, data=data, ms=self._ms(start))
        except TimeoutError:
            res = ToolResult(False, name, error=f"timeout:{t.timeout_s}", ms=self._ms(start))
        except Exception as exc:
            res = ToolResult(False, name, error=f"tool_error:{type(exc).__name__}:{str(exc)[:180]}",
                             ms=self._ms(start))
        return self._log(res, name, args)

    @staticmethod
    def _ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000, 1)

    def _log(self, res: ToolResult, name: str, args: dict) -> ToolResult:
        self.call_log.append({"tool": name, "ok": res.ok, "ms": res.ms,
                              "error": res.error, "args": _safe(args)})
        return res


def _safe(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
        out[k] = s[:160]
    return out
