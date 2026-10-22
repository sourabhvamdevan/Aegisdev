# AI-Assisted Development Disclosure

> Required by the assessment. Be honest and specific. You (the candidate) remain
> responsible for every submitted line and must be able to explain, debug, and
> modify it in the live technical defence. Edit the "Independently verified"
> notes to reflect what **you** actually checked.

## Tools used

| Tool | Where it assisted |
|------|-------------------|
| Claude (Anthropic) | Scaffolding the project structure; first drafts of the sandbox executor, RAG pipeline, MCP server, FastAPI layer, tests, and this documentation. |
| _(add any others you used, e.g. Copilot, local LLM)_ | |

## Major areas where AI assisted

- **Sandbox isolation** (`app/sandbox/`): drafting the rlimit/namespace approach,
  the allowlist/jail checks, and the adversarial tests.
- **RAG pipeline** (`app/rag/`): parsers, chunking, the SQLite vector store with
  hashing/dedup/reindex, and the prompt-injection baseline.
- **MCP server** (`app/mcp_server/`): tool/resource/prompt definitions and the
  in-process client used by the API.
- **Agent loop** (`app/agent/`): the analyze→plan→retrieve→patch→sandbox→critique
  structure and the fail-safe success invariant.
- **Web UI & API** (`web/`, `app/api/`, `app/main.py`).

## Representative prompts / workflow

- "Implement a POSIX sandbox that blocks non-allowlisted commands, jails the
  workspace, caps CPU/memory/processes, denies network by default, and kills
  runaway processes; include adversarial tests."
- "Expose ingestion, retrieval, repo inspection, patch proposal and sandbox
  execution as MCP tools with typed args, timeouts, and structured errors."
- Iterative debugging: fixing a double-`setsid()` bug in the sandbox preexec,
  loosening injection regexes, and correcting a test assumption.

## Independently verified by me (the candidate)

> Replace these with your own words after you have read and run the code.

- [ ] I read `app/sandbox/executor.py` line by line and can explain why
      `start_new_session=True` plus a process-group kill defeats fork bombs, and
      what the rlimit/namespace trade-off is versus a full VM.
- [ ] I ran `make check` locally and confirmed all tests and the retrieval eval
      pass.
- [ ] I can extend the RAG pipeline (e.g. swap the hashing embedder for
      sentence-transformers) and explain the privacy implications.
- [ ] I can point to where the web app calls MCP tools rather than bypassing them.
- [ ] I wired the local-LLM seam (`_default_patch_generator`) to Ollama/llama.cpp
      and can describe how real patch synthesis plugs in.

## What is a real prototype vs a documented seam

To be transparent (and defensible): the entire pipeline — MCP, retrieval,
citations, injection fencing, sandboxing, diffing, approval gate, self-critique,
and safe reporting — is **real and runnable with zero cost**. The single
model-dependent step (LLM patch *synthesis*) ships as a deterministic generator
with a clearly marked seam where a local model plugs in, so the loop runs and is
testable everywhere without paid APIs.
