# kemo-graph MarkItDown 核心转换层

这是 kemo-graph 内部的轻量文档归一化模块，参考 Microsoft MarkItDown 的
Converter 调度思路重新实现，不复制完整上游项目。

本模块随 kemo-graph **2.0.0** 发布，包名为 `kemo-graph-markitdown`；版本元数据与项目 `version.yaml` 同步。这不是 Microsoft 上游 MarkItDown 的版本号。

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

支持本地普通文件：PDF、DOCX/DOCM、PPTX/PPTM、XLSX/XLSM/XLS、CSV、JSON、XML、YAML、TXT、
Markdown、本地 HTML，以及可选的 EML、EPUB、RTF、RST。

不处理 URL、网页抓取、视频、音频、图片 OCR、YouTube、Azure 云服务或浏览器内核。
这些内容应由上游智能体先处理为 Markdown 或本地文档。

每种格式的依赖都在对应 Converter 内延迟导入；新增格式只需要新增一个
`DocumentConverter` 子类并注册到 `default_converters()`。`supported_suffixes()` 从注册表
派生格式集合；实例注册的扩展能力可用 `converter.supported_suffixes()` 查询。

无扩展名文件使用 Magika 识别内容，识别后仍须匹配已注册的 Converter。
已有扩展名优先，不把任意二进制文件回退为文本，也不自动纠正错误扩展名。
产品入口允许无扩展名文件进入检测，不代表所有无扩展名文件均可转换。

### Office 与 `application/vnd.ms-*` 能力边界

| 文件 | 支持状态 | 读取方式及限制 |
|---|---|---|
| `.docx` | 支持 | Mammoth + markdownify 优先；缺少该链路或读取失败时可回退 python-docx |
| `.docm` | 支持 | 必须使用 Mammoth + markdownify；python-docx 拒绝宏启用的 main-part Content-Type，不回退 |
| `.pptx`、`.pptm` | 支持 | python-pptx 提取幻灯片文本和表格 |
| `.xlsx`、`.xlsm` | 支持 | openpyxl 读取工作表、文本和公式，不执行宏或重新计算公式 |
| `.xls` | 支持 | xlrd 读取旧二进制工作簿 |
| `.doc`、`.ppt` | 不支持 | 旧二进制 Word / PowerPoint 不在转换范围内 |
| `.dot`、`.dotx`、`.dotm` | 不支持 | Word 模板未注册 |
| `.pot`、`.potx`、`.potm`、`.pps`、`.ppsx`、`.ppsm`、`.ppa`、`.ppam` | 不支持 | PowerPoint 模板、放映和加载项未注册 |
| `.xlsb`、`.xlt`、`.xltx`、`.xltm`、`.xla`、`.xlam` | 不支持 | 二进制工作簿、模板和加载项未注册 |
| 其他 `application/vnd.ms-*`（包括 Visio、Project 等） | 不支持 | 不根据 MIME 家族推断可转换性 |

宏容器回归使用真实 OOXML 包，并把 `[Content_Types].xml` 的主文档类型设为
`macroEnabled.main+xml`，并非只给普通文档改名。DOCM 已用 Mammoth 1.12.2、
PPTM 已用 python-pptx 1.0.2 验证正文读取。只提取文档内容，不执行、索引或承诺保留 VBA。

### PDF 表格

PDF 读取已有文本层，保留分页标题，并去除重复页眉页脚。pdfplumber 能识别的
网格表格按页面位置输出为 Markdown 表格；表内字符不再重复作为正文输出，
表前、表旁及表后文字保留。单元格中的管道符转义，多行文本用 `<br>` 表示，
空单元格不输出 `None`。

少于两行、两列或有效单元格文字合计不足 8 字符的候选表格按普通文本处理。
无有效表格的页面沿用原来的文本输出路径。`PdfConverter.extract_tables` 默认为
`True`，可在类或实例上设为 `False`，使用纯文本提取。无边框复杂排版不保证被识别为
表格；不处理扫描件或 OCR。

### 依赖与错误

项目 `requirements.txt` 使用 `magika>=0.6.1,<2.0`、
`mammoth>=1.12.2,<2.0`、`pdfplumber>=0.11.9,<2.0`；
其他格式按各自 Converter 的依赖安装。独立安装本包不会自动安装全部格式依赖。

产品适配层负责把输入/输出超限映射为 HTTP 413，把不安全路径映射为 400，
不支持格式映射为 415，转换失败及缺少可选依赖映射为 422，并保留具体依赖名。
独立 Python/CLI 调用仍使用 `markitdown` 自身的异常，不依赖 kemo-graph 的 `core`。

原文件预览保留独立渲染器，以保留图片和 HTML 展示结构；Graph/RAG 只读取独立的
规范 Markdown 处理产物。转换层继续净化图片二进制、Data URI、脚本和样式。
