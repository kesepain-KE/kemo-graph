"""回归：文档导入异常基类必须能从 core.knowledge_support 解析。

背景（2026-09-08 `refactor(core): 知识库与 RAG 引擎继续领域化拆分` 遗漏）：

`knowledge_support.py` 在领域化拆分时从 `.knowledge_models` 导入了
`DocumentImportConflictError` / `DocumentImportPathError` / `DocumentTooLargeError`，
唯独漏掉了它们的基类 `DocumentImportError`，但文件里仍有两处使用它：

1. `_stable_import_snapshot()` 的 `except (DocumentImportError, OSError, ValueError)`
   —— 快照文件描述符的清理守卫。异常一旦进入该 try，Python 求值 except 元组时
   立即 NameError，**顶掉原本的业务异常，同时跳过描述符清理**。
2. `_source_record_for_relative_path()` 的「导入后未注册来源」分支。

由于正常导入路径不经过这两处，既有测试全部通过而缺陷长期潜伏。
本文件锁定「异常路径不得二次退化为 NameError」这一契约。
"""

from __future__ import annotations

import pytest

from core import knowledge_support as ks


def test_document_import_error_is_resolvable() -> None:
    """模块命名空间必须能解析 DocumentImportError（防止再次漏导入）。"""

    assert hasattr(ks, "DocumentImportError")
    assert ks.DocumentImportError is not None


def test_document_import_error_is_base_class() -> None:
    """其余导入异常都派生自该基类，因此 except 子句依赖它才能成立。"""

    assert issubclass(ks.DocumentTooLargeError, ks.DocumentImportError)
    assert issubclass(ks.DocumentImportConflictError, ks.DocumentImportError)
    assert issubclass(ks.DocumentImportPathError, ks.DocumentImportError)


def test_unregistered_source_raises_document_import_error(monkeypatch) -> None:
    """导入后 sources 表查不到记录时，抛 DocumentImportError 而非 NameError。"""

    class _Cursor:
        def fetchone(self):
            return None

    class _Connection:
        def execute(self, *args, **kwargs):
            return _Cursor()

        def close(self) -> None:
            pass

    monkeypatch.setattr(ks, "connect_sources", lambda paths: _Connection())

    with pytest.raises(ks.DocumentImportError) as excinfo:
        ks._source_record_for_relative_path(object(), "not-registered.md")

    assert not isinstance(excinfo.value, NameError)
    assert "not-registered.md" in str(excinfo.value)
