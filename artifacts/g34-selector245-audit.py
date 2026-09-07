"""Read-only audit of the frozen Execute241 selector evidence; no live search."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from continual.store import Store

ROOT = Path(__file__).resolve().parents[1]
RUN = ".continual/runs/run-work-recovery-gen9-durability-repair"
WORK = ".continual/work-model/invocations/invoke-ab18054106a14fd4e80e2eb4"
ART = "artifacts/unit-generation34-baseline-first-public-regression-v1"
TASK = ".continual/work-model/invocations/invoke-a0551e9297ec325275767bd8/request.json"
NATIVE = RUN + "/invocations/invoke-7413e7f86b5a5d80048301af.json"
checks = []
started = datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads((ROOT / path).read_text())


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=20)
    if r.returncode:
        raise RuntimeError("read-only git failed: " + r.stderr.decode(errors="replace"))
    return r.stdout


def check(name, condition, evidence):
    checks.append({"name": name, "pass": bool(condition), "evidence": evidence})


def digest(value):
    return Store.stable_digest(value, length=64)


def valid_digest(value, field, volatile=()):
    body = {k: v for k, v in value.items() if k != field and k not in volatile}
    return digest(body) == value[field]


try:
    task = read(TASK)
    unit = task["payload"]["execution_unit"]
    pub = unit["publication_evidence"]
    request, response = read(WORK + "/request.json"), read(WORK + "/response.json")
    protocol = request["payload"]["execution_unit"]["selection_protocol"]
    original = read(RUN + "/execution-units/unit-generation34-baseline-first-public-regression-v1.json")
    ledger, output = read(ART + "/selection-ledger.json"), read(ART + "/result.json")
    responses = [read(ART + "/search-" + str(i) + ".json") for i in (1, 2, 3)]
    order = ["pallets/click", "python-attrs/attrs", "dateutil/dateutil"]
    check("frozen_selector_unchanged", protocol == original["selection_protocol"] == ledger["selector"],
          {"repository_order": protocol["repository_order"], "query_template": protocol["query_template"]})
    check("search_budget_and_repository_order",
          len(ledger["searches"]) == 3 and [s["repository"] for s in ledger["searches"]] == order,
          {"search_count": len(ledger["searches"])})
    observed_candidates = []
    previous_end = None
    for i, (repo, saved, result) in enumerate(zip(order, ledger["searches"], responses), 1):
        q = "repo:" + repo + " is:issue is:open label:bug created:>=2026-01-01"
        url = urlsplit(saved["url"])
        params = parse_qs(url.query)
        query_ok = url.scheme == "https" and url.netloc == "api.github.com" and url.path == "/search/issues"
        query_ok = query_ok and params == {"q": [q], "sort": ["created"], "order": ["asc"], "per_page": ["3"]}
        check("query_" + str(i), query_ok and saved["query"] == q and not saved["isError"],
              {"url": saved["url"], "query": saved["query"]})
        begin, end = datetime.fromisoformat(saved["started_at"]), datetime.fromisoformat(saved["ended_at"])
        check("observation_order_" + str(i), begin <= end and (previous_end is None or begin >= previous_end),
              {"start": saved["started_at"], "end": saved["ended_at"]})
        previous_end = end
        check("response_count_" + str(i), result["incomplete_results"] is False and result["total_count"] == [0, 0, 1][i - 1]
              and len(result["items"]) == [0, 0, 1][i - 1] and saved["total_count"] == result["total_count"],
              {"total_count": result["total_count"], "returned_items": len(result["items"])})
        for item in result["items"]:
            key = {"repository": repo, "number": item["number"]}
            if key not in observed_candidates:
                observed_candidates.append(key)
    check("candidate_order", observed_candidates == ledger["candidate_order"] == [{"repository": "dateutil/dateutil", "number": 1472}],
          observed_candidates)
    candidate = responses[2]["items"][0]
    eligibility = ledger["candidate_eligibility"][0]
    check("contamination_exclusion", "When I remove that part, it works fine for me." in candidate["body"]
          and eligibility["decision"] == "EXCLUDED"
          and eligibility["contamination"]["solution_direction_exposure"] is True,
          {"issue_url": candidate["html_url"], "exclusion": eligibility["reason_codes"]})
    check("recorded_no_followup_or_patch", all(eligibility["contamination"][k] is False for k in
          ("linked_source_opened", "issue_comments_opened", "fixing_pr_or_patch_opened"))
          and all(eligibility[k] is False for k in ("upstream_source_acquired", "baseline_attempted", "healthy_control_attempted", "patch_attempted")),
          "Saved-record claim only; cannot establish absence of all unrecorded process effects.")
    result = output["result"]
    check("negative_outcome_not_upgraded", result["outcome"] == ledger["outcome"] == "NOT_EVALUABLE"
          and result["baseline_exit_code"] is None and result["selected_target"] is None
          and result["repair_iterations"] == ledger["repair_iterations"] == 0
          and not result["repair_success"] and not result["agi_claim_supported"]
          and not ledger["target_locked"] and ledger["reproduction_attempt_count"] == 0,
          {"outcome": result["outcome"], "baseline_exit_code": result["baseline_exit_code"], "repair_iterations": result["repair_iterations"]})
    check("work_digests", valid_digest(request, "request_digest", ("created_at",))
          and digest(request["payload"]) == request["payload_digest"]
          and valid_digest(response, "response_digest", ("received_at",))
          and digest(response["output"]) == response["output_digest"]
          and response["request_digest"] == request["request_digest"], {"request_digest": request["request_digest"], "response_digest": response["response_digest"]})
    native = read(NATIVE)
    events = [json.loads(line) for line in git("show", pub["native_merge_sha"] + ":" + RUN + "/events.jsonl").decode().splitlines()]
    hits = [e for e in events if e.get("type") == "invocation_completed" and e.get("invocation_id") == "invoke-7413e7f86b5a5d80048301af"]
    check("once_only_native_output", native["status"] == "complete" and native["output"] == response["output"] == output
          and len(hits) == 1, {"completed_at": native["completed_at"], "matching_events": hits})
    local = read(RUN + "/local-learn/invoke-7413e7f86b5a5d80048301af-execute.json")
    check("local_learn_retained", local == output["local_learn"], {"local_learn_present": bool(local)})
    for phase in ("response", "native"):
        commit, head = pub[phase + "_merge_sha"], pub[phase + "_head_sha"]
        matches = []
        for f in pub[phase + "_files"]:
            actual = git("rev-parse", commit + ":" + f["path"]).decode().strip()
            body = git("show", commit + ":" + f["path"])
            computed = hashlib.sha1(("blob " + str(len(body)) + "\0").encode() + body).hexdigest()
            matches.append({"path": f["path"], "sha": actual, "pass": actual == f["sha"] == computed})
        parents = git("show", "-s", "--format=%P", commit).decode().split()
        check(phase + "_immutable_publication", all(m["pass"] for m in matches) and head in parents,
              {"merge_sha": commit, "exact_head": head, "files": matches})
    first_search = datetime.fromisoformat(ledger["searches"][0]["started_at"])
    for name, commit in (("root", ledger["pre_observation_publication"]["root_merge_sha"]),
                         ("execute_request", ledger["pre_observation_publication"]["execute_request_merge_sha"])):
        at = git("show", "-s", "--format=%cI", commit).decode().strip()
        check(name + "_published_before_search", datetime.fromisoformat(at) < first_search, {"commit": commit, "committed_at": at})
    ci = read("artifacts/g34-selector245-ci-readback.json")["observations"]
    expected = {rid: pub[phase + "_head_sha"] for phase in ("response", "native") for rid in pub[phase + "_ci_run_ids"]}
    check("exact_existing_ci", len(ci) == 4 and {o["run"]["id"] for o in ci} == set(expected)
          and all(o["run"]["status"] == "completed" and o["run"]["conclusion"] == "success"
                  and o["run"]["head_sha"] == expected[o["run"]["id"]] for o in ci),
          [{"id": o["run"]["id"], "head_sha": o["run"]["head_sha"], "conclusion": o["run"]["conclusion"]} for o in ci])
except Exception as exc:
    check("audit_exception", False, {"type": type(exc).__name__, "message": str(exc)})

report = {
    "schema_version": 1, "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
    "audit": "selector241-frozen-evidence-read-only", "checks": checks,
    "all_checks_passed": bool(checks) and all(c["pass"] for c in checks),
    "scope": "Evidence fidelity only, not a reproduced defect, repair success, independent evaluation or AGI.",
    "live_issue_search_performed": False, "upstream_patch_performed": False,
    "upper_objective_achieved": False
}
print(json.dumps(report, indent=2))
sys.exit(0 if report["all_checks_passed"] else 1)

