"""
Sandbox security policy.

Central place for every isolation knob so the rules are auditable in one file
rather than scattered through the executor. If you want to defend a security
decision in the live review, this is the file to open.

Design principle: DENY BY DEFAULT. Everything the sandbox is allowed to do is
listed explicitly here. Anything not listed is refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet

# --- Command allowlist -------------------------------------------------------
# Only these executables may be launched inside the sandbox. Anything else is
# refused before a process is ever spawned. We intentionally keep this tiny.
ALLOWED_COMMANDS: FrozenSet[str] = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "pip",  # only reachable when an install step is explicitly approved
    }
)

# Commands that are ALWAYS treated as high-risk and therefore require explicit
# human approval, even if present in ALLOWED_COMMANDS.
HIGH_RISK_COMMANDS: FrozenSet[str] = frozenset({"pip"})

# --- File type allowlist -----------------------------------------------------
# The agent may only create/modify files with these extensions inside the
# workspace. This blocks it from dropping shared objects, shell scripts, etc.
ALLOWED_FILE_EXTENSIONS: FrozenSet[str] = frozenset(
    {".py", ".txt", ".md", ".json", ".cfg", ".ini", ".toml"}
)

# --- Resource limits ---------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimits:
    """Hard limits enforced on every sandboxed process."""

    wall_time_seconds: int = 20          # killed after this even if productive
    cpu_time_seconds: int = 15           # RLIMIT_CPU
    memory_bytes: int = 512 * 1024 * 1024  # RLIMIT_AS (512 MB)
    max_processes: int = 64              # RLIMIT_NPROC -> defeats fork bombs
    max_output_bytes: int = 1 * 1024 * 1024  # 1 MB of combined stdout+stderr
    max_file_size_bytes: int = 32 * 1024 * 1024  # RLIMIT_FSIZE (32 MB)
    max_open_files: int = 256            # RLIMIT_NOFILE


@dataclass(frozen=True)
class SandboxPolicy:
    """The full policy handed to an executor invocation."""

    limits: ResourceLimits = field(default_factory=ResourceLimits)
    network_enabled: bool = False        # network OFF by default
    allowed_commands: FrozenSet[str] = ALLOWED_COMMANDS
    high_risk_commands: FrozenSet[str] = HIGH_RISK_COMMANDS
    allowed_extensions: FrozenSet[str] = ALLOWED_FILE_EXTENSIONS

    def is_command_allowed(self, executable: str) -> bool:
        return executable in self.allowed_commands

    def is_high_risk(self, executable: str) -> bool:
        return executable in self.high_risk_commands


# A single shared default. Callers may construct their own for stricter runs.
DEFAULT_POLICY = SandboxPolicy()
