#!/usr/bin/env python3
"""Execute the complete paired protocol; resume only unchanged task evidence."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from evaluation_protocol import source_files

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2));temporary.replace(path)


def stages(prefix,manifest,config):
    protocol=config['protocol'];model=protocol['primary_model'];second=protocol['second_model']
    run_static=prefix+'-static';dynamic_manifest='experiments/v3/'+prefix+'-pipeline/dynamic.json'
    def suite(name,partition,methods,kind='static',model_id=model,task_manifest=manifest):
        run=prefix+'-'+name
        command=['scripts/run_suite.py','--manifest',task_manifest,'--partition',partition,'--kind',kind,'--methods',*methods,
                 '--model',model_id,'--run-id',run,'--seconds',str(protocol['seconds_static'] if kind=='static' else protocol['seconds_dynamic']),
                 '--steps',str(protocol['model_calls']),'--tokens',str(protocol['tokens']),'--tool-limit',str(protocol['tool_calls']),
                 '--output-tokens',str(protocol['output_tokens']),'--request-timeout',str(protocol['request_timeout']),
                 '--attempts',str(protocol['attempts']),'--workers',str(protocol['workers'])]
        if protocol['shared_context']:command.append('--shared-context')
        return [(name,command),('score-'+name,['scripts/score_suite.py','--run-id',run]),
                ('analyze-'+name,['eval/metrics.py','--run-id',run])]
    result=suite('static','test',protocol['initial_methods'])
    result+=suite('components','components',protocol['components'])
    for i in range(protocol['repeat_additional']):result+=suite('repeat-'+str(i+1),'repeat',protocol['initial_methods'])
    result+=[('construct-dynamic',['eval/witnesses.py','--static-run-id',run_static,'--output',dynamic_manifest])]
    result+=suite('dynamic','dynamic',protocol['dynamic_methods'],'dynamic',model,dynamic_manifest)
    result+=suite('second-model','second_model',['structured','b2'],model_id=second)
    result+=[('report',['eval/report.py','--prefix',prefix])]
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prefix',required=True)
    parser.add_argument('--manifest',default='config/completion-tasks.json');parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,60}',args.prefix):parser.error('invalid prefix')
    output=ROOT/'experiments/v3'/(args.prefix+'-pipeline')
    if output.exists() and not args.resume:parser.error('Directory exists; inspect evidence and use --resume or a new prefix')
    if not output.exists() and args.resume:parser.error('No pipeline exists to resume')
    output.mkdir(parents=True,exist_ok=True)
    lock=(output/'pipeline.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Pipeline is already active')
    manifest=ROOT/args.manifest;config=json.loads(manifest.read_text());commands=stages(args.prefix,args.manifest,config)
    expected={'sources':source_files(ROOT),'manifest':hashlib.sha256(manifest.read_bytes()).hexdigest(),'stages':commands}
    expected=json.loads(json.dumps(expected))
    snapshot=output/'protocol.json'
    if snapshot.exists() and json.loads(snapshot.read_text())!=expected:raise SystemExit('Implementation or settings changed; use a new prefix')
    write(snapshot,expected)
    state_path=output/'state.json';state=json.loads(state_path.read_text()) if state_path.exists() else {'completed':[]}
    for name,command in commands:
        if name in state['completed']:continue
        if source_files(ROOT)!=expected['sources']:raise SystemExit('Implementation changed during evaluation')
        # Never pass a partial constructive cohort off as the requested sample.
        if name=='dynamic':
            dynamic=json.loads((output/'dynamic.json').read_text())
            if any(dynamic['shortfall'].values()):
                state.update(stage=name,status='cohort_shortfall',shortfall=dynamic['shortfall']);write(state_path,state)
                raise SystemExit('Constructive event target not met; cohort and all attempts preserved')
        state.update(stage=name,status='running');write(state_path,state)
        with (output/(name+'.log')).open('a') as log:
            result=subprocess.run([sys.executable,*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            state.update(status='stopped',returncode=result.returncode);write(state_path,state);raise SystemExit(result.returncode)
        state['completed'].append(name);state.update(status='stage_complete');write(state_path,state)
        print('STAGE_DONE',name,flush=True)
    state.update(stage='all',status='complete');write(state_path,state);print('PROTOCOL_COMPLETE',flush=True)


if __name__=='__main__':main()
