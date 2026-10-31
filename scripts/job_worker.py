#!/usr/bin/env python3
"""One allowlisted benchmark job; parent supervises and preserves its journal."""
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.models.config import resolve_model_config
from trippilot.tools.agent_tools import ToolBox
from trippilot.agent.loop import run_react
from trippilot.planning.structured import run_structured
from trippilot.planning.context import public_context
from trippilot.plan.checker import check_plan
from trippilot.agent_input import build_agent_input
from trippilot.execution.budget import ResourceBudget,activate
from trippilot.repair.agent import repair_incremental
from trippilot.repair.events import ChangeEvent,apply_event_to_task,event_text
from trippilot.repair.verification import validate_revision
from trippilot.repair.negotiation import begin_revision,resolve_revision


def main():
    path=Path(sys.argv[1]);job=json.loads(path.read_text());folder=path.parent
    os.environ['TRIPPILOT_REQUEST_JOURNAL']=str(folder/'requests.jsonl')
    os.environ['TRIPPILOT_RESOURCE_JOURNAL']=str(folder/'resources.jsonl')
    config=dataclasses.replace(resolve_model_config(model_override=job['model']),
        request_timeout_seconds=job['request_timeout'],max_attempts=job['attempts'])
    budget=ResourceBudget(model_limit=job['steps'],token_limit=job.get('tokens',500000),tool_limit=job.get('tool_limit',400),output_limit=job.get('output_tokens',10000));activate(budget)
    box=ToolBox();task=build_agent_input(job['task']);clock=time.monotonic()
    if job['kind']=='static':
        facts=public_context(task,box,clock+job['seconds']) if job.get('shared_context') else None
        remaining=max(0,job['seconds']-(time.monotonic()-clock))
        if job['method'] in ('structured','no_feedback','direct_plan'):
            out=run_structured(task,config,box,job['steps'],remaining,
                feedback=job['method']!='no_feedback',compile_plan=job['method']!='direct_plan',shared_facts=facts)
        elif job['method'] in ('b1','b2','b0'):
            if facts:
                task=dict(task);task['nature_language_en']=(task.get('nature_language_en') or '')+'\nShared public evidence packet: '+json.dumps(facts,ensure_ascii=False)
            if job['method']=='b0':
                from trippilot.agent.loop import run_single_shot
                out=run_single_shot(task,config,max_seconds=remaining,toolbox=box)
            else:
                out=run_react(task,config,box,method=job['method'],max_steps=job['steps'],max_seconds=remaining,checker=lambda t,p:check_plan(t,p,toolbox=box))
        else:raise ValueError('unknown static method')
    elif job['kind']=='dynamic':
        seed=job['seed_plan']
        e=dict(job['event'])
        if e.get('new_budget') is not None:e['new_budget']=Decimal(str(e['new_budget']))
        event=ChangeEvent(**e)
        clarification=begin_revision(task,seed,event)
        if clarification['status']=='needs_clarification':
            if job.get('clarification_answer'):
                clarification=resolve_revision(clarification,job['clarification_answer'],current_seed=seed)
                task=clarification['task']
            else:
                from trippilot.agent.loop import Trajectory
                out=Trajectory(task['uid'],job['method'],config.model,'needs_clarification',None,
                               metadata={'clarification':clarification})
                (folder/'result.json').write_text(json.dumps(out.as_dict(),ensure_ascii=False,indent=2))
                return
        if job['method'] in ('patch','rules'):out=repair_incremental(task,seed,event,config,box,policy=job['method'],max_steps=job['steps'],max_seconds=job['seconds'])
        elif job['method']=='b2':out=run_react(apply_event_to_task(task,event),config,box,method='b2',
            seed_plan=seed,event_text=event_text(event),max_steps=job['steps'],max_seconds=job['seconds'],
            checker=lambda t,p:validate_revision(task,seed,p,event,box))
        else:raise ValueError('unknown dynamic method')
    else:raise ValueError('unknown job kind')
    result=out.as_dict();result['wall_seconds']=time.monotonic()-clock
    result['resources']=budget.as_dict()
    if budget.exhausted:result['terminal_state']='budget_exhausted'
    if job['kind']=='dynamic':result['clarification']=clarification
    if job['kind']=='dynamic':
        result['event']=job['event'];result['event_check']=validate_revision(task,seed,out.plan,event,box).as_dict() if out.plan else None
    temp=folder/'result.tmp';temp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));temp.replace(folder/'result.json')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        from trippilot.execution.budget import current, BudgetExceeded
        budget=current();job=json.loads(Path(sys.argv[1]).read_text())
        terminal='budget_exhausted' if isinstance(exc,BudgetExceeded) else 'timeout' if isinstance(exc,TimeoutError) else 'request_error' if type(exc).__name__=='ModelRequestError' else 'worker_error'
        result={'task_uid':job['uid'],'method':job['method'],'terminal_state':terminal,'plan':None,'error_type':type(exc).__name__,'resources':budget.as_dict() if budget else None}
        (Path(sys.argv[1]).parent/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
