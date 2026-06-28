--> Create a Database, Schema
CREATE DATABASE IF NOT EXISTS RESEARCH_COMPASS;
CREATE SCHEMA IF NOT EXISTS RESEARCH_COMPASS.RAG;

--> Create a stage
CREATE OR REPLACE STAGE RESEARCH_COMPASS.RAG.PDF_STAGE
    DIRECTORY = (ENABLE = TRUE)
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

--> Create a Metadata table for the papers
CREATE OR REPLACE TABLE RESEARCH_COMPASS.RAG.PAPERS (
    paper_id VARCHAR,
    filename VARCHAR,
    title VARCHAR,
    upload_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);

--> Create a chunks table
CREATE OR REPLACE TABLE RESEARCH_COMPASS.RAG.CHUNKED_PAPERS (
    paper_id VARCHAR,
    filename VARCHAR,
    chunk_index INT,
    chunk_text TEXT
);

--> Create a Cortex Search Service
CREATE OR REPLACE CORTEX SEARCH SERVICE RESEARCH_COMPASS.RAG.PAPER_SEARCH_SERVICE
    ON chunk_text
    ATTRIBUTES paper_id, filename
    WAREHOUSE = COMPUTE_WH
    TARGET_LAG = '1 minute'
    AS (
        SELECT
            chunk_text,
            paper_id,
            filename
        FROM RESEARCH_COMPASS.RAG.CHUNKED_PAPERS
    );
    