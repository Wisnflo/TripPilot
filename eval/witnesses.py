"""Construct known-feasible revision tasks before running compared repair methods.

Uses public tools and original-plan alternatives, never repair.agent/proposals.
Hidden constraints are checked only here, in the independent task-construction role.
"""
from collections import Counter,defaultdict
import contextlib
import copy
from decimal import Decimal,ROUND_FLOOR
import io
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'eval'),str(ROOT/'scripts'),str(ROOT/'upstream/ChinaTravel')]
import score_run
from score_incremental import rewrite_day_annotation
from score_suite import is_lodging_only
from chinatravel.evaluation.schema_constraint import evaluate_schema_constraints
from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints
from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2
from trippilot.repair.events import ChangeEvent,apply_event_to_task
from trippilot.repair.verification import event_violations
from trippilot.repair.negotiation import begin_revision,resolve_revision
from trippilot.plan.checker import check_plan,_tool_city,_minutes,CITY_ZH_TO_EN
from trippilot.money import plan_total,multiply,amount
from trippilot.tools.agent_tools import ToolBox


def load(p):return json.loads(Path(p).read_text())


class Judge:
    def __init__(self):
        self.schema=load(ROOT/'upstream/ChinaTravel/chinatravel/evaluation/output_schema.json');self.maps=score_run.build_name_maps()
    def passes(self,task,label,seed,plan,event,state):
        if event_violations(task,seed,plan,event):return False
        query={**task,**label};query=apply_event_to_task(query,event);query['start_city']=_tool_city(query['start_city']);query['target_city']=_tool_city(query['target_city'])
        codes=[];changed=0
        for code in query['hard_logic_py']:
            if event.kind=='return_earlier':code,yes=rewrite_day_annotation(code,event.new_days);changed+=int(yes)
            if state.get('explicit_lodging_waiver') and is_lodging_only(code):code='result=True'
            codes.append(code)
        query['hard_logic_py']=codes
        if event.kind=='return_earlier' and changed!=1:return False
        uid=task['uid']
        try:
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                _,_,sp=evaluate_schema_constraints([uid],{uid:plan},self.schema)
                _,_,_,cp=evaluate_commonsense_constraints([uid],{uid:query},{uid:plan},verbose=False,lang='en')
                hard=score_run.translate_plan_for_hard(plan,self.maps)
                _,_,_,_,_,hp=evaluate_hard_constraints_v2([uid],{uid:query},{uid:hard},env_pass_id=cp,verbose=False,lang='zh')
            usable=bool(plan.get('itinerary')) and all(any(a.get('type')=='attraction' for a in d['activities']) and any(a.get('type') in ('breakfast','lunch','dinner') for a in d['activities']) for d in plan['itinerary'])
            return bool(uid in sp and uid in cp and uid in hp and usable)
        except Exception:return False


def route(box,city,start,end,when,mode,people):
    if start==end:return []
    value=box.call('goto',{'city':city,'start':start,'end':end,'start_time':when,'transport_type':mode})
    legs=value.get('data')
    if not value.get('success') or not isinstance(legs,list) or not legs:return None
    if any(not isinstance(leg,dict) or _minutes(leg.get('start_time')) is None or _minutes(leg.get('end_time')) is None for leg in legs):return None
    legs=copy.deepcopy(legs)
    for leg in legs:
        leg['price']=leg.get('cost',0)
        if leg.get('mode')=='taxi':leg.update(cars=1,cost=multiply(leg['price'],1))
        elif leg.get('mode')=='metro':leg.update(tickets=people,cost=multiply(leg['price'],people))
        else:leg['cost']=0
    return legs


def position(a):return a.get('end') if a.get('type') in ('train','airplane') else a.get('position')

def public_rows(box,start,end,kind):
    def city(value):
        value=_tool_city(value)
        return next((v for v in CITY_ZH_TO_EN.values() if v.lower() in str(value).lower()),value)
    start,end=city(start),city(end)
    return [r for r in box.intercity_rows(start,end,kind) if isinstance(r.get('Cost'),(int,float)) and math.isfinite(r['Cost']) and isinstance(r.get('TrainID') or r.get('FlightID'),str) and _minutes(r.get('EndTime')) is not None and _minutes(r.get('BeginTime')) is not None and _minutes(r['EndTime'])>_minutes(r['BeginTime'])]


def replacements(seed,box):
    for d,day in enumerate(seed['itinerary']):
        for a,old in enumerate(day['activities']):
            if old.get('type') not in ('train','airplane'):continue
            rows=public_rows(box,old['start'],old['end'],old['type'])
            rows.sort(key=lambda r:amount(r['Cost']))
            for row in rows[:24]:
                field='TrainID' if old['type']=='train' else 'FlightID'
                if row[field]==old.get(field):continue
                # Only change the departure if it fits the unchanged adjacent
                # route. This constructor is distinct from the repair search.
                lower=_minutes(old['transports'][-1]['end_time']) if old.get('transports') else _minutes(day['activities'][a-1]['end_time']) if a else 0
                upper=_minutes(day['activities'][a+1]['transports'][0]['start_time']) if a+1<len(day['activities']) and day['activities'][a+1].get('transports') else 1439
                if _minutes(row['BeginTime'])<lower or _minutes(row['EndTime'])>upper:continue
                new=copy.deepcopy(seed);v=new['itinerary'][d]['activities'][a]
                v.update(start=row['From'],end=row['To'],start_time=row['BeginTime'],end_time=row['EndTime'],price=float(amount(row['Cost'])),cost=multiply(row['Cost'],seed['people_number']))
                v[field]=row[field]
                if (v['start'],v['end'])!=(old['start'],old['end']):continue
                yield v[field],old[field],new


def shorter(task,seed,box):
    n=len(seed['itinerary'])-1
    if n<1:return
    candidate=copy.deepcopy(seed);candidate['itinerary']=candidate['itinerary'][:n]
    last=candidate['itinerary'][-1]['activities'];last[:]=[a for a in last if a.get('type')!='accommodation']
    if not last:return
    if _tool_city(task['start_city'])==_tool_city(task['target_city']):yield candidate;return
    old_return=seed['itinerary'][-1]['activities'][-1]
    if old_return.get('type') not in ('train','airplane'):return
    kind=old_return['type'];field='TrainID' if kind=='train' else 'FlightID'
    rows=public_rows(box,_tool_city(task['target_city']),_tool_city(task['start_city']),kind)
    rows.sort(key=lambda r:(r[field]!=old_return.get(field),amount(r['Cost'])))
    previous=last[-1]
    for r in rows[:24]:
        for mode in ('taxi','metro','walk'):
            legs=route(box,_tool_city(task['target_city']),position(previous),r['From'],previous['end_time'],mode,seed['people_number'])
            if legs is None or legs and _minutes(legs[-1]['end_time'])>_minutes(r['BeginTime']):continue
            value={'type':kind,'start':r['From'],'end':r['To'],'start_time':r['BeginTime'],'end_time':r['EndTime'],'price':float(amount(r['Cost'])),'cost':multiply(r['Cost'],seed['people_number']),'tickets':seed['people_number'],'transports':legs,field:r[field]}
            new=copy.deepcopy(candidate);new['itinerary'][-1]['activities'].append(value);yield new


def cheaper_routes(task,seed,box):
    current=copy.deepcopy(seed)
    for d,day in enumerate(seed['itinerary']):
        for a,activity in enumerate(day['activities']):
            old=activity.get('transports') or []
            if not old:continue
            options=[]
            for mode in ('walk','metro','taxi'):
                legs=route(box,_tool_city(task['target_city']),old[0]['start'],old[-1]['end'],old[0]['start_time'],mode,seed['people_number'])
                if legs is not None and (not legs or _minutes(legs[-1]['end_time'])<=_minutes(activity['start_time'])):options.append(legs)
            if options:
                best=min(options,key=lambda rows:sum(amount(x.get('cost',0)) for x in rows))
                if sum(amount(x.get('cost',0)) for x in best)>=sum(amount(x.get('cost',0)) for x in old):continue
                current['itinerary'][d]['activities'][a]['transports']=best;yield copy.deepcopy(current)


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--static-run-id',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    run=ROOT/'experiments/v3'/a.static_run_id;protocol=load(run/'protocol.json');scores=load(run/'scores.json')
    passing={(r['method'],r['uid']) for r in scores['rows'] if r.get('all_pass') and r.get('usability')}
    policy=load(ROOT/'config/completion-tasks.json')['protocol'];methods=policy['dynamic_seed_priority']
    output=ROOT/a.output
    if output.exists():raise SystemExit('Output exists; use a new task set')
    output.parent.mkdir(parents=True,exist_ok=True);proof_dir=output.parent/(output.stem+'-solutions');proof_dir.mkdir(exist_ok=False)
    judge=Judge();box=ToolBox();selected=[];counts=Counter();by_split=defaultdict(Counter);attempted=Counter()
    strata_targets=policy['dynamic_event_split_targets'];targets={kind:sum(groups.values()) for kind,groups in strata_targets.items()};clock=time.monotonic()
    # The parent order and method priority are declared before repair results.
    strata={split:[e for e in protocol['events'] if e['split']==split] for split in ('easy','medium','human')}
    ordered=[strata[split][i] for i in range(max(map(len,strata.values()))) for split in strata if i<len(strata[split])]
    for spec in ordered:
        uid=spec['uid'];split=spec['split'];valid=[]
        for method in methods:
            if (method,uid) in passing:
                path=run/method/uid/'result.json'
                if path.exists():valid.append((method,path,load(path)['plan']))
        if not valid:continue
        method,source,seed=valid[0];task=load(ROOT/'data/agent_visible'/split/(uid+'.json'));label=load(ROOT/'data/scoring'/split/(uid+'.json'))
        variants=list(replacements(seed,box))
        for kind in targets:
            if counts[kind]>=targets[kind] or by_split[kind][split]>=strata_targets[kind][split]:continue
            if kind=='return_earlier':
                n=len(seed['itinerary'])-1
                if n<1:continue
                event=ChangeEvent(kind,new_days=n);candidates=shorter(task,seed,box)
            elif kind=='transport_unavailable':
                if not variants:continue
                event=ChangeEvent(kind,target=variants[0][1]);candidates=(plan for _,target,plan in variants if target==event.target)
            else:
                budget=(plan_total(seed)*Decimal('0.95')).to_integral_value(rounding=ROUND_FLOOR)
                event=ChangeEvent(kind,new_budget=budget)
                alternatives=[plan for _,_,plan in valid[1:]]+[plan for _,_,plan in variants]
                def budget_candidates():
                    yield from alternatives
                    yield from cheaper_routes(task,seed,box)
                candidates=budget_candidates()
            state=begin_revision(task,seed,event)
            if state['status']=='needs_clarification':state=resolve_revision(state,'cancel_overnight',current_seed=seed)
            for number,candidate in enumerate(candidates):
                if number>=policy['dynamic_reference_search_limit']:break
                attempted[kind]+=1
                if kind=='budget_cut' and plan_total(candidate)>event.new_budget:continue
                if not judge.passes(task,label,seed,candidate,event,state):continue
                values={k:str(v) if isinstance(v,Decimal) else v for k,v in event.__dict__.items()}
                values={k:v for k,v in values.items() if v is not None}
                name=uid+'-'+kind;proof=proof_dir/(name+'.json')
                proof.write_text(json.dumps({'task_uid':uid,'event':values,'plan':candidate,'complete_constraint_check':True,'construction':'independent public-tool constructor and previously valid alternative initial plans; repair methods not executed','seed_method':method,'clarification':state},ensure_ascii=False,indent=2))
                selected.append({'uid':uid,'split':split,'seed':str(source.relative_to(ROOT)),'event':values,'clarification_answer':'cancel_overnight' if state.get('explicit_lodging_waiver') else None,'solution_reference':str(proof.relative_to(ROOT))})
                counts[kind]+=1;by_split[kind][split]+=1;print('CONSTRUCTED',kind,counts[kind],uid,flush=True);break
        if sum(counts.values())==sum(targets.values()):break
    result={'dynamic':selected,'targets':targets,'strata_targets':strata_targets,'strata_completed':{k:dict(v) for k,v in by_split.items()},'completed':dict(counts),'shortfall':{k:targets[k]-counts[k] for k in targets},'construction_attempts':dict(attempted),'construction_seconds':time.monotonic()-clock,'initial_parent_count':len(protocol['events']),'dynamic_parent_count':len({s['uid'] for s in selected}),'seed_method_priority':methods,'note':'Known-feasible constructive tasks; parent order and quotas fixed before compared repair outcomes. The solution files are offline-only, never passed to workers.'}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='dynamic'}),flush=True)

if __name__=='__main__':main()
