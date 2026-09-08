from pathlib import Path
import json, sys, hashlib, subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / 'O'
sys.path.insert(0, str(ROOT / 'src'))
from continual.store import Store
from continual.work_session import submit_work_response, verify_work_invocations
from continual.self_application import record_self_application

store = Store(ROOT)
run = 'run-work-recovery-gen9-durability-repair'
inv = 'invoke-08094bcf99610e486c801e11'
request = store.read_json(ROOT / f'.continual/work-model/invocations/{inv}/request.json')
assert request['payload']['mode'] == 'post-result'
actual = request['payload']['actual_result']
assert actual['verdict'] == 'FAIL'
evidence = [actual['evidence'][key] for key in sorted(actual['evidence'])]
output = {
    'result': {
        'mode': 'post-result', 'target_component': 'execute',
        'candidate_id': 'candidate-agi-benchmark-suite-v1',
        'scope': 'agi/capability-evaluation',
        'exact_trial_scope': 'unit-generation29-external-arc-three-rule-selector-v1/execute',
        'decision': 'REMAIN_CANDIDATE', 'activation': False,
        'reason': 'The actual frozen selector event failed its previously-unused-task condition. Its exact prediction is inadmissible as success evidence because task 1cf80156 had already been used. Preserving the FAIL is appropriate evidence handling, but no matched baseline or protected scoped activation measurement identifies a benefit caused by the overlay. This invalid event does not establish that the overlay or broader rule-selection family is ineffective.',
        'evidence': evidence,
        'supporting_observations': ['The immutable Execute response retained the failure, excluded its provisional score from success evidence, and authorized no replacement task, repeated reveal, retry, activation or production routing.'],
        'contradictory_evidence': [
            'The prior-use scan failed open because rg -h emitted help; the chosen task was already present in O history.',
            'The held-out provisional 24/24 versus identity 0/24 cannot satisfy the precommitted task novelty condition.',
            'The overlay did not prevent the selector-integrity failure in this exact event; its causal effect relative to the unchanged baseline is unmeasured.'
        ],
        'rejected_reasons': ['Activation is unsupported by this invalid trial and by the absence of a protected matched-baseline gate.', 'Rejecting the whole candidate or mechanism family would exceed the evidence for this exact implementation defect.'],
        'regression_status': 'Protected CI for PR504 passed at d867e5258d686fe93571cf086db804e9c2df8967, including the later portable scanner repair. This is internal software validation; it neither repairs the revealed experiment nor establishes overlay efficacy.',
        'cost_assessment': {'quality': 'One invalid trial; no causal overlay improvement established.', 'latency': 'No matched end-to-end overlay timing was collected.', 'token_and_tool_cost': 'No matched attribution is available.', 'failure_risk': 'Prior-use exclusion was a decisive unprotected dependency in the original event.'},
        'rollback': 'Keep the candidate inactive and the active Execute baseline unchanged. Preserve the failed event and every immutable request/response. Do not rerun this selected task.',
        'reevaluate_on': 'A materially different, prospectively committed trial with verified task exclusion and a matched baseline yields admissible evidence for this overlay.',
        'continuation': 'Consume this post-result decision after exact-head publication and readback, then return to the ordinary Root, Task Evaluate, Consolidate and Learn lifecycle. Select a materially different falsifiable unit; handover and this evaluation do not terminate the primary run.',
        'unit_verdict': 'FAIL', 'task_completion_verdict': 'FAIL', 'user_level_verdict': 'FAIL',
        'upper_objective_verdict': 'FAIL', 'agi_claim_supported': False, 'production_routing': False,
        'independent_external_evaluation': False,
        'claim_boundary': 'Internal post-result candidate judgment for one invalid selector event. No general capability, family rejection, candidate activation or completion claim.'
    },
    'local_learn': {
        'decision': 'NO_CHANGE', 'candidates': [],
        'observations': ['A correct prediction cannot rescue a violated precommitted selection condition.', 'An evidence overlay must not receive causal credit merely because the executor reports a failure honestly.', 'Evaluate the tested implementation and conditions; preserve uncertainty about untested mechanisms and the wider family.']
    },
    'fragment': {
        'component': 'candidate_evaluate', 'mode': 'post-result',
        'purpose': 'Evaluate the actual failed three-rule selector trial without widening its evidence.',
        'candidate_id': 'candidate-agi-benchmark-suite-v1', 'decision': 'REMAIN_CANDIDATE',
        'evidence_refs': [item['path'] for item in evidence],
        'observations': ['The immutable event remains FAIL; the candidate remains inactive.', 'There is no valid matched comparison showing candidate benefit or family-wide failure.'],
        'unresolved': ['Independent task evaluation, consolidation, learning and materially different next work remain required.']
    }
}
receipt = submit_work_response(ROOT, inv, output, executor_binding='current_chatgpt_work_session', model_identity='chatgpt-work-model-unverified')
verified = {k:v for k,v in verify_work_invocations(ROOT, run_id=run).items() if k!='invocation_ids'}
assert verified['native_completed'] == 197 and verified['pending'] == 0
old_execute = ROOT / '.continual/work-model/invocations/invoke-5789b4199c170ebb04b4df70/response.json'
data = old_execute.read_bytes()
assert hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest() == '2768f680b439e5cae863d3ad7b3c528d225f6338'
report_ref = 'artifacts/unit-generation30-preflight-cache-recovery-v1-continuation.json'
report = {'recorded_at': store.utc_now(), 'run_id': run, 'native_integrity': verified,
    'execute_consumed_once': True, 'execute_native_journal': 'invoke-ffa5842478673c5c153a2c2d',
    'execute_response_unchanged': True, 'post_result_invocation': inv,
    'post_result_request_digest': request['request_digest'], 'post_result_response_digest': receipt['response_digest'],
    'post_result_decision': 'REMAIN_CANDIDATE', 'snapshot_revision': 194,
    'snapshot_phase': 'unit_pending', 'pending_native_response_consumption': True,
    'claim_boundary': 'Local native progression verified; protected CI, merge, remote readback and next resume still pending.'}
store.atomic_json(ROOT / report_ref, report)
record = {
    'execution_id': 'development-g30-preflight-cache-recovery-v1',
    'request': 'Continue the inherited O execution without stopping at handover.',
    'objective': 'Repair the missing completed preflight cache, suppress a duplicate, and consume the original Execute response without repeating its external effects.',
    'executor': {'binding': 'current_chatgpt_work_session', 'model_identity': 'chatgpt-work-model-unverified', 'model_verified': False},
    'started_at': '2026-09-05T10:57:18Z', 'completed_at': store.utc_now(),
    'verdict': 'UNCERTAIN', 'scope': 'agi/repository-development/native-preflight-durability',
    'context': {'boundary': 'development', 'risk_signals': ['stale_state_risk'], 'logical_reset_used': False, 'fresh_execution_used': False, 'independent_execution_used': False},
    'actions': ['Compared original and duplicate requests; only mutable snapshot metadata differed.', 'Reconstructed the deterministic cache exactly from a completed native journal and immutable response.', 'Preserved the duplicate request with explicit DEFER and failed duplicate journal.', 'Consumed the unchanged failed Execute response; created and answered the actual post-result Candidate request.'],
    'decisions': ['No experiment retry or replacement.', 'Keep the candidate inactive with REMAIN_CANDIDATE.', 'Include generated preflight caches and source observations in the next exact publication.'],
    'observations': [verified, report],
    'failures': ['Inherited publication omitted the completed preflight cache.', 'The first resume created a duplicate pre-application request before consuming Execute; the duplicate is explicitly retained and suppressed.'],
    'unknowns': ['Remote publication, protected validation and the next native resume have not yet completed.'],
    'artifacts': ['artifacts/unit-generation30-preflight-cache-recovery-v1-result.json', report_ref, f'.continual/work-model/invocations/{inv}/response.json'],
    'validation': [verified], 'claims': ['Only local deterministic recovery and exactly bound response consumption are verified. UNCERTAIN remains until remote durability and the required logical source reset; this is internal development evidence.'],
    'unresolved': ['Publish, validate, merge and rehydrate from exact main; consume the post-result response and continue without a milestone exit.']
}
self_result = record_self_application(ROOT, record)
(HERE/'preflight-cache-self-result.json').write_text(json.dumps(self_result,indent=2)+'\n')
print(json.dumps({'receipt': {k:v for k,v in receipt.items() if k!='output'}, 'native': verified, 'self_application': self_result},indent=2))
