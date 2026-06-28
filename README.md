# Research Compass

A dynamic academic paper Q&A system built on Snowflake Cortex RAG. Upload any research paper as a PDF and ask questions — answers are generated exclusively from your uploaded papers, never from external sources or the LLM's training data.

---

## Overview

Reading and extracting insights from academic papers is time-consuming. Research Compass addresses this by letting users upload PDFs through a Streamlit interface, automatically ingesting them into a RAG pipeline, and answering natural language questions grounded entirely in the uploaded content.

The system supports any academic topic — it is not pre-loaded with specific papers. Users bring their own documents.

---

## Features

- **Dynamic upload** — Upload any academic PDF at runtime through the Streamlit UI
- **Automatic ingestion** — Papers are parsed, chunked, and indexed automatically on upload
- **Hybrid retrieval** — Combines semantic vector search with keyword matching for better coverage
- **Context expansion** — Automatically expands around section headers to capture full arguments
- **HyDE** — Optional Hypothetical Document Embedding mode improves retrieval for vocabulary-heavy queries
- **Paper filter** — Ask questions across all uploaded papers or filter to a specific one
- **Source citation** — Every answer cites which paper it came from

---

## Pipeline

**1. Setup**
Database, schema, chunks table, and Cortex Search Service are created via `Setup_File.sql`.

**2. Upload**
The user uploads a PDF through the Streamlit UI. The file is streamed directly to a Snowflake internal stage.

**3. Parse**
`SNOWFLAKE.CORTEX.PARSE_DOCUMENT` extracts text from the PDF in layout mode, preserving document structure. The parsed content is stored in a temporary table to avoid SQL escaping issues with raw text.

**4. Chunk**
`SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER` splits the parsed text into overlapping chunks (500 tokens, 50 token overlap) in markdown mode, which respects section boundaries.

**5. Index**
Chunks are inserted into `CHUNKED_PAPERS` with a unique `paper_id` (MD5 hash of filename), `filename`, and `chunk_index`. The Cortex Search Service picks up new chunks automatically within ~1 minute via `TARGET_LAG`.

**6. Retrieval**
Three-layer hybrid retrieval pipeline:
- Semantic search via Cortex Search (`snowflake-arctic-embed-m-v1.5` embeddings)
- Keyword search via SQL `ILIKE` pattern matching
- Context expansion — forward expansion of 4–8 chunks around anchor chunks depending on whether they are section headers

**7. HyDE (optional)**
When enabled, `mistral-large2` generates a hypothetical academic answer to the user's question first. This hypothetical is used for retrieval instead of the raw query, improving matching for queries that use different vocabulary than the paper. The original query is still used for the final answer generation.

**8. Generation**
Retrieved chunks are passed to `mistral-large2` via `SNOWFLAKE.CORTEX.COMPLETE` with a prompt that instructs the model to answer only from the provided context and cite sources.

---

## Project Structure

```
research-compass/
├── Setup_File.sql               # Database, schema, tables, Cortex Search Service
├── Research_Compass.ipynb       # Full pipeline: ingestion, retrieval, HyDE, RAG
├── streamlit_app.py             # Streamlit front-end with upload UI and Q&A
├── DEVELOPMENT_NOTES.md         # Engineering decisions and known limitations
└── README.md
```

---

## Tech Stack

| Component | Tool |
|---|---|
| Data platform | Snowflake |
| Document parsing | Snowflake Cortex `PARSE_DOCUMENT` |
| Chunking | Snowflake Cortex `SPLIT_TEXT_RECURSIVE_CHARACTER` |
| Vector search | Snowflake Cortex Search |
| Embeddings | `snowflake-arctic-embed-m-v1.5` |
| LLM | `mistral-large2` |
| Front-end | Streamlit in Snowflake |
