# Compact evidence for the accepted harness transition

Historical method: `f7c61660bdaf`. These files belong to the accepted live experiment, before subsequent
boundary fixes. They are evidence, not the current recommended harness or a state directory to resume.

Read with the [result report](../harness_result_20261003.md):

1. [Proposal](candidate_proposal.json) and [diff](candidate_diff.patch): cited evidence and harness changes.
2. [Independent checks](candidate_validation.json): candidate acceptance.
3. [Execution events](run_j1500453/harness_events.jsonl) and [delivery receipt](round_01/harness_receipt.json): loaded hashes and hook calls.
4. [Debug prompt excerpt](run_j1500453/debug_prompt_step11_excerpt.txt): the timeout note reached AIDE.
5. [Reward](round_01/reward.json) and [dynamics summary](round_01/dynamics_summary.json): observed outcomes.

`H0/` and `H1/` preserve the evaluated files and manifests. `method.json` identifies the frozen method.
`round_00/` contains the reused baseline evidence. Paths and job IDs are historical provenance; no credentials,
datasets, container images or full run directories are included. Evidence files are preserved as recorded.
