"""Read-only saved-readiness audit; no external calls, solver or provider launch."""
import json, hashlib, subprocess, re
from pathlib import Path
from datetime import datetime, timezone
from continual.store import Store

def read(p): return json.loads(Path(p).read_text())
def git(*args): return subprocess.check_output(["git", *args])
def blob(b): return hashlib.sha1(f"blob {len(b)}\0".encode() + b).hexdigest()
def time(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))

base = "artifacts/unit-generation34-execution-route-readiness-reevaluation-v1/"
obs, wf, report = [read(base + n) for n in ["source-observations.json", "workflow-observations.json", "result.json"]]
protocol = read("artifacts/g34-readiness229-observation-precommit-v1.json")
assert [s["path"] for s in obs["sources"]] == protocol["read_only_sources"]["repository_files"]
for s in obs["sources"]:
    b = s["content"].encode()
    assert b == git("show", s["ref"] + ":" + s["path"]) and blob(b) == s["sha"]
old, new = json.loads(obs["sources"][0]["content"]), read("agi/USER_REQUEST_QUEUE.json")
assert (old["revision"], new["revision"]) == (1, 2)
assert old["policy"] == new["policy"] and old["schema_version"] == new["schema_version"]
allowed = {"last_reviewed_at", "review_history", "review_evidence_ref", "reevaluate_by", "reevaluate_on", "reason"}
changes = []
for a, b in zip(old["requests"], new["requests"], strict=True):
    changed = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    assert changed <= allowed and a["id"] == b["id"] and b["non_blocking"] and b["status"] == "open"
    assert a["previous_review"] == b["previous_review"]
    h = b["review_history"][-1]
    assert h["reviewed_at"] == a["last_reviewed_at"]
    for k in ["reason", "reevaluate_by", "reevaluate_on", "review_evidence_ref"]: assert h[k] == a[k]
    assert time(b["reevaluate_by"]) > time(b["last_reviewed_at"])
    changes.append({"id": a["id"], "changed_fields": sorted(changed), "history_preserved": True})
selector = wf["selector"]
selected = sorted([x for x in selector["catalog"] if re.search("live|provider|copilot|campaign", x["name"], re.I)], key=lambda x: x["path"])[:3]
assert selected == selector["selected"] and len(selected) == 2
for s in wf["observations"]:
    b = s["definition"].encode()
    assert blob(b) == s["sha"] and b == git("show", s["ref"] + ":" + s["path"])
assert obs["issue"]["projection"]["comments"] == 0
ledger = json.loads(next(s["content"] for s in obs["sources"] if s["path"] == "evidence/external_ledger.json"))
counts = {k: len(ledger.get(k, [])) for k in ["evaluation_requests", "challenges", "disclosures", "attestations"]}
assert counts == report["observations"]["external_ledger_counts"]
assert report["observations"]["registered_verifiers"]["current_count"] is None
assert report["observations"]["workflow_metadata_and_recent_runs"]["status"] == "unknown_connector_endpoint_rejected"
wid, nid = "invoke-6c0e673691e843ccd1d61709", "invoke-304f4d22b268aa6ee485a4a7"
run = ".continual/runs/run-work-recovery-gen9-durability-repair/"
rq, rs = [read(f".continual/work-model/invocations/{wid}/{name}.json") for name in ["request", "response"]]
for v, key, volatile in [(rq, "request_digest", "created_at"), (rs, "response_digest", "received_at")]:
    assert Store.stable_digest({k: x for k, x in v.items() if k not in [key, volatile]}, length=64) == v[key]
assert Store.stable_digest(rs["output"], length=64) == rs["output_digest"]
journal = read(run + "invocations/" + nid + ".json")
assert journal["status"] == "complete" and journal["output"] == rs["output"]
events = [json.loads(x) for x in Path(run + "events.jsonl").read_text().splitlines()]
done = [x for x in events if x.get("invocation_id") == nid and x["type"] == "invocation_completed"]
assert len(done) == 1 and Path(run + "local-learn/" + nid + "-execute.json").exists()
chronology = {}
for label, sha in [("protocol", "10efa181d8b27c5c3e1191998d17605fb4c27d96"), ("execute_request", "88547d54fed6a386248faca85016c74b3ea292d5"), ("review_result", "585e123b0aada5b49b11c271077b71fa2babaf46"), ("native_consume", "f4aa75b49e33c0a6f18dab98436c5c203bfdc8b1")]:
    chronology[label] = {"sha": sha, "committed_at": git("show", "-s", "--format=%cI", sha).decode().strip()}
first = min(s["observed_at"] for s in obs["sources"])
assert time(chronology["protocol"]["committed_at"]) < time(chronology["execute_request"]["committed_at"]) < time(first)
assert time(chronology["review_result"]["committed_at"]) < time(done[0]["at"]) < time(chronology["native_consume"]["committed_at"])
state, publications = read("agi/WORK_EXECUTION_STATE.json"), []
for key in ["generation34_readiness229_response_v1", "generation34_execute229_consume_v1"]:
    e = state[key]
    for f in e["main_readback"]: assert blob(git("show", e["merge_sha"] + ":" + f["path"])) == f["sha"]
    assert all(r["status"] == "completed" and r["conclusion"] == "success" for r in e["ci"]["workflow_runs"])
    publications.append({"pr": e["pr"], "merge_sha": e["merge_sha"], "files_verified": len(e["main_readback"]), "ci_runs": [r["id"] for r in e["ci"]["workflow_runs"]]})
print(json.dumps({"schema_version": 1, "audit_at": datetime.now(timezone.utc).isoformat(), "source_main_sha": git("rev-parse", "HEAD").decode().strip(), "audit_type": "read_only_saved_artifact_recomputation", "sources": [{k:s[k] for k in ["path", "sha", "observed_at"]} for s in obs["sources"]], "queue": {"old_revision": 1, "new_revision": 2, "changes": changes}, "workflow_selection": {"catalog_count": len(selector["catalog"]), "selected_paths": [s["path"] for s in selected], "definitions_match_exact_source": True}, "issue_comments": 0, "ledger_counts": counts, "chronology": chronology, "first_file_observation": first, "native_completed_event": done[0], "request_digest_valid": True, "response_digest_valid": True, "native_output_equals_response": True, "publication": publications, "independent_evaluation": False, "readiness_source_requeries": 0, "solver_replays": 0, "provider_launches": 0, "current_capacity": "unknown", "current_verifiers": "not_reobserved", "upper_objective_achieved": False}, ensure_ascii=False))
