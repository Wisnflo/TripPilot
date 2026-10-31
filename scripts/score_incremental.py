#!/usr/bin/env python3
"""OFFLINE ONLY: derive dynamic checks from existing static annotations.

Only an exact day_count(plan)==N annotation is rewritten for return_earlier.
All other conditions stay unchanged; unavailable resources are checked explicitly.
These are derived dynamic labels, not official benchmark dynamic gold. Runtime
modules do not import this file or read scoring data.
"""
import argparse
import ast
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'eval'), str(ROOT/'upstream/ChinaTravel')]
import score_run
from trippilot.repair.events import ChangeEvent, apply_event_to_task
from trippilot.repair.verification import event_violations
from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints
from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2
from chinatravel.evaluation.schema_constraint import evaluate_schema_constraints


def rewrite_day_annotation(source, new_days):
    """Match the complete simple assignment, never a substring replacement."""
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return source, False
    assign = tree.body[0]
    value = assign.value
    if (len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name) or assign.targets[0].id != 'result'
        or not isinstance(value, ast.Compare) or len(value.ops) != 1 or not isinstance(value.ops[0], ast.Eq)
        or not isinstance(value.left, ast.Call) or not isinstance(value.left.func, ast.Name)
        or value.left.func.id != 'day_count' or len(value.left.args) != 1
        or not isinstance(value.left.args[0], ast.Name) or value.left.args[0].id != 'plan'
        or not isinstance(value.comparators[0], ast.Constant) or type(value.comparators[0].value) is not int):
        return source, False
    value.comparators[0] = ast.Constant(value=new_days)
    return ast.unparse(ast.fix_missing_locations(tree)), True


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',required=True)
    a=p.parse_args()
    root=ROOT/a.run_dir
    protocol=json.loads((root/'protocol.json').read_text())
    schema=json.loads((ROOT/'upstream/ChinaTravel/chinatravel/evaluation/output_schema.json').read_text())
    names=score_run.build_name_maps()
    all_rows=[]
    for method in protocol['methods']:
        for spec in protocol['events']:
            path=root/method/(spec['uid']+'-'+spec['event']+'.json')
            if not path.exists():
                all_rows.append({'uid':spec['uid'],'method':method,'completed':False,'derived_pass':False})
                continue
            result=json.loads(path.read_text())
            plan=result.get('plan') or {}
            task=json.loads((ROOT/'data/agent_visible'/spec['split']/(spec['uid']+'.json')).read_text())
            labels=json.loads((ROOT/'data/scoring'/spec['split']/(spec['uid']+'.json')).read_text())
            from decimal import Decimal
            event_data = dict(result['event'])
            if event_data.get('new_budget') is not None:
                event_data['new_budget'] = Decimal(str(event_data['new_budget']))
            event=ChangeEvent(**event_data)
            query=apply_event_to_task(task,event)
            query.update(copy.deepcopy(labels))
            query['start_city']=score_run.CITY_ZH_TO_EN.get(task.get('start_city'),task.get('start_city'))
            query['target_city']=score_run.CITY_ZH_TO_EN.get(task.get('target_city'),task.get('target_city'))
            query['nature_language']=query.get('nature_language_en') or query.get('nature_language')
            changed=0
            if event.kind=='return_earlier':
                replaced=[]
                for code in query['hard_logic_py']:
                    code,matched=rewrite_day_annotation(code,event.new_days)
                    replaced.append(code)
                    changed+=int(matched)
                query['hard_logic_py']=replaced
            supported=event.kind!='return_earlier' or changed==1
            row={'uid':spec['uid'],'method':method,'completed':True,'annotation_rewrites':changed,
                 'adaptation_supported':supported,'derived_pass':False}
            try:
                uid=spec['uid']
                _,_,schema_pass=evaluate_schema_constraints([uid],{uid:plan},schema)
                _,_,_,cs_pass=evaluate_commonsense_constraints([uid],{uid:query},{uid:plan},verbose=False,lang='en')
                hard_plan=score_run.translate_plan_for_hard(plan,names)
                _,_,_,_,_,hard_pass=evaluate_hard_constraints_v2([uid],{uid:query},{uid:hard_plan},env_pass_id=cs_pass,verbose=False,lang='zh')
                clean=result['terminal_state']=='checked_ok'
                public_ok=bool((result.get('event_check') or {}).get('ok'))
                row.update(schema=uid in schema_pass,commonsense=uid in cs_pass,hard=uid in hard_pass,
                           derived_pass=bool(supported and clean and public_ok and uid in schema_pass and uid in cs_pass and uid in hard_pass))
            except Exception as exc:
                row['evaluation_error']=type(exc).__name__
            all_rows.append(row)
    totals={m:{'expected':len(protocol['events']),'completed':sum(r['completed'] for r in all_rows if r['method']==m),
               'derived_pass':sum(r['derived_pass'] for r in all_rows if r['method']==m)} for m in protocol['methods']}
    output={'note':'Derived dynamic evaluation: exact day-count rewrite only; not official dynamic gold.',
            'totals':totals,'rows':all_rows}
    (root/'derived-scores.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps(totals),flush=True)


if __name__=='__main__':main()
