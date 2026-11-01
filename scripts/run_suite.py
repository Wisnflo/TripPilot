#!/usr/bin/env python3
"""Run fixed paired tasks with supervised workers and immutable input settings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.execution.supervisor import supervise,terminate_all,reset_cancellation
from trippilot.execution.ledger import summarize
from trippilot.execution.budget import read_resources
from evaluation_protocol import source_files,input_files,changed_files,make_job


def save(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2));temporary.replace(path)


def service_requires_stop(statuses, terminal):
    return any(v in (401,403) for v in statuses) or (terminal=='request_error' and 429 in statuses)


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',default='config/v3-tasks.json')
    p.add_argument('--partition',choices=['dev','test','dynamic','repeat','components','second_model'],required=True)
    p.add_argument('--kind',choices=['static','dynamic'],default='static')
    p.add_argument('--methods',nargs='+',default=['structured','b2'])
    p.add_argument('--model',default='qwen3.8-reasoner');p.add_argument('--run-id',required=True)
    p.add_argument('--seconds',type=float,default=240);p.add_argument('--steps',type=int,default=16)
    p.add_argument('--shared-context',action='store_true');p.add_argument('--tokens',type=int,default=500000)
    p.add_argument('--tool-limit',type=int,default=400);p.add_argument('--output-tokens',type=int,default=10000)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--limit',type=int)
    p.add_argument('--request-timeout',type=float,default=60);p.add_argument('--attempts',type=int,default=2)
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}',a.run_id):p.error('invalid run ID')
    if min(a.seconds,a.steps,a.workers,a.request_timeout,a.attempts,a.tokens,a.tool_limit,a.output_tokens)<=0:p.error('budgets must be positive')
    if a.limit is not None and a.limit<=0:p.error('limit must be positive')
    allowed={'static':{'structured','b2','b1','b0','no_feedback','direct_plan'},'dynamic':{'patch','b2','rules'}}[a.kind]
    if not a.methods or not set(a.methods)<=allowed or len(a.methods)!=len(set(a.methods)):p.error('invalid or duplicate methods')
    manifest=ROOT/a.manifest;events=json.loads(manifest.read_text())[a.partition]
    keys=[(e['uid'],e['event']['kind'] if a.kind=='dynamic' else '') for e in events]
    if not events or len(keys)!=len(set(keys)):p.error('empty task set or duplicate identities')
    if a.limit:events=events[:a.limit]
    folder=ROOT/'experiments/v3'/a.run_id;folder.mkdir(parents=True,exist_ok=True)
    lock=(folder/'run.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('This run is already active')
    hashes=source_files(ROOT);inputs=input_files(ROOT,events,a.kind)
    price_path=ROOT/'config/v3-pricing.json';price_document=json.loads(price_path.read_text());prices=price_document['models'].get(a.model)
    settings={'model':a.model,'steps':a.steps,'seconds':a.seconds,'request_timeout':a.request_timeout,
              'attempts':a.attempts,'tokens':a.tokens,'tool_limit':a.tool_limit,'output_tokens':a.output_tokens,'shared_context':a.shared_context}
    protocol={**settings,'pricing':{'currency':'CNY','rates':prices,'observed_date':price_document['observed_date'],
              'sha256':hashlib.sha256(price_path.read_bytes()).hexdigest()},'kind':a.kind,'partition':a.partition,
              'events':events,'methods':a.methods,'workers':a.workers,'feedback_limit':4,
              'data_sha256':inputs,'source_sha256':hashes,'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    protocol_path=folder/'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text())!=protocol:raise SystemExit('Protocol changed; use a new run ID')
    save(protocol_path,protocol)
    if not (folder/'source.tar.gz').exists():
        with tarfile.open(folder/'source.tar.gz','w:gz') as archive:
            for relative in hashes:archive.add(ROOT/relative,arcname=relative)
    from trippilot.models.config import resolve_model_config
    config=resolve_model_config(model_override=a.model,env_file=Path(os.environ['TRIPPILOT_CONFIG_FILE']) if os.environ.get('TRIPPILOT_CONFIG_FILE') else None)
    os.environ['CHINATRAVEL_OPENAI_API_KEY']=config.api_key
    if config.base_url:os.environ['CHINATRAVEL_OPENAI_BASE_URL']=config.base_url
    reset_cancellation();stop=threading.Event();health={'empty_failures':0};health_lock=threading.Lock()
    jobs=[]
    for i,event in enumerate(events):
        methods=a.methods[i%len(a.methods):]+a.methods[:i%len(a.methods)]
        jobs.extend((event,method) for method in methods)
    def execute(pair):
        if stop.is_set():return
        event,method=pair;key=event['uid']+('-'+event['event']['kind'] if a.kind=='dynamic' else '')
        work=folder/method/key
        if (work/'record.json').exists():return
        work.mkdir(parents=True,exist_ok=True)
        changed=changed_files(ROOT,hashes,inputs)
        if changed:
            stop.set();save(folder/'integrity-error.json',{'changed_files':changed});terminate_all()
            print('STOP_INPUT_CHANGED',changed,flush=True);return
        # An interrupted attempt may already have incurred usage. Preserve it as
        # an outcome rather than overwrite its journal and buy another attempt.
        if (work/'job.json').exists():
            job=json.loads((work/'job.json').read_text())
            result={'task_uid':event['uid'],'method':method,'terminal_state':'interrupted','plan':None}
            supervision=json.loads((work/'supervision.json').read_text()) if (work/'supervision.json').exists() else {'wall_seconds':None,'time_observation':'unknown after interrupted parent'}
            if (work/'result.json').exists() and supervision.get('returncode')==0 and not supervision.get('hard_timeout'):
                result=json.loads((work/'result.json').read_text())
        else:
            job=make_job(ROOT,event,a.kind,dict(settings,method=method));save(work/'job.json',job)
            print('START',a.run_id,key,method,flush=True)
            supervision=supervise([sys.executable,str(ROOT/'scripts/job_worker.py'),str(work/'job.json')],work,a.seconds,cwd=ROOT,restricted=True)
            result=json.loads((work/'result.json').read_text()) if (work/'result.json').exists() and not supervision['hard_timeout'] else {
                'task_uid':event['uid'],'method':method,'terminal_state':'cancelled' if supervision.get('cancelled') else 'hard_timeout' if supervision['hard_timeout'] else 'worker_error','plan':None}
        record={'job':job,'result':result,'supervision':supervision,'ledger':summarize(work/'requests.jsonl',prices),'resources':read_resources(work/'resources.jsonl')}
        save(work/'record.json',record)
        print('END',key,method,result['terminal_state'],round(supervision['wall_seconds'],2) if supervision.get('wall_seconds') is not None else None,record['ledger']['observed_usage']['total_tokens'],flush=True)
        statuses=[e.get('status_code') for e in record['ledger']['events'] if e['kind']=='error']
        with health_lock:
            if service_requires_stop(statuses,result.get('terminal_state')):stop.set()
            if result.get('terminal_state') in ('request_error','worker_error','hard_timeout') and record['ledger']['observed_usage']['total_tokens']==0:health['empty_failures']+=1
            else:health['empty_failures']=0
            if health['empty_failures']>=3:stop.set()
    pool=ThreadPoolExecutor(max_workers=a.workers)
    try:list(pool.map(execute,jobs))
    except BaseException:
        stop.set();terminate_all();pool.shutdown(wait=True,cancel_futures=True);raise
    else:pool.shutdown(wait=True)
    final_changes=changed_files(ROOT,hashes,inputs)
    if final_changes:
        stop.set();save(folder/'integrity-error.json',{'changed_files':final_changes})
    summary={m:{'expected':len(events),'completed':0,'known_tokens':0} for m in a.methods}
    for path in folder.glob('*/*/record.json'):
        record=json.loads(path.read_text());method=record['job']['method']
        summary[method]['completed']+=1
        summary[method]['known_tokens']+=record['ledger']['observed_usage']['total_tokens']
    save(folder/'summary.json',summary);print(json.dumps(summary),flush=True)
    if stop.is_set() or any(v['completed']!=v['expected'] for v in summary.values()):raise SystemExit(3)


if __name__=='__main__':main()
