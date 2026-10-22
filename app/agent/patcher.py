"""
Unified-diff patch generation and application.

The agent proposes changes as reviewable unified diffs (never silent writes).
Applying a diff is a separate, approval-gated step. Originals are always kept so
we can fail safely and restore.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ProposedPatch:
    diff: str
    files_changed: List[str]
    new_contents: Dict[str, str] = field(default_factory=dict)
    original_contents: Dict[str, str] = field(default_factory=dict)


def make_unified_diff(original: str, updated: str, filename: str) -> str:
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def propose(changes: Dict[str, str], originals: Dict[str, str]) -> ProposedPatch:
    """
    Build a ProposedPatch from a map of {filename: new_content}. `originals`
    supplies current content (empty string for new files).
    """
    diffs: List[str] = []
    files_changed: List[str] = []
    for filename, new_content in changes.items():
        original = originals.get(filename, "")
        if original == new_content:
            continue
        diffs.append(make_unified_diff(original, new_content, filename))
        files_changed.append(filename)
    return ProposedPatch(
        diff="\n".join(diffs),
        files_changed=files_changed,
        new_contents=dict(changes),
        original_contents=dict(originals),
    )
