"""Recompute descriptive source-table results; never call a model or the network."""
from pathlib import Path
from collections import Counter, defaultdict
import csv
import hashlib
import json
import statistics
import sys

def analyze(path: Path) -> dict:
    data = path.read_bytes()
    sha = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    if sha != 'ff5d3c48312e58af2842917c731760f5f0d3f959':
        raise ValueError('source blob mismatch')
    rows = list(csv.DictReader(data.decode().splitlines()))
    keys = [(r['dataset'], r['episode_id'], r['rung'], r['rep']) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate episode/policy/repeat key')
    for row in rows:
        if row['was_correct'] not in {'True','False'} or row['now_correct'] not in {'True','False'}:
            raise ValueError('missing or invalid correctness label')
    summary = {}
    for rung in sorted({r['rung'] for r in rows}):
        group = [r for r in rows if r['rung'] == rung]
        wrong = [r for r in group if r['was_correct'] == 'False']
        healthy = [r for r in group if r['was_correct'] == 'True']
        reps = []
        for rep in sorted({r['rep'] for r in wrong}):
            g = [r for r in wrong if r['rep'] == rep]
            recovered = sum(r['now_correct'] == 'True' for r in g)
            reps.append({'rep':rep,'n':len(g),'recovered':recovered,'rate':recovered/len(g)})
        recovered = sum(r['now_correct'] == 'True' for r in wrong)
        summary[rung] = {'rows':len(group),'wrong_before':len(wrong),'recoveries':recovered,
            'recovery_rate':recovered/len(wrong) if wrong else None,'healthy_before':len(healthy),
            'healthy_broken':sum(r['now_correct'] != 'True' for r in healthy),'per_repeat':reps}
    base = {(r['dataset'],r['episode_id'],r['rep']):r for r in rows if r['rung']=='resample' and r['was_correct']=='False'}
    located = {(r['dataset'],r['episode_id'],r['rep']):r for r in rows if r['rung']=='located' and r['was_correct']=='False'}
    if base.keys() != located.keys():
        raise ValueError('policy pairing mismatch')
    pairs = [(k,int(located[k]['now_correct']=='True')-int(base[k]['now_correct']=='True')) for k in located]
    clusters = defaultdict(list)
    for key,difference in pairs:
        clusters[key[:2]].append(difference)
    return {'source_commit':'1b3e07fee53ae13407173c3ea932adb4a43e8230','source_blob_sha':sha,
        'rows':len(rows),'unique_episodes':len({(r['dataset'],r['episode_id']) for r in rows}),
        'summary':summary,'paired_comparison':{'matched_episode_repeat_pairs':len(pairs),
            'unique_task_clusters':len(clusters),'mean_located_minus_resample':statistics.mean(d for k,d in pairs),
            'mean_task_cluster_difference':statistics.mean(statistics.mean(v) for v in clusters.values()),
            'repeat_difference_counts':dict(Counter(d for k,d in pairs))},
        'claim_boundary':'Retrospective arithmetic audit of author-released result rows, not a new model run or independent replication. Repeats are grouped by task; no new significance or O efficacy claim.'}

if __name__ == '__main__':
    print(json.dumps(analyze(Path(sys.argv[1])),indent=2))
