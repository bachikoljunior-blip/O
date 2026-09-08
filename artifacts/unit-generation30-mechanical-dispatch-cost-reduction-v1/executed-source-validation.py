from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "O"
with tempfile.TemporaryDirectory(prefix="proposed-dispatch-source-", dir=HERE) as directory:
    source = Path(directory) / "src"
    shutil.copytree(ROOT / "src", source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(HERE / "proposed_learning_engine.py", source / "continual/learning_engine.py")
    sys.path.insert(0, str(source))
    from continual.engine import Engine
    from agi.execution_learning import EXECUTION_SCOPE, run_learned_tool_execution_campaign
    from agi.materialized_runtime_replay import _mechanical_engine
    root = Path(directory) / "fixture"
    root.mkdir()
    shutil.copytree(ROOT / "prompts", root / "prompts")
    shutil.copytree(ROOT / ".continual/system", root / ".continual/system")
    (root / ".continual/candidates").mkdir(parents=True)
    (root / ".continual/candidates/index.json").write_text('{"schema_version":2,"candidates":[]}\n')
    report = run_learned_tool_execution_campaign(root, "dispatch-equivalence-controls")
    engine = _mechanical_engine(root)
    run_id = "run-actual-proposed-dispatch-controls"
    engine.store.run_dir(run_id).mkdir(parents=True)
    engine.store.atomic_json(engine.store.run_dir(run_id) / "snapshot.json", {"revision": 0, "status": "continue", "phase": "unit_pending"})
    payload = {"snapshot": {"revision": 0}, "execution_unit": {"goal": "retained transform", "scope": EXECUTION_SCOPE, "learned_tool_call": {"tool_id": report["tool_id"], "input": "FreshInput"}}}
    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)
    assert first == second and first["result"]["output"] == "tupnIhserF#tupnIhserF#"
    assert len(list((engine.store.run_dir(run_id) / "invocations").glob("invoke-learned-tool-*.json"))) == 1
    semantic = deepcopy(payload)
    semantic["execution_unit"].pop("learned_tool_call")
    with patch.object(Engine, "_invoke", lambda self, run, component, value: value):
        enriched = engine._invoke(run_id, "execute", semantic)
    assert len(enriched["verified_learned_tools"]) == 1
    assert enriched["verified_learned_tools"][0]["tool_id"] == report["tool_id"]
    controls = ["mechanical_output", "journal_reused_once", "semantic_descriptor_enrichment_preserved"]
    wrong = deepcopy(payload); wrong["execution_unit"]["scope"] = "agi/wrong-scope"
    mismatch = deepcopy(payload); mismatch["execution_unit"]["learned_tool_call"]["scope"] = "agi/wrong-scope"
    for label, request in (("wrong_scope_denied", wrong), ("scope_mismatch_denied", mismatch)):
        try:
            engine._invoke(run_id, "execute", request)
        except ValueError:
            controls.append(label)
        else:
            raise AssertionError(label)
    path = root / ".continual/candidates" / report["candidate_id"] / "candidate.json"
    candidate = json.loads(path.read_text())
    candidate["scope_states"][EXECUTION_SCOPE] = "REJECTED_FOR_SCOPE"
    path.write_text(json.dumps(candidate) + "\n")
    try:
        engine._invoke(run_id, "execute", payload)
    except ValueError:
        controls.append("revoked_scope_cannot_reuse_journal")
    else:
        raise AssertionError("revoked scope was reused")
    result = {"status": "passed", "actual_proposed_source_sha256": hashlib.sha256((source / "continual/learning_engine.py").read_bytes()).hexdigest(), "controls": controls, "live_model_forbidden_by_existing_fixture": True, "active_checkout_changed": False}
    (HERE / "actual-proposed-dispatch-source-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
