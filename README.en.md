# kemo-graph

<p align="center">
  <img src="kemo-graph-logo.png" alt="kemo-graph logo" width="200">
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

<p align="center">
  <strong>Local knowledge graph and retrieval infrastructure for the Kemo ecosystem.</strong>
</p>

<p align="center">
  Turn multi-format materials into a traceable knowledge graph and vector index,<br>
  so agents can not only find the source text, but also understand concepts, relations, provenance and context.
</p>

<p align="center">
  <a href="version.json"><img src="https://img.shields.io/badge/version-1.5.1-00a98f" alt="version 1.5.1"></a>
  <a href="https://github.com/kesepain-KE/kemo-graph"><img src="https://img.shields.io/badge/status-early%20development-5966d9" alt="status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license"></a>
  <a href="api.md"><img src="https://img.shields.io/badge/API-agent%20integration-0ea5e9" alt="API"></a>
</p>

---

## What if agents didn't have to guess from a pile of files

An agent that serves you over the long term will eventually run into the same problem.

Materials keep growing: project documents, course notes, design drafts, saved web pages, PDFs, Word files, spreadsheets and scattered records — all carefully kept. Yet when the agent actually needs to answer a question, it often ends up retrieving a few similar passages and guessing the context around them.

It may find "what was mentioned", but not necessarily: which concepts this one relates to; which documents support a given relation; which source a search result came from; or which knowledge is still trustworthy after files are updated or deleted.

**kemo-graph aims to be the layer in the Kemo ecosystem that handles exactly this.**

It ties raw materials, converted Markdown, graph nodes, relation evidence and text vectors into one maintainable chain. Agents no longer just "search for answers" in files — they can follow the knowledge structure to find relations, then return to the original text to confirm the evidence.

It is not another agent that chats. It is the knowledge layer that lets agents use, understand and maintain materials over the long term.

---

## What it does

| Scenario | Capability |
|---|---|
| Project knowledge accumulation | Connect design documents, notes and decision records into a queryable concept network |
| Agent knowledge collaboration | Let kemo-agent fetch graph relations and source evidence through the API on demand |
| Multi-format ingestion | Convert local PDF, Word, PowerPoint, Excel, email, text, tabular and structured-data files into Markdown |
| Source tracing | Every graph or retrieval hit can go back to its original document — no conclusion without evidence |
| Multiple retrieval modes | Graph, vector, hybrid, Q&A and global-topic search for different kinds of questions |
| Tunable extraction and robust recall | Fine/standard/coarse graph extraction profiles, query expansion, multi-query FAISS fusion and exact-term fallback for both semantic and short-keyword hits |
| Incremental maintenance | Only affected data is updated when files change, instead of rebuilding the whole knowledge base |
| Project document management | Project-based uploads, Markdown preview/editing, renaming and individual or batch moves; available in both the default knowledge base and portable Stores |
| Visible processing | Dedicated build status, real query-stage progress and terminal/query/internal log tabs |
| Lower read overhead | Bounded memory caching for unchanged status, logs and knowledge-state fingerprints |
| Safe deletion | Shared sources are checked before deletion to avoid harming knowledge supported by other documents |
| Standalone deployment | Local Web, CLI and HTTP API, or as a knowledge backend for the wider Kemo ecosystem |

---

## Quick start

### Requirements

- Python 3.10+
- Node.js 18+ (needed to build the web frontend)
- Git
- An accessible Kemo gateway (kemo-adapter-api) with registered LLM, Embedding and Rerank models

### Get and run

Windows PowerShell example:

```powershell
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
```

At minimum, configure your gateway key in `.env`:

```dotenv
KEMO_API_KEY=your-kemo-gateway-key
```

Graph extraction defaults to `large` (coarse) and can be adjusted in the Web “System settings” panel or `config/config.json` to `small` (fine), `medium` (standard), or `large` (coarse). Use `graph_extract_chunk_size` to tune the baseline section size. Coarse mode also applies per-section entity/relation budgets and puts details into summaries instead of creating graph fragments. Retrieval keeps the original query, adds bounded synonym/concept expansions, fuses multi-query candidates, and uses an exact-term fallback for short keywords and identifiers.

After changing the graph profile, the next scan marks completed documents as Graph-pending automatically. Run `python start.py rebuild-knowledge-base` to rebuild the graph without rebuilding unchanged RAG vectors.

Confirm the gateway address and models in `config/config.json`, then build the web frontend and start:

```powershell
cd web\frontend
npm install
npm run build
cd ..\..

python start_web.py
```

Open `http://127.0.0.1:8000`.

Linux/macOS terminals use the same application entry point:

```bash
git clone https://github.com/kesepain-KE/kemo-graph.git
cd kemo-graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and config/config.json for your gateway and models
cd web/frontend
npm ci
npm run build
cd ../..
python start_web.py
```

---

## Basic usage

### Web

The browser provides project-based document management, Markdown preview and editing, dedicated build status, graph exploration, five retrieval modes, recycle-bin management, background task tracking, query-stage progress and runtime logs.

<p align="center">
  <img src="kemo-graph-web.png" alt="kemo-graph web knowledge retrieval interface" width="100%">
</p>

<p align="center">
  <sub>A local-first knowledge retrieval workspace combining graph, vector, hybrid retrieval and LLM answers.</sub>
</p>

#### Project folders and document management

The Documents page supports creating project folders, browsing by project and selecting an upload destination. Existing root-level files appear under **Ungrouped**. Projects are real first-level folders under the current knowledge base's normalized Markdown directory, **not separate databases or retrieval boundaries**.

- Use **Rename** and **Move** in the document toolbar, or select documents for a batch move.
- Renaming/moving preserves the source ID, content hash and existing Graph/RAG indexes, with no model calls. Content edits still require a manual rebuild.
- Source paths and `file_map.json` are updated together; cached results containing old paths become stale. Target/source-identity conflicts are rejected without overwriting other documents.
- Clearing the selected project affects only its documents. Deleting the entire library from the All Documents view includes all projects in the current knowledge base.
- Wait for background tasks before relocating documents. Externally synchronized records remain managed by their upstream system.
- Portable Stores expose the same organization through the API: `POST /api/v1/stores/projects/list`, `POST /api/v1/stores/projects/create`, `POST /api/v1/stores/documents/location` and `POST /api/v1/stores/documents/move-batch`, with `store_root` in the request body. Project actions in the browser apply to the currently opened knowledge base.

#### Query progress

Short labels show stages actually executed by the current query: LLM optimization, keyword/subquery processing, query embedding, graph/vector/lexical recall, chunk aggregation, reranking and answer generation. Skipped work is not marked completed; cache hits and planner fallback are identified separately. Internal navigation does not interrupt a query; reloading or closing the page does not preserve its waiting state.

Document chunking and document embedding belong to ingestion. Retrieval processes questions and existing chunks, without rebuilding documents just to show progress. Live stages use bounded, short-lived process memory, with no extra model calls or disk progress log. Multi-worker deployments require query and progress requests to reach the same worker; otherwise only that worker's confirmed stages are available.

#### Build status

The filename search appears directly below the document-list heading, above project folders. Graph / RAG status filters now live on the sidebar's dedicated **Build Status** page, alongside pending, processing, ready and failed counts for each pipeline. Combine filename and status filters, browse up to six documents per page, and scroll inside the list. Refresh manually or every five seconds; leaving the page stops polling without interrupting background builds. Uploading, editing and manual batch rebuilding remain in Document Management.

#### Runtime status and logs

The Runtime Status page includes a log card with **Terminal startup**, **Queries**, and **Internal events** tabs. Select a UTC date, refresh manually or every five seconds, and optionally follow the newest entries. Logs scroll inside the card.

Terminal logging starts after the upgraded service restarts and covers Web lifecycle events and Python/Uvicorn logging; previous shell output and arbitrary third-party stdout are not captured. Query lifecycle events include a `query_id`, outcome and duration, without newly recording question bodies or model answers. The viewer shows up to 200 entries and reads at most the final 2 MB of the selected daily log. API keys, Bearer tokens and other credential patterns are redacted.

#### In-memory read cache

Long-running Web/API processes share a bounded read-through cache for knowledge-state fingerprints, document pages, runtime status (including FAISS health results), logs, configuration text and the query-planning prompt digest. This supplements, rather than replaces, the existing SQLite search-result cache; forced queries, history cleanup and database writes retain their behavior.

- Limits per process: 256 entries, 32 MiB of serialized payload, and 2 MiB per entry. These are cache-payload limits, not a cap on total process memory. Least-recently-used entries are evicted; oversized values are not retained.
- Reads check file identity, size and timestamps, plus the 100-byte SQLite header. Normal commits, renames, deletions and database replacements invalidate affected reads. Even unchanged entries expire after 30 seconds.
- Database caching is bypassed while a nonempty WAL or transaction journal exists. No long-lived database connections are retained, allowing rebuilds to replace files.
- Concurrent reads for the same data share a load; callers receive independent values. Failed reads are not cached. Log-cache keys also reflect the credentials used for redaction.
- Absolute Store paths isolate entries. Each process has its own cache, empty after restart; one-shot CLI processes benefit only within that invocation. Database and log writes remain synchronous with their existing paths; no delayed-write buffer is introduced.

#### Knowledge-document preview and rendering

The Web preview renders the normalized Markdown that is actually used by Graph/RAG, rather than sending PDF, Office or other binary files directly to the browser. It uses `react-markdown`, GFM, KaTeX and a Mermaid renderer loaded on demand. Supported features include:

- headings, bold, italic, strikethrough, nested lists, task lists, block quotes, thematic breaks, tables, footnotes, links, images and fenced code;
- inline math such as `$a^2+b^2=c^2$` and display math with `$$...$$`;
- common LaTeX wrappers `\(...\)` and `\[...\]`, plus `aligned`, `cases`, matrices, fractions, integrals, sums, limits, Chinese `\text{}` and `\xrightarrow{}`;
- language-aware code highlighting;
- Mermaid fenced blocks, for example:

  ````markdown
  ```mermaid
  flowchart TD
      A[Source] --> B[Markdown]
      B --> C[Knowledge graph]
      B --> D[RAG vectors]
  ```
  ````

- Obsidian-style links: `[[Data structures]]` and `[[Data structures|open the concept]]`;
- Obsidian-style callouts:

  ```markdown
  > [!NOTE] Note
  > This body is rendered as a themed information panel.
  ```

- safe placeholder rendering for `![[document or image]]` embeds;
- YAML/TOML frontmatter recognition, hidden from the normal reading flow.

Math is rendered with KaTeX. Wide matrices and long formulas scroll inside the document preview instead of breaking the surrounding card. Mermaid is dynamically loaded only when a document contains a diagram, so ordinary documents do not pay the chart-engine startup cost.

Raw HTML and `<script>` are intentionally not executed, preventing imported documents from becoming script-injection surfaces. Web pages, video, audio, OCR and remote links should still be normalized by the upstream agent before they reach kemo-graph. Unsupported custom KaTeX macros are kept as source with a localized error indicator; they do not blank the whole document.

### Command line

```powershell
# Convert to Markdown only; no knowledge-base initialization or model call
python -m markitdown $env:KEMO_GRAPH_IMPORT_FILE -o output.md
python convert.py $env:KEMO_GRAPH_IMPORT_FILE -o output.md

# Import a file (without spending model quota yet)
python start.py import $env:KEMO_GRAPH_IMPORT_FILE --no-ingest

# Scan and ingest all pending documents
python start.py ingest

# Query
python start.py query-hybrid "how does a knowledge graph improve retrieval"
python start.py query-answer "answer based on both graph and source text"

# Sync authoritative kemo-agent table records into their independent Store
python start.py --store-root $env:KEMO_GRAPH_STORE_ROOT source-sync records.json

# Status and maintenance
python start.py status
python start.py list-docs
python start.py organize-graph
python start.py rebuild-all

# Check and apply updates
python start.py update-check
python start.py update

# Root updater: asks whether to force a refresh when versions are identical
python update.py
```

The normalization layer handles local ordinary files only. Web crawling, video, audio, OCR and remote links should be handled by the upstream agent before it passes Markdown or a local file to kemo-graph; the converter never accesses the network.

### HTTP API

kemo-graph can run as a standalone service, exposing graph query, hybrid retrieval, import and maintenance endpoints to agents such as kemo-agent:

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

The full request fields, response envelope and error codes are defined in [api.md](api.md).

---

## Data and privacy

- Converted Markdown, graph and index data all live in your local working directory; Markdown is the source of truth, everything else can be rebuilt at any time.
- Graph building, Embedding and Rerank call models through the Kemo gateway; before processing sensitive materials, review your gateway, model and network boundaries.
- Application query-lifecycle logs do not additionally retain question bodies or model answers. The viewer redacts known secrets and common authentication fields; third-party Python logs can still contain business information, so protect log and service access.

> **Note**: the external API has no built-in application-level authentication. It should only listen on `127.0.0.1` by default; for cross-device or public access, put a VPN, reverse proxy, TLS or authentication layer in front. Never expose an unprotected port directly.

---

## What we want it to become

kemo-graph does not try to replace every file manager, every database, or every search system.

It aims to be a stable knowledge foundation in the Kemo ecosystem: materials keep their provenance once they enter the system; relations can be discovered without drifting from source evidence; when files change, the system knows what to update and what to keep; models and providers may change, but the local source of truth stays in your hands.

An agent that truly accompanies a long-lived project should not only have a longer context window — it should also have a knowledge foundation that can keep understanding materials, verifying sources and maintaining structure.

---

## Current status

The core loop is already runnable: unified import, incremental updates, graph and vector retrieval, hybrid Q&A, safe deletion, scheduled maintenance, plus three entry points (Web, CLI, HTTP API) and an external knowledge-service interface for agents such as kemo-agent.

The current release is **1.5.1**. This release adds document organization to portable Stores: external knowledge bases can now create project folders, rename documents, and move one or many documents just like the default knowledge base does, with `store_root` supplied in the request body. It also fixes two defects — the document import pipeline had lost its `DocumentImportError` import and degraded into `NameError`, and Kemo request identifiers had roughly a six-in-ten chance of starting with a digit and being rejected by the gateway with HTTP 400, which silently downgraded query planning to the raw query. On the web side, runtime logs became a terminal-style stream, the settings page gained version detection, and the log date filter now uses the in-house date picker. Project folders, the build-status page, query progress and the bounded read cache are unchanged.

See [CHANGELOG.md](CHANGELOG.md) for the release summary, upgrade notes and verification commands. The root `version.json` is the application-version source; frontend and converter-package release metadata share the same version. The Kemo protocol remains 1.0 and HTTP routes remain under `/api/v1`.

Still being polished: conversion quality for complex document layouts, storage and index strategy for large knowledge bases and high concurrency, built-in authentication and permission tiers for the external API, and richer manual graph correction and provenance review interfaces.

If you are trying this early version, bug reports, retrieval feedback, sample document formats, and real scenarios of how you want agents to use a knowledge base are all welcome.

---

## Related Kemo ecosystem projects

- [kemo-agent](https://github.com/kesepain-KE/kemo-agent) — a local multi-user Agent Runtime for personal AI infrastructure; it can use kemo-graph's graph and RAG knowledge through the API.
- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api) — the unified Kemo model gateway, providing LLM, Embedding and Rerank models to ecosystem components over one protocol.

Each project can be used independently, or together as one local AI infrastructure where each plays its own role.

---

## Maintainer

[@kesepain](https://github.com/kesepain-KE)

---

## Contributing

kemo-graph is still in early development. Bug reports, format samples, retrieval quality feedback, documentation improvements and feature contributions are all welcome.

Suggested flow: Fork this repository → create a feature branch → make your changes and run the necessary tests → open a Pull Request explaining what changed, why, and how it was verified.

---

## License

This project is open-sourced under the [Apache License 2.0](LICENSE).
