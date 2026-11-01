#!/usr/bin/env python3
"""Run all three corrected component controls on the fixed parent cohort."""
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


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--initial-prefix',required=True)
    p.add_argument('--repair-prefix',required=True);p.add_argument('--resume',action='store_true');a=p.parse_args()
    for value in (a.run_id,a.initial_prefix,a.repair_prefix):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,60}',value):p.error('invalid run identifier')
    prior=ROOT/'experiments/v3'/(a.repair_prefix+'-pipeline')
    if json.loads((prior/'state.json').read_text()).get('status')!='complete':
        raise SystemExit('Finish the active repair/second-model pipeline before changing implementation')
    prior_lock=(prior/'pipeline.lock').open('r')
    try:fcntl.flock(prior_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Earlier evaluation is still active')
    folder=ROOT/'experiments/v3'/(a.run_id+'-pipeline')
    if folder.exists()!=a.resume:p.error('use a fresh run ID or --resume for existing evidence')
    config=json.loads((ROOT/'config/completion-tasks.json').read_text())
    if len(config['components'])!=24:raise SystemExit('Expected the original 24 preregistered component parents')
    commands=[(name,command) for name,command in stages(a.run_id,'config/completion-tasks.json',config) if name in ('components','score-components','analyze-components')]
    for _,command in commands:
        command[command.index(a.run_id+'-components')]=a.run_id
    command=commands[0][1];index=command.index('--methods');end=command.index('--model')
    command[index+1:end]=['structured','no_feedback','direct_plan']
    commands.append(('report',['eval/report.py','--prefix',a.repair_prefix,'--initial-prefix',a.initial_prefix,
                              '--component-run',a.run_id,'--output',str(folder/'report.md')]))
    inherited={}
    for prefix,stages_ in ((a.initial_prefix,('static','components','repeat-1','repeat-2')),(a.repair_prefix,('dynamic','second-model'))):
        for stage in stages_:
            for name in ('protocol.json','scores.json','analysis.json'):
                path=ROOT/'experiments/v3'/(prefix+'-'+stage)/name
                inherited[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    expected=json.loads(json.dumps({'sources':source_files(ROOT),'inherited':inherited,'stages':commands,
             'reason':'Correct feedback switch and complete-plan output contract. Rerun all three settings on the same fixed 24 parents; retain the original invalid-control study as diagnostic only. No task selection by outcome.'}))
    folder.mkdir(parents=True,exist_ok=True);lock=(folder/'pipeline.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Component review is already active')
    path=folder/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=expected:raise SystemExit('Code or evidence changed; use a fresh run ID')
    write(path,expected);state_path=folder/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'completed':[]}
    for name,command in commands:
        if name in state['completed']:continue
        if source_files(ROOT)!=expected['sources']:raise SystemExit('Implementation changed during evaluation')
        if any(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=wanted for relative,wanted in inherited.items()):
            raise SystemExit('Prior evidence changed')
        state.update(stage=name,status='running');state.pop('returncode',None);write(state_path,state)
        with (folder/(name+'.log')).open('a') as log:result=subprocess.run([sys.executable,*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            state.update(status='stopped',returncode=result.returncode);write(state_path,state);raise SystemExit(result.returncode)
        state['completed'].append(name);state.update(status='stage_complete');write(state_path,state)
        print('STAGE_DONE',name,flush=True)
    state.update(stage='all',status='complete');write(state_path,state);print('COMPONENT_REVIEW_COMPLETE',flush=True)


if __name__=='__main__':main()
