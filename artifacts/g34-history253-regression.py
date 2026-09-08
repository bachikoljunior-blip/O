"""Three prospectively fixed synthetic representation pairs; no prompt comparison."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from continual.store import Store
from continual.work_session import WorkSessionError, verify_declared_repository_blob_bindings

def stamp():
    return datetime.now(timezone.utc).isoformat()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def blob(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()

def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()

def require(value, message):
    if not value:
        raise AssertionError(message)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    protocol_bytes = Path(args.protocol).read_bytes()
    p = json.loads(protocol_bytes)
    script = Path(__file__).resolve()
    validator = root / "src/continual/work_session.py"
    report = {"schema_version": 1, "unit_id": p["unit_id"], "attempt": 1,
              "started_at": stamp(), "protocol_sha256": sha(protocol_bytes),
              "script_sha256": sha(script.read_bytes()), "conditions": [],
              "claim_scope": "synthetic deterministic representation feasibility only",
              "prompt_overlay_efficacy_tested": False, "candidate_activated": False,
              "independent_evaluation": False, "upper_objective_achieved": False}
    # Exclusive reservation prevents accidentally overwriting/repeating this receipt.
    with Path(args.receipt).open("x", encoding="utf-8") as out:
        start = time.monotonic()
        before_validator = validator.read_bytes()
        try:
            require(report["script_sha256"] == p["script_sha256"], "script precommit mismatch")
            require(sha(before_validator) == p["validator_sha256"], "validator precommit mismatch")
            require(len(p["cases"]) == 3, "protocol must contain exactly three pairs")
            env = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "Synthetic Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "Synthetic Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                   "GIT_AUTHOR_DATE": p["git_date"], "GIT_COMMITTER_DATE": p["git_date"]}
            with tempfile.TemporaryDirectory(prefix="g34-history-fixture-", dir=root / "artifacts") as td:
                fixture = Path(td)
                git_calls = []
                def git(*argv):
                    require(time.monotonic() - start < 300, "wall limit exceeded")
                    r = subprocess.run(["git", "-C", str(fixture), *argv],
                                       env=env, capture_output=True, timeout=5)
                    git_calls.append({"argv": list(argv), "exit_code": r.returncode})
                    if r.returncode:
                        raise RuntimeError("fixture git failure: " + r.stderr.decode(errors="replace")[:600])
                    return r.stdout
                def current(value):
                    return verify_declared_repository_blob_bindings(fixture, value, verify_canonical_json=True)
                def historical(ref):
                    require(re.fullmatch("[0-9a-f]{40}", ref["commit"]) is not None, "malformed history commit")
                    require(ref["file"] == "episode.json", "unexpected history file")
                    spec = ref["commit"] + ":" + ref["file"]
                    actual = git("rev-parse", "--verify", spec).decode().strip()
                    require(actual == ref["historical_blob"], "historical blob identity mismatch")
                    raw = git("show", spec)
                    require(blob(raw) == ref["historical_blob"], "historical bytes mismatch")
                    return raw
                def condition(case_id, condition_id, fn):
                    item = {"case_id": case_id, "condition_id": condition_id, "started_at": stamp()}
                    try:
                        item["observations"] = fn()
                        item["verdict"] = "PASS"
                    except Exception as exc:
                        item["verdict"] = "FAIL"
                        item["error"] = type(exc).__name__ + ": " + str(exc)
                    item["finished_at"] = stamp()
                    report["conditions"].append(item)
                current_raw = p["current_json_utf8"].encode()
                old_snapshot = p["old_snapshot_utf8"].encode()
                new_snapshot = p["new_snapshot_utf8"].encode()
                prior = copy.deepcopy(p["prior_episode"])
                prior["evidence_refs"] = [{"path": "snapshot.json", "git_blob_sha": blob(old_snapshot)}]
                prior_raw = encode(prior)
                (fixture / "current.json").write_bytes(current_raw)
                (fixture / "snapshot.json").write_bytes(old_snapshot)
                (fixture / "episode.json").write_bytes(prior_raw)
                git("init", "-q")
                git("add", "current.json", "snapshot.json", "episode.json")
                git("commit", "-q", "-m", "fixed synthetic historical evidence")
                historical_commit = git("rev-parse", "HEAD").decode().strip()
                binding = {"path": "current.json", "git_blob_sha": blob(current_raw),
                           "sha256_canonical_json": Store.stable_digest(json.loads(current_raw), length=64)}
                def valid_current():
                    proof = current({"current": binding})
                    require(len(proof) >= 1, "current declaration not verified")
                    require((fixture / "episode.json").read_bytes() == prior_raw, "prior mutated")
                    return {"verified_current_declarations": len(proof), "prior_bytes_unchanged": True}
                def wrong_canonical():
                    wrong = {**binding, "sha256_canonical_json": "0" * 64}
                    try:
                        current({"current": wrong})
                    except WorkSessionError as exc:
                        require("canonical JSON digest mismatch" in str(exc), "wrong rejection reason")
                        return {"expected_rejection": type(exc).__name__ + ": " + str(exc)}
                    raise AssertionError("wrong canonical digest accepted")
                condition("current_binding_control", "valid_current_accept", valid_current)
                condition("current_binding_control", "wrong_canonical_reject", wrong_canonical)
                (fixture / "snapshot.json").write_bytes(new_snapshot)
                git("add", "snapshot.json")
                git("commit", "-q", "-m", "advance only synthetic snapshot")
                changed = git("diff", "--name-only", historical_commit, "HEAD").decode().splitlines()
                require(changed == ["snapshot.json"], "unexpected history change")
                ref = {"commit": historical_commit, "file": "episode.json", "historical_blob": blob(prior_raw)}
                def stale_embedded():
                    try:
                        current({"current": binding, "prior_history": prior})
                    except WorkSessionError as exc:
                        require("evidence blob identity mismatch" in str(exc), "wrong rejection reason")
                        return {"expected_rejection": type(exc).__name__ + ": " + str(exc)}
                    raise AssertionError("stale embedded snapshot accepted")
                def exact_history():
                    raw = historical(ref)
                    require(raw == prior_raw and json.loads(raw) == prior, "complete prior record differs")
                    require(json.loads(raw)["negative_findings"] == p["prior_episode"]["negative_findings"], "negative lost")
                    require(json.loads(raw)["unknowns"] == p["prior_episode"]["unknowns"], "unknown lost")
                    require(json.loads(raw)["observed_at"] == p["prior_episode"]["observed_at"], "time lost")
                    require((fixture / "episode.json").read_bytes() == prior_raw, "prior checkout changed")
                    verified = current({"current": binding, "historical_reference": ref})
                    require(len(verified) >= 1, "current binding not checked")
                    return {"historical_lookup": "separate explicit git verifier",
                            "historical_ref": ref, "complete_prior_bytes_equal": True,
                            "complete_prior_json_equal": True, "negative_unknown_time_retained": True,
                            "current_declarations_verified": len(verified)}
                condition("historical_snapshot_drift", "stale_embedded_reject", stale_embedded)
                condition("historical_snapshot_drift", "immutable_history_preserve", exact_history)
                def corrupt_history():
                    wrong = {**ref, "historical_blob": "0" * 40}
                    require(wrong["historical_blob"] != ref["historical_blob"], "corruption not distinct")
                    try:
                        historical(wrong)
                    except AssertionError as exc:
                        require(str(exc) == "historical blob identity mismatch", "wrong history rejection")
                        return {"changed_field": "historical_blob",
                                "expected_rejection": "AssertionError: " + str(exc),
                                "verifier": "separate explicit git verifier; not current validator"}
                    raise AssertionError("corrupt historical identity accepted")
                condition("historical_provenance_corruption", "wrong_history_blob_reject", corrupt_history)
                condition("historical_provenance_corruption", "valid_history_control", exact_history)
                report["synthetic_history"] = {"historical_commit": historical_commit,
                    "historical_episode_blob": blob(prior_raw), "prior_episode_utf8": prior_raw.decode(),
                    "advanced_commit": git("rev-parse", "HEAD").decode().strip(),
                    "changed_paths": changed, "git_calls": git_calls,
                    "temporary_repository_removed_after_run": True}
            require(validator.read_bytes() == before_validator, "production validator changed")
            require(time.monotonic() - start < 300, "wall limit exceeded")
            actual_ids = [x["condition_id"] for x in report["conditions"]]
            expected_ids = [y for x in p["cases"] for y in x["condition_ids"]]
            require(actual_ids == expected_ids, "case order or cardinality differs from precommit")
            report["validator_unchanged"] = True
            report["verdict"] = "PASS" if all(x["verdict"] == "PASS" for x in report["conditions"]) else "FAIL"
        except Exception as exc:
            report["verdict"] = "FAIL"
            report["setup_or_integrity_error"] = type(exc).__name__ + ": " + str(exc)
        report["finished_at"] = stamp()
        report["wall_seconds"] = time.monotonic() - start
        report["exit_code_to_return"] = 0 if report["verdict"] == "PASS" else 1
        out.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"verdict": report["verdict"], "conditions": len(report["conditions"]),
                      "exit_code_to_return": report["exit_code_to_return"], "receipt": args.receipt}))
    return report["exit_code_to_return"]

if __name__ == "__main__":
    raise SystemExit(main())
