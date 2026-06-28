import streamlit as st
from snowflake.snowpark.context import get_active_session
import json
import hashlib

# --- Session Setup ---
session = get_active_session()

# --- Session State ---
if "result" not in st.session_state:
    st.session_state.result = None
if "query" not in st.session_state:
    st.session_state.query = ""

# --- Custom CSS ---
st.markdown("""
<style>
    .answer-card {
        background-color: #f8f9fa;
        border-left: 4px solid #2563eb;
        border-radius: 8px;
        padding: 20px 24px;
        margin: 16px 0;
        font-size: 15px;
        line-height: 1.7;
        color: #1e293b;
    }
    .hypothetical-card {
        background-color: #fefce8;
        border-left: 4px solid #eab308;
        border-radius: 8px;
        padding: 16px 20px;
        margin: 8px 0;
        font-size: 13px;
        line-height: 1.6;
        color: #713f12;
    }
    .paper-card {
        background-color: #f1f5f9;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 8px 0;
        font-size: 14px;
    }
    .badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 600;
        margin-right: 8px;
        background-color: #dbeafe;
        color: #1d4ed8;
    }
    .how-it-works {
        background-color: #f8fafc;
        border-radius: 8px;
        padding: 16px;
        font-size: 14px;
        color: #475569;
    }
    div[data-testid="stButton"] button {
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

# --- Retrieval Functions ---
def retrieve_chunks(query, paper_id=None, limit=5):
    filter_clause = ""
    if paper_id:
        filter_clause = f', "filter": {{"@eq": {{"PAPER_ID": "{paper_id}"}}}}'
    result = session.sql(f"""
        SELECT PARSE_JSON(
            SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
                'RESEARCH_COMPASS.RAG.PAPER_SEARCH_SERVICE',
                '{{"query": "{query}", "columns": ["chunk_text", "paper_id", "filename"], "limit": {limit}{filter_clause}}}'
            )
        )['results'] AS results
    """).collect()
    raw = result[0]["RESULTS"]
    if raw is None:
        return []
    return json.loads(raw)

def retrieve_chunks_hybrid(query, paper_id=None, limit=5):
    semantic_chunks = retrieve_chunks(query, paper_id, limit)
    semantic_texts = set([c["chunk_text"][:50] for c in semantic_chunks])
    paper_clause = f"AND paper_id = '{paper_id}'" if paper_id else ""
    keywords = [w for w in query.split() if len(w) > 4]
    keyword_conditions = " OR ".join([
        f"LOWER(chunk_text) LIKE LOWER('%{kw}%')" for kw in keywords
    ])
    if keyword_conditions:
        keyword_rows = session.sql(f"""
            SELECT chunk_text, paper_id, filename, chunk_index
            FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
            WHERE ({keyword_conditions})
            {paper_clause}
            LIMIT {limit}
        """).collect()
        keyword_chunks = [
            {"chunk_text": r["CHUNK_TEXT"],
             "paper_id": r["PAPER_ID"],
             "filename": r["FILENAME"],
             "chunk_index": r["CHUNK_INDEX"]}
            for r in keyword_rows
            if r["CHUNK_TEXT"][:50] not in semantic_texts
        ]
    else:
        keyword_chunks = []
    return semantic_chunks + keyword_chunks

def retrieve_chunks_hybrid_expanded(query, paper_id=None, limit=5, max_chunks=20):
    semantic_chunks = retrieve_chunks(query, paper_id, limit)
    if not semantic_chunks:
        return []
    paper_clause = f"AND paper_id = '{paper_id}'" if paper_id else ""
    semantic_texts_escaped = [c["chunk_text"][:100].replace("'", "''") for c in semantic_chunks]
    text_conditions = " OR ".join([f"LEFT(chunk_text, 100) = '{t}'" for t in semantic_texts_escaped])
    index_rows = session.sql(f"""
        SELECT chunk_text, paper_id, filename, chunk_index
        FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
        WHERE ({text_conditions})
        {paper_clause}
    """).collect()
    semantic_with_index = [{"chunk_text": r["CHUNK_TEXT"],
                             "paper_id": r["PAPER_ID"],
                             "filename": r["FILENAME"],
                             "chunk_index": r["CHUNK_INDEX"]} for r in index_rows]
    semantic_indices = set([int(c["chunk_index"]) for c in semantic_with_index])
    expanded_indices = set(semantic_indices)
    header_markers = [
        "abstract", "introduction", "conclusion", "related work",
        "methodology", "results", "algorithm", "our approach",
        "proposed method", "debiasing", "experiments", "discussion"
    ]
    for c in semantic_with_index:
        idx = int(c["chunk_index"])
        text = c["chunk_text"].lower()
        is_header = any(marker in text for marker in header_markers)
        forward = 8 if is_header else 4
        for offset in range(1, forward + 1):
            expanded_indices.add(idx + offset)
    indices_str = ','.join([str(i) for i in sorted(expanded_indices)])
    expanded_rows = session.sql(f"""
        SELECT chunk_text, paper_id, filename, chunk_index
        FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
        WHERE chunk_index IN ({indices_str})
        {paper_clause}
        ORDER BY chunk_index
        LIMIT {max_chunks}
    """).collect()
    expanded_chunks = [{"chunk_text": r["CHUNK_TEXT"],
                        "paper_id": r["PAPER_ID"],
                        "filename": r["FILENAME"],
                        "chunk_index": r["CHUNK_INDEX"]} for r in expanded_rows]
    hybrid_chunks = retrieve_chunks_hybrid(query, paper_id, limit)
    existing_keys = set([(c.get("filename"), c.get("chunk_index")) for c in expanded_chunks])
    for c in hybrid_chunks:
        if (c.get("filename"), c.get("chunk_index")) not in existing_keys:
            expanded_chunks.append(c)
    expanded_chunks = sorted(
        expanded_chunks,
        key=lambda x: (x.get("filename", ""), int(x.get("chunk_index", 0)))
    )
    return expanded_chunks[:max_chunks]

def build_prompt(query, chunks):
    context = ""
    for i, chunk in enumerate(chunks):
        filename = chunk.get("filename", "Unknown")
        text = chunk.get("chunk_text", "")
        context += f"[Chunk {i+1} - {filename}]\n{text}\n\n"
    return f"""You are an academic research assistant. Answer the user's question based on the provided context from uploaded research papers.

Instructions:
- Use ONLY the provided context to answer
- If the exact terms in the question differ from the paper's terminology, look for conceptually equivalent content
- For example, if asked about "hard vs soft debiasing trade-offs", look for content about advantages and disadvantages of different debiasing approaches
- Always cite which paper and which section your answer comes from
- Only say you cannot find information if the topic is genuinely absent from the context

Context:
{context}

Question: {query}

Answer:"""

def generate_hypothetical_answer(query):
    prompt = f"""You are an academic research assistant.
A user is looking for information in research papers about the following question:

"{query}"

Write a short hypothetical answer (3-5 sentences) that such a paper might contain.
Use academic language and terminology that would appear in a research paper.
Do not say "I think" or "perhaps" — write it as if it is a factual excerpt from a paper.

Hypothetical answer:"""
    response = session.sql(f"""
        SELECT SNOWFLAKE.CORTEX.COMPLETE(
            'mistral-large2',
            '{prompt.replace("'", "''")}'
        ) AS hypothetical_answer
    """).collect()
    return response[0]["HYPOTHETICAL_ANSWER"].strip()

def rag_query(query, paper_id=None):
    chunks = retrieve_chunks_hybrid_expanded(query, paper_id, limit=5, max_chunks=20)
    if not chunks:
        return {
            "answer": "No papers have been uploaded yet. Please upload a paper first.",
            "sources": [],
            "chunks_used": 0,
            "hypothetical": None
        }
    prompt = build_prompt(query, chunks)
    response = session.sql(f"""
        SELECT SNOWFLAKE.CORTEX.COMPLETE(
            'mistral-large2',
            '{prompt.replace("'", "''")}'
        ) AS answer
    """).collect()
    sources = list(set([c.get("filename", "Unknown") for c in chunks]))
    return {
        "answer": response[0]["ANSWER"],
        "sources": sources,
        "chunks_used": len(chunks),
        "hypothetical": None
    }

def rag_query_hyde(query, paper_id=None):
    hypothetical = generate_hypothetical_answer(query)
    hypothetical_clean = (hypothetical
        .replace('"', ' ')
        .replace("'", ' ')
        .replace('\n', ' ')
        .replace('\r', ' ')
        .strip()
    )
    hyde_chunks = retrieve_chunks_hybrid_expanded(
        hypothetical_clean, paper_id, limit=5, max_chunks=10
    )
    original_chunks = retrieve_chunks_hybrid_expanded(
        query, paper_id, limit=5, max_chunks=10
    )
    seen = set()
    all_chunks = []
    for c in hyde_chunks + original_chunks:
        key = (c.get("filename"), c.get("chunk_index"))
        if key not in seen:
            seen.add(key)
            all_chunks.append(c)
    all_chunks = sorted(
        all_chunks,
        key=lambda x: (x.get("filename", ""), int(x.get("chunk_index", 0)))
    )[:20]
    if not all_chunks:
        return {
            "answer": "No relevant information found in the uploaded papers.",
            "sources": [],
            "chunks_used": 0,
            "hypothetical": hypothetical
        }
    prompt = build_prompt(query, all_chunks)
    response = session.sql(f"""
        SELECT SNOWFLAKE.CORTEX.COMPLETE(
            'mistral-large2',
            '{prompt.replace("'", "''")}'
        ) AS answer
    """).collect()
    sources = list(set([c.get("filename", "Unknown") for c in all_chunks]))
    return {
        "answer": response[0]["ANSWER"],
        "sources": sources,
        "chunks_used": len(all_chunks),
        "hypothetical": hypothetical
    }

def ingest_paper(filename, title=None):
    paper_id = hashlib.md5(filename.encode()).hexdigest()[:8]
    existing = session.sql(f"""
        SELECT COUNT(*) AS cnt
        FROM RESEARCH_COMPASS.RAG.PAPERS
        WHERE paper_id = '{paper_id}'
    """).collect()
    if existing[0]["CNT"] > 0:
        return None, "already_exists"
    session.sql(f"""
        CREATE OR REPLACE TEMPORARY TABLE RESEARCH_COMPASS.RAG.TEMP_PARSED AS
        SELECT SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
            @RESEARCH_COMPASS.RAG.PDF_STAGE,
            '{filename}',
            {{'mode': 'LAYOUT'}}
        ) AS parsed_content
    """).collect()
    session.sql(f"""
        INSERT INTO RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
            (paper_id, filename, chunk_index, chunk_text)
        SELECT
            '{paper_id}',
            '{filename}',
            chunk.index,
            chunk.value::STRING
        FROM RESEARCH_COMPASS.RAG.TEMP_PARSED,
        LATERAL FLATTEN(
            INPUT => SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
                parsed_content:content::STRING,
                'markdown',
                500,
                50
            )
        ) AS chunk
    """).collect()
    display_title = title if title else filename.replace('.pdf', '').replace('_', ' ').title()
    session.sql(f"""
        INSERT INTO RESEARCH_COMPASS.RAG.PAPERS
            (paper_id, filename, title)
        VALUES
            ('{paper_id}', '{filename}', '{display_title}')
    """).collect()
    chunk_count = session.sql(f"""
        SELECT COUNT(*) AS cnt
        FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
        WHERE paper_id = '{paper_id}'
    """).collect()[0]["CNT"]
    return paper_id, chunk_count

def get_papers():
    rows = session.sql("""
        SELECT paper_id, filename, title, upload_timestamp
        FROM RESEARCH_COMPASS.RAG.PAPERS
        ORDER BY upload_timestamp DESC
    """).collect()
    return rows

def delete_paper(paper_id):
    session.sql(f"DELETE FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS WHERE paper_id = '{paper_id}'").collect()
    session.sql(f"DELETE FROM RESEARCH_COMPASS.RAG.PAPERS WHERE paper_id = '{paper_id}'").collect()

# --- UI ---
st.set_page_config(page_title="Research Compass", layout="wide")

st.markdown("""
<div style='background: linear-gradient(90deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
     padding: 28px 32px; border-radius: 12px; margin-bottom: 16px;'>
    <h1 style='color: white; margin: 0; font-size: 2rem;'>Research Compass</h1>
    <p style='color: #94a3b8; margin: 8px 0 0 0; font-size: 1rem;'>
        Upload academic papers and ask questions — powered by Snowflake Cortex RAG
    </p>
</div>
""", unsafe_allow_html=True)

with st.expander("How does this work?"):
    st.markdown("""
    <div class='how-it-works'>
    <ol>
        <li><b>Upload</b> — Add any academic PDF through the Library tab</li>
        <li><b>Parse & Chunk</b> — The paper is automatically parsed and split into searchable chunks</li>
        <li><b>Ask</b> — Ask any question in the Q&A tab — search across all papers or filter to one</li>
        <li><b>Answer</b> — Answers are generated only from your uploaded papers, never from external sources</li>
        <li><b>HyDE</b> — Enable HyDE in settings to improve retrieval for vocabulary-heavy questions</li>
    </ol>
    </div>
    """, unsafe_allow_html=True)

tab1, tab2 = st.tabs(["Library", "Q&A"])

# --- Sidebar ---
with st.sidebar:
    st.header("Settings")
    st.divider()
    st.markdown("**Retrieval method:**")
    use_hyde = st.toggle("Enable HyDE", value=False, key="hyde_toggle")
    if use_hyde:
        st.markdown("""
        <div style='background:#fefce8; border-radius:8px; padding:10px; font-size:12px; color:#713f12;'>
        <b>HyDE ON</b> — generates a hypothetical answer first, then uses it to find better matching chunks.
        Uses 2x LLM calls per query.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style='background:#f0fdf4; border-radius:8px; padding:10px; font-size:12px; color:#166534;'>
        <b>Standard ON</b> — hybrid semantic + keyword retrieval with context expansion.
        </div>
        """, unsafe_allow_html=True)
    st.divider()
    st.markdown("**LLM:** `mistral-large2`")
    st.markdown("**Embeddings:** `snowflake-arctic-embed-m-v1.5`")
    st.markdown("**Chunking:** 500 tokens, 50 overlap")

# --- Tab 1: Library ---
with tab1:
    st.subheader("Upload a Paper")
    uploaded_file = st.file_uploader("Choose a PDF", type="pdf")
    custom_title = st.text_input("Paper title (optional — leave blank to use filename)")

    if st.button("Upload & Ingest", type="primary") and uploaded_file:
        with st.spinner("Uploading to Snowflake stage..."):
            file_bytes = uploaded_file.read()
            filename = uploaded_file.name.replace(" ", "_")
            session.file.put_stream(
                input_stream=__import__('io').BytesIO(file_bytes),
                stage_location=f"@RESEARCH_COMPASS.RAG.PDF_STAGE/{filename}",
                overwrite=True,
                auto_compress=False
            )
        with st.spinner("Parsing and chunking paper..."):
            title = custom_title if custom_title else None
            paper_id, result = ingest_paper(filename, title)
        if result == "already_exists":
            st.warning(f"'{filename}' has already been uploaded.")
        else:
            st.success(f"Paper ingested with {result} chunks! Searchable in about 1 minute.")

    st.divider()
    st.subheader("Uploaded Papers")
    papers = get_papers()
    if not papers:
        st.info("No papers uploaded yet. Upload a paper above to get started.")
    else:
        for paper in papers:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"""
                <div class='paper-card'>
                    <b>{paper['TITLE']}</b><br>
                    <span style='color: #64748b; font-size: 12px;'>
                        {paper['FILENAME']} · Uploaded {str(paper['UPLOAD_TIMESTAMP'])[:10]}
                    </span>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if st.button("Delete", key=f"del_{paper['PAPER_ID']}"):
                    delete_paper(paper['PAPER_ID'])
                    st.rerun()

# --- Tab 2: Q&A ---
with tab2:
    st.subheader("Ask a Question")
    papers = get_papers()

    if not papers:
        st.info("No papers uploaded yet. Go to the Library tab to upload papers first.")
    else:
        paper_options = {"All Papers": None}
        for p in papers:
            paper_options[p["TITLE"]] = p["PAPER_ID"]

        selected_paper = st.selectbox("Search in:", list(paper_options.keys()))
        selected_paper_id = paper_options[selected_paper]

        st.markdown("**Try a sample question:**")
        samples = [
            "What bias mitigation techniques are proposed?",
            "What datasets were used in the experiments?",
            "What are the main findings of this research?",
            "What are the limitations of the approach?"
        ]
        cols = st.columns(4)
        for i, sample in enumerate(samples):
            with cols[i]:
                if st.button(sample, key=f"sample_{i}", use_container_width=True):
                    st.session_state.query = sample
                    st.session_state.result = None
                    st.rerun()

        st.divider()

        query = st.text_input(
            "Ask a question about your papers:",
            value=st.session_state.query,
            placeholder="e.g. What bias mitigation techniques are proposed?"
        )

        if st.button("Search", type="primary", key="search_btn") and query:
            st.session_state.query = query
            if use_hyde:
                with st.spinner("Generating hypothetical answer and retrieving chunks..."):
                    st.session_state.result = rag_query_hyde(query, paper_id=selected_paper_id)
            else:
                with st.spinner("Searching papers and generating answer..."):
                    st.session_state.result = rag_query(query, paper_id=selected_paper_id)

        if st.session_state.result:
            result = st.session_state.result
            if result.get("hypothetical"):
                with st.expander("HyDE — Hypothetical answer used for retrieval"):
                    st.markdown(f"""
                    <div class='hypothetical-card'>{result['hypothetical']}</div>
                    """, unsafe_allow_html=True)
            st.markdown("**Sources:**")
            for source in result["sources"]:
                st.markdown(f"<span class='badge'>{source}</span>", unsafe_allow_html=True)
            st.markdown(f"<div class='answer-card'>{result['answer']}</div>",
                       unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Chunks Retrieved", result["chunks_used"])
            with col2:
                st.metric("Papers Searched", len(result["sources"]))