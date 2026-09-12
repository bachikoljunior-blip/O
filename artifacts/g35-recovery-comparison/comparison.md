The fixed release supports task recovery on observed failed episodes, while harm to initially successful episodes remains unmeasured.

The prospective protocol was published as `37b2b54be402bda62e4ff8fecd9a3be85127e8da` and all eight files were read back before any outcome request. It fixes author commit `d756e2d509f7e01967aee43b713112ef29eb89ba`, the first100 manifest IDs and repeat1. All100 IDs have exactly one accounting row. There are48 matched repair triples and52 absent triples, with no duplicate selected keys or contradictory labels.

| Fixed analysis | Result | Denominator and limit |
|---|---:|---|
| Baseline failures |49/100 (49%)|Author baseline labels;51 successes|
| Resampling recovery |10/48 (20.8%; Wilson95%11.7–34.3%)|Observed paired failures|
| Located-hint recovery |21/48 (43.8%; Wilson95%30.7–57.7%)|Observed paired failures|
| Located minus resample |+22.9 percentage points|17 versus6 discordant pairs; exact two-sided McNemar p0.03469|
| Disruption after repair |UNKNOWN|No initially successful episode in the paired repair subset|
| Whole100 net task effect |UNKNOWN|Missing pairs comprise51 baseline successes and1 failure|

The preregistered conservative net bounds are−41 to+11 points for resampling and−30 to+22 points for located repair. These bounds allow every missing policy result to vary; they are not observed damage estimates. No source absence was converted into success or an unflagged trajectory. The baseline label table still establishes the original51 successes; the missing `none` policy rows in the strict paired analysis do not erase that separate observation.

The [first paper, Table4](https://arxiv.org/html/2602.03338v1) reports configuration-dependent recovery/disruption outcomes; the [second paper, Section10](https://arxiv.org/html/2608.02464v1) reports author-side live repair with no correct episode flagged in that arm. Detailed source configurations and preserved inconsistencies are in `paper-comparison.json`. Our fixed100-ID, one-repeat slice does not reproduce the second paper's120-episode, three-repeat net-success headline.

Reproduce with `python scripts/analyze_recovery_author_release.py --source-dir <pinned-source-files>`. The analyzer verifies the protocol blob and source hashes, selects only the fixed IDs/policies/repeat, retains missing or conflicting rows, and checks count identities. Source hashes, selected facts, all100 accounting records, native observation receipts and the execution receipt are retained. Upstream code was not run; there were zero provider calls or upstream writes.

The proposed next unit measures the missing question using8 new repository tasks plus8 declared no-fault controls, under a fixed baseline/resample/localized-hint comparison. It is only a recommendation for Root. This result is same-operator author-data analysis, not an O behavioral gain, independent evaluation, general recovery guarantee or AGI.
