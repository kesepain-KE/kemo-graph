"""基于 kemo 1.0 协议的 LLM 与工具调用入口。"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from core.config import AppConfig, load_config

from . import (
    ProviderResponseError,
    build_endpoint_url,
    get_api_key,
    kemo_headers,
    request_json,
)


ToolHandler = Callable[[str, dict[str, Any]], Any]
_CAPABILITY_CACHE_TTL_SECONDS = 300.0
_CAPABILITY_CACHE: dict[tuple[str, str], tuple[bool, float]] = {}
_CAPABILITY_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class _ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class _ToolExecution:
    content: str
    is_error: bool


def chat(
    system: str,
    user: str,
    model: str | None = None,
    *,
    settings: AppConfig | None = None,
) -> str:
    """通过 kemo ``/model/responses`` 返回单轮文本响应。"""

    _validate_messages(system, user)
    active_settings = settings or load_config()
    input_items = [_user_message_item(user)]
    response = _send_response_request(
        system_prompt=system,
        input_items=input_items,
        settings=active_settings,
        model=model,
    )
    tool_calls = _extract_tool_calls(response, "kemo")
    if tool_calls:
        raise ProviderResponseError(
            "kemo LLM 在无工具请求中返回了 tool_calls",
            provider="kemo",
        )
    return _extract_text(response, "kemo")


def chat_with_tools(
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    tool_handler: ToolHandler,
    *,
    settings: AppConfig | None = None,
    max_iterations: int | None = None,
) -> str:
    """循环调用 kemo LLM，执行工具并回传结果，直至得到纯文本。"""

    _validate_messages(system, user)
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise TypeError("tools 必须是工具 Schema 对象列表")
    if not callable(tool_handler):
        raise TypeError("tool_handler 必须可调用")

    active_settings = settings or load_config()
    iteration_limit = (
        active_settings.graph_tool_max_iterations
        if max_iterations is None
        else max_iterations
    )
    if not isinstance(iteration_limit, int) or isinstance(iteration_limit, bool):
        raise TypeError("max_iterations 必须是整数")
    if iteration_limit < 1:
        raise ValueError("max_iterations 必须大于等于 1")

    provider = "kemo"
    input_items: list[dict[str, Any]] = [_user_message_item(user)]
    parent_request_id: str | None = None
    for _ in range(iteration_limit):
        response = _send_response_request(
            system_prompt=system,
            input_items=input_items,
            settings=active_settings,
            tools=tools,
            parent_request_id=parent_request_id,
        )
        tool_calls = _extract_tool_calls(response, provider)
        if not tool_calls:
            return _extract_text(response, provider)

        input_items.extend(_response_input_items(response, provider))
        for call in tool_calls:
            execution = _execute_tool(call, tool_handler)
            input_items.append(_tool_result_item(call, execution))
        parent_request_id = _response_request_id(response)

    raise ProviderResponseError(
        f"{provider} 工具调用达到最大迭代次数 {iteration_limit}",
        provider=provider,
    )


def chat_structured(
    system: str,
    user: str,
    schema: dict[str, Any],
    model: str | None = None,
    *,
    settings: AppConfig | None = None,
    tool_name: str = "submit_structured_output",
) -> dict[str, Any]:
    """用 Kemo 内部结构化输出工具执行一次请求并返回参数对象。

    该工具只承载最终结果，不执行工具、不追加 tool_result，也不产生第二轮模型请求。
    """

    _validate_messages(system, user)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise TypeError("schema 必须是根类型为 object 的 JSON Schema")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("tool_name 不能为空")
    active_settings = settings or load_config()
    tool = {
        "type": "function",
        "name": tool_name.strip(),
        "description": "提交符合输出 Schema 的最终结构化结果。",
        "parameters": deepcopy(schema),
        "strict": True,
        "permission": "write",
        "metadata": {"purpose": "structured_output"},
        "extensions": {},
    }
    response = _send_response_request(
        system_prompt=system,
        input_items=[_user_message_item(user)],
        settings=active_settings,
        model=model,
        tools=[tool],
    )
    calls = _extract_tool_calls(response, "kemo")
    if len(calls) != 1:
        raise ProviderResponseError(
            "kemo 结构化输出必须且只能返回一个工具调用",
            provider="kemo",
        )
    call = calls[0]
    if call.name != tool_name:
        raise ProviderResponseError(
            f"kemo 结构化输出工具名错误：期望 {tool_name}，实际 {call.name}",
            provider="kemo",
        )
    if not call.arguments:
        raise ProviderResponseError(
            "kemo 结构化输出参数不能为空",
            provider="kemo",
        )
    return call.arguments


def supports_structured_output(
    model: str | None = None,
    *,
    settings: AppConfig | None = None,
) -> bool:
    """读取 Kemo 模型能力声明；短暂缓存结果以避免每篇文档重复发现。"""

    active_settings = settings or load_config()
    selected_model = model or active_settings.models.llm
    cache_key = (active_settings.kemo.base_url.rstrip("/"), selected_model)
    now = monotonic()
    with _CAPABILITY_CACHE_LOCK:
        cached = _CAPABILITY_CACHE.get(cache_key)
        if cached is not None and cached[1] > now:
            return cached[0]

    api_key = get_api_key(active_settings.kemo)
    headers = kemo_headers(api_key)
    response = request_json(
        "GET",
        build_endpoint_url(
            active_settings.kemo.base_url,
            f"model/models/{quote(selected_model, safe='')}/capabilities",
        ),
        provider="kemo",
        headers=headers,
        timeout=active_settings.kemo.request_timeout,
    )
    if not isinstance(response, dict):
        raise ProviderResponseError(
            "kemo 模型能力响应必须是对象",
            provider="kemo",
        )
    supported = response.get("structured_output") is True
    tools = response.get("tools")
    if isinstance(tools, dict):
        supported = supported and tools.get("function_calling") is True
    with _CAPABILITY_CACHE_LOCK:
        _CAPABILITY_CACHE[cache_key] = (
            supported,
            monotonic() + _CAPABILITY_CACHE_TTL_SECONDS,
        )
    return supported


def _clear_capability_cache() -> None:
    """仅供测试和运行时配置刷新。"""

    with _CAPABILITY_CACHE_LOCK:
        _CAPABILITY_CACHE.clear()


def _send_response_request(
    *,
    system_prompt: str,
    input_items: list[dict[str, Any]],
    settings: AppConfig,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    parent_request_id: str | None = None,
) -> Any:
    api_key = get_api_key(settings.kemo)
    headers = kemo_headers(api_key)
    payload: dict[str, Any] = {
        "protocol_version": settings.kemo.protocol_version,
        "request_id": headers["X-Request-ID"],
        "attempt": 1,
        "model": model or settings.models.llm,
        "stream": False,
        "system_prompt": system_prompt,
        "reasoning": None,
        "generation": {"parallel_tool_calls": True},
        "output": {"modalities": ["text"]},
        "tools": tools or [],
        "input": input_items,
        "provider_options": {},
        "metadata": {"capability": "conversation"},
        "extensions": {},
    }
    if parent_request_id:
        payload["parent_request_id"] = parent_request_id
    return request_json(
        "POST",
        build_endpoint_url(settings.kemo.base_url, "model/responses"),
        provider="kemo",
        headers=headers,
        payload=payload,
        timeout=settings.kemo.request_timeout,
    )


def _validate_messages(system: str, user: str) -> None:
    if not isinstance(system, str) or not isinstance(user, str):
        raise TypeError("system 和 user 必须是字符串")
    if not user.strip():
        raise ValueError("user 消息不能为空")


def _user_message_item(user: str) -> dict[str, Any]:
    return {
        "id": f"user-{uuid4().hex}",
        "type": "message",
        "role": "user",
        "status": "completed",
        "content": [{"type": "text", "text": user}],
        "metadata": {},
        "extensions": {},
    }


def _tool_result_item(
    call: _ToolCall,
    execution: _ToolExecution,
) -> dict[str, Any]:
    return {
        "id": f"result-{uuid4().hex}",
        "type": "tool_result",
        "status": "completed",
        "call_id": call.call_id,
        "name": call.name,
        "is_error": execution.is_error,
        "content": [{"type": "text", "text": execution.content}],
        "metadata": {},
        "extensions": {},
    }


def _response_input_items(response: Any, provider: str) -> list[dict[str, Any]]:
    if not isinstance(response, dict) or not isinstance(response.get("output"), list):
        raise ProviderResponseError(
            f"{provider} LLM 响应缺少 output 数组",
            provider=provider,
        )
    items: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    for item in response["output"]:
        if not isinstance(item, dict):
            raise ProviderResponseError(
                f"{provider} LLM output 项必须是对象",
                provider=provider,
            )
        copied = deepcopy(item)
        old_id = copied.get("id")
        if not isinstance(old_id, str) or not old_id.strip():
            raise ProviderResponseError(
                f"{provider} LLM output 项缺少 id",
                provider=provider,
            )
        new_id = f"history-{uuid4().hex}"
        id_map[old_id] = new_id
        copied["id"] = new_id
        items.append(copied)
    for item in items:
        _remap_reference_targets(item, id_map)
    return items


def _remap_reference_targets(value: Any, id_map: dict[str, str]) -> None:
    if isinstance(value, dict):
        target_id = value.get("target_id")
        if isinstance(target_id, str) and target_id in id_map:
            value["target_id"] = id_map[target_id]
        for nested in value.values():
            _remap_reference_targets(nested, id_map)
    elif isinstance(value, list):
        for nested in value:
            _remap_reference_targets(nested, id_map)


def _response_request_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    request_id = response.get("request_id")
    return request_id if isinstance(request_id, str) and request_id.strip() else None


def _extract_tool_calls(response: Any, provider: str) -> list[_ToolCall]:
    _raise_for_failed_response(response, provider)
    raw_calls: Any = None
    if isinstance(response, dict):
        if "tool_calls" in response:
            raw_calls = response.get("tool_calls")
        elif isinstance(response.get("message"), dict):
            raw_calls = response["message"].get("tool_calls")
        elif isinstance(response.get("output"), list):
            output_calls = [
                item
                for item in response["output"]
                if isinstance(item, dict) and item.get("type") == "tool_call"
            ]
            if output_calls:
                raw_calls = output_calls
            else:
                for item in response["output"]:
                    if isinstance(item, dict) and "tool_calls" in item:
                        raw_calls = item.get("tool_calls")
                        break
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise ProviderResponseError(
            f"{provider} LLM 响应中的 tool_calls 必须是数组",
            provider=provider,
        )
    return [_parse_tool_call(item, provider) for item in raw_calls]


def _parse_tool_call(item: Any, provider: str) -> _ToolCall:
    if not isinstance(item, dict):
        raise ProviderResponseError(
            f"{provider} LLM 工具调用项必须是对象",
            provider=provider,
        )
    function = item.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        arguments = function.get("arguments", {})
    else:
        name = item.get("name")
        arguments = item.get("arguments", {})
    call_id = item.get("call_id", item.get("id"))
    if not isinstance(call_id, str) or not call_id.strip():
        raise ProviderResponseError(
            f"{provider} LLM 工具调用缺少 call_id",
            provider=provider,
        )
    if not isinstance(name, str) or not name.strip():
        raise ProviderResponseError(
            f"{provider} LLM 工具调用缺少 name",
            provider=provider,
        )
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(
                f"{provider} LLM 工具参数不是合法 JSON：{name}",
                provider=provider,
            ) from exc
    if not isinstance(arguments, dict):
        raise ProviderResponseError(
            f"{provider} LLM 工具参数必须是对象：{name}",
            provider=provider,
        )
    return _ToolCall(call_id=call_id, name=name, arguments=arguments)


def _extract_text(response: Any, provider: str) -> str:
    _raise_for_failed_response(response, provider)
    content = _extract_optional_text(response)
    if not content.strip():
        raise ProviderResponseError(
            f"{provider} LLM 返回了空文本",
            provider=provider,
        )
    return content


def _extract_optional_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    candidates: list[Any] = [response.get("output_text"), response.get("content")]
    message = response.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("content"))
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if (
                not isinstance(item, dict)
                or item.get("type") != "message"
                or item.get("role") != "assistant"
            ):
                continue
            candidates.append(item.get("content"))
    parts: list[str] = []
    for candidate in candidates:
        parts.extend(_content_text_parts(candidate))
    return "\n".join(part for part in parts if part).strip()


def _content_text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {
            "text",
            "output_text",
        }:
            text = block.get("text", block.get("content"))
            if isinstance(text, str):
                parts.append(text)
    return parts


def _raise_for_failed_response(response: Any, provider: str) -> None:
    if not isinstance(response, dict):
        raise ProviderResponseError(
            f"{provider} LLM 响应必须是对象",
            provider=provider,
        )
    status = response.get("status")
    if status not in {"failed", "incomplete", "cancelled"} and not response.get(
        "error"
    ):
        return
    error = response.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    suffix = f"：{message}" if isinstance(message, str) and message else ""
    raise ProviderResponseError(
        f"{provider} LLM 请求未完成（{status or 'unknown'}）{suffix}",
        provider=provider,
    )


def _execute_tool(call: _ToolCall, tool_handler: ToolHandler) -> _ToolExecution:
    try:
        result: Any = {
            "ok": True,
            "data": tool_handler(call.name, call.arguments),
            "error": None,
        }
        return _ToolExecution(
            content=json.dumps(result, ensure_ascii=False, default=str),
            is_error=False,
        )
    except Exception as exc:
        return _ToolExecution(
            content=json.dumps(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                },
                ensure_ascii=False,
            ),
            is_error=True,
        )


__all__ = ["ToolHandler", "chat", "chat_with_tools"]
