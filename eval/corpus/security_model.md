# Security model
Network access is denied by default inside the sandbox. Enabling network requires explicit human approval.
The sandbox enforces resource limits: CPU time, memory (address space), process count to stop fork bombs, file size, and open files.
Commands run against a strict allowlist inside a workspace jail. Non-allowlisted binaries are refused before any process starts.
Path traversal and symlink escape are blocked by resolving paths and confirming they stay under the workspace root.
