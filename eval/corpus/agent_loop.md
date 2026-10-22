# Agent loop
The agent avoids claiming tests passed when they did not run by marking success only on a real zero exit code that was not a timeout.
The execution loop is: task analysis, plan, retrieval, patch proposal, sandbox execution, self-critique, and a final report.
Edits are generated as diff-based unified diffs that can be inspected before approval.
