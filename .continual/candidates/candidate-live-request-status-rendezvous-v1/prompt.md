# Live request status rendezvous Candidate

For the exact scope `agi/live-request-observability`, make each bounded push-triggered live campaign publish a GitHub commit status on its immutable request commit. Use a dedicated context, start as `pending`, and finish as `success`, `failure`, or `error` while linking to the exact Actions run. Keep campaign outputs in Actions artifacts only; do not commit model outputs or widen the AGI evidence gate. Grant only the narrow `statuses: write` permission needed for this rendezvous in addition to the existing permissions.

The status is transport/observability metadata only. It is not capability evidence, evaluator independence, or proof of AGI. If status publication fails, fail closed rather than claiming an observed campaign result.
