# Live campaign stage-isolation Candidate

For the exact scope `agi/live-campaign-evidence-coverage`, execute the requested capability campaign and long-horizon protocol as two bounded stages even if either stage fails. Preserve each stage's exit status and artifact outputs, then fail the overall job if either stage failed. Do not relax any task evaluator, parser, scoring rule, Candidate gate, sandbox rule, or external AGI evidence gate.

This change increases falsifiable evidence yield from an already-authorized bounded live request; it must not create additional workflow runs, increase configured run/instance bounds, commit model outputs, or relabel internal development evidence as external evidence.
