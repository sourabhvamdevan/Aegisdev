"""
Tests for the local-LLM (Ollama) patch generator.

These exercise the pure parsing/validation logic with canned model output, so
they run offline with no Ollama server. They prove the security boundary: the
model is untrusted, and only policy-allowed files survive.
"""
from app.agent.ollama_generator import parse_model_output


def test_parses_valid_files_block():
    content = '{"files": {"solution.py": "def solve():\\n    return 1\\n", "test_solution.py": "def test():\\n    assert True\\n"}}'
    files = parse_model_output(content)
    assert set(files.keys()) == {"solution.py", "test_solution.py"}


def test_rejects_path_traversal_filenames():
    content = '{"files": {"../evil.py": "x = 1", "sub/dir.py": "y = 2"}}'
    files = parse_model_output(content)
    assert files == {}  # both filenames contain separators -> dropped


def test_rejects_disallowed_extensions():
    content = '{"files": {"payload.sh": "rm -rf /", "lib.so": "binary"}}'
    files = parse_model_output(content)
    assert files == {}


def test_bad_json_returns_empty():
    assert parse_model_output("not json at all") == {}


def test_accepts_top_level_map_without_files_key():
    content = '{"solution.py": "print(1)"}'
    files = parse_model_output(content)
    assert files == {"solution.py": "print(1)"}
