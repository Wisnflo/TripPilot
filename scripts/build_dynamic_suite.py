#!/usr/bin/env python3
"""Predeclared balanced conditional dynamic recipe: <=4 valid parents/split.

Select by UID hash, never by revised-plan outcomes. Each parent yields ALL three
events. Initial failures/selection shortfalls are reported, never silently filled.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--static-run-id',required=True)
    p.add_argument('--output',default='experiments/frozen/v3-protocol-20261031/dynamic.json')
    p.add_argument('--per-split',type=int,default=4);a=p.parse_args()
    run=ROOT/'experiments/v3'/a.static_run_id;protocol=json.loads((run/'protocol.json').read_text())
    scores=json.loads((run/'scores.json').read_text())
    passing={r['uid'] for r in scores['rows'] if r['method']=='structured' and r['all_pass']}
    all_specs=protocol['events'];events=[];cohort=[];shortfall={}
    for split in ['easy','medium','human']:
        selected=[]
        candidates=sorted([x for x in all_specs if x['split']==split],key=lambda x:hashlib.sha256((x['uid']+'20261031').encode()).hexdigest())
        for spec in candidates:
            if spec['uid'] not in passing:continue
            source=run/'structured'/spec['uid']/'result.json';plan=json.loads(source.read_text()).get('plan')
            if not plan or len(plan['itinerary'])<2:continue
            intercity=[x for d in plan['itinerary'] for x in d['activities'] if x.get('type') in ('train','airplane')]
            if not intercity:continue
            if len(selected)>=a.per_split:break
            selected.append(spec['uid']);cohort.append(spec['uid'])
            target=intercity[0].get('TrainID') or intercity[0].get('FlightID')
            cost=sum(x.get('cost',0)+sum(t.get('cost',0) for t in x.get('transports',[])) for d in plan['itinerary'] for x in d['activities'])
            for event in [{'kind':'return_earlier','new_days':len(plan['itinerary'])-1},
                          {'kind':'transport_unavailable','target':target},
                          {'kind':'budget_cut','new_budget':str(math.floor(cost*0.95))}]:
                events.append({'uid':spec['uid'],'split':split,'seed':str(source.relative_to(ROOT)),
                    'event':event,'clarification_answer':'cancel_overnight' if event['kind']=='return_earlier' and event['new_days']==1 else None})
        shortfall[split]=max(0,a.per_split-len(selected))
    output=ROOT/a.output
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise SystemExit('Manifest exists; choose a new output, never overwrite.')
    output.write_text(json.dumps({'dynamic':events,'parent_count':len(cohort),'initial_expected':len(all_specs),
        'initial_successful':len(passing),'shortfall_parents':shortfall,'recipe':'UID-hash sorted <=4 valid multi-day intercity structured seeds/split; all 3 events each; budget=floor(0.95*seed spend). No feasibility filtering.',
        'clarification_policy':'Scripted user explicitly cancels lodging ONLY when asked about 1-day return. This is not inferred human consent.',
        'note':'Conditional repair cohort; initial failures remain initial denominator. Not a combined end-to-end success rate.'},ensure_ascii=False,indent=2))
    print('parents',len(cohort),'events',len(events),'shortfall',shortfall)


if __name__=='__main__':main()
