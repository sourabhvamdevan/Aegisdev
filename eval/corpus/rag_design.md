# RAG design
Citations are formatted using the filename plus a locator such as page, section, or line range.
Incremental re-indexing replaces only the rows for a re-ingested file by deleting its old chunks and adding fresh ones.
Local embeddings are the default for privacy: a local sentence-transformers model, with a deterministic hashing embedding backend as an offline fallback.
Content hashing with sha256 provides duplicate detection so identical chunks are skipped.
