"""Offline-only metrics for planning and plan revision, including failed tasks."""
from collections import Counter
import csv
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from statistics_tools import wilson,paired as paired_statistics
from trippilot.repair.dependencies import affected_activity_ids
from trippilot.plan.compare import activity_map


def activity_rows(plan):
    if not isinstance(plan,dict) or not isinstance(plan.get('itinerary'),list):return
    for day in plan['itinerary']:
        if not isinstance(day,dict) or not isinstance(day.get('activities'),list):continue
        for activity in day['activities']:
            if isinstance(activity,dict):yield day.get('day'),activity


def load(path):return json.loads(Path(path).read_text())
def quantile(values,p):
    if not values:return None
    values=sorted(values);position=p*(len(values)-1);lower=math.floor(position);upper=math.ceil(position)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


class EntityIndex:
    def __init__(self):
        self.places=set();self.transports=set()
        base=ROOT/'upstream/ChinaTravel/chinatravel/environment'
        for folder in ['database','database_en']:
            for path in (base/folder).rglob('*.csv'):
                with path.open() as handle:
                    for row in csv.DictReader(handle):
                        if row.get('name'):self.places.add(row['name'])
                        for field in ['TrainID','FlightID']:
                            if row.get(field):self.transports.add(row[field])
        for folder in ['database','database_en']:
            for path in (base/folder/'intercity_transport').rglob('*'):
                if not path.is_file() or path.suffix not in ('.json','.jsonl'):continue
                rows=json.loads(path.read_text()) if path.suffix=='.json' else (json.loads(line) for line in path.read_text().splitlines() if line.strip())
                for row in rows:
                    for field in ('TrainID','FlightID'):
                        value=row.get(field)
                        if isinstance(value,str) and value:self.transports.add(value)

    def measure(self,plan):
        checked=missing=unmatched=0
        for _,a in activity_rows(plan):
            intercity=a.get('type') in ('train','airplane')
            value=(a.get('TrainID') or a.get('FlightID')) if intercity else a.get('position')
            if not isinstance(value,str) or not value:missing+=1;continue
            checked+=1
            if value not in (self.transports if intercity else self.places):unmatched+=1
        return {'checked':checked,'unmatched':unmatched,'missing':missing}


def changes(seed,plan,event):
    before=Counter((day,json.dumps(a,sort_keys=True,ensure_ascii=False)) for day,a in activity_rows(seed))
    after=Counter((day,json.dumps(a,sort_keys=True,ensure_ascii=False)) for day,a in activity_rows(plan))
    unchanged=sum((before&after).values());removed=sum((before-after).values());added=sum((after-before).values())
    # Match by day and content, not a shifted post-deletion array index.
    affected=set(affected_activity_ids(seed,event));mapping=activity_map(seed)
    unaffected=Counter((int(k.split(':')[0][1:]),json.dumps(a,sort_keys=True,ensure_ascii=False)) for k,a in mapping.items() if k not in affected)
    kept=sum((unaffected&after).values())
    return {'unchanged':unchanged,'removed_or_modified':removed,'added_or_modified':added,
            'unaffected_total':sum(unaffected.values()),'unaffected_kept':kept,'edit_total':removed+added}


def analyze(folder):
    folder=Path(folder);protocol=load(folder/'protocol.json');scores=load(folder/'scores.json')
    lookup={(r['method'],r['key']):r for r in scores['rows']};index=EntityIndex();rows=[]
    for method in protocol['methods']:
        for spec in protocol['events']:
            key=spec['uid']+('-'+spec['event']['kind'] if protocol['kind']=='dynamic' else '')
            path=folder/method/key/'record.json';record=load(path) if path.exists() else {}
            result=record.get('result',{});plan=result.get('plan');score=lookup.get((method,key),{})
            if not isinstance(plan,dict):plan=None
            ledger=record.get('ledger',{});resource=record.get('resources') or result.get('resources') or {}
            row={'uid':spec['uid'],'key':key,'split':spec['split'],'method':method,'success':bool(score.get('all_pass')),
                 'schema':bool(score.get('schema')),'commonsense':bool(score.get('commonsense')),'hard':bool(score.get('hard')),
                 'usable':bool(score.get('all_pass') and score.get('usability')),'terminal':result.get('terminal_state','missing'),
                 'seconds':record.get('supervision',{}).get('wall_seconds'),'tokens':ledger.get('observed_usage',{}).get('total_tokens',0),
                 'requests':ledger.get('request_attempts',0),'tools':resource.get('tool_calls'),'fallback':bool(result.get('metadata',{}).get('fallback')),
                 'known_cost':ledger.get('observed_usage_cost_CNY',{}),'evaluation_error':score.get('evaluation_error'),'unknown_billing':ledger.get('unobserved_billing_possible',True),
                 'event':spec.get('event',{}).get('kind'),'submitted':bool(plan),'entities':index.measure(plan) if plan else None,
                 'unsupported':len((result.get('metadata',{}).get('predicted_requirements') or {}).get('unsupported',[]))}
            if protocol['kind']=='dynamic' and plan:
                from trippilot.repair.events import ChangeEvent
                from decimal import Decimal
                event=dict(spec['event'])
                if event.get('new_budget') is not None:event['new_budget']=Decimal(str(event['new_budget']))
                seed=load(ROOT/spec['seed'])['plan'];row['changes']=changes(seed,plan,ChangeEvent(**event));row['event']=event['kind']
            rows.append(row)
    summary={}
    for method in protocol['methods']:
        selected=[r for r in rows if r['method']==method];valid=[r for r in selected if r['submitted']]
        success=sum(r['success'] for r in selected);entities=[r['entities'] for r in valid]
        durations=[r['seconds'] for r in selected if r['seconds'] is not None]
        known_low=sum(r['known_cost'].get('lower') or 0 for r in selected);known_high=sum(r['known_cost'].get('upper') or 0 for r in selected)
        delta=[r['changes'] for r in selected if 'changes' in r]
        summary[method]={'tasks':len(selected),'success':success,'success_rate':success/len(selected) if selected else None,'wilson_95':wilson(success,len(selected)) if protocol['kind']=='static' else None,'usable':sum(r['usable'] for r in selected),
            'schema':sum(r['schema'] for r in selected),'commonsense':sum(r['commonsense'] for r in selected),'hard':sum(r['hard'] for r in selected),
            'tokens':sum(r['tokens'] or 0 for r in selected),'requests':sum(r['requests'] for r in selected),
            'tool_queries':sum(r['tools'] or 0 for r in selected),'tasks_with_unknown_tool_count':sum(r['tools'] is None for r in selected),
            'seconds':sum(durations),'unknown_duration_tasks':len(selected)-len(durations),'p50':quantile(durations,.5),'p95':quantile(durations,.95),
            'known_usage_cost_CNY':{'lower':known_low,'upper':known_high},
            'known_usage_cost_per_success_CNY':{'lower':known_low/success if success else None,'upper':known_high/success if success else None},
            'unpriced_observed_usage_tasks':sum(r['tokens']>0 and r['known_cost'].get('upper') is None for r in selected),'unknown_billing_tasks':sum(r['unknown_billing'] for r in selected),'invoice_total_CNY':None,
            'fallback_tasks':sum(r['fallback'] for r in selected),'submitted_plans':len(valid),'entity_mentions':sum(v['checked'] for v in entities),'unmatched_entity_mentions':sum(v['unmatched'] for v in entities),
            'plans_with_unmatched_entities':sum(v['unmatched']>0 for v in entities),'missing_entity_mentions':sum(v['missing'] for v in entities),
            'changes':{k:sum(v[k] for v in delta) for k in ['unchanged','removed_or_modified','added_or_modified','unaffected_total','unaffected_kept','edit_total']},
            'failure_states':dict(Counter(r['terminal'] for r in selected if not r['success'])),
            'constraint_failures':{k:sum(r['submitted'] and not r[k] for r in selected) for k in ('schema','commonsense','hard')},
            'evaluation_errors':dict(Counter(r['evaluation_error'] for r in selected if r['evaluation_error']))}
    paired={}
    for method in [m for m in protocol['methods'] if 'b2' in protocol['methods'] and m!='b2']:
        matched=[]
        ref={r['key']:r for r in rows if r['method']=='b2'}
        for row in rows:
            if row['method']==method and row['success'] and ref.get(row['key'],{}).get('success'):matched.append((row,ref[row['key']]))
        paired[method]={'both_successful':len(matched),
            'method_tokens':sum(a['tokens'] or 0 for a,b in matched),'react_tokens':sum(b['tokens'] or 0 for a,b in matched),
            'method_seconds':sum(a['seconds'] or 0 for a,b in matched),'react_seconds':sum(b['seconds'] or 0 for a,b in matched),
            'method_edits':sum(a.get('changes',{}).get('edit_total',0) for a,b in matched),
            'react_edits':sum(b.get('changes',{}).get('edit_total',0) for a,b in matched)}
    strata={}
    for method in protocol['methods']:
        strata[method]={}
        for row in (r for r in rows if r['method']==method):
            group=row['event'] or row['split']
            counters=strata[method].setdefault(group,{'tasks':0,'success':0})
            counters['tasks']+=1;counters['success']+=int(row['success'])
    report={'run':folder.name,'kind':protocol['kind'],'model':protocol['model'],'summary':summary,'paired_successes':paired,'rows':rows,
            'paired_statistics':{m:paired_statistics(rows,m) for m in protocol['methods'] if 'b2' in protocol['methods'] and m!='b2'},
            'strata':strata,
            'definitions':{'entities':'Exact public name/transport-ID membership across bilingual tool tables; report missing identifiers separately. Denominator is returned plans, with submission rate separately shown.',
                           'preservation':'Multiset equality of full activity content plus day, evaluated against the declared event dependency footprint.',
                           'cost':'Known returned usage at configured list rates only; failed or pending remote usage and actual invoice remain unknown.'}}
    (folder/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return report


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',required=True);args=parser.parse_args()
    result=analyze(ROOT/'experiments/v3'/args.run_id);print(json.dumps(result['summary'],ensure_ascii=False))

if __name__=='__main__':main()
