from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(".").resolve()
REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ["GITHUB_REF_NAME"]
RUN_ID = "run-work-recovery-gen9-durability-repair"
EXPECTED_MAIN = "866c6711e2140141a12d0bcc2c9330156316ed1a"
EXPECTED_STATE_BLOB = "107bb0b15b85ef2aa68a6411d03083575f4f3d4b"
EXPECTED_SNAPSHOT_BLOB = "1b27068d7d66aeba3ba045113a813a6732ae0569"
EXPECTED_INBOX_BLOB = "89a98913189a88ddef9e7cc53ed5436b1c3379fa"
WORK_ID = "invoke-7bc390ea03a682f60389a491"
REQUEST_BLOB = "b627e6d5a8ad51e3c1d5d60da001ecb77a8f069c"
REQUEST_DIGEST = "20e50875915d8f5cba76f1eda9d0873945d9655631eb5d5eeee6e1feaa73f007"
NATIVE_ID = "invoke-130962a1be814974604e4148"
NATIVE_BLOB = "aabef732c86666e0e095f033d0fe4007bec25944"
UNIT_ID = "unit-generation29-matched-evidence-acquisition-loop-v1"
EXECUTOR = "current_chatgpt_work_session"
MODEL = "chatgpt-work-model-unverified"
WORKFLOW = ".github/workflows/gen29-matched-evidence-candidate-task-evaluate-v1.yml"
SCRIPT = ".github/scripts/gen29_matched_evidence_candidate_consume.py"


def run(*args: str, capture: bool = False) -> str:
    proc = subprocess.run(args, check=True, text=True, capture_output=capture)
    return proc.stdout if capture else ""


def git_blob(path: str | Path) -> str:
    return run("git", "hash-object", str(path), capture=True).strip()


run("git", "fetch", "origin", "main", "--no-tags")
runtime_main = run("git", "rev-parse", "origin/main", capture=True).strip()
if runtime_main != EXPECTED_MAIN:
    raise SystemExit(f"main mismatch {runtime_main}")
if run("git", "merge-base", EXPECTED_MAIN, "HEAD", capture=True).strip() != EXPECTED_MAIN:
    raise SystemExit("branch base mismatch")
observed = run("git", "diff", "--name-only", EXPECTED_MAIN, "HEAD", capture=True).splitlines()
if observed != [SCRIPT, WORKFLOW]:
    raise SystemExit(f"launch delta mismatch {observed!r}")

state_doc = json.loads(run("gh", "api", f"repos/{REPO}/contents/agi/WORK_EXECUTION_STATE.json?ref={runtime_main}", capture=True))
inbox_doc = json.loads(run("gh", "api", f"repos/{REPO}/contents/agi/USER_INPUT_INBOX.json?ref={runtime_main}", capture=True))
if state_doc["sha"] != EXPECTED_STATE_BLOB or inbox_doc["sha"] != EXPECTED_INBOX_BLOB:
    raise SystemExit("state or inbox blob mismatch")
state_bytes = base64.b64decode("".join(state_doc["content"].splitlines()))
inbox = json.loads(base64.b64decode("".join(inbox_doc["content"].splitlines())))
state_path = ROOT / "agi/WORK_EXECUTION_STATE.json"
state_path.write_bytes(state_bytes)
if git_blob(state_path) != EXPECTED_STATE_BLOB:
    raise SystemExit("decoded state blob mismatch")
snapshot_path = ROOT / ".continual" / "runs" / RUN_ID / "snapshot.json"
request_path = ROOT / ".continual" / "work-model" / "invocations" / WORK_ID / "request.json"
native_path = ROOT / ".continual" / "runs" / RUN_ID / "invocations" / f"{NATIVE_ID}.json"
if git_blob(snapshot_path) != EXPECTED_SNAPSHOT_BLOB or git_blob(request_path) != REQUEST_BLOB or git_blob(native_path) != NATIVE_BLOB:
    raise SystemExit("frozen lifecycle blob mismatch")

from continual.continuity_preflight import assert_work_resume_continuity_preflight
from continual.store import Store
from continual.work_session import pending_work_invocations, verified_work_invocation, verified_work_request, verify_work_invocations
from continual.work_source_observation import verify_work_source_observation

state = json.loads(state_path.read_text())
request = verified_work_request(ROOT, WORK_ID)
native = json.loads(native_path.read_text())
snapshot = json.loads(snapshot_path.read_text())
if state.get("execution_id") != "work-recovery-20260828T122724304Z-b9f52bccaba0472923908d65b1fb59c3":
    raise SystemExit("execution mismatch")
if state.get("lease_generation") != 29 or hashlib.sha256(state["fence_token"].encode()).hexdigest() != "c03cdd410193ed54352ee4fd5480cf5e4fccb77d5a1aca67809b7c3e4341890d":
    raise SystemExit("generation/fence mismatch")
if state.get("status") != "running" or state.get("resume_required") is not True:
    raise SystemExit("authority not resumable")
if state.get("active_component") != "generation29_matched_evidence_candidate_evaluate_pending_native_consume":
    raise SystemExit("active mismatch")
exact = state.get("exact_continuation", {})
expected_exact = {
    "pending_native_invocation_id": NATIVE_ID,
    "pending_native_invocation_blob_sha": NATIVE_BLOB,
    "pending_work_invocation_id": WORK_ID,
    "pending_request_blob_sha": REQUEST_BLOB,
    "pending_request_digest": REQUEST_DIGEST,
    "pending_response_blob_sha": None,
    "snapshot_revision": 145,
    "native_phase": "unit_pending",
    "pending_component": "candidate_evaluate",
    "target_component": "task_evaluate",
    "durability_status": "remote_main_readback_verified",
}
for key, value in expected_exact.items():
    if exact.get(key) != value:
        raise SystemExit(f"exact mismatch {key}: {exact.get(key)!r}")
if exact.get("verified_remote_readback") is not True:
    raise SystemExit("exact continuation is not verified")
durability = state.get("continuation_durability", {})
if durability.get("pending_work_invocation_id") != WORK_ID or durability.get("pending_native_invocation_id") != NATIVE_ID or durability.get("verified_remote_readback") is not True:
    raise SystemExit("continuation durability mismatch")
if inbox.get("revision") != 40 or state.get("user_input_inbox", {}).get("highest_acknowledged_revision") != 40:
    raise SystemExit("inbox mismatch")
if snapshot.get("revision") != 145 or snapshot.get("phase") != "unit_pending" or snapshot.get("current_component") != "task_evaluate" or snapshot.get("current_unit") != UNIT_ID:
    raise SystemExit("snapshot mismatch")
if request.get("component") != "candidate_evaluate" or request.get("request_digest") != REQUEST_DIGEST:
    raise SystemExit("request mismatch")
payload = request.get("payload", {})
candidates = payload.get("candidate_index", {}).get("candidates", [])
if payload.get("mode") != "pre-application" or payload.get("target_component") != "task_evaluate" or len(candidates) != 1 or candidates[0].get("candidate_id") != "candidate-task-evaluate-unit-verdict-v1":
    raise SystemExit("Candidate payload mismatch")
if request_path.with_name("response.json").exists():
    raise SystemExit("Candidate already answered")
if native.get("status") != "awaiting_work_model" or native.get("component") != "candidate_evaluate" or native.get("work_invocation_id") != WORK_ID or native.get("work_request_digest") != REQUEST_DIGEST:
    raise SystemExit("native mismatch")
pending = pending_work_invocations(ROOT, run_id=RUN_ID)
if len(pending) != 1 or pending[0].get("invocation_id") != WORK_ID:
    raise SystemExit("not unique pending")
before = verify_work_invocations(ROOT, run_id=RUN_ID)
for key, value in {"valid": True, "requests": 161, "responses": 160, "pending": 1, "native_completed": 154, "native_fragments": 154, "native_local_learn": 140}.items():
    if before.get(key) != value:
        raise SystemExit(f"before count mismatch {key}: {before.get(key)!r}")

prep = json.loads(run("continual", "--root", ".", "work-source-observation-prepare", RUN_ID, "--state-blob-sha", EXPECTED_STATE_BLOB, "--expected-commit-sha", EXPECTED_MAIN, "--model-identity", MODEL, capture=True))
projection = {
    "status": state["status"],
    "owner_kind": state["owner_kind"],
    "execution_id": state["execution_id"],
    "lease_generation": state["lease_generation"],
    "fence_token_digest": Store(ROOT).stable_digest(state["fence_token"], length=64),
    "heartbeat_at": state["heartbeat_at"],
}
projection_path = Path("/tmp/work-projection.json")
projection_path.write_text(json.dumps(projection, sort_keys=True) + "\n")
observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
run("continual", "--root", ".", "work-source-observation-record", RUN_ID, prep["observation_id"], "--request-digest", prep["request_digest"], "--commit-sha", EXPECTED_MAIN, "--blob-sha", EXPECTED_STATE_BLOB, "--projection", str(projection_path), "--observed-at", observed_at, "--executor-binding", EXECUTOR, "--model-identity", MODEL)
run("git", "fetch", "origin", "main", "--no-tags")
if run("git", "rev-parse", "origin/main", capture=True).strip() != EXPECTED_MAIN:
    raise SystemExit("main moved before mutation")
current_state_doc = json.loads(run("gh", "api", f"repos/{REPO}/contents/agi/WORK_EXECUTION_STATE.json?ref={EXPECTED_MAIN}", capture=True))
if current_state_doc["sha"] != EXPECTED_STATE_BLOB:
    raise SystemExit("state moved before mutation")
now = Store(ROOT).utc_now()
verify_work_source_observation(ROOT, run_id=RUN_ID, state=state, state_blob_sha=EXPECTED_STATE_BLOB, now=now)
continuity = assert_work_resume_continuity_preflight(ROOT, run_id=RUN_ID, executor_binding=EXECUTOR, model_identity=MODEL)
if continuity.get("resume_authorized") is not True:
    raise SystemExit("continuity denied")

output = {
    "fragment": {
        "candidate_activation": False,
        "claim_boundary": "One-call scoped Task Evaluate overlay trial only; no Candidate activation, promotion, production claim, generalized reliability claim, AGI claim, upper-objective claim, user-level completion, or monitor completion.",
        "component": "candidate_evaluate",
        "continuation": "Independently Task Evaluate the exact merged matched-evidence acquisition guard with separate bounded-unit and unchanged upper-objective verdicts, preserve all fail-closed controls and negative evidence, then continue the post-result lifecycle.",
        "mode": "pre-application",
        "selected_strategy": "TRIAL_CANDIDATE candidate-task-evaluate-unit-verdict-v1 as an additive overlay on the unchanged active Task Evaluate prompt",
        "unit_id": UNIT_ID,
    },
    "local_learn": {
        "candidates": [],
        "decision": "NO_CHANGE",
        "observations": [
            "The sole indexed Candidate targets the exact upcoming Task Evaluate component and incremental task-evaluation scope.",
            "Prior evidence proposes activation but is not VERIFIED_FOR_SCOPE, so only a one-call additive trial is justified.",
            "Dual-verdict discipline prevents a bounded routing-mechanism PASS from being conflated with the unchanged AGI upper objective.",
        ],
    },
    "result": {
        "activation": False,
        "active_version": "prompts/task_evaluate.md",
        "agi_claim_supported": False,
        "applicability": "Directly applicable only to this exact Task Evaluate call because it adds a separately falsifiable bounded-unit verdict while preserving the unchanged original-task verdict.",
        "baseline_need": "Retain the active Task Evaluate contract. The overlay may add unit_verdict PASS, FAIL, or UNCERTAIN but cannot relax or replace the upper-objective verdict.",
        "candidate_activation": False,
        "candidate_id": "candidate-task-evaluate-unit-verdict-v1",
        "candidate_version": ".continual/candidates/candidate-task-evaluate-unit-verdict-v1/prompt.md",
        "conflicts": [],
        "cost_dimensions": {
            "external": "No external dispatch, deployment, production routing, Candidate promotion, or unrelated mutation.",
            "failure_risk": "Verdict conflation or claim widening requires rollback.",
            "latency": "One bounded overlay call only.",
            "quality": "Require exact trigger, controls, idempotency, recurrence, authority, CI, merge, and readback evidence.",
            "token": "Record only directly measured usage.",
            "tool": "Candidate selection authorizes no additional external tool action.",
        },
        "decision": "TRIAL_CANDIDATE",
        "dependencies": [],
        "evaluated_candidate_id": "candidate-task-evaluate-unit-verdict-v1",
        "evidence_to_collect": [
            "exact merged guard and engine blobs with protected exact-head CI and expected-head merge",
            "typed sole-cause positive schedule and every fail-closed negative control",
            "deterministic idempotency and ordinary Root domain-switch recurrence",
            "exact source-clock, generation, fence, request, response, and receipt bindings",
            "separate original-task and bounded-unit verdicts with negative evidence, unmet conditions, and repair information",
            "proof that bounded-unit PASS does not imply Candidate activation, generalized autonomy, AGI, or user completion",
        ],
        "exact_scope": "continual/incremental-task-evaluation/matched-evidence-acquisition-loop-v1",
        "expected_scope": "continual/incremental-task-evaluation",
        "global_activation_authorized": False,
        "reason": "The Candidate targets the exact component and scope without dependency or conflict, but remains unverified for this unit. A one-call TRIAL_CANDIDATE tests additive dual-verdict discipline without promotion.",
        "regression_checks": [
            "original verdict evaluates the unchanged AGI upper objective",
            "unit_verdict covers only the exact matched-evidence guard and declared controls",
            "unit PASS cannot imply upper-objective PASS or remove unmet AGI conditions",
            "missing trigger, control, idempotency, recurrence, authority, CI, merge, or readback evidence prevents unit PASS",
            "negative evidence stays explicit",
            "Candidate remains unactivated and no external effect is authorized",
        ],
        "rollback": "If either verdict is omitted, scopes are conflated, a claim widens, negative evidence is suppressed, a failure condition weakens, or the active baseline changes, discard the overlay result, retain the baseline and negative evidence, and leave the Candidate unactivated.",
        "scope": "continual/incremental-task-evaluation/matched-evidence-acquisition-loop-v1",
        "status": "candidate",
        "upper_objective_achieved": False,
        "user_level_objective_met": False,
    },
}
output_path = Path("/tmp/candidate-output.json")
output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
run("continual", "--root", ".", "work-submit", WORK_ID, "--response", str(output_path), "--executor-binding", EXECUTOR, "--model-identity", MODEL)
run("continual", "--root", ".", "work-resume", RUN_ID, "--max-steps", "2", "--executor-binding", EXECUTOR, "--model-identity", MODEL)

after = verify_work_invocations(ROOT, run_id=RUN_ID)
expected_after = {"valid": True, "requests": 162, "responses": 161, "pending": 1, "native_completed": 155, "native_fragments": 155, "native_local_learn": 141}
for key, value in expected_after.items():
    if after.get(key) != value:
        raise SystemExit(f"after count mismatch {key}: {after.get(key)!r}")
verified = verified_work_invocation(ROOT, WORK_ID)
if verified.get("output") != output:
    raise SystemExit("persisted output mismatch")
candidate_native = json.loads(native_path.read_text())
if candidate_native.get("status") != "complete" or candidate_native.get("output") != output:
    raise SystemExit("Candidate native mismatch")
if git_blob(snapshot_path) != EXPECTED_SNAPSHOT_BLOB:
    raise SystemExit("snapshot changed")
pending = pending_work_invocations(ROOT, run_id=RUN_ID)
if len(pending) != 1 or pending[0].get("component") != "task_evaluate":
    raise SystemExit("Task Evaluate not unique")
next_request = pending[0]
next_payload = next_request.get("payload", {})
candidate = next_payload.get("candidate", {})
if candidate.get("candidate_id") != "candidate-task-evaluate-unit-verdict-v1" or candidate.get("prompt_mode") != "overlay":
    raise SystemExit("overlay mismatch")
if next_payload.get("preflight_selection") != output["result"] or next_payload.get("execution_unit") != UNIT_ID:
    raise SystemExit("selection or unit mismatch")
if "candidate-task-evaluate-unit-verdict-v1" not in next_request.get("prompt_path", ""):
    raise SystemExit("effective prompt mismatch")
next_work_id = next_request["invocation_id"]
next_request_rel = f".continual/work-model/invocations/{next_work_id}/request.json"
awaiting = []
for path in sorted((ROOT / ".continual" / "runs" / RUN_ID / "invocations").glob("*.json")):
    value = json.loads(path.read_text())
    if value.get("status") == "awaiting_work_model":
        awaiting.append((path, value))
if len(awaiting) != 1:
    raise SystemExit("awaiting native count mismatch")
next_native_path, next_native = awaiting[0]
if next_native.get("component") != "task_evaluate" or next_native.get("work_invocation_id") != next_work_id or next_native.get("work_request_digest") != next_request.get("request_digest"):
    raise SystemExit("next native mismatch")
response_rel = f".continual/work-model/invocations/{WORK_ID}/response.json"
local_learn_rel = f".continual/runs/{RUN_ID}/local-learn/{NATIVE_ID}-candidate_evaluate.json"
summary = {
    "candidate_native_invocation_id": NATIVE_ID,
    "candidate_work_invocation_id": WORK_ID,
    "candidate_response_ref": response_rel,
    "candidate_response_digest": verified["response"]["response_digest"],
    "candidate_response_blob_sha": git_blob(response_rel),
    "candidate_native_blob_sha": git_blob(native_path),
    "candidate_fragment_ref": candidate_native["fragment_ref"],
    "candidate_fragment_blob_sha": git_blob(candidate_native["fragment_ref"]),
    "candidate_local_learn_ref": local_learn_rel,
    "candidate_local_learn_blob_sha": git_blob(local_learn_rel),
    "pending_native_invocation_id": next_native["invocation_id"],
    "pending_native_invocation_ref": next_native_path.relative_to(ROOT).as_posix(),
    "pending_native_invocation_blob_sha": git_blob(next_native_path),
    "pending_work_invocation_id": next_work_id,
    "pending_request_ref": next_request_rel,
    "pending_request_digest": next_request["request_digest"],
    "pending_request_blob_sha": git_blob(next_request_rel),
    "pending_component": "task_evaluate",
    "snapshot_revision": 145,
    "snapshot_phase": "unit_pending",
    "snapshot_blob_sha": EXPECTED_SNAPSHOT_BLOB,
    "verification": expected_after,
}
receipt = {
    "schema_version": 1,
    "record_type": "generation29_matched_evidence_candidate_consume_receipt",
    "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "authority": {"execution_id": state["execution_id"], "lease_generation": 29, "fence_token_digest": "c03cdd410193ed54352ee4fd5480cf5e4fccb77d5a1aca67809b7c3e4341890d"},
    "source_main_sha": EXPECTED_MAIN,
    "source_state_blob_sha": EXPECTED_STATE_BLOB,
    "idempotency_key": "o-work-gen29:consume-candidate-invoke-7bc390ea03a682f60389a491:freeze-task-evaluate:v1",
    "completed_effect": summary,
    "request_recreated": False,
    "candidate_reinvoked": False,
    "candidate_activation": False,
    "resume_required": True,
    "upper_objective_achieved": False,
    "agi_achieved": False,
    "next_action": "Exact-head validate and merge this Candidate response plus Task Evaluate freeze, bind state by two-phase CAS/readback, then answer only the exact pending Task Evaluate request once and continue.",
}
receipt_path = ROOT / ".continual" / "runs" / RUN_ID / "recovery-receipts" / "gen29-matched-evidence-candidate-consume-task-evaluate-freeze-v1.json"
receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

run("git", "checkout", "--", "agi/WORK_EXECUTION_STATE.json")
if git_blob(state_path) != EXPECTED_STATE_BLOB or git_blob(snapshot_path) != EXPECTED_SNAPSHOT_BLOB:
    raise SystemExit("state or snapshot drift before push")
for line in run("git", "status", "--porcelain", capture=True).splitlines():
    path = line[3:].split(" -> ")[-1]
    if path in {WORKFLOW, SCRIPT} or path.startswith(f".continual/runs/{RUN_ID}/") or path.startswith(".continual/work-model/invocations/") or path.startswith("src/continual_chatgpt.egg-info/") or "__pycache__/" in path:
        continue
    raise SystemExit(f"unexpected mutation path {path}")
required = [
    f".continual/runs/{RUN_ID}/events.jsonl",
    f".continual/runs/{RUN_ID}/invocations/{NATIVE_ID}.json",
    response_rel,
    summary["candidate_fragment_ref"],
    summary["candidate_local_learn_ref"],
    summary["pending_native_invocation_ref"],
    summary["pending_request_ref"],
    receipt_path.relative_to(ROOT).as_posix(),
]
for path in required:
    if not (ROOT / path).is_file():
        raise SystemExit(f"required path missing {path}")
run("git", "add", "-f", "--", ".continual")
run("git", "rm", WORKFLOW, SCRIPT)
run("git", "config", "user.name", "github-actions[bot]")
run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
run("git", "commit", "-m", "[native-matched-evidence-candidate-result] consume Candidate and freeze Task Evaluate")
run("git", "push", "origin", f"HEAD:{BRANCH}")
