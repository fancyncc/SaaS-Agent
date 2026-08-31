# Demo knowledge corpus

The deterministic, original Chinese demo corpus is currently embedded in `backend/rag.py` so the application works without an embedding API. The retrieval interface deliberately returns citation IDs and scores. Replace that implementation with PostgreSQL full-text search plus pgvector for production while preserving the function contract.

