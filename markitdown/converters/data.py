"""CSV、JSON、XML 和 YAML 等本地数据文档 Converter。"""

from __future__ import annotations

import csv
import json
import xml.dom.minidom
import xml.etree.ElementTree as ET
from io import StringIO
from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._utils import (
    finalize_markdown,
    markdown_table,
    read_text,
    title_from_path,
)
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError


class CsvConverter(DocumentConverter):
    extensions = frozenset({".csv", ".tsv"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        text = read_text(source)
        delimiter = "\t" if info.extension.casefold() == ".tsv" else None
        try:
            sample = text[:65536]
            dialect = None
            if delimiter is None:
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|" )
                except csv.Error:
                    delimiter = ","
            reader = (
                csv.reader(StringIO(text), dialect=dialect)
                if dialect
                else csv.reader(StringIO(text), delimiter=delimiter or ",")
            )
            rows = [list(row) for row in reader]
        except csv.Error as exc:
            raise DocumentConversionError(f"无法解析 CSV：{source}: {exc}") from exc
        content = finalize_markdown(markdown_table(rows), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class JsonConverter(DocumentConverter):
    extensions = frozenset({".json", ".jsonl", ".ndjson"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        text = read_text(source)
        suffix = info.extension.casefold()
        try:
            if suffix == ".json":
                rendered = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                language = "json"
            else:
                objects = [
                    json.loads(line) for line in text.splitlines() if line.strip()
                ]
                rendered = "\n".join(
                    json.dumps(item, ensure_ascii=False) for item in objects
                )
                language = "jsonl"
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DocumentConversionError(f"无法解析 JSON：{source}: {exc}") from exc
        content = finalize_markdown(f"```{language}\n{rendered.strip()}\n```", source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class XmlConverter(DocumentConverter):
    extensions = frozenset({".xml"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        text = read_text(source)
        try:
            from defusedxml import ElementTree as SafeET

            parsed = SafeET.fromstring(text)
            rendered = xml.dom.minidom.parseString(
                ET.tostring(parsed, encoding="utf-8")
            ).toprettyxml(indent="  ", encoding=None)
        except ImportError as exc:
            raise MissingOptionalDependencyError("defusedxml", "XML") from exc
        except Exception as exc:
            raise DocumentConversionError(f"无法解析 XML：{source}: {exc}") from exc
        content = finalize_markdown(f"```xml\n{rendered.strip()}\n```", source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class YamlConverter(DocumentConverter):
    extensions = frozenset({".yaml", ".yml"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        text = read_text(source)
        try:
            import yaml

            rendered = yaml.safe_dump(
                yaml.safe_load(text),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        except ImportError as exc:
            raise MissingOptionalDependencyError("PyYAML", "YAML") from exc
        except Exception as exc:
            raise DocumentConversionError(f"无法解析 YAML：{source}: {exc}") from exc
        content = finalize_markdown(f"```yaml\n{rendered.strip()}\n```", source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


__all__ = ["CsvConverter", "JsonConverter", "XmlConverter", "YamlConverter"]
