#!/usr/bin/env python3
"""V3 fixed manifest runner, process deadlines, source hashes and full attempt ledger."""
import argparse
import hashlib
import json
import sys
import tarfile
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.execution.supervisor import supervise,terminate_all,reset_cancellation
import threading
from trippilot.execution.ledger import summarize


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',default='config/v3-tasks.json')
    p.add_argument('--partition',choices=['dev','test','dynamic','repeat'],required=True)
    p.add_argument('--kind',choices=['static','dynamic'],default='static')
    p.add_argument('--methods',nargs='+',default=['structured','b2'])
    p.add_argument('--model',default='qwen3.8-reasoner');p.add_argument('--run-id',required=True)
    p.add_argument('--seconds',type=float,default=240);p.add_argument('--steps',type=int,default=16)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--limit',type=int)
    p.add_argument('--request-timeout',type=float,default=60);p.add_argument('--attempts',type=int,default=2)
    a=p.parse_args()
    if min(a.seconds,a.steps,a.workers,a.request_timeout,a.attempts)<=0:p.error('budgets must be positive')
    if a.limit is not None and a.limit<=0:p.error('limit must be positive when provided')
    allowed={'static':{'structured','b2'},'dynamic':{'patch','b2'}}[a.kind]
    if not a.methods or not set(a.methods)<=allowed or len(a.methods)!=len(set(a.methods)):p.error('invalid or duplicate methods for job kind')
    events=json.loads((ROOT/a.manifest).read_text())[a.partition]
    keys=[(e['uid'],e['event']['kind'] if a.kind=='dynamic' else '') for e in events]
    if len(keys)!=len(set(keys)):p.error('manifest contains duplicate job identities')
    if a.limit:events=events[:a.limit]
    folder=ROOT/'experiments/v3'/a.run_id;folder.mkdir(parents=True,exist_ok=True)
    hashes={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for base in ['src','scripts','tests'] for f in sorted((ROOT/base).rglob('*.py'))}
    input_hashes={}
    for event in events:
        candidates=[ROOT/'data/agent_visible'/event['split']/(event['uid']+'.json'), ROOT/'data/scoring'/event['split']/(event['uid']+'.json')]
        if a.kind=='dynamic':candidates.append(ROOT/event['seed'])
        for f in candidates:input_hashes[str(f.relative_to(ROOT))]=hashlib.sha256(f.read_bytes()).hexdigest()
    price_path=ROOT/'config/v3-pricing.json'
    price_document=json.loads(price_path.read_text())
    prices=price_document['models'].get(a.model)
    protocol={'pricing':{'currency':'CNY','rates':prices,'observed_date':price_document['observed_date'],'sha256':hashlib.sha256(price_path.read_bytes()).hexdigest()},'kind':a.kind,'partition':a.partition,'events':events,'methods':a.methods,'model':a.model,
              'steps':a.steps,'seconds':a.seconds,'timeout':a.request_timeout,'attempts':a.attempts,'workers':a.workers,'data_sha256':input_hashes,
              'source_sha256':hashes,'manifest_sha256':hashlib.sha256((ROOT/a.manifest).read_bytes()).hexdigest()}
    frozen=folder/'protocol.json'
    if frozen.exists() and json.loads(frozen.read_text())!=protocol:raise SystemExit('Protocol changed; use new run-id.')
    frozen.write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
    if not (folder/'source.tar.gz').exists():
        with tarfile.open(folder/'source.tar.gz','w:gz') as archive:
            for relative in hashes:archive.add(ROOT/relative,arcname=relative)
            for relative in ['pyproject.toml','uv.lock','.python-version']:archive.add(ROOT/relative,arcname=relative)
    prices=json.loads((ROOT/'config/v3-pricing.json').read_text())['models'].get(a.model)
    reset_cancellation()
    stop=threading.Event()
    health={'empty_failures':0}
    health_lock=threading.Lock()
    jobs=[]
    for i,event in enumerate(events):
        methods=a.methods[i%len(a.methods):]+a.methods[:i%len(a.methods)]
        for method in methods:jobs.append((event,method))
    def execute(pair):
        if stop.is_set():return
        event,method=pair;key=event['uid']+('-'+event['event']['kind'] if a.kind=='dynamic' else '')
        work=folder/method/key
        if (work/'record.json').exists():return
        work.mkdir(parents=True,exist_ok=True)
        changed=[relative for relative,wanted in {**hashes,**input_hashes}.items() if not (ROOT/relative).exists() or hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=wanted]
        if changed:
            (work/'record.json').write_text(json.dumps({'job':dict(event,method=method),'result':{'terminal_state':'code_changed','plan':None},'supervision':{'wall_seconds':0},'ledger':{'observed_usage':{'total_tokens':0}},'changed_files':changed},indent=2))
            print('STOP_CODE_CHANGED',changed,flush=True)
            return
        job={**event,'method':method,'kind':a.kind,'model':a.model,'steps':a.steps,'seconds':a.seconds,
             'request_timeout':a.request_timeout,'attempts':a.attempts}
        (work/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
        print('START',a.run_id,key,method,flush=True)
        supervision=supervise([sys.executable,str(ROOT/'scripts/job_worker.py'),str(work/'job.json')],work,a.seconds,cwd=ROOT)
        result=json.loads((work/'result.json').read_text()) if (work/'result.json').exists() and not supervision['hard_timeout'] else {
            'task_uid':event['uid'],'method':method,'terminal_state':'cancelled' if supervision.get('cancelled') else 'hard_timeout' if supervision['hard_timeout'] else 'worker_error','plan':None}
        record={'job':job,'result':result,'supervision':supervision,'ledger':summarize(work/'requests.jsonl',prices)}
        temp=work/'record.tmp';temp.write_text(json.dumps(record,ensure_ascii=False,indent=2));temp.replace(work/'record.json')
        print('END',key,method,result['terminal_state'],round(supervision['wall_seconds'],2),record['ledger']['observed_usage']['total_tokens'],flush=True)
        statuses=[e.get('status_code') for e in record['ledger']['events'] if e['kind']=='error']
        with health_lock:
            if any(v in (401,403,429) for v in statuses):stop.set()
            if result.get('terminal_state') in ('request_error','worker_error','hard_timeout') and record['ledger']['observed_usage']['total_tokens']==0:
                health['empty_failures']+=1
            else:health['empty_failures']=0
            if health['empty_failures']>=3:stop.set()
    pool=ThreadPoolExecutor(max_workers=a.workers)
    try:
        list(pool.map(execute,jobs))
    except BaseException:
        stop.set();terminate_all()
        pool.shutdown(wait=True,cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    records=[json.loads(f.read_text()) for f in folder.glob('*/*/record.json')]
    summary={m:{'expected':len(events),'completed':sum(r['job']['method']==m for r in records),
                   'known_tokens':sum(r['ledger']['observed_usage']['total_tokens'] for r in records if r['job']['method']==m)} for m in a.methods}
    (folder/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
    if stop.is_set() or any(r['result'].get('terminal_state')=='code_changed' for r in records):raise SystemExit(3)


if __name__=='__main__':main()
