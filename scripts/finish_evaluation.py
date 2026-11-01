#!/usr/bin/env python3
"""Complete a rate-aware repair comparison while retaining prior-stage evidence."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from evaluation_protocol import source_files
from run_completion import stages,write

ROOT=Path(__file__).resolve().parents[1]
COMPLETED=('static','components','repeat-1','repeat-2')


def main():
    p=argparse.ArgumentParser();p.add_argument('--prefix',required=True);p.add_argument('--initial-prefix',required=True);p.add_argument('--resume',action='store_true');a=p.parse_args()
    for value in (a.prefix,a.initial_prefix):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,60}',value):p.error('invalid prefix')
    if a.prefix==a.initial_prefix:p.error('amended execution needs a new prefix')
    parent=ROOT/'experiments/v3'/(a.initial_prefix+'-pipeline');output=ROOT/'experiments/v3'/(a.prefix+'-pipeline')
    if output.exists()!=a.resume:p.error('use a fresh prefix, or --resume for existing evidence')
    parent_lock=(parent/'pipeline.lock').open('r')
    try:fcntl.flock(parent_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Original pipeline is still active')
    cohort=(parent/'dynamic.json').read_bytes();document=json.loads(cohort)
    if len(document['dynamic'])!=100 or any(document['shortfall'].values()):raise SystemExit('Incomplete reference cohort')
    inherited={}
    for stage in COMPLETED:
        folder=ROOT/'experiments/v3'/(a.initial_prefix+'-'+stage)
        protocol=json.loads((folder/'protocol.json').read_text());scores=json.loads((folder/'scores.json').read_text())
        if len(scores['rows'])!=len(protocol['events'])*len(protocol['methods']) or not all(r['complete'] for r in scores['rows']):raise SystemExit('Incomplete inherited stage: '+stage)
        for name in ('protocol.json','scores.json','analysis.json'):
            path=folder/name;inherited[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    config=json.loads((ROOT/'config/completion-tasks.json').read_text())
    keep={'dynamic','score-dynamic','analyze-dynamic','second-model','score-second-model','analyze-second-model','report'}
    commands=[(name,cmd) for name,cmd in stages(a.prefix,'config/completion-tasks.json',config) if name in keep]
    for name,cmd in commands:
        if name=='report':cmd.extend(['--initial-prefix',a.initial_prefix])
    expected={'sources':source_files(ROOT),'inherited':inherited,'initial_prefix':a.initial_prefix,'cohort_sha256':hashlib.sha256(cohort).hexdigest(),
              'stages':commands,'amendment':'Respect upstream reset time for retries; all 100 repair events are rerun for all three methods. The previous limited-rate execution and every failure remain intact. Planner prompts, reference cohort and per-task caps are unchanged. Prior static/component/repeat data describe their own recorded execution implementation.'}
    expected=json.loads(json.dumps(expected));output.mkdir(parents=True,exist_ok=True)
    lock=(output/'pipeline.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Amended pipeline is already active')
    snapshot=output/'protocol.json'
    if snapshot.exists() and json.loads(snapshot.read_text())!=expected:raise SystemExit('Code or evidence changed; use a fresh prefix')
    write(snapshot,expected)
    target=output/'dynamic.json'
    if target.exists() and target.read_bytes()!=cohort:raise SystemExit('Cohort changed')
    if not target.exists():target.write_bytes(cohort)
    state_path=output/'state.json';state=json.loads(state_path.read_text()) if state_path.exists() else {'completed':[],'initial_prefix':a.initial_prefix}
    for name,command in commands:
        if name in state['completed']:continue
        if source_files(ROOT)!=expected['sources']:raise SystemExit('Implementation changed during execution')
        for relative,wanted in inherited.items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=wanted:raise SystemExit('Inherited evidence changed')
        state.pop('returncode',None);state.update(stage=name,status='running');write(state_path,state)
        with (output/(name+'.log')).open('a') as log:result=subprocess.run([sys.executable,*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            state.update(status='stopped',returncode=result.returncode);write(state_path,state);raise SystemExit(result.returncode)
        state['completed'].append(name);state.update(status='stage_complete');write(state_path,state);print('STAGE_DONE',name,flush=True)
    state.update(stage='all',status='complete');write(state_path,state);print('PROTOCOL_COMPLETE',flush=True)


if __name__=='__main__':main()
