"""Temporary imports; removed in stage 10."""
from core.ingest.validation import _semantic_key, _required_text, _validate_string_list, _unique_texts, _load_json_string_list
from core.documents.errors import GraphExtractionError as _graph_extraction_error
from core.documents.paths import _resolve_external_dir, _safe_source_path, _hash_relative_path, _path_key
from core.storage.locks import get_knowledge_base_lock as _get_write_lock
from core.common.time import now_iso as _now_iso, elapsed_ms as _elapsed_ms
from core.common.jsonio import write_json_atomic as _write_json_atomic
from core.common.fs import validated_path as _validated_path
