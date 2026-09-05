"""Inactive, process-local performance experiment; no production edits.

ABBA ordering uses the existing five-round deterministic regression on fresh
fixtures. It is an engineering comparison, not held-out capability evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import signal
import statistics
import sys
import tempfile
import time
from typing import Mapping
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "O"
sys.path.insert(0, str(ROOT / "src"))
from continual.engine import Engine
from continual.learning_engine import LearningEnabledEngine
from continual.learned_tools import LearnedToolError
from agi.execution_learning import EXECUTION_SCOPE, run_learned_tool_execution_campaign
from agi.materialized_runtime_replay import _mechanical_engine

TEST_NAME = "test_five_crash_recovered_generated_rounds_remain_functionally_replayable"
TEST_FILE = ROOT / "tests/test_generated_cross_round_five_round_retention.py"
spec = importlib.util.spec_from_file_location("paired_existing_regression", TEST_FILE)
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
BASELINE = LearningEnabledEngine._invoke
DESCRIPTORS = LearningEnabledEngine._scope_tool_descriptors

def candidate_invoke(self, run_id, component, payload):
    enriched = deepcopy(payload)
    if component == "root":
        enriched["verified_learned_tools"] = list(self._verified_tool_catalog())
    if component == "execute":
        unit = enriched.get("execution_unit")
        call = unit.get("learned_tool_call") if isinstance(unit, Mapping) else None
        if call is not None:
            if not isinstance(call, Mapping):
                raise LearnedToolError("learned_tool_call must be an object")
            return self._mechanical_learned_tool_call(run_id, enriched, call)
        scope = unit.get("scope") if isinstance(unit, Mapping) else None
        if isinstance(scope, str) and scope.strip():
            enriched["verified_learned_tools"] = list(self._scope_tool_descriptors(scope).values())
    return Engine._invoke(self, run_id, component, enriched)

def fixture(path):
    shutil.copytree(ROOT / "prompts", path / "prompts")
    shutil.copytree(ROOT / ".continual/system", path / ".continual/system")
    (path / ".continual/candidates").mkdir(parents=True)
    (path / ".continual/candidates/index.json").write_text('{"schema_version":2,"candidates":[]}\n')

def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def save(name, value):
    name = name.replace("mechanical-dispatch-comparison", "mechanical-dispatch-comparison-v2")
    (HERE / name).write_text(json.dumps(value, indent=2) + "\n")

def deadline(signum, frame):
    raise TimeoutError("paired regression exceeded 600-second per-attempt budget")

protocol = {
    "schema_version": 1, "record_type": "inactive_mechanical_dispatch_comparison_precommit",
    "protocol_revision": 2,
    "prior_setup_failure_ref": "mechanical-dispatch-comparison-v1-setup-failure.json",
    "recorded_at": now(), "source_head": "d867e5258d686fe93571cf086db804e9c2df8967",
    "candidate_id": "candidate-oengine-mechanical-descriptor-elision-v1",
    "candidate_status": "inactive_process_local_variant", "active_engine_changed": False,
    "mechanism": "Skip descriptor enrichment that is unused by explicit mechanical calls; preserve the mechanical call descriptor check and registry.apply re-verification.",
    "order": ["baseline", "candidate", "candidate", "baseline"],
    "test": TEST_NAME, "per_attempt_budget_seconds": 600,
    "decision_rule": "Eligible for a code review only if all four existing regressions and both variants' journal-reuse, wrong-scope, scope-mismatch, and revoked-scope controls pass; candidate descriptor calls must fall and median measured wall time must improve by at least 10 percent. Two samples per variant are diagnostic, not a general performance guarantee.",
    "source_test_sha256": hashlib.sha256(TEST_FILE.read_bytes()).hexdigest(),
    "source_engine_sha256": hashlib.sha256((ROOT / "src/continual/learning_engine.py").read_bytes()).hexdigest(),
    "experiment_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "claim_boundary": "Deterministic internal engineering evidence, no new model, held-out task, AGI claim, Candidate activation, or frozen Work replay.",
}
save("mechanical-dispatch-comparison-precommit.json", protocol)
print(json.dumps({"precommitted": True, "recorded_at": protocol["recorded_at"], "order": protocol["order"]}), flush=True)

controls = []
for variant, method in (("baseline", BASELINE), ("candidate", candidate_invoke)):
    with tempfile.TemporaryDirectory(prefix=f"dispatch-controls-{variant}-", dir=HERE) as directory:
        path = Path(directory)
        fixture(path)
        report = run_learned_tool_execution_campaign(path, "dispatch-equivalence-controls")
        with patch.object(LearningEnabledEngine, "_invoke", method):
            engine = _mechanical_engine(path)
            run_id = "run-dispatch-controls"
            engine.store.run_dir(run_id).mkdir(parents=True)
            engine.store.atomic_json(engine.store.run_dir(run_id) / "snapshot.json", {"revision": 0, "status": "continue", "phase": "unit_pending"})
            unit = {"goal": "apply retained transform", "scope": EXECUTION_SCOPE, "learned_tool_call": {"tool_id": report["tool_id"], "input": "FreshInput"}}
            payload = {"snapshot": {"revision": 0}, "execution_unit": unit}
            first = engine._invoke(run_id, "execute", payload)
            second = engine._invoke(run_id, "execute", payload)
            assert first == second and first["result"]["output"] == "tupnIhserF#tupnIhserF#"
            assert len(list((engine.store.run_dir(run_id) / "invocations").glob("invoke-learned-tool-*.json"))) == 1
            denials = []
            wrong = deepcopy(payload); wrong["execution_unit"]["scope"] = "agi/wrong-scope"
            mismatch = deepcopy(payload); mismatch["execution_unit"]["learned_tool_call"]["scope"] = "agi/wrong-scope"
            for label, request in (("wrong_scope", wrong), ("scope_mismatch", mismatch)):
                try:
                    engine._invoke(run_id, "execute", request)
                except (ValueError, RuntimeError):
                    denials.append(label)
                else:
                    raise AssertionError(f"{variant} permitted {label}")
            candidate_path = path / ".continual/candidates" / report["candidate_id"] / "candidate.json"
            candidate = json.loads(candidate_path.read_text())
            candidate["scope_states"][EXECUTION_SCOPE] = "REJECTED_FOR_SCOPE"
            candidate_path.write_text(json.dumps(candidate) + "\n")
            try:
                engine._invoke(run_id, "execute", payload)
            except (ValueError, RuntimeError):
                denials.append("revoked_scope_cannot_reuse_journal")
            else:
                raise AssertionError(f"{variant} reused a revoked learned tool")
            controls.append({"variant": variant, "journal_reused_once": True, "denials": denials, "status": "passed"})
save("mechanical-dispatch-comparison-controls.json", controls)
print(json.dumps({"controls": controls}), flush=True)

signal.signal(signal.SIGALRM, deadline)
attempts = []
for index, variant in enumerate(protocol["order"]):
    count = {"descriptor_calls": 0}
    def count_descriptors(self, scope):
        count["descriptor_calls"] += 1
        return DESCRIPTORS(self, scope)
    with tempfile.TemporaryDirectory(prefix=f"dispatch-abba-{index}-", dir=HERE) as directory:
        path = Path(directory); fixture(path)
        started_at = now(); started = time.monotonic()
        signal.alarm(600)
        try:
            with patch.object(LearningEnabledEngine, "_invoke", BASELINE if variant == "baseline" else candidate_invoke), patch.object(LearningEnabledEngine, "_scope_tool_descriptors", count_descriptors):
                getattr(tests, TEST_NAME)(path)
            status = "passed"
        finally:
            signal.alarm(0)
        attempt = {"index": index, "variant": variant, "status": status, "started_at": started_at, "completed_at": now(), "elapsed_seconds": time.monotonic() - started, **count}
        attempts.append(attempt)
        save("mechanical-dispatch-comparison-attempts.json", attempts)
        print(json.dumps(attempt), flush=True)
baseline = [item for item in attempts if item["variant"] == "baseline"]
candidate = [item for item in attempts if item["variant"] == "candidate"]
before = statistics.median(item["elapsed_seconds"] for item in baseline)
after = statistics.median(item["elapsed_seconds"] for item in candidate)
improvement = 1 - after / before
result = {"protocol": protocol, "controls": controls, "attempts": attempts, "baseline_median_seconds": before, "candidate_median_seconds": after, "median_elapsed_reduction_fraction": improvement, "descriptor_calls_reduced": max(item["descriptor_calls"] for item in candidate) < min(item["descriptor_calls"] for item in baseline), "eligible_for_review": improvement >= 0.10 and max(item["descriptor_calls"] for item in candidate) < min(item["descriptor_calls"] for item in baseline), "candidate_activated": False, "claim_boundary": protocol["claim_boundary"]}
save("mechanical-dispatch-comparison-result.json", result)
print(json.dumps({"comparison_complete": True, **{key: result[key] for key in ("baseline_median_seconds", "candidate_median_seconds", "median_elapsed_reduction_fraction", "descriptor_calls_reduced", "eligible_for_review")}}), flush=True)
