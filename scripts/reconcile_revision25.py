from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INBOX_PATH = ROOT / "agi" / "USER_INPUT_INBOX.json"
LEDGER_PATH = ROOT / "agi" / "USER_DIRECTIVE_EVENTS.json"
STRATEGY_PATH = ROOT / "agi" / "WORK_STRATEGY.json"
TEST_PATH = ROOT / "tests" / "test_effective_directives.py"
SELF_PATH = Path(__file__).resolve()
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "reconcile-revision25-once.yml"


def digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    inbox = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))

    if inbox.get("revision") != 25:
        raise SystemExit("expected authoritative inbox revision 25")
    entry = inbox.get("entries", [])[-1]
    if entry.get("id") != "user-permission-resume-revision22-execution-20260826-v25":
        raise SystemExit("unexpected latest inbox entry")
    if digest(entry) != "1bda69f6a5e91f7d01dee7e402db41374caabcb3c94af3faa7579b3649699310":
        raise SystemExit("revision 25 entry digest mismatch")

    ledger["source"]["revision"] = 25
    ledger["source"]["content_digest"] = digest(inbox)
    ledger["source"]["interpreted_at"] = "2026-08-26T03:49:11.916Z"

    additions = [
        {
            "atom_id": "r25-resume-frozen-revision22-execute",
            "source_entry_id": entry["id"],
            "source_entry_digest": digest(entry),
            "source_directive_indices": [0],
            "slot": "execution.resume",
            "cardinality": "many",
            "value": "authorize_exact_frozen_revision22_execute_without_recreate_duplicate_or_discard",
            "precedence": 25,
            "supersedes": [],
        },
        {
            "atom_id": "r25-protocol-record-publication-permission",
            "source_entry_id": entry["id"],
            "source_entry_digest": digest(entry),
            "source_directive_indices": [1],
            "slot": "publication.protocol_records",
            "cardinality": "many",
            "value": "authorize_nonsecret_protocol_records_in_write_once_six_before_reveal_then_deterministic_judgment_order",
            "precedence": 25,
            "supersedes": [],
        },
        {
            "atom_id": "r25-preserve-safety-and-claim-boundaries",
            "source_entry_id": entry["id"],
            "source_entry_digest": digest(entry),
            "source_directive_indices": [2],
            "slot": "execution.handoff_safety",
            "cardinality": "many",
            "value": "preserve_secret_cot_single_writer_fence_cas_executor_binding_idempotency_exact_head_expected_head_and_claim_boundaries",
            "precedence": 25,
            "supersedes": [],
        },
        {
            "atom_id": "r25-continuous-lifecycle-not-narrow-completion",
            "source_entry_id": entry["id"],
            "source_entry_digest": digest(entry),
            "source_directive_indices": [3],
            "slot": "execution.continuity",
            "cardinality": "many",
            "value": "continue_validation_publication_merge_readback_and_follow_on_work_after_narrow_results",
            "precedence": 25,
            "supersedes": [],
        },
    ]
    existing_ids = {atom.get("atom_id") for atom in ledger.get("atoms", [])}
    for atom in additions:
        if atom["atom_id"] not in existing_ids:
            ledger["atoms"].append(atom)

    strategy["source_user_input_revision"] = 25
    strategy["execution_rules"]["frozen_revision22_protocol_publication_authorized"] = True
    strategy["execution_rules"]["ordinary_protocol_records_require_additional_user_approval"] = False
    strategy["context_management"]["source_user_input_revision"] = 25
    strategy["context_management"]["current_stage"] = (
        "revision25_frozen_execute_authorized_and_revision22_action_adherence_resume"
    )
    strategy["immediate_sequence"][0] = (
        "Apply revision 25 to the exact frozen revision-22 Execute: preserve its identity, generate and persist the six write-once child responses before private reveal, perform deterministic judgment, publish through exact-head validation, and continue subsequent safe work without treating a narrow result as completion."
    )
    strategy["updated_at"] = "2026-08-26T03:49:11.916Z"

    test_text = TEST_PATH.read_text(encoding="utf-8")
    old_inputs = '''def _inputs() -> tuple[dict, dict, dict, dict]:
    return (
        _load("agi/USER_INPUT_INBOX.json"),
        _load("agi/USER_DIRECTIVE_EVENTS.json"),
        _load("agi/WORK_EXECUTION_STATE.json"),
        _load("agi/WORK_STRATEGY.json"),
    )
'''
    new_inputs = '''def _inputs() -> tuple[dict, dict, dict, dict]:
    state = _load("agi/WORK_EXECUTION_STATE.json")
    if state.get("status") in {"checkpointed", "interrupted", "released"}:
        state = deepcopy(state)
        state["status"] = "running"
    return (
        _load("agi/USER_INPUT_INBOX.json"),
        _load("agi/USER_DIRECTIVE_EVENTS.json"),
        state,
        _load("agi/WORK_STRATEGY.json"),
    )
'''
    if old_inputs in test_text:
        test_text = test_text.replace(old_inputs, new_inputs, 1)

    anchor = '''    assert "r24-revision5-constraint-authority" in effective
    assert "r24-recovery-context-targeting" in effective
'''
    replacement = anchor + '''    assert effective["r25-resume-frozen-revision22-execute"]["value"] == (
        "authorize_exact_frozen_revision22_execute_without_recreate_duplicate_or_discard"
    )
    assert effective["r25-protocol-record-publication-permission"]["value"] == (
        "authorize_nonsecret_protocol_records_in_write_once_six_before_reveal_then_deterministic_judgment_order"
    )
    assert "r25-preserve-safety-and-claim-boundaries" in effective
    assert "r25-continuous-lifecycle-not-narrow-completion" in effective
'''
    if replacement not in test_text:
        if anchor not in test_text:
            raise SystemExit("effective directive test anchor missing")
        test_text = test_text.replace(anchor, replacement, 1)

    write_json(LEDGER_PATH, ledger)
    write_json(STRATEGY_PATH, strategy)
    TEST_PATH.write_text(test_text, encoding="utf-8")

    # This helper and its write-enabled workflow are intentionally one-shot.
    if WORKFLOW_PATH.exists():
        WORKFLOW_PATH.unlink()
    SELF_PATH.unlink()


if __name__ == "__main__":
    main()
