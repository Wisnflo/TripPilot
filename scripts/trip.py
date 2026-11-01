#!/usr/bin/env python3
"""One supervised CLI for first planning, clarification and incremental revision."""
import argparse
import dataclasses
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str));temp.replace(path)


def worker(path):
    from trippilot.execution.budget import ResourceBudget,activate,deactivate,BudgetExceeded
    from trippilot.agent_input import build_agent_input
    from trippilot.models.config import resolve_model_config
    from trippilot.models.openai_adapter import ModelRequestError
    from trippilot.planning.structured import run_structured
    from trippilot.repair.intent import parse_change
    from trippilot.repair.negotiation import begin_revision,resolve_revision,state_event
    from trippilot.repair.agent import repair_incremental
    from trippilot.repair.transaction import revision
    from trippilot.repair.events import apply_event_to_task
    from trippilot.tools.agent_tools import ToolBox
    job=json.loads(path.read_text());folder=path.parent;clock=time.monotonic();deadline=clock+job['seconds']
    budget=ResourceBudget(token_limit=job.get('tokens',500000),tool_limit=job.get('tool_limit',400));budget_token=activate(budget)
    os.environ['TRIPPILOT_REQUEST_JOURNAL']=str(folder/'requests.jsonl')
    os.environ['TRIPPILOT_RESOURCE_JOURNAL']=str(folder/'resources.jsonl')
    config=dataclasses.replace(resolve_model_config(model_override=job['model']),
        request_timeout_seconds=job['request_timeout'],max_attempts=job['attempts'])
    try:
        if job['command']=='plan':
            task=build_agent_input(job['task'])
            trajectory=run_structured(task,config,ToolBox(),max_steps=16,max_seconds=job['seconds'])
            result={'status':trajectory.terminal_state,'task':task,'result':trajectory.as_dict(),'plan':trajectory.plan}
        else:
            if job['command']=='revise':
                task=build_agent_input(job['task']);seed=job['seed']
                event,usage=parse_change(job['change'],seed,config,deadline=deadline)
                state=begin_revision(task,seed,event);state.pop('state_revision')
                state['seed_path']=job['seed_path'];state['intent_usage']=usage;state['state_revision']=revision(state)
            else:
                state=resolve_revision(job['state'],job['answer'],current_seed=job['current_seed'])
            task=state['task'];result={'status':state['status'],'task':task,'state':state,'plan':state.get('plan')}
            if state['status']=='ready':
                remaining=deadline-time.monotonic()
                if remaining<=0:result['status']='timeout'
                else:
                    trajectory=repair_incremental(task,state['seed_plan'],state_event(state),config,ToolBox(),
                        max_steps=16-len(state.get('intent_usage',{}).get('requests',[])),max_seconds=remaining)
                    result.update(status=trajectory.terminal_state,result=trajectory.as_dict(),plan=trajectory.plan)
                    if trajectory.terminal_state=='checked_ok' and trajectory.plan is not None:
                        result['task']=apply_event_to_task(task,state_event(state))
        result['worker_wall_seconds']=time.monotonic()-clock
    except BudgetExceeded:
        result={'status':'budget_exhausted','plan':None,'worker_wall_seconds':time.monotonic()-clock}
    except ModelRequestError:
        result={'status':'request_error','plan':None,'worker_wall_seconds':time.monotonic()-clock}
    except (ValueError,KeyError,TypeError) as exc:
        result={'status':'invalid_request','plan':None,'message':str(exc),'worker_wall_seconds':time.monotonic()-clock}
    result['resources']=budget.as_dict()
    if budget.exhausted:result['status']='budget_exhausted'
    write(folder/'worker-result.json',result)
    deactivate(budget_token)


def parse_args():
    p=argparse.ArgumentParser(description='Travel Agent using sample ChinaTravel data; writes new local versions, no bookings.')
    p.add_argument('--worker-job',help=argparse.SUPPRESS)
    # Worker dispatch happens before parsing public subcommands.
    if '--worker-job' in sys.argv:return p.parse_args()
    subs=p.add_subparsers(dest='command',required=True)
    plan=subs.add_parser('plan');plan.add_argument('--task');plan.add_argument('--from-city');plan.add_argument('--to-city')
    plan.add_argument('--days',type=int);plan.add_argument('--people',type=int,default=1);plan.add_argument('--request')
    revise=subs.add_parser('revise');revise.add_argument('--task',required=True);revise.add_argument('--seed',required=True);revise.add_argument('--change',required=True)
    answer=subs.add_parser('answer');answer.add_argument('--state',required=True);answer.add_argument('--answer',choices=['cancel_overnight','keep_original'],required=True)
    for sub in [plan,revise,answer]:
        sub.add_argument('--output-dir',required=True);sub.add_argument('--model',default='qwen3.8-reasoner')
        sub.add_argument('--tokens',type=int,default=500000);sub.add_argument('--tool-limit',type=int,default=400)
        sub.add_argument('--seconds',type=float,default=360 if sub is plan else 240)
        sub.add_argument('--request-timeout',type=float,default=90);sub.add_argument('--attempts',type=int,default=2)
    a=p.parse_args()
    if min(a.seconds,a.request_timeout,a.attempts,a.tokens,a.tool_limit)<=0:p.error('time budgets/attempts must be positive')
    if a.command=='plan' and not a.task and not all([a.from_city,a.to_city,a.days,a.request]):p.error('supply --task or from/to/days/request')
    return a


def main():
    a=parse_args()
    if getattr(a,'worker_job',None):worker(Path(a.worker_job));return 0
    from trippilot.agent_input import build_agent_input
    from trippilot.plan.schema import validate_plan
    from trippilot.plan.checker import _tool_city,CITY_ZH_TO_EN
    from trippilot.execution.supervisor import supervise,terminate_all,reset_cancellation
    from trippilot.execution.ledger import summarize
    job={'command':a.command,'model':a.model,'seconds':a.seconds,'request_timeout':a.request_timeout,'attempts':a.attempts,'tokens':a.tokens,'tool_limit':a.tool_limit}
    if a.command=='plan':
        task=json.loads(Path(a.task).read_text()) if a.task else {
            'uid':'interactive-'+uuid.uuid4().hex[:12],'start_city':a.from_city,'target_city':a.to_city,
            'days':a.days,'people_number':a.people,'nature_language':a.request,'nature_language_en':'',
            'limit_rooms':False,'limits_room_type':False}
        task.setdefault('uid','interactive-'+uuid.uuid4().hex[:12])
        task=build_agent_input(task)
        if int(task.get('days',0))<=0 or int(task.get('people_number',0))<=0:raise SystemExit('days/people must be positive')
        if any(_tool_city(task.get(k)) not in CITY_ZH_TO_EN.values() for k in ['start_city','target_city']):raise SystemExit('Unsupported sandbox city')
        job['task']=task
    elif a.command=='revise':
        job['task']=build_agent_input(json.loads(Path(a.task).read_text()))
        payload=json.loads(Path(a.seed).read_text());seed=payload.get('plan',payload)
        if validate_plan(seed):raise SystemExit('Seed is not a valid plan; no model was called.')
        job.update(seed=seed,seed_path=str(Path(a.seed).resolve()),change=a.change)
    else:
        state=json.loads(Path(a.state).read_text());payload=json.loads(Path(state['seed_path']).read_text())
        job.update(state=state,answer=a.answer,current_seed=payload.get('plan',payload))
    folder=Path(a.output_dir);folder.mkdir(parents=True,exist_ok=False);write(folder/'job.json',job)
    from trippilot.models.config import resolve_model_config
    config=resolve_model_config(model_override=a.model,env_file=Path(os.environ['TRIPPILOT_CONFIG_FILE']) if os.environ.get('TRIPPILOT_CONFIG_FILE') else None)
    os.environ['CHINATRAVEL_OPENAI_API_KEY']=config.api_key
    if config.base_url:os.environ['CHINATRAVEL_OPENAI_BASE_URL']=config.base_url
    reset_cancellation();outcomes=[];errors=[];clock=time.monotonic()
    finished=threading.Event();cancel_requested=False
    def execute():
        try:outcomes.append(supervise([sys.executable,str(Path(__file__).resolve()),'--worker-job',str((folder/'job.json').resolve())],folder,a.seconds,cwd=ROOT,restricted=True))
        except Exception as exc:errors.append(type(exc).__name__)
        finally:finished.set()
    thread=threading.Thread(target=execute)
    try:
        thread.start()
        while not finished.wait(timeout=0.2):pass
    except KeyboardInterrupt:
        cancel_requested=True
        terminate_all()
        # Interrupted Thread.join can report a stopped thread before it has
        # finished. Wait on the completion event before reading its journals.
        if thread.ident is not None:finished.wait()
    supervision=outcomes[0] if outcomes else {'hard_timeout':False,'cancelled':cancel_requested,'wall_seconds':time.monotonic()-clock,'errors':errors}
    if cancel_requested:supervision['cancelled']=True
    if supervision.get('cancelled'):result={'status':'cancelled','plan':None}
    elif supervision['hard_timeout']:result={'status':'hard_timeout','plan':None}
    elif (folder/'worker-result.json').exists():result=json.loads((folder/'worker-result.json').read_text())
    else:result={'status':'worker_error','plan':None}
    prices=json.loads((ROOT/'config/v3-pricing.json').read_text());rates=prices['models'].get(a.model)
    record={'result':result,'supervision':supervision,'ledger':summarize(folder/'requests.jsonl',rates),
            'pricing_source':prices['source'],'pricing_observed_date':prices['observed_date'],
            'validation_scope':'public frozen tool facts and predicted requirements; not a live booking or hidden-label guarantee'}
    write(folder/'record.json',record)
    if result.get('state'):write(folder/'state.json',result['state'])
    if result.get('task'):write(folder/'task.json',result['task'])
    if result.get('plan') and result['status'] in ('checked_ok','cancelled'):write(folder/'plan.json',result['plan'])
    print(json.dumps({'status':result['status'],'output_dir':str(folder.resolve()),
        'question':result.get('state',{}).get('question') if result['status']=='needs_clarification' else None,
        'choices':result.get('state',{}).get('choices') if result['status']=='needs_clarification' else None,
        'seconds':supervision['wall_seconds'],'known_tokens':record['ledger']['observed_usage']['total_tokens'],
        'observed_usage_cost_CNY':record['ledger']['observed_usage_cost_CNY']},ensure_ascii=False))
    return 0 if result['status'] in ('checked_ok','needs_clarification','cancelled') else 2


if __name__=='__main__':raise SystemExit(main())
