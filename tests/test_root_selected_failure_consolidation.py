"""A failed unit can be consolidated by an explicit Root choice without passing it."""
import json
from pathlib import Path

from continual.engine import Engine


class FailureConsolidationModel:
    model = 'fixture-no-live-model'

    def __init__(self, unit_verdict):
        self.unit_verdict = unit_verdict
        self.calls = []
        self.root_calls = 0

    def call(self, component, payload, prompt_path=None):
        self.calls.append(component)
        fragment = {'component': component, 'observations': ['isolated regression fixture']}
        output = {'fragment': fragment, 'local_learn': {'decision': 'NO_CHANGE', 'candidates': []}}
        if component == 'entry':
            result = {'goal': 'Retain failed work and continue the unmet task.'}
        elif component == 'root':
            self.root_calls += 1
            selected = {1: 'execute', 2: 'task_evaluate', 3: 'consolidate_episode'}.get(self.root_calls, 'execute')
            result = {'component': selected, 'goal': f'Explicitly run {selected}', 'scope': 'regression/failed-unit-learning'}
        elif component == 'execute':
            result = {'verdict': self.unit_verdict, 'evidence': ['bounded attempt did not pass']}
        elif component == 'task_evaluate':
            result = {'verdict': 'FAIL', 'unit_verdict': self.unit_verdict, 'evidence': ['original task unmet', 'bounded attempt did not pass']}
        elif component == 'consolidate_episode':
            result = {'task': 'Retain failed work', 'outcome': 'failed-unit-evidence-preserved',
                      'unit_verdict': payload['snapshot']['unit_completion_verdict'],
                      'unresolved': ['The original task remains unmet.']}
        elif component == 'learn':
            result = {'decision': 'NO_CHANGE', 'candidates': [],
                      'source_outcome': payload['current_episode']['outcome']}
            output.pop('local_learn')
        else:
            raise AssertionError(f'unexpected fixture component {component}')
        output['result'] = result
        return output


def _assert_failed_episode_is_learned(runtime_repo: Path, unit_verdict: str):
    model = FailureConsolidationModel(unit_verdict)
    engine = Engine(runtime_repo, model=model)
    run_id = engine.start('Retain this unsuccessful attempt, then continue.', max_steps=8)
    snapshot = engine.store.snapshot(run_id)
    assert model.calls == ['entry', 'root', 'execute', 'root', 'task_evaluate', 'root', 'consolidate_episode', 'learn']
    assert snapshot['status'] == 'continue'
    assert snapshot['phase'] == 'root_pending'
    assert snapshot['task_completion_verdict'] == 'FAIL'
    assert snapshot['unit_completion_verdict'] == unit_verdict
    episode = json.loads((runtime_repo / '.continual/episodes' / snapshot['episode_id'] / 'episode.json').read_text())
    assert episode['outcome'] == 'failed-unit-evidence-preserved'
    assert episode['unit_verdict'] == unit_verdict
    learned = json.loads((engine.store.run_dir(run_id) / 'artifacts/post-task-learn.json').read_text())
    assert learned['source_outcome'] == episode['outcome']
    events = (engine.store.run_dir(run_id) / 'events.jsonl').read_text()
    assert '"type": "unit_learned"' in events
    assert '"type": "run_finished"' not in events


def test_root_selected_failed_unit_consolidates_and_learns(runtime_repo: Path):
    _assert_failed_episode_is_learned(runtime_repo, 'FAIL')


def test_root_selected_uncertain_unit_consolidates_and_learns(runtime_repo: Path):
    _assert_failed_episode_is_learned(runtime_repo, 'UNCERTAIN')
