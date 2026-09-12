"""Reproduce the fixed g35 author-release slice without models or upstream code.

Example:
  python scripts/analyze_recovery_author_release.py --source-dir <pinned files>
All outcome-dependent choices are frozen in the published selection protocol.
Only selected IDs, policies and repeat 1 are projected from the downloaded CSVs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_blob(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def boolean(value):
    if value not in ("True", "False"):
        raise ValueError("noncanonical released Boolean")
    return value == "True"


LABELS = {"healthy": True, "arithmetic_error": False, "hallucinated": False,
          "incomplete": False, "other": False}
POLICIES = ("none", "resample", "located")


def proportion(k, n):
    if not n:
        return {"numerator": k, "denominator": n, "estimate": None, "status": "UNKNOWN_ZERO_DENOMINATOR", "wilson_95": None}
    assert 0 <= k <= n
    z = 1.959963984540054
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"numerator": k, "denominator": n, "estimate": p, "status": "SUPPORTED_ON_STATED_DENOMINATOR", "wilson_95": [max(0.0, center - half), min(1.0, center + half)]}


def selected_rows(source_dir, protocol):
    ids = set(protocol["selected_dataset"]["episode_ids"])
    columns = {
        "organic_hallucination_cold.csv": ["episode_id", "label"],
        "verification_cold.csv": ["episode_id", "label", "total_consistency", "required_coverage"],
        "repair_policies.csv": ["episode_id", "rung", "rep", "label", "was_correct", "now_correct", "now_correct_checks_parser", "resumed_from", "steps_before", "steps_after", "model_calls", "checks_flag_before", "checks_flag_after"],
    }
    result = {}
    for filename, fields in columns.items():
        raw = (source_dir / filename).read_bytes()
        binding = next(x for x in protocol["sources"] if x["path"].endswith("/" + filename))
        assert hashlib.sha256(raw).hexdigest() == binding["raw_sha256"]
        assert git_blob(raw) == binding["blob_sha"]
        reader = csv.DictReader(raw.decode("utf-8").splitlines())
        assert set(fields) <= set(reader.fieldnames)
        rows = []
        for row in reader:
            if row["episode_id"] not in ids:
                continue
            if filename == "repair_policies.csv" and (row["rung"] not in POLICIES or row["rep"] != "1"):
                continue
            rows.append({k: row[k] for k in fields})
        result[filename] = rows
    return result


def analyze(rows, protocol):
    ids = protocol["selected_dataset"]["episode_ids"]
    assert len(ids) == 100 and len(set(ids)) == 100 and ids == sorted(ids, key=lambda x: x.encode("utf-8"))
    indexed = {}
    for filename, data in rows.items():
        by_id = defaultdict(list)
        for row in data:
            by_id[row["episode_id"]].append(row)
        indexed[filename] = by_id
    accounting = []
    for eid in ids:
        baseline_rows = indexed["organic_hallucination_cold.csv"][eid]
        verify_rows = indexed["verification_cold.csv"][eid]
        repairs = indexed["repair_policies.csv"][eid]
        baseline_label = baseline_rows[0]["label"] if len(baseline_rows) == 1 else None
        baseline = LABELS.get(baseline_label)
        verification_label = verify_rows[0]["label"] if len(verify_rows) == 1 else None
        errors, grade_conflicts = [], []
        if len(baseline_rows) != 1 or baseline is None:
            grade_conflicts.append("missing_duplicate_or_unknown_baseline_label")
        if len(verify_rows) != 1:
            grade_conflicts.append("missing_or_duplicate_verification_label")
        elif verification_label != baseline_label:
            grade_conflicts.append("baseline_verification_label_disagreement")
        records, outcomes, was_correct = {}, {}, {}
        key_counts = Counter(r["rung"] for r in repairs)
        for policy in POLICIES:
            selected = [r for r in repairs if r["rung"] == policy]
            if len(selected) != 1:
                errors.append(policy + (":missing" if not selected else ":duplicate"))
                continue
            record = selected[0]
            records[policy] = record
            try:
                outcomes[policy] = boolean(record["now_correct"])
                was_correct[policy] = boolean(record["was_correct"])
                boolean(record["checks_flag_before"])
                boolean(record["checks_flag_after"])
            except ValueError:
                errors.append(policy + ":malformed_boolean")
                continue
            if baseline is not None and was_correct[policy] != baseline:
                grade_conflicts.append(policy + ":baseline_success_disagreement")
            if record["label"] != baseline_label:
                grade_conflicts.append(policy + ":baseline_label_disagreement")
        if len(was_correct) == 3:
            if len(set(was_correct.values())) != 1:
                errors.append("policy_baseline_disagreement")
            if outcomes.get("none") != was_correct["none"]:
                errors.append("none_outcome_baseline_disagreement")
            if len({r["checks_flag_before"] for r in records.values()}) != 1:
                errors.append("pre_intervention_flag_disagreement")
            for field in ("steps_before", "resumed_from"):
                if records["resample"][field] != records["located"][field]:
                    errors.append(field + ":unmatched_control_intervention")
        paired = len(outcomes) == 3 and not errors
        accounting.append({"episode_id": eid, "baseline_table_rows": len(baseline_rows), "baseline_label": baseline_label, "baseline_success": baseline,
                           "verification_table_rows": len(verify_rows), "verification_label": verification_label,
                           "selected_policy_row_counts": {p: key_counts[p] for p in POLICIES},
                           "policy_outcomes": {p: outcomes.get(p) for p in POLICIES}, "policy_was_correct": {p: was_correct.get(p) for p in POLICIES},
                           "pairing_valid": paired, "pairing_errors": errors, "grade_conflicts": grade_conflicts,
                           "whole_population_compatible": not grade_conflicts, "missing_policy_results": [p for p in POLICIES if p not in outcomes]})
    assert [r["episode_id"] for r in accounting] == ids
    paired = [r for r in accounting if r["pairing_valid"]]
    n = len(paired)
    failures = sum(not r["policy_was_correct"]["none"] for r in paired)
    successes = n - failures
    metrics = {}
    for policy in POLICIES:
        correct = sum(r["policy_outcomes"][policy] for r in paired)
        recovered = sum(not r["policy_was_correct"]["none"] and r["policy_outcomes"][policy] for r in paired)
        broken = sum(r["policy_was_correct"]["none"] and not r["policy_outcomes"][policy] for r in paired)
        assert correct == successes + recovered - broken
        delta = Fraction(recovered - broken, n) if n else None
        if n and failures and successes:
            assert delta == Fraction(failures, n) * Fraction(recovered, failures) - Fraction(successes, n) * Fraction(broken, successes)
        metrics[policy] = {"success": proportion(correct, n), "recovery": proportion(recovered, failures), "disruption": proportion(broken, successes), "recovered_count": recovered, "disrupted_count": broken,
                           "net_success_change": float(delta) if delta is not None else None, "net_denominator": n}
    located_only = sum(r["policy_outcomes"]["located"] and not r["policy_outcomes"]["resample"] for r in paired)
    resample_only = sum(r["policy_outcomes"]["resample"] and not r["policy_outcomes"]["located"] for r in paired)
    discordant = located_only + resample_only
    mcnemar = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(located_only, resample_only) + 1)) / (2 ** discordant)) if discordant else 1.0
    baseline_known = [r for r in accounting if r["baseline_success"] is not None]
    compatible = len(baseline_known) == 100 and all(r["whole_population_compatible"] for r in accounting)
    population = {"nominal_denominator": 100, "compatible_baseline": compatible, "source_specific_baseline_failure_prevalence": proportion(sum(not r["baseline_success"] for r in baseline_known), len(baseline_known)),
                  "source_specific_scope": "Author baseline label export only; this is not a harmonized repair-grade prevalence when conflicts exist.", "policies": {}}
    for policy in POLICIES:
        if not compatible:
            population["policies"][policy] = {"r": None, "d": None, "net_success_change": None, "bounds": None, "status": "UNKNOWN_INCOMPATIBLE_BASELINE_GRADES"}
            continue
        F = sum(not r["baseline_success"] for r in accounting)
        S = 100 - F
        known = [r for r in accounting if r["pairing_valid"]]
        C = sum(not r["baseline_success"] and r["policy_outcomes"][policy] for r in known)
        B = sum(r["baseline_success"] and not r["policy_outcomes"][policy] for r in known)
        missing_f = F - sum(not r["baseline_success"] for r in known)
        missing_s = S - sum(r["baseline_success"] for r in known)
        population["policies"][policy] = {"r": C / F if F and not missing_f else None, "d": B / S if S and not missing_s else None,
            "net_success_change": (C - B) / 100 if not (missing_f + missing_s) else None,
            "missing_baseline_failures": missing_f, "missing_baseline_successes": missing_s,
            "bounds": {"recovery": [C / F, (C + missing_f) / F] if F else None, "disruption": [B / S, (B + missing_s) / S] if S else None,
                       "net_success_change": [(C - B - missing_s) / 100, (C + missing_f - B) / 100]},
            "status": "SUPPORTED_COMPLETE_PAIRS" if not (missing_f + missing_s) else "PARTIALLY_IDENTIFIED_MISSING_OUTCOMES"}
    summary = {"schema_version": 1, "protocol_digest": protocol["protocol_digest"], "source_commit": protocol["selected_dataset"]["release_commit"],
        "fixed_rep": "1", "policies": list(POLICIES), "selected_episode_count": 100, "accounting_rows": len(accounting), "complete_paired_episode_count": n,
        "unpaired_episode_count": 100 - n, "selected_source_rows": {k: len(v) for k, v in rows.items()},
        "selected_duplicate_keys": sum(max(0, n - 1) for r in accounting for n in r["selected_policy_row_counts"].values()),
        "baseline_duplicate_or_missing_ids": [r["episode_id"] for r in accounting if r["baseline_table_rows"] != 1],
        "grade_conflict_ids": [r["episode_id"] for r in accounting if r["grade_conflicts"]],
        "pairing_error_counts": dict(Counter(e for r in accounting for e in r["pairing_errors"])),
        "paired_subset": {"N": n, "baseline_failures": failures, "baseline_successes": successes, "baseline_failure_prevalence": proportion(failures, n), "policy_metrics": metrics},
        "primary_paired_policy_comparison": {"contrast": "located minus resample, rep1", "N": n, "located_only_success": located_only, "resample_only_success": resample_only,
            "success_rate_difference": (located_only - resample_only) / n if n else None, "exact_two_sided_mcnemar_p": mcnemar if n else None},
        "population": population,
        "claim_boundary": "Same-operator reanalysis of author-produced labels on a fixed nonrandom source slice. Not independent regrading, a new live trial, O capability evidence, a universal recovery benefit, or AGI.",
        "uncertainty": "Wilson intervals are model-based on the explicitly stated observed denominator. Zero successful baseline episodes cannot establish absence of disruption. Missing and incompatible grades are not imputed.",
        "provider_calls": 0, "upstream_writes": 0, "author_code_executed": False}
    return summary, accounting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, default=Path("artifacts/g35-recovery-comparison"))
    args = parser.parse_args()
    artifact = args.artifact_dir
    raw_protocol = (artifact / "selection-protocol.json").read_bytes()
    protocol = json.loads(raw_protocol)
    receipt = read_json(artifact / "protocol-publication-receipt.json")
    assert receipt["commit_sha"] == "37b2b54be402bda62e4ff8fecd9a3be85127e8da"
    binding = next(x for x in receipt["verified_files"] if x["path"].endswith("/selection-protocol.json"))
    assert binding["exact_utf8_readback"] and git_blob(raw_protocol) == binding["blob_sha"]
    assert receipt["protocol_digest"] == protocol["protocol_digest"]
    assert receipt["individual_outcomes_inspected"] is False
    rows = selected_rows(args.source_dir, protocol)
    summary, accounting = analyze(rows, protocol)
    summary["analysis_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    summary["protocol_publication_commit"] = receipt["commit_sha"]
    write_json(artifact / "selected-release-rows.json", rows)
    write_json(artifact / "episode-accounting.json", accounting)
    write_json(artifact / "numerical-result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
