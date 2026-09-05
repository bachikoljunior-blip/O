from pathlib import Path
import hashlib, json, sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / 'O'
sys.path.insert(0, str(ROOT / 'src'))
from continual.store import Store
from continual.work_session import submit_work_response, verify_work_invocations
from continual.contracts import validate_component_output

store = Store(ROOT)
run = 'run-work-recovery-gen9-durability-repair'
rd = store.run_dir(run)
old_id = 'invoke-142bf5ed2549ebb4a5d64fed'
duplicate_id = 'invoke-2050793458fd8280a5c6957a'
work = ROOT / '.continual/work-model/invocations'
old = store.read_json(work / old_id / 'request.json')
new = store.read_json(work / duplicate_id / 'request.json')
response = store.read_json(work / old_id / 'response.json')
journal = store.read_json(rd / 'invocations/invoke-2d144500474faafb046ba208.json')
assert journal['status'] == 'complete'
assert journal['output'] == response['output']
assert old['payload_digest'].startswith(journal['payload_digest'])
assert old['payload']['candidate_index'] == new['payload']['candidate_index']
assert old['payload']['execution_unit']['execution_unit'] == new['payload']['execution_unit']['execution_unit']
for key in old['payload']:
    if key != 'execution_unit':
        assert old['payload'][key] == new['payload'][key]
identity = {
    'mode': 'pre-application', 'target_component': 'execute',
    'execution_unit': old['payload']['execution_unit']['execution_unit'],
    'candidate_index': old['payload']['candidate_index'],
}
cache_ref = f'.continual/runs/{run}/preflight/preflight-execute-{Store.stable_digest(identity)}.json'
assert not (ROOT / cache_ref).exists()
validate_component_output('candidate_evaluate', response['output'], evaluator_mode='pre-application')
output = {
    'result': {
        'mode': 'pre-application', 'target_component': 'execute',
        'decision': 'DEFER', 'scope': 'unit-generation29-external-arc-three-rule-selector-v1/execute',
        'reason': 'This request duplicates an already completed pre-application decision. Only mutable snapshot revision/error metadata differs; the exact execution unit, candidate index, environment, prompt and executor are unchanged. Recover the missing deterministic cache from the completed native journal instead of making another candidate selection.',
        'authoritative_request_ref': f'.continual/work-model/invocations/{old_id}/request.json',
        'authoritative_response_ref': f'.continual/work-model/invocations/{old_id}/response.json',
        'authoritative_native_journal_ref': f'.continual/runs/{run}/invocations/invoke-2d144500474faafb046ba208.json',
        'activation': False, 'execute_replay_authorized': False, 'agi_claim_supported': False,
        'continuation': 'Restore the exact completed preflight output under its deterministic cache key, suppress this duplicate native continuation, and resume the original state-bound Execute response once.'
    },
    'local_learn': {'decision': 'NO_CHANGE', 'candidates': [], 'observation': 'Completed native fragments are insufficient for restart durability when the deterministic preflight cache is omitted. Include all run-generated cache records in publication manifests and verify the exact next resume path.'},
    'fragment': {'component': 'candidate_evaluate', 'mode': 'pre-application', 'decision': 'DEFER', 'purpose': 'Suppress a duplicate caused by a missing completed preflight cache.', 'observations': ['The original candidate decision is preserved byte-for-byte in its immutable response and completed native journal.', 'No second candidate selection, benchmark replay, activation or AGI claim is made.']}
}
receipt = submit_work_response(ROOT, duplicate_id, output, executor_binding='current_chatgpt_work_session', model_identity='chatgpt-work-model-unverified')
store.atomic_json(ROOT / cache_ref, response['output'])
dp = rd / 'invocations/invoke-5956f225fd984c0f3b848caa.json'
duplicate_journal = store.read_json(dp)
assert duplicate_journal['status'] == 'awaiting_work_model'
duplicate_journal.update(status='failed', failed_at=store.utc_now(), error={'type': 'DuplicateContinuationSuppressed', 'message': 'Missing preflight cache reconstructed exactly from completed journal invoke-2d144500474faafb046ba208 and its immutable response. The duplicate request and explicit DEFER response are retained; the original frozen Execute remains authoritative.'})
store.atomic_json(dp, duplicate_journal)
report = {
    'schema_version': 1, 'recorded_at': store.utc_now(), 'record_type': 'missing_preflight_cache_recovery',
    'run_id': run, 'lease_generation': 30, 'cache_ref': cache_ref,
    'source_native_journal': 'invoke-2d144500474faafb046ba208',
    'source_work_invocation': old_id, 'source_response_digest': response['response_digest'],
    'source_output_digest': response['output_digest'],
    'cache_equals_completed_output': store.read_json(ROOT / cache_ref) == journal['output'],
    'duplicate_work_invocation': duplicate_id, 'duplicate_decision': 'DEFER',
    'duplicate_response_digest': receipt['response_digest'],
    'duplicate_request_preserved': True, 'execute_response_unchanged': True,
    'execute_consumed': False, 'cause': 'The completed candidate preflight cache was absent from the inherited publication. Snapshot error/revision drift consequently created a second pre-application request before the already-answered Execute could be consumed.',
    'claim_boundary': 'Internal continuation repair, not a new candidate trial, behavioral experiment or completion claim.'
}
report_ref = ROOT / 'artifacts/unit-generation30-preflight-cache-recovery-v1-result.json'
assert not report_ref.exists()
store.atomic_json(report_ref, report)
verified = verify_work_invocations(ROOT, run_id=run)
print(json.dumps({'report': report, 'verified': {k:v for k,v in verified.items() if k!='invocation_ids'}}, indent=2))
