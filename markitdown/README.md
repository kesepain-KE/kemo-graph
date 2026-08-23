# kemo-graph MarkItDown 核心转换层

这是 kemo-graph 内部的轻量文档归一化模块，参考 Microsoft MarkItDown 的
Converter 调度思路重新实现，不复制完整上游项目。

## 入口

```python
from markitdown import MarkItDown

converter = MarkItDown()
result = converter.convert("document.pdf")
print(result.text_content)
```

```powershell
python -m markitdown document.pdf -o document.md
python convert.py document.pdf -o document.md
```

## 当前边界

支持本地普通文件：PDF、DOCX、PPTX、XLSX/XLS、CSV、JSON、XML、YAML、TXT、
Markdown、本地 HTML，以及可选的 EML、EPUB、RTF、RST。

不处理 URL、网页抓取、视频、音频、图片 OCR、YouTube、Azure 云服务或浏览器内核。
这些内容应由上游智能体先处理为 Markdown 或本地文档。

每种格式的依赖都在对应 Converter 内延迟导入；新增格式只需要新增一个
`DocumentConverter` 子类并注册到 `default_converters()`。
