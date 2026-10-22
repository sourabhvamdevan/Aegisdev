"""Agent execution-loop tests, including the fail-safe honesty invariant."""
from app.agent import CodingAgent
from app.rag import RagPipeline


def test_agent_produces_plan_diff_and_requires_approval():
    agent = CodingAgent(RagPipeline())
    report = agent.run("add a solve function and a test")
    assert len(report.plan) >= 5
    assert report.patch is not None
    assert report.patch.files_changed
    assert report.requires_approval is True
    assert report.sandbox_result is not None


def test_agent_never_claims_success_when_tests_fail():
    """
    Feed a generator that writes a FAILING test. The agent must report
    success=False and must not claim the tests passed.
    """
    def failing_generator(task, retrieval, files):
        return {
            "solution.py": "def solve():\n    return 1\n",
            "test_solution.py": "from solution import solve\n\ndef test():\n    assert solve() == 2\n",
        }

    agent = CodingAgent(RagPipeline(), patch_generator=failing_generator)
    report = agent.run("intentionally failing task")
    assert report.success is False
    assert report.sandbox_result.exit_code != 0


def test_agent_preserves_original_and_reports_partial():
    agent = CodingAgent(RagPipeline())
    task = "already-solved task"
    # Pre-seed solution.py with the exact marker the default generator checks,
    # so it recognises the work is done and proposes no change.
    original = {"solution.py": f"# task: {task[:80]}\ndef solve():\n    return 42\n"}
    report = agent.run(task, files=original)
    # generator returns nothing new -> no patch, notes explain it
    assert report.patch is None
    assert any("No change" in n for n in report.notes)
