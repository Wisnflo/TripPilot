#!/usr/bin/env python3
"""Run preregistered evaluation stages sequentially, with <=4 model jobs total."""
import json
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'experiments/v3/acceptance-20261031'


def stage(name,args):
    OUTPUT.mkdir(exist_ok=True,parents=True)
    (OUTPUT/'state.json').write_text(json.dumps({'stage':name,'state':'running'},indent=2))
    with (OUTPUT/(name+'.log')).open('w') as log:
        result=subprocess.run([sys.executable,*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        (OUTPUT/'state.json').write_text(json.dumps({'stage':name,'state':'failed','code':result.returncode},indent=2))
        raise SystemExit(result.returncode)
    print('STAGE_DONE',name,flush=True)


def main():
    global OUTPUT
    parser=argparse.ArgumentParser()
    parser.add_argument('--prefix',default='v3',help='use a new prefix for a new reproduction; completed outputs are never overwritten')
    args=parser.parse_args()
    prefix=args.prefix
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',prefix):parser.error('invalid run prefix')
    OUTPUT=ROOT/'experiments/v3'/('acceptance-20261031' if prefix=='v3' else 'acceptance-'+prefix)
    if OUTPUT.exists():raise SystemExit('Acceptance directory exists; use a NEW --prefix. Existing evidence was not modified.')
    static=['scripts/run_suite.py','--partition','test','--methods','structured','b2',
            '--run-id',prefix+'-static-test-01','--seconds','360','--request-timeout','90','--workers','4']
    stage('static',static)
    stage('score-static',['scripts/score_suite.py','--run-id',prefix+'-static-test-01'])
    for number in [1,2]:
        run=prefix+'-static-repeat-%02d'%number
        stage(run,['scripts/run_suite.py','--manifest','config/v3-repeat.json','--partition','repeat',
                   '--methods','structured','b2','--run-id',run,'--seconds','360','--request-timeout','90','--workers','4'])
        stage('score-'+run,['scripts/score_suite.py','--run-id',run])
    dynamic=ROOT/'experiments/frozen'/(prefix+'-protocol-20261031')/'dynamic.json'
    if not dynamic.exists():stage('build-dynamic',['scripts/build_dynamic_suite.py','--static-run-id',prefix+'-static-test-01','--output',str(dynamic)])
    stage('dynamic',['scripts/run_suite.py','--manifest',str(dynamic),'--partition','dynamic','--kind','dynamic',
                     '--methods','patch','b2','--run-id',prefix+'-dynamic-test-01','--seconds','240','--request-timeout','90','--workers','4'])
    stage('score-dynamic',['scripts/score_suite.py','--run-id',prefix+'-dynamic-test-01'])
    (OUTPUT/'state.json').write_text(json.dumps({'stage':'all','state':'complete'},indent=2))
    print('ACCEPTANCE_COMPLETE',flush=True)


if __name__=='__main__':main()
