"""Phase 2 Provider 与 Phase 3A RAG 引擎验收测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np

from core.chunker import (
    _pre_split_by_paragraphs,
    chunk,
    chunking_signature,
    document_chunks,
    estimate_token_count,
)
from core.config import AppConfig
from core.db import (
    connect_rag,
    connect_sources,
    initialize_databases,
    read_rag_meta,
)
from core.rag_engine import (
    FaissIndexManager,
    IndexIntegrityError,
    RAGEngine,
    RAGQueryError,
)
from provider import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    get_api_key,
    kemo_headers,
    request_json,
)
from provider.embedding import EmbeddingResult, _clear_capability_cache, embed
from provider.engine import chat, chat_with_tools
from provider.rerank import rerank


def _settings(dimensions: int = 3) -> AppConfig:
    return AppConfig(
        kemo={
            "base_url": "https://gateway.test",
            "api_key_env": "TEST_KEMO_API_KEY",
            "protocol_version": "1.0",
            "request_timeout": 123,
        },
        models={
            "llm": "test-chat",
            "embedding": "test-embedding",
            "embedding_dimensions": dimensions,
            "rerank": "test-rerank",
        },
    )


class ProviderErrorTests(unittest.TestCase):
    def test_explicit_api_key_has_priority_over_environment(self) -> None:
        settings = _settings()
        settings.kemo.api_key = "config-secret"
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "environment-secret"}, clear=True):
            self.assertEqual(get_api_key(settings.kemo), "config-secret")

    def test_missing_api_key_raises_configuration_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderConfigurationError):
                chat("system", "hello", settings=_settings())

    def test_authentication_error_is_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "denied"}, request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderAuthenticationError) as context:
                request_json(
                    "POST",
                    "https://example.test/v1",
                    provider="test",
                    client=client,
                )
        self.assertEqual(context.exception.status_code, 401)

    def test_timeout_error_is_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ProviderTimeoutError):
                request_json(
                    "POST",
                    "https://example.test/v1",
                    provider="test",
                    client=client,
                )


class ProviderFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_capability_cache()

    def test_kemo_headers_share_one_request_id(self) -> None:
        headers = kemo_headers(" secret ")

        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["X-Kemo-Protocol-Version"], "1.0")
        self.assertEqual(headers["X-Request-ID"], headers["Idempotency-Key"])

    def test_chat_builds_kemo_request(self) -> None:
        settings = _settings()
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "answer"}],
                }
            ],
        }
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.engine.request_json",
                return_value=response,
            ) as request:
                result = chat("rules", "question", model="override", settings=settings)

        self.assertEqual(result, "answer")
        self.assertEqual(
            request.call_args.args[1],
            "https://gateway.test/model/responses",
        )
        payload = request.call_args.kwargs["payload"]
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(payload["model"], "override")
        self.assertEqual(payload["protocol_version"], "1.0")
        self.assertEqual(payload["request_id"], headers["X-Request-ID"])
        self.assertEqual(payload["request_id"], headers["Idempotency-Key"])
        self.assertEqual(payload["attempt"], 1)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["system_prompt"], "rules")
        self.assertEqual(payload["generation"], {"parallel_tool_calls": False})
        self.assertEqual(payload["output"], {"modalities": ["text"]})
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["provider_options"], {})
        self.assertEqual(payload["metadata"], {"capability": "conversation"})
        self.assertEqual(payload["extensions"], {})
        self.assertEqual(len(payload["input"]), 1)
        self.assertEqual(payload["input"][0]["type"], "message")
        self.assertEqual(payload["input"][0]["role"], "user")
        self.assertEqual(
            payload["input"][0]["content"],
            [{"type": "text", "text": "question"}],
        )
        self.assertNotIn("messages", payload)
        self.assertEqual(request.call_args.kwargs["timeout"], 123)

    def test_chat_rejects_malformed_response(self) -> None:
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch("provider.engine.request_json", return_value={"output": []}):
                with self.assertRaises(ProviderResponseError):
                    chat("rules", "question", settings=_settings())

    def test_chat_with_tools_replays_history_and_isolates_tool_errors(self) -> None:
        responses = [
            {
                "request_id": "request-first",
                "status": "requires_action",
                "output": [
                    {
                        "type": "tool_call",
                        "id": "item-1",
                        "call_id": "call-ok",
                        "name": "search_entities",
                        "arguments": {"query": "graph"},
                    },
                    {
                        "type": "tool_call",
                        "id": "item-2",
                        "call_id": "call-fail",
                        "name": "broken_tool",
                        "arguments": {},
                    },
                ],
            },
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "finished"}],
                    }
                ],
            },
        ]

        def handler(name: str, arguments: dict) -> dict:
            if name == "broken_tool":
                raise RuntimeError("tool failed")
            return {"name": name, "arguments": arguments}

        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.engine.request_json",
                side_effect=responses,
            ) as request:
                result = chat_with_tools(
                    "rules",
                    "document",
                    [
                        {
                            "type": "function",
                            "name": "search_entities",
                            "description": "搜索实体",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                                "additionalProperties": False,
                            },
                            "strict": True,
                        }
                    ],
                    handler,
                    settings=_settings(),
                )

        self.assertEqual(result, "finished")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].kwargs["payload"]["generation"],
            {"parallel_tool_calls": False},
        )
        self.assertEqual(
            request.call_args_list[1].kwargs["payload"]["generation"],
            {"parallel_tool_calls": False},
        )
        second_payload = request.call_args_list[1].kwargs["payload"]
        replayed = second_payload["input"]
        self.assertEqual(len(replayed), 5)
        self.assertEqual(second_payload["parent_request_id"], "request-first")
        self.assertEqual(replayed[0]["role"], "user")
        self.assertEqual(replayed[1]["type"], "tool_call")
        self.assertEqual(replayed[2]["type"], "tool_call")
        self.assertEqual(replayed[3]["type"], "tool_result")
        self.assertEqual(replayed[3]["call_id"], "call-ok")
        self.assertIs(replayed[3]["is_error"], False)
        self.assertIn('"ok": true', replayed[3]["content"][0]["text"])
        self.assertEqual(replayed[4]["call_id"], "call-fail")
        self.assertIs(replayed[4]["is_error"], True)
        self.assertIn('"ok": false', replayed[4]["content"][0]["text"])
        self.assertIn("tool failed", replayed[4]["content"][0]["text"])

    def test_chat_with_tools_enforces_configured_iteration_limit(self) -> None:
        settings = _settings()
        settings.graph_tool_max_iterations = 1
        response = {
            "status": "requires_action",
            "output": [
                {
                    "type": "tool_call",
                    "id": "item-1",
                    "call_id": "call-1",
                    "name": "search_entities",
                    "arguments": {"query": "graph"},
                }
            ],
        }
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.engine.request_json", return_value=response
            ) as request:
                with self.assertRaises(ProviderResponseError) as context:
                    chat_with_tools(
                        "rules",
                        "document",
                        [],
                        lambda name, arguments: [],
                        settings=settings,
                    )

        self.assertIn("最大迭代次数 1", str(context.exception))
        self.assertEqual(request.call_count, 1)

    def test_chat_with_tools_retries_transient_gateway_failure(self) -> None:
        responses = [
            ProviderResponseError(
                "kemo API 返回 HTTP 502",
                provider="kemo",
                status_code=502,
            ),
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "recovered"}],
                    }
                ],
            },
        ]
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with (
                patch("provider.engine.request_json", side_effect=responses) as request,
                patch("provider.engine.sleep"),
            ):
                result = chat_with_tools(
                    "rules",
                    "document",
                    [],
                    lambda name, arguments: [],
                    settings=_settings(),
                )

        self.assertEqual(result, "recovered")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["payload"]["attempt"], 1)
        self.assertEqual(request.call_args_list[1].kwargs["payload"]["attempt"], 2)

    def test_failed_response_retry_metadata_is_preserved(self) -> None:
        response = {
            "status": "failed",
            "error": {
                "code": "PROVIDER_UNAVAILABLE",
                "message": "Internal server error",
                "retryable": True,
                "provider_status": 500,
            },
        }
        with self.assertRaises(ProviderResponseError) as context:
            from provider.engine import _extract_tool_calls

            _extract_tool_calls(response, "kemo")

        error = context.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.provider_code, "PROVIDER_UNAVAILABLE")
        self.assertEqual(error.provider_status, 500)
        self.assertEqual(error.status_code, 500)

    def test_chat_with_tools_rebases_repeated_gateway_item_ids(self) -> None:
        def action_response(request_id: str, call_id: str) -> dict:
            return {
                "request_id": request_id,
                "status": "requires_action",
                "output": [
                    {
                        "id": "rs_0",
                        "type": "reasoning",
                        "summary": "working",
                    },
                    {
                        "id": "tool_0",
                        "type": "tool_call",
                        "call_id": call_id,
                        "name": "finish",
                        "arguments": {},
                    },
                ],
            }

        responses = [
            action_response("request-1", "call-1"),
            action_response("request-2", "call-2"),
            {
                "status": "completed",
                "output": [
                    {
                        "id": "msg_0",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    }
                ],
            },
        ]
        schema = {
            "type": "function",
            "name": "finish",
            "description": "完成",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "strict": True,
        }

        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.engine.request_json",
                side_effect=responses,
            ) as request:
                result = chat_with_tools(
                    "rules",
                    "document",
                    [schema],
                    lambda name, arguments: {"finished": True},
                    settings=_settings(),
                )

        self.assertEqual(result, "done")
        final_input = request.call_args_list[2].kwargs["payload"]["input"]
        item_ids = [item["id"] for item in final_input]
        self.assertEqual(len(item_ids), len(set(item_ids)))
        self.assertNotIn("rs_0", item_ids)
        self.assertNotIn("tool_0", item_ids)

    def test_embedding_orders_and_validates_float32_vectors(self) -> None:
        settings = _settings()
        capabilities = {
            "model": "test-embedding",
            "task": "embedding",
            "embedding": {"max_batch_size": 64},
        }
        response = {
            "vector_space_id": "test-space@v1:3:normalized",
            "data": [
                {"id": "chunk-1", "index": 1, "vector": [0.0, 1.0, 0.0]},
                {"id": "chunk-0", "index": 0, "vector": [1.0, 0.0, 0.0]},
            ],
        }
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.embedding.request_json",
                side_effect=[capabilities, response],
            ) as request:
                result = embed(["first", "second"], settings=settings)

        self.assertIsInstance(result, EmbeddingResult)
        self.assertEqual(result.vector_space_id, "test-space@v1:3:normalized")
        self.assertEqual(result.vectors, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.assertEqual(np.asarray(result.vectors, dtype=np.float32).dtype, np.float32)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].args[1],
            "https://gateway.test/model/models/test-embedding/capabilities",
        )
        self.assertEqual(request.call_args_list[0].args[0], "GET")
        embedding_request = request.call_args_list[1]
        self.assertEqual(
            embedding_request.args[1],
            "https://gateway.test/model/embeddings",
        )
        payload = embedding_request.kwargs["payload"]
        self.assertEqual(payload["input_type"], "document")
        self.assertEqual(
            payload["inputs"],
            [
                {"id": "chunk-0", "text": "first"},
                {"id": "chunk-1", "text": "second"},
            ],
        )
        self.assertEqual(
            payload["request_id"],
            embedding_request.kwargs["headers"]["Idempotency-Key"],
        )

    def test_embedding_batches_according_to_gateway_capability(self) -> None:
        settings = _settings()
        settings.embedding_batch_size = 64
        texts = [f"text-{index}" for index in range(166)]

        def embedding_response(*args, **kwargs) -> dict:
            inputs = kwargs["payload"]["inputs"]
            return {
                "vector_space_id": "test-space@v1:3:normalized",
                "data": [
                    {
                        "id": item["id"],
                        "index": local_index,
                        "vector": [
                            float(int(item["id"].removeprefix("chunk-"))),
                            1.0,
                            0.0,
                        ],
                    }
                    for local_index, item in enumerate(inputs)
                ],
            }

        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.embedding._get_max_batch_size",
                return_value=64,
            ):
                with patch(
                    "provider.embedding.request_json",
                    side_effect=embedding_response,
                ) as request:
                    result = embed(texts, settings=settings)

        self.assertEqual(request.call_count, 3)
        self.assertEqual(
            [len(call.kwargs["payload"]["inputs"]) for call in request.call_args_list],
            [64, 64, 38],
        )
        self.assertEqual(len(result.vectors), 166)
        self.assertEqual(result.vectors[0], [0.0, 1.0, 0.0])
        self.assertEqual(result.vectors[65], [65.0, 1.0, 0.0])
        self.assertEqual(result.vectors[-1], [165.0, 1.0, 0.0])
        for call in request.call_args_list:
            self.assertEqual(
                call.kwargs["payload"]["request_id"],
                call.kwargs["headers"]["Idempotency-Key"],
            )

    def test_embedding_respects_local_safety_batch_size(self) -> None:
        settings = _settings()
        settings.embedding_batch_size = 8

        def embedding_response(*args, **kwargs) -> dict:
            inputs = kwargs["payload"]["inputs"]
            return {
                "vector_space_id": "space",
                "data": [
                    {
                        "id": item["id"],
                        "index": local_index,
                        "vector": [1.0, 0.0, 0.0],
                    }
                    for local_index, item in enumerate(inputs)
                ],
            }

        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.embedding._get_max_batch_size",
                return_value=64,
            ):
                with patch(
                    "provider.embedding.request_json",
                    side_effect=embedding_response,
                ) as request:
                    result = embed(
                        [f"text-{index}" for index in range(18)], settings=settings
                    )

        self.assertEqual(len(result.vectors), 18)
        self.assertEqual(
            [len(call.kwargs["payload"]["inputs"]) for call in request.call_args_list],
            [8, 8, 2],
        )

    def test_embedding_rejects_vector_space_change_between_batches(self) -> None:
        responses = [
            {
                "vector_space_id": "space-a",
                "data": [
                    {"id": "chunk-0", "index": 0, "vector": [1.0, 0.0, 0.0]},
                    {"id": "chunk-1", "index": 1, "vector": [0.0, 1.0, 0.0]},
                ],
            },
            {
                "vector_space_id": "space-b",
                "data": [{"id": "chunk-2", "index": 0, "vector": [0.0, 0.0, 1.0]}],
            },
        ]
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch(
                "provider.embedding._get_max_batch_size",
                return_value=2,
            ):
                with patch(
                    "provider.embedding.request_json",
                    side_effect=responses,
                ):
                    with self.assertRaises(ProviderResponseError) as context:
                        embed(["first", "second", "third"], settings=_settings())

        self.assertIn("vector_space_id 不一致", str(context.exception))

    def test_embedding_requires_vector_space_id(self) -> None:
        response = {"data": [{"id": "chunk-0", "index": 0, "vector": [1.0, 0.0, 0.0]}]}
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch("provider.embedding._get_max_batch_size", return_value=64):
                with patch("provider.embedding.request_json", return_value=response):
                    with self.assertRaises(ProviderResponseError) as context:
                        embed(["text"], settings=_settings())

        self.assertIn("vector_space_id", str(context.exception))

    def test_embedding_supports_query_input_type(self) -> None:
        response = {
            "vector_space_id": "space-query",
            "data": [
                {"id": "chunk-0", "index": 0, "vector": [1.0, 0.0, 0.0]}
            ],
        }
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            with patch("provider.embedding._get_max_batch_size", return_value=64):
                with patch(
                    "provider.embedding.request_json",
                    return_value=response,
                ) as request:
                    result = embed(
                        ["semantic query"],
                        settings=_settings(),
                        input_type="query",
                    )

        self.assertEqual(result.vector_space_id, "space-query")
        self.assertEqual(request.call_args.kwargs["payload"]["input_type"], "query")

    def test_embedding_rejects_nan_and_wrong_dimensions(self) -> None:
        settings = _settings()
        responses = [
            {
                "vector_space_id": "space",
                "data": [
                    {
                        "id": "chunk-0",
                        "index": 0,
                        "vector": [1.0, float("nan"), 0.0],
                    }
                ],
            },
            {
                "vector_space_id": "space",
                "data": [{"id": "chunk-0", "index": 0, "vector": [1.0, 0.0]}],
            },
        ]
        with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
            for response in responses:
                with self.subTest(response=response):
                    with patch(
                        "provider.embedding._get_max_batch_size", return_value=64
                    ):
                        with patch(
                            "provider.embedding.request_json", return_value=response
                        ):
                            with self.assertRaises(ProviderResponseError) as context:
                                embed(["bad"], settings=settings)
                    self.assertIn("第 0 个向量", str(context.exception))

    def test_rerank_calls_api_once_then_uses_cache(self) -> None:
        settings = _settings()
        response = {
            "results": [
                {"rank": 1, "document_id": "chunk-b", "relevance_score": 0.9},
                {"rank": 2, "document_id": "chunk-a", "relevance_score": 0.4},
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            cache_path = Path(temporary_dir) / "rerank_cache.txt"
            with patch.dict(os.environ, {"TEST_KEMO_API_KEY": "secret"}, clear=True):
                with patch(
                    "provider.rerank.request_json", return_value=response
                ) as request:
                    first = rerank(
                        "query",
                        ["doc-a", "doc-b"],
                        2,
                        settings=settings,
                        cache_path=cache_path,
                        document_ids=["chunk-a", "chunk-b"],
                    )
                    second = rerank(
                        "query",
                        ["doc-a", "doc-b"],
                        2,
                        settings=settings,
                        cache_path=cache_path,
                        document_ids=["chunk-a", "chunk-b"],
                    )

            self.assertEqual(first, [(1, 0.9), (0, 0.4)])
            self.assertEqual(second, first)
            self.assertEqual(request.call_count, 1)
            payload = request.call_args.kwargs["payload"]
            self.assertEqual(
                request.call_args.args[1],
                "https://gateway.test/model/rerank",
            )
            self.assertEqual(
                payload["documents"],
                [
                    {"id": "chunk-a", "text": "doc-a"},
                    {"id": "chunk-b", "text": "doc-b"},
                ],
            )
            self.assertEqual(
                payload["request_id"],
                request.call_args.kwargs["headers"]["Idempotency-Key"],
            )
            cache_lines = cache_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(cache_lines), 2)
            self.assertTrue(any("\tchunk-b\t" in line for line in cache_lines))


class ChunkerTests(unittest.TestCase):
    def test_chunk_size_and_overlap(self) -> None:
        self.assertEqual(
            chunk("one two three four five", chunk_size=3, chunk_overlap=1),
            ["one two three", "three four five"],
        )
        self.assertEqual(estimate_token_count("知识 graph"), 3)

    def test_empty_text_and_invalid_overlap(self) -> None:
        self.assertEqual(chunk("   ", chunk_size=3, chunk_overlap=1), [])
        with self.assertRaises(ValueError):
            chunk("text", chunk_size=3, chunk_overlap=3)

    def test_hierarchical_chunks_have_unique_spans_and_parent_links(self) -> None:
        settings = _settings()
        settings.chunking_mode = "hierarchical"
        settings.chunk_small_size = 128
        settings.chunk_size = 256
        settings.chunk_large_size = 512
        settings.chunk_overlap = 32
        text = " ".join(f"token_{index}" for index in range(700))

        chunks = document_chunks(text, settings=settings)

        self.assertEqual(
            {item.granularity for item in chunks},
            {"small", "medium", "large"},
        )
        self.assertEqual(
            len({(item.token_start, item.token_end) for item in chunks}),
            len(chunks),
        )
        self.assertTrue(
            all(
                item.parent_index is not None
                for item in chunks
                if item.granularity == "small"
            )
        )
        self.assertTrue(
            any(
                item.parent_index is not None
                for item in chunks
                if item.granularity == "medium"
            )
        )
        for item in chunks:
            if item.parent_index is None:
                continue
            parent = chunks[item.parent_index]
            child_center = (item.token_start + item.token_end - 1) / 2
            self.assertLess(item.chunk_index, len(chunks))
            self.assertLess(
                {"small": 0, "medium": 1, "large": 2}[item.granularity],
                {"small": 0, "medium": 1, "large": 2}[parent.granularity],
            )
            self.assertLessEqual(parent.token_start, child_center)
            self.assertLess(child_center, parent.token_end)

    def test_llm_chunks_use_verified_boundaries_without_rewriting_text(self) -> None:
        settings = _settings()
        settings.chunking_mode = "llm"
        text = "# 主题\n\n第一段事实。\n\n## 子主题\n\n第二段事实。"
        response = {
            "chunks": [
                {"start_block": 1, "end_block": 2},
                {"start_block": 3, "end_block": 4},
            ]
        }

        with patch("core.chunker.chat_structured", return_value=response) as request:
            chunks = document_chunks(text, settings=settings)

        self.assertEqual(
            [item.content for item in chunks],
            ["# 主题\n\n第一段事实。", "## 子主题\n\n第二段事实。"],
        )
        self.assertEqual([item.granularity for item in chunks], ["medium", "medium"])
        self.assertEqual(chunks[0].token_end, chunks[1].token_start)
        schema = request.call_args.args[2]
        self.assertIn("start_block", schema["properties"]["chunks"]["items"]["properties"])

    def test_llm_invalid_boundaries_fall_back_to_fixed_chunks(self) -> None:
        settings = _settings()
        settings.chunking_mode = "llm"
        settings.chunk_size = 128
        settings.chunk_overlap = 16
        text = "\n\n".join(
            " ".join(f"token_{paragraph}_{index}" for index in range(220))
            for paragraph in range(3)
        )
        invalid_response = {
            "chunks": [
                {"start_block": 1, "end_block": 1},
                {"start_block": 3, "end_block": 3},
            ]
        }

        with patch("core.chunker.chat_structured", return_value=invalid_response):
            chunks = document_chunks(text, settings=settings)

        expected = chunk(text, settings=settings)
        self.assertEqual([item.content for item in chunks], expected)

    def test_long_document_pre_split_is_lossless_and_bounded(self) -> None:
        text = "第一段内容。\n\n第二段比较长的内容。\n\n第三段内容。"
        parts = _pre_split_by_paragraphs(text, 18)

        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) <= 18 for part in parts))

    def test_llm_chunking_signature_tracks_llm_settings(self) -> None:
        first = _settings()
        first.chunking_mode = "llm"
        second = first.model_copy(deep=True)
        second.chunking_llm_max_input_chars = 9000

        self.assertNotEqual(chunking_signature(first), chunking_signature(second))


class FaissTests(unittest.TestCase):
    def test_create_add_load_search_delete_and_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            index_path = Path(temporary_dir) / "index.faiss"
            manager = FaissIndexManager(index_path, 3)
            manager.add(
                [1, 2, 3],
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.8, 0.2, 0.0]],
            )
            self.assertTrue(index_path.exists())
            self.assertEqual(manager.search([[1.0, 0.0, 0.0]], 2)[0][0][0], 1)

            loaded = FaissIndexManager(index_path, 3)
            self.assertEqual(loaded.ids, {1, 2, 3})
            self.assertEqual(loaded.delete([1]), 1)
            self.assertNotIn(1, loaded.ids)

            loaded.rebuild([9], [[0.0, 0.0, 1.0]])
            self.assertEqual(loaded.ids, {9})
            self.assertEqual(loaded.search([[0.0, 0.0, 1.0]], 1), [[(9, 1.0)]])


class RAGEngineTests(unittest.TestCase):
    def test_database_rebuild_and_end_to_end_query(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_source(paths, "source-1", "notes.md")
            _insert_chunks_and_vectors(paths, settings)

            def fake_embedder(texts: list[str]) -> EmbeddingResult:
                self.assertTrue(texts)
                return EmbeddingResult(
                    vectors=[[1.0, 0.0, 0.0] for _ in texts],
                    vector_space_id="test-space",
                )

            def fake_reranker(
                query: str, documents: list[str], top_n: int
            ) -> list[tuple[int, float]]:
                self.assertEqual(query, "graph query")
                graph_index = documents.index("graph document")
                weak_index = documents.index("vector document")
                return [(graph_index, 0.95), (weak_index, 0.4)][:top_n]

            engine = RAGEngine(
                temporary_dir,
                settings=settings,
                embedder=fake_embedder,
                reranker=fake_reranker,
            )
            result = engine.query("graph query", top_k=2, threshold=0.5)

            self.assertEqual(engine.index.ids, {1, 2})
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["chunk_id"], "chunk-2")
            self.assertEqual(
                result["results"][0]["source"],
                {"source_id": "source-1", "relative_path": "notes.md"},
            )
            self.assertEqual(read_rag_meta(paths, settings)["total_vectors"], 2)
            self.assertEqual(
                read_rag_meta(paths, settings)["vector_space_id"],
                "test-space",
            )

            with self.assertRaises(RAGQueryError):
                engine.query("graph query", top_k=0)

    def test_model_mismatch_requires_reembedding(self) -> None:
        original_settings = _settings()
        changed_settings = _settings()
        changed_settings.models.embedding = "different-model"
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, original_settings)
            _insert_source(paths, "source-1", "notes.md")
            _insert_chunks_and_vectors(paths, original_settings)

            with self.assertRaises(IndexIntegrityError):
                RAGEngine(
                    temporary_dir,
                    settings=changed_settings,
                    embedder=lambda texts: EmbeddingResult([], "test-space"),
                    reranker=lambda query, documents, top_n: [],
                )

    def test_direct_keyword_match_survives_low_rerank_score(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_source(paths, "source-1", "notes.md")
            _insert_chunks_and_vectors(paths, settings)
            engine = RAGEngine(
                temporary_dir,
                settings=settings,
                embedder=lambda texts: EmbeddingResult(
                    [[1.0, 0.0, 0.0] for _ in texts],
                    "test-space",
                ),
                reranker=lambda query, documents, top_n: [
                    (documents.index("graph document"), 0.04),
                    (documents.index("vector document"), 0.02),
                ][:top_n],
            )

            result = engine.query("graph", top_k=2, threshold=0.6)

            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["chunk_id"], "chunk-2")
            self.assertEqual(result["results"][0]["score"], 0.75)

    def test_hierarchical_results_deduplicate_family_and_include_context(self) -> None:
        settings = _settings()
        settings.chunking_mode = "hierarchical"
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_source(paths, "source-1", "notes.md")
            connection = connect_rag(paths)
            try:
                chunks = [
                    ("large", "large context", "large", None),
                    ("medium", "medium needle context", "medium", "large"),
                    ("small", "needle", "small", "medium"),
                ]
                for vector_id, (chunk_id, content, granularity, parent) in enumerate(
                    chunks,
                    start=1,
                ):
                    connection.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, source_id, content, chunk_index, token_count,
                            granularity, parent_chunk_id
                        ) VALUES (?, 'source-1', ?, ?, 2, ?, ?)
                        """,
                        (chunk_id, content, vector_id - 1, granularity, parent),
                    )
                    connection.execute(
                        """
                        INSERT INTO embeddings (
                            vector_id, chunk_id, source_id, vector_blob,
                            dimensions, model_name, vector_space_id
                        ) VALUES (?, ?, 'source-1', ?, 3, ?, 'test-space')
                        """,
                        (
                            vector_id,
                            chunk_id,
                            np.asarray([1.0, 0.0, 0.0], dtype=np.float32).tobytes(),
                            settings.models.embedding,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            rerank_documents: list[str] = []

            def reranker(
                query: str,
                documents: list[str],
                top_n: int,
            ) -> list[tuple[int, float]]:
                rerank_documents.extend(documents)
                scores = {
                    "needle": 0.95,
                    "medium needle context": 0.9,
                    "large context": 0.8,
                }
                return sorted(
                    (
                        (index, scores[document])
                        for index, document in enumerate(documents)
                    ),
                    key=lambda item: item[1],
                    reverse=True,
                )[:top_n]

            engine = RAGEngine(
                temporary_dir,
                settings=settings,
                embedder=lambda texts: EmbeddingResult(
                    [[1.0, 0.0, 0.0] for _ in texts],
                    "test-space",
                ),
                reranker=reranker,
            )
            result = engine.query("needle", top_k=3, threshold=0.6)

            self.assertEqual(len(result["results"]), 1)
            item = result["results"][0]
            self.assertEqual(rerank_documents, ["medium needle context"])
            self.assertEqual(item["chunk_id"], "small")
            self.assertEqual(item["granularity"], "small")
            self.assertEqual(item["parent_chunk_id"], "medium")
            self.assertEqual(item["context"]["chunk_id"], "medium")


def _insert_source(paths, source_id: str, relative_path: str) -> None:
    connection = connect_sources(paths)
    try:
        connection.execute(
            """
            INSERT INTO sources (
                source_id, original_path, relative_path, path_hash, content_hash
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, "C:/docs/notes.md", relative_path, "path-hash", "content-hash"),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_chunks_and_vectors(paths, settings: AppConfig) -> None:
    rows = [
        ("chunk-1", "vector document", [1.0, 0.0, 0.0]),
        ("chunk-2", "graph document", [0.8, 0.2, 0.0]),
    ]
    connection = connect_rag(paths)
    try:
        for vector_id, (chunk_id, content, vector) in enumerate(rows, start=1):
            connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, source_id, content, chunk_index, token_count
                ) VALUES (?, 'source-1', ?, ?, ?)
                """,
                (chunk_id, content, vector_id - 1, 2),
            )
            connection.execute(
                """
                INSERT INTO embeddings (
                    vector_id, chunk_id, source_id, vector_blob,
                    dimensions, model_name, vector_space_id
                ) VALUES (?, ?, 'source-1', ?, ?, ?, ?)
                """,
                (
                    vector_id,
                    chunk_id,
                    np.asarray(vector, dtype=np.float32).tobytes(),
                    settings.models.embedding_dimensions,
                    settings.models.embedding,
                    "test-space",
                ),
            )
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
