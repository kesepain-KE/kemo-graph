from __future__ import annotations

from typing import Any
from provider.embedding import EmbeddingResult
from core.rag.errors import IndexIntegrityError

def _coerce_embedding_result(
    value: EmbeddingResult,
) -> EmbeddingResult:
    """验证向量提供方返回的向量空间身份。"""

    if isinstance(value, EmbeddingResult):
        vector_space_id = _validate_vector_space_id(value.vector_space_id)
        return EmbeddingResult(
            vectors=value.vectors,
            vector_space_id=vector_space_id,
        )
    raise TypeError("Embedding 返回值必须是 EmbeddingResult")

def _validate_vector_space_id(value: Any, vector_id: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        prefix = f"vector_id={vector_id} 的" if vector_id is not None else ""
        raise IndexIntegrityError(f"{prefix}vector_space_id 为空")
    return value.strip()

from typing import Any
from provider.embedding import EmbeddingResult, embed
from core.rag.errors import IndexIntegrityError

def _embed_texts(self, texts: list[str], *, input_type: str) -> EmbeddingResult:
    if self._embedder is None:
        value = embed(
            texts,
            settings=self.settings,
            input_type=input_type,
        )
    else:
        value = self._embedder(texts)
    return _coerce_embedding_result(value)
