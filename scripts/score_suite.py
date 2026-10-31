#!/usr/bin/env python3
"""Offline-only independent scoring. No label is used by a planning worker."""
import argparse
import ast
import contextlib
import io
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'eval'),str(ROOT/'scripts'),str(ROOT/'upstream/ChinaTravel')]
import score_run
from score_incremental import rewrite_day_annotation
from chinatravel.evaluation.schema_constraint import evaluate_schema_constraints
from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints
from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2
from trippilot.repair.events import ChangeEvent,apply_event_to_task
from trippilot.repair.verification import event_violations


def is_lodging_only(code):
    """Conservative waiver classifier, used only AFTER explicit consent.

    Mixed clauses are retained. Removing a hotel condition is a user-approved
    task change; the same change applies to every method and is disclosed.
    """
    tree=ast.parse(code)
    calls={node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)}
    lodging={'accommodation_type','room_type','room_count'}
    allowed=lodging|{'allactivities','activity_type','activity_position','set'}
    guarded=any(isinstance(n,ast.Constant) and n.value=='accommodation' for n in ast.walk(tree))
    other_activity=any(isinstance(n,ast.Constant) and n.value in ('attraction','breakfast','lunch','dinner','train','airplane') for n in ast.walk(tree))
    return (bool(calls & lodging) or (guarded and 'activity_position' in calls)) and calls<=allowed and not other_activity


def has_lodging_consent(spec, result):
    event=spec.get('event') or {}
    state=result.get('clarification') or {}
    return (event.get('kind')=='return_earlier' and type(event.get('new_days')) is int and event['new_days']==1
            and spec.get('clarification_answer')=='cancel_overnight'
            and state.get('explicit_lodging_waiver') is True
            and state.get('answer')=='cancel_overnight'
            and bool(state.get('question'))
            and 'cancel_overnight' in (state.get('choices') or {}))


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);a=p.parse_args()
    folder=ROOT/'experiments/v3'/a.run_id;protocol=json.loads((folder/'protocol.json').read_text())
    schema=json.loads((ROOT/'upstream/ChinaTravel/chinatravel/evaluation/output_schema.json').read_text())
    maps=score_run.build_name_maps();rows=[]
    for method in protocol['methods']:
        for spec in protocol['events']:
            key=spec['uid']+('-'+spec['event']['kind'] if protocol['kind']=='dynamic' else '')
            path=folder/method/key/'record.json';row={'uid':spec['uid'],'split':spec['split'],'method':method,'key':key,'all_pass':False,'complete':path.exists()}
            if not path.exists():rows.append(row);continue
            record=json.loads(path.read_text());result=record['result'];plan=result.get('plan') or {}
            visible=json.loads((ROOT/'data/agent_visible'/spec['split']/(spec['uid']+'.json')).read_text())
            label=json.loads((ROOT/'data/scoring'/spec['split']/(spec['uid']+'.json')).read_text());query={**visible,**label}
            query['start_city']=score_run.CITY_ZH_TO_EN.get(query['start_city'],query['start_city'])
            query['target_city']=score_run.CITY_ZH_TO_EN.get(query['target_city'],query['target_city'])
            query['nature_language']=query.get('nature_language_en') or query['nature_language']
            event_ok=True;rewrites=0;waived=[];supported=True
            if protocol['kind']=='dynamic':
                from decimal import Decimal
                values=dict(spec['event'])
                if values.get('new_budget') is not None:values['new_budget']=Decimal(str(values['new_budget']))
                event=ChangeEvent(**values);query=apply_event_to_task(query,event)
                seed=json.loads((ROOT/spec['seed']).read_text())['plan']
                event_ok=bool(plan) and not event_violations(visible,seed,plan,event)
                codes=[]
                for index,code in enumerate(query['hard_logic_py']):
                    if event.kind=='return_earlier':code,changed=rewrite_day_annotation(code,event.new_days);rewrites+=int(changed)
                    if has_lodging_consent(spec,result) and is_lodging_only(code):
                        waived.append(index);code='result=True'
                    codes.append(code)
                query['hard_logic_py']=codes
                supported=event.kind!='return_earlier' or rewrites==1
            try:
                uid=spec['uid']
                with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                    _,_,sp=evaluate_schema_constraints([uid],{uid:plan},schema)
                    _,_,_,cp=evaluate_commonsense_constraints([uid],{uid:query},{uid:plan},verbose=False,lang='en')
                    hard=score_run.translate_plan_for_hard(plan,maps)
                    _,_,_,_,_,hp=evaluate_hard_constraints_v2([uid],{uid:query},{uid:hard},env_pass_id=cp,verbose=False,lang='zh')
                clean=result.get('terminal_state') in ('checked_ok','checked_with_violations','answer')
                row.update(schema=uid in sp,commonsense=uid in cp,hard=uid in hp,event_ok=event_ok,
                           all_pass=bool(clean and supported and event_ok and uid in sp and uid in cp and uid in hp),
                           terminal=result.get('terminal_state'),annotation_rewrites=rewrites,explicit_lodging_waivers=waived,
                           usability=bool(plan) and all(any(v.get('type')=='attraction' for v in d.get('activities',[])) and
                               any(v.get('type') in ('breakfast','lunch','dinner') for v in d.get('activities',[])) for d in plan.get('itinerary',[])))
            except Exception as exc:row['evaluation_error']=type(exc).__name__
            rows.append(row)
    summary={m:{'expected':len(protocol['events']),'complete':sum(r['complete'] for r in rows if r['method']==m),
                'all_pass':sum(r['all_pass'] for r in rows if r['method']==m),
                'usable_and_pass':sum(r['all_pass'] and r.get('usability',False) for r in rows if r['method']==m)} for m in protocol['methods']}
    output={'kind':protocol['kind'],'note':'Original official static scoring; dynamic uses explicit event adaptations/consent, NOT official dynamic gold.', 'summary':summary,'rows':rows}
    (folder/'scores.json').write_text(json.dumps(output,ensure_ascii=False,indent=2));print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
