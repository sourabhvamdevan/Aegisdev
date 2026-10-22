"""Adversarial, security-focused tests for the sandbox (the core safety boundary)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

from app.sandbox import ApprovalRequired, ResourceLimits, Sandbox, SandboxError, SandboxPolicy


def test_blocks_non_allowlisted_command():
    with Sandbox() as box:
        with pytest.raises(SandboxError):
            box.run(["curl", "http://example.com"])


def test_blocks_path_traversal_on_write():
    with Sandbox() as box:
        with pytest.raises(SandboxError):
            box.write_file("../escape.py", "x = 1")


def test_blocks_disallowed_extension():
    with Sandbox() as box:
        with pytest.raises(SandboxError):
            box.write_file("payload.sh", "rm -rf /")


def test_timeout_kills_runaway_process():
    box = Sandbox(policy=SandboxPolicy(limits=ResourceLimits(wall_time_seconds=2)))
    box.write_file("loop.py", "while True:\n    pass\n")
    result = box.run(["python", "loop.py"])
    box.cleanup()
    assert result.timed_out is True
    assert result.exit_code is None


@pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "RLIMIT_NPROC containment is Linux-specific: on macOS the limit is "
        "counted per-UID across the whole system (not per-process-tree), so "
        "this assertion doesn't hold outside Linux/CI."
    ),
)
def test_fork_bomb_is_contained():
    box = Sandbox(policy=SandboxPolicy(limits=ResourceLimits(wall_time_seconds=6, max_processes=16)))
    box.write_file(
        "fork.py",
        "import os\nfor _ in range(10000):\n    try:\n        os.fork()\n    except OSError:\n        break\n",
    )
    result = box.run(["python", "fork.py"])
    box.cleanup()
    # Either an OSError was raised (nproc limit hit) or it timed out -- never a
    # clean success that forked thousands of processes.
    assert result.timed_out or result.exit_code != 0 or "OSError" in result.stderr


def test_high_risk_command_requires_approval():
    with Sandbox() as box:
        with pytest.raises(ApprovalRequired):
            box.run(["pip", "install", "requests"])  # pip is high-risk
        # with explicit approval it is at least attempted (allowlist passes)
        result = box.run(["pip", "--version"], approved_high_risk=True)
        assert result is not None


def test_network_denied_by_default_flag():
    with Sandbox() as box:
        box.write_file("net.py", "import os\nprint(os.environ.get('SANDBOX_NETWORK'))\n")
        result = box.run(["python", "net.py"])
        assert result.stdout.strip() == "0"


def test_blocks_symlink_escape_on_read():
    with Sandbox() as box, tempfile.TemporaryDirectory() as outside:
        secret = Path(outside) / "secret.txt"
        secret.write_text("outside-the-jail")
        link = box.workspace / "escape_link"
        os.symlink(secret, link)
        with pytest.raises(SandboxError):
            box.read_file("escape_link")


def test_blocks_symlink_escape_on_write():
    with Sandbox() as box, tempfile.TemporaryDirectory() as outside:
        link_dir = box.workspace / "escape_dir"
        os.symlink(outside, link_dir)
        with pytest.raises(SandboxError):
            box.write_file("escape_dir/payload.py", "x = 1")
        assert not (Path(outside) / "payload.py").exists()


def test_oversized_output_is_truncated():
    box = Sandbox(policy=SandboxPolicy(limits=ResourceLimits(max_output_bytes=1024, wall_time_seconds=10)))
    box.write_file("flood.py", "print('x' * 2_000_000)\n")
    result = box.run(["python", "flood.py"])
    box.cleanup()
    assert result.truncated is True
    assert len(result.stdout) <= 1024


def test_sandboxed_process_does_not_inherit_host_secrets():
    """
    The child must get a minimal, explicit environment, not a copy of the host
    process's environment -- otherwise any secret exported into the server
    process (API keys, cloud credentials, etc.) would leak straight into
    agent-generated code running inside the "isolated" sandbox.
    """
    os.environ["SF_TEST_SECRET_TOKEN"] = "super-secret-value-should-not-leak"
    try:
        with Sandbox() as box:
            box.write_file("dump_env.py", "import os\nprint(dict(os.environ))\n")
            result = box.run(["python", "dump_env.py"])
        assert "SF_TEST_SECRET_TOKEN" not in result.stdout
        assert "super-secret-value-should-not-leak" not in result.stdout
    finally:
        del os.environ["SF_TEST_SECRET_TOKEN"]
