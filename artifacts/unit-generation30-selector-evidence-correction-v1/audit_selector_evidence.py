"""Read existing selector evidence only; never select, reveal, score or replay."""
from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, sys, subprocess

ROOT = Path(sys.argv[1]).resolve()
OUTPUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / 'src'))
from continual.store import Store

def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

source_head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
response_ids = ['invoke-5789b4199c170ebb04b4df70', 'invoke-08094bcf99610e486c801e11', 'invoke-b2c83f59df55b0e1e39b18a3']
response_audits = []
for inv in response_ids:
    ref = f'.continual/work-model/invocations/{inv}/response.json'
    b = (ROOT / ref).read_bytes()
    response = json.loads(b)
    evidence = response['output']['result']['evidence']
    if isinstance(evidence,dict): evidence = list(evidence.values())
    checks = []
    for binding in evidence:
        path = (ROOT / binding['path']).resolve()
        path.relative_to(ROOT)
        data = path.read_bytes()
        value = json.loads(data)
        canonical = hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert canonical == Store.stable_digest(value,length=64)
        check = {'path':binding['path'],'declared_git_blob_sha':binding['git_blob_sha'],
                 'actual_git_blob_sha':blob(data),'git_blob_exact':blob(data)==binding['git_blob_sha'],
                 'actual_sha256_canonical_json':canonical}
        if 'sha256_canonical_json' in binding:
            check['declared_sha256_canonical_json'] = binding['sha256_canonical_json']
            check['canonical_digest_exact'] = canonical == binding['sha256_canonical_json']
        checks.append(check)
    response_audits.append({'response_ref':ref,'response_git_blob_sha':blob(b),'response_digest':response['response_digest'],
                            'checks':checks,'canonical_mismatch_count':sum(x.get('canonical_digest_exact') is False for x in checks)})

prior_ref = 'artifacts/unit-generation29-external-arc-nonzero-bounding-box-transfer-v1-precommit.json'
prior_bytes = (ROOT / prior_ref).read_bytes()
prior = json.loads(prior_bytes)
prefix = 'artifacts/unit-generation29-external-arc-three-rule-selector-v1'
correction = json.loads((ROOT / (prefix + '-selector-correction.json')).read_text())
precommit = json.loads((ROOT / (prefix + '-precommit.json')).read_text())
causal = json.loads((ROOT / (prefix + '-causal-order.json')).read_text())
assert '1cf80156' in prior['selector']['candidate_task_ids']
assert prior['selector']['selected_task_ids'] == []
assert prior['answer_access']['holder_reveal_called'] is False
assert all(x['canonical_mismatch_count']==3 for x in response_audits)
assert all(c['git_blob_exact'] for x in response_audits for c in x['checks'])
report = {
    'schema_version':1,'record_type':'append_only_selector_evidence_correction',
    'observed_at':datetime.now(timezone.utc).isoformat(), 'local_source_head':source_head,
    'audit_mode':'read_only_existing_artifacts_no_experiment_rerun',
    'canonical_algorithm':'SHA256 of UTF-8 json.dumps(value, sort_keys=True, separators=(comma, colon), ensure_ascii=True); cross-checked with Store.stable_digest(length=64).',
    'response_audits':response_audits,
    'finding':'Each of the immutable Execute, post-result Candidate and later Root responses carries the same three incorrect auxiliary canonical JSON digest claims. All seven Git blob bindings in each response match actual bytes. The later Root copied inherited incorrect claims; successful blob validation did not validate those auxiliary claims.',
    'correction_policy':'Preserve every original request/response and source artifact. This separate record supplies computed identities and marks the original claims invalid; it does not rewrite, resubmit or upgrade the event.',
    'prior_occurrence':{'path':prior_ref,'git_blob_sha':blob(prior_bytes),'recorded_at':prior['recorded_at'],
                        'candidate_task_ids':prior['selector']['candidate_task_ids'],'selected_task_ids':prior['selector']['selected_task_ids'],
                        'answer_access':prior['answer_access'],
                        'finding':'Task 1cf80156 appeared as an earlier scanned candidate. That prior unit selected no task and reports no held-out reveal. This violates the later strict no-prior-exact-ID-occurrence predicate but does not itself prove prior held-out-answer exposure.'},
    'chronology_scope':{
        'correction_recorded_at':correction['recorded_at'],
        'precommit_recorded_at':precommit['recorded_at'],
        'causal_receipt_recorded_at':causal['recorded_at'],
        'finding':'The correction timestamp already describes a revealed outcome before the later precise precommit timestamp. These self-authored times do not resolve actual reveal order. The inspected write_artifact helper verifies a local JSON round trip and local Git hash; this implementation and later remote publication do not establish pre-reveal remote commitment for the historical event.',
        'independent_event_receipt_observed':False,
    },
    'no_holder_reveal':True,'no_new_selection':True,'no_experiment_replay':True,
    'unit_success_admissible':False,'candidate_activation':False,
    'independent_external_evaluation':False,'agi_claim_supported':False,
    'next_action':'Use these computed bindings and bounded contradictions in the pending Task Evaluate lifecycle. Preserve the unit FAIL and distinguish prior candidate exposure from proven prior answer exposure. Future output must compute any declared canonical digest from actual bytes before submission.',
}
OUTPUT.parent.mkdir(parents=True,exist_ok=True)
OUTPUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'output':str(OUTPUT),'git_blob_checks':sum(len(x['checks']) for x in response_audits),
                  'distinct_bad_canonical_claims':3,'affected_immutable_responses':len(response_audits)},indent=2))
