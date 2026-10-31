#!/usr/bin/env python3
"""Conversational revision CLI. Reads public task + current plan; writes new file."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.models.config import resolve_model_config
from trippilot.models.openai_adapter import ModelRequestError
from trippilot.repair.intent import parse_change
from trippilot.repair.agent import repair_incremental
from trippilot.repair.transaction import revision, patch_between
from trippilot.repair.verification import validate_revision
from trippilot.tools.agent_tools import ToolBox


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--task',required=True,help='agent-visible task JSON, never scoring JSON')
    p.add_argument('--seed',required=True,help='plan or trajectory JSON')
    p.add_argument('--change',required=True)
    p.add_argument('--output',required=True,help='new output file; existing files are refused')
    p.add_argument('--model',default='qwen3.8-reasoner')
    p.add_argument('--seconds',type=float,default=180)
    p.add_argument('--rules-only',action='store_true',help='offline explicit parser + rules; no LLM calls')
    a=p.parse_args()
    output=Path(a.output)
    if output.exists():
        raise SystemExit('Output already exists; choose a new revision filename.')
    task=json.loads(Path(a.task).read_text())
    # Reuse the same allowlist boundary as the benchmark runtime.
    from trippilot.agent_input import build_agent_input
    task=build_agent_input(task)
    payload=json.loads(Path(a.seed).read_text())
    seed=payload.get('plan',payload)
    config=None if a.rules_only else resolve_model_config(model_override=a.model)
    clock=time.monotonic()
    try:
        event,parser=parse_change(a.change,seed,config,deadline=clock+a.seconds)
    except ModelRequestError as exc:
        # A parser request can also fail after being billed. Preserve attempts,
        # but never invent usage when the provider returned none.
        data={'terminal_state':'request_error','phase':'intent_parser',
              'base_revision':revision(seed),'request_count':exc.attempts,
              'request_latencies':exc.request_latencies,'usage_complete':False,
              'combined_usage':None,'cost':None,'plan':None,
              'error_type':type(exc.last_error).__name__,
              'total_wall_seconds':time.monotonic()-clock}
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x') as file:
            json.dump(data,file,ensure_ascii=False,indent=2)
        print(json.dumps({'terminal':'request_error','phase':'intent_parser','output':str(output.resolve())}))
        return 2
    box=ToolBox()
    result=repair_incremental(task,seed,event,config,box,policy='rules' if a.rules_only else 'patch',
                              max_seconds=max(0,a.seconds-(time.monotonic()-clock)),max_steps=16)
    data=result.as_dict()
    data.update(change=a.change,event=event.__dict__,intent_parser=parser,
                total_wall_seconds=time.monotonic()-clock,base_revision=revision(seed),
                preservation=patch_between(seed,result.plan) if result.plan else None)
    if result.plan:
        check=validate_revision(task,seed,result.plan,event,box)
        data['event_check']=check.as_dict()
    # The benchmark starts with typed events; this product command also reports
    # the parser's costs, making that distinction explicit.
    data['combined_usage']={key: None if parser['usage'].get(key) is None else
                            parser['usage'].get(key,0)+(result.usage.get(key,0) or 0)
                            for key in ('prompt_tokens','completion_tokens','total_tokens')}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as file:
        json.dump(data,file,ensure_ascii=False,indent=2,default=str)
    print(json.dumps({'terminal':result.terminal_state,'output':str(output.resolve()),
                      'seconds':data['total_wall_seconds'],'tokens':data['combined_usage']['total_tokens'],
                      'preservation':data['preservation']},ensure_ascii=False))
    return 0 if result.terminal_state=='checked_ok' else 2


if __name__=='__main__':raise SystemExit(main())
