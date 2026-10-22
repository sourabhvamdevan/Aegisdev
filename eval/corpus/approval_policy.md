# Approval policy
Before a generated patch is applied, an explicit human approval decision must be recorded. No change is applied silently.
Applying a patch, enabling network access, or running a high-risk command requires explicit human approval.
If a run fails, the original repository is preserved and partial failures are reported. Tests are never reported as passed if they did not run.
