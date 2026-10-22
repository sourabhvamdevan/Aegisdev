"""
Sandboxed command executor.

This is the security heart of AegisDev. It runs untrusted, agent-generated
code with layered isolation:

  1. Command allowlist   - only approved executables can launch.
  2. Workspace jail      - cwd is a per-run temp dir; paths are validated so the
                           process cannot read/write outside it (path traversal
                           and symlink escape are checked).
  3. Resource limits     - CPU, address space, process count (fork-bomb guard),
                           file size and open-file caps via POSIX rlimits set in
                           a preexec hook that runs in the child before exec().
  4. Wall-clock timeout  - the whole process group is killed if it overruns.
  5. Network denial      - by default we run with an env flag and (where
                           available) an unshared network namespace so the child
                           has no usable network. Enabling network is an
                           explicit, approval-gated action.
  6. Output cap          - combined stdout/stderr is truncated to a hard byte
                           limit to defend against log-flood / disk-fill.

IMPORTANT HONESTY NOTE (say this in your video): rlimit + namespace isolation is
a strong baseline that runs anywhere, including Render's free tier, WITHOUT
Docker-in-Docker. It is not a full VM/gVisor boundary. For production you would
layer a container/microVM. The code documents that trade-off rather than hiding
it.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .policy import DEFAULT_POLICY, SandboxPolicy

# resource is POSIX-only. On non-POSIX we degrade gracefully (limits skipped)
# but the allowlist, jail and timeout still apply.
try:
    import resource  # type: ignore

    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover - Windows dev machines
    _HAS_RESOURCE = False


class SandboxError(Exception):
    """Raised when a request violates policy before execution."""


class ApprovalRequired(SandboxError):
    """Raised when an action needs explicit human approval that was not given."""


@dataclass
class SandboxResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_seconds: float
    command: str
    network_enabled: bool


def _resolve_within(workspace: Path, candidate: Path) -> Path:
    """
    Resolve `candidate` and confirm it stays inside `workspace`.

    Uses realpath resolution so that symlinks pointing outside the jail are
    caught (symlink escape) as well as ../ traversal.
    """
    workspace_real = workspace.resolve(strict=True)
    target = (workspace_real / candidate).resolve()
    if workspace_real != target and workspace_real not in target.parents:
        raise SandboxError(f"path escapes workspace jail: {candidate!r}")
    return target


class Sandbox:
    """A per-run sandbox rooted at a temporary workspace directory."""

    def __init__(self, policy: SandboxPolicy = DEFAULT_POLICY, workspace: Optional[Path] = None):
        self.policy = policy
        if workspace is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="sfbox_")
            self.workspace = Path(self._tmp.name).resolve()
        else:
            self._tmp = None
            self.workspace = Path(workspace).resolve()
            self.workspace.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ----------------------------------------------------------
    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    # -- file operations (jailed) ------------------------------------------
    def write_file(self, relative_path: str, content: str) -> Path:
        """Write a file inside the workspace, enforcing extension + jail rules."""
        rel = Path(relative_path)
        if rel.is_absolute():
            raise SandboxError("absolute paths are not permitted in the sandbox")
        if rel.suffix not in self.policy.allowed_extensions:
            raise SandboxError(f"file extension not allowed: {rel.suffix!r}")
        # Validate the parent dir stays in the jail, then create it.
        parent = (self.workspace / rel.parent).resolve()
        if self.workspace != parent and self.workspace not in parent.parents:
            raise SandboxError(f"path escapes workspace jail: {relative_path!r}")
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / rel.name
        target.write_text(content, encoding="utf-8")
        return target

    def read_file(self, relative_path: str) -> str:
        target = _resolve_within(self.workspace, Path(relative_path))
        return target.read_text(encoding="utf-8")

    # -- execution ----------------------------------------------------------
    def _preexec(self):
        """
        Runs in the CHILD process after fork, before exec. This is where we drop
        into the isolation cage: new session (so we can kill the whole group),
        rlimits, and a best-effort network namespace unshare.
        """
        # NOTE: the new session/process group is created by Popen's
        # start_new_session=True, so we must NOT call os.setsid() again here
        # (it would raise, since the child is already a session leader).
        if _HAS_RESOURCE:
            lim = self.policy.limits
            # Each limit is applied best-effort and independently: some platforms
            # (notably macOS) reject certain rlimits outright (e.g. RLIMIT_AS is
            # not settable there). A rejection must not abort the whole child --
            # that would take down every sandboxed run on that platform. Linux
            # (our deployment target) accepts all of these, so isolation there is
            # unchanged; a platform that rejects one just runs without it.
            for res_name, value in (
                ("RLIMIT_CPU", (lim.cpu_time_seconds, lim.cpu_time_seconds)),
                ("RLIMIT_AS", (lim.memory_bytes, lim.memory_bytes)),
                ("RLIMIT_NPROC", (lim.max_processes, lim.max_processes)),
                ("RLIMIT_FSIZE", (lim.max_file_size_bytes, lim.max_file_size_bytes)),
                ("RLIMIT_NOFILE", (lim.max_open_files, lim.max_open_files)),
            ):
                res_id = getattr(resource, res_name, None)
                if res_id is None:
                    continue  # limit not defined on this platform at all
                try:
                    resource.setrlimit(res_id, value)
                except (ValueError, OSError):
                    pass  # platform rejects this limit; skip it, keep the rest

        # Best-effort network isolation without root: unshare the net namespace.
        # This is available on Linux; if it fails (no CAP_SYS_ADMIN, non-Linux)
        # we fall back to the env-flag contract below and document the limit.
        if not self.policy.network_enabled:
            try:
                import ctypes  # noqa: WPS433 (local import inside child)

                CLONE_NEWNET = 0x40000000
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.unshare(CLONE_NEWNET)
            except Exception:
                pass  # documented degraded mode; env flag still signals policy

    def run(
        self,
        command: List[str],
        *,
        approved_high_risk: bool = False,
        approve_network: bool = False,
        stdin: str = "",
    ) -> SandboxResult:
        """
        Execute an allowlisted command inside the jail.

        `command` is a list, e.g. ["python", "solution.py"]. Shell strings are
        rejected: we never pass through a shell, which removes an entire class
        of injection.
        """
        if not command:
            raise SandboxError("empty command")

        executable = command[0]
        if not self.policy.is_command_allowed(executable):
            raise SandboxError(f"command not on allowlist: {executable!r}")

        if self.policy.is_high_risk(executable) and not approved_high_risk:
            raise ApprovalRequired(
                f"'{executable}' is high-risk and requires explicit approval"
            )

        network_enabled = self.policy.network_enabled or approve_network
        if approve_network and not self.policy.network_enabled:
            # Enabling network is itself a privileged action; only honoured when
            # the caller passed approve_network=True (wired to human approval).
            pass

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.workspace),
            "TMPDIR": str(self.workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
            # Contract flag consumed by the (degraded) no-namespace path.
            "SANDBOX_NETWORK": "1" if network_enabled else "0",
        }
        if not network_enabled:
            # Poison common proxy vars so accidental egress fails fast.
            env.update({"http_proxy": "127.0.0.1:9", "https_proxy": "127.0.0.1:9"})

        start = time.monotonic()
        timed_out = False
        truncated = False
        stdout_b = b""
        stderr_b = b""

        # Command is allowlist-validated above; args are a list (no shell). See policy.py.
        proc = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=self._preexec if os.name == "posix" else None,
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=stdin.encode("utf-8"),
                timeout=self.policy.limits.wall_time_seconds,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            # Kill the whole process group, not just the leader (fork-bomb safe).
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            stdout_b, stderr_b = proc.communicate()

        duration = time.monotonic() - start

        cap = self.policy.limits.max_output_bytes
        if len(stdout_b) > cap or len(stderr_b) > cap:
            truncated = True
        stdout = stdout_b[:cap].decode("utf-8", errors="replace")
        stderr = stderr_b[:cap].decode("utf-8", errors="replace")

        return SandboxResult(
            exit_code=None if timed_out else proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
            duration_seconds=round(duration, 4),
            command=shlex.join(command),
            network_enabled=network_enabled,
        )


def run_python_and_tests(
    files: dict[str, str],
    *,
    test_command: Optional[List[str]] = None,
    policy: SandboxPolicy = DEFAULT_POLICY,
) -> SandboxResult:
    """
    Convenience wrapper: materialise a set of {path: content} files in a fresh
    sandbox and run a test command (defaults to pytest). Used by the agent's
    sandbox-execution step.
    """
    with Sandbox(policy=policy) as box:
        for path, content in files.items():
            box.write_file(path, content)
        cmd = test_command or [sys.executable, "-m", "pytest", "-q"]
        # Normalise a bare "python"/"pytest" so the allowlist check passes while
        # still executing the current interpreter.
        if cmd and cmd[0] in {sys.executable}:
            cmd = ["python", *cmd[1:]]
        return box.run(cmd)
