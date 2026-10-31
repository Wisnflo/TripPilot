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
from trippilot.plan.checker import check_plan
from trippilot.runner import load_task
from trippilot.repair.agent import repair_incremental
from trippilot.repair.events import ChangeEvent,apply_event_to_task,event_text
from trippilot.repair.verification import validate_revision
from trippilot.repair.negotiation import begin_revision,resolve_revision


def main():
    path=Path(sys.argv[1]);job=json.loads(path.read_text());folder=path.parent
    os.environ['TRIPPILOT_REQUEST_JOURNAL']=str(folder/'requests.jsonl')
    config=dataclasses.replace(resolve_model_config(model_override=job['model']),
        request_timeout_seconds=job['request_timeout'],max_attempts=job['attempts'])
    box=ToolBox();task=load_task(job['split'],job['uid']);clock=time.monotonic()
    if job['kind']=='static':
        if job['method']=='structured':out=run_structured(task,config,box,job['steps'],job['seconds'])
        elif job['method']=='b2':out=run_react(task,config,box,method='b2',max_steps=job['steps'],max_seconds=job['seconds'],checker=lambda t,p:check_plan(t,p,toolbox=box))
        else:raise ValueError('unknown static method')
    elif job['kind']=='dynamic':
        payload=json.loads((ROOT/job['seed']).read_text());seed=payload.get('plan')
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
        if job['method']=='patch':out=repair_incremental(task,seed,event,config,box,max_steps=job['steps'],max_seconds=job['seconds'])
        elif job['method']=='b2':out=run_react(apply_event_to_task(task,event),config,box,method='b2',
            seed_plan=seed,event_text=event_text(event),max_steps=job['steps'],max_seconds=job['seconds'],
            checker=lambda t,p:validate_revision(task,seed,p,event,box))
        else:raise ValueError('unknown dynamic method')
    else:raise ValueError('unknown job kind')
    result=out.as_dict();result['wall_seconds']=time.monotonic()-clock
    if job['kind']=='dynamic':result['clarification']=clarification
    if job['kind']=='dynamic':
        result['event']=job['event'];result['event_check']=validate_revision(task,seed,out.plan,event,box).as_dict() if out.plan else None
    temp=folder/'result.tmp';temp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));temp.replace(folder/'result.json')


if __name__=='__main__':main()
