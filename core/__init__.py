"""Public knowledge APIs, resolved on access without initializing a runtime.

Lazy exports keep provider-first imports independent of the service layer.
"""
from importlib import import_module

_EXPORT_MODULES = {'AppConfig': 'core.settings',
 'ConfigLoadError': 'core.settings',
 'load_config': 'core.settings',
 'DatabasePaths': 'core.storage',
 'initialize_databases': 'core.storage',
 'get_database_paths': 'core.storage',
 'KnowledgeBaseService': 'core.service',
 'KnowledgeBaseNotInitializedError': 'core.service',
 'KnowledgeBaseProcessingError': 'core.service',
 'DocumentImportError': 'core.service',
 'UnsupportedDocumentFormatError': 'core.service',
 'DocumentImportPathError': 'core.service',
 'DocumentTooLargeError': 'core.service',
 'DocumentIngestError': 'core.service',
 'DocumentContentConflictError': 'core.service',
 'DocumentImportConflictError': 'core.service',
 'GraphEngine': 'core.graph',
 'RAGEngine': 'core.rag',
 'HybridEngine': 'core.retrieval',
 'Ingestor': 'core.ingest',
 'SearchCache': 'core.retrieval',
 'MaintenanceJobManager': 'core.maintenance',
 'IngestError': 'core.ingest',
 'DocumentNotFoundError': 'core.ingest'}

__all__ = ['AppConfig',
 'ConfigLoadError',
 'load_config',
 'DatabasePaths',
 'initialize_databases',
 'get_database_paths',
 'KnowledgeBaseService',
 'KnowledgeBaseNotInitializedError',
 'KnowledgeBaseProcessingError',
 'DocumentImportError',
 'UnsupportedDocumentFormatError',
 'DocumentImportPathError',
 'DocumentTooLargeError',
 'DocumentIngestError',
 'DocumentContentConflictError',
 'DocumentImportConflictError',
 'GraphEngine',
 'RAGEngine',
 'HybridEngine',
 'Ingestor',
 'SearchCache',
 'MaintenanceJobManager',
 'IngestError',
 'DocumentNotFoundError']

def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
