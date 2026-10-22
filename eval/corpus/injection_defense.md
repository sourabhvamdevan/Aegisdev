# Injection defense
The system detects prompt injection inside documents by scanning retrieved content for override patterns such as "ignore previous instructions".
Retrieved document content is treated as untrusted data and fenced so a downstream model treats it as evidence to cite, never as instructions.
Flagged chunks are reported so the user interface can warn about possible prompt injection.
