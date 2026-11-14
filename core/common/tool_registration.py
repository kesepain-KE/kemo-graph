from __future__ import annotations
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any
ToolFunction = Callable[..., Any]
ToolRegistration = dict[str, Any]

def _register(
    schemas: list[dict[str, Any]],
    functions: dict[str, ToolFunction],
    *,
    injected_connection: sqlite3.Connection | None = None,
) -> list[ToolRegistration]:
    registrations: list[ToolRegistration] = []
    for schema in schemas:
        name = str(schema["name"])
        function = functions[name]
        registrations.append(
            {
                "name": name,
                "schema": schema,
                "handler": _safe_handler(function, injected_connection),
            }
        )
    return registrations

def _safe_handler(
    function: ToolFunction,
    connection: sqlite3.Connection | None,
) -> Callable[..., dict[str, Any]]:
    def handler(
        arguments: Mapping[str, Any] | None = None,
        **keyword_arguments: Any,
    ) -> dict[str, Any]:
        try:
            if arguments is not None and not isinstance(arguments, Mapping):
                raise TypeError("工具参数必须是对象")
            parameters = dict(arguments or {})
            parameters.update(keyword_arguments)
            result = (
                function(connection, **parameters)
                if connection is not None
                else function(**parameters)
            )
            return {"ok": True, "data": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return handler
