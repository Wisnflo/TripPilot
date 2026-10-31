"""Deterministic parent-paired intervals; no independent-event assumption."""
from collections import defaultdict
import math
import random


def wilson(success,n):
    if not n:return None
    z=1.959963984540054;p=success/n;scale=1+z*z/n
    center=(p+z*z/(2*n))/scale
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/scale
    return [max(0,center-radius),min(1,center+radius)]


def interval(values):
    values=sorted(v for v in values if v is not None)
    if not values:return None
    return [values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]]


def paired(rows,method,reference='b2',resamples=3000,seed=20261031):
    left={r['key']:r for r in rows if r['method']==method};right={r['key']:r for r in rows if r['method']==reference}
    groups=defaultdict(list)
    for key in sorted(left.keys()&right.keys()):groups[left[key]['uid']].append((left[key],right[key]))
    names=sorted(groups);pairs=[p for group in groups.values() for p in group]
    if not pairs:return {'parents':0,'pairs':0}
    exclusive_left=sum(a['success'] and not b['success'] for a,b in pairs)
    exclusive_right=sum(b['success'] and not a['success'] for a,b in pairs)
    independent=all(len(group)==1 for group in groups.values())
    discordant=exclusive_left+exclusive_right
    pvalue=min(1.0,2*sum(math.comb(discordant,k) for k in range(min(exclusive_left,exclusive_right)+1))/(2**discordant)) if independent else None
    def values(selected):
        success=sum(int(a['success'])-int(b['success']) for a,b in selected)/len(selected)
        tokens_ref=sum(b['tokens'] or 0 for a,b in selected)
        reduction=1-sum(a['tokens'] or 0 for a,b in selected)/tokens_ref if tokens_ref else None
        timed=[(a,b) for a,b in selected if a['seconds'] is not None and b['seconds'] is not None]
        seconds_ref=sum(b['seconds'] for a,b in timed)
        seconds=1-sum(a['seconds'] for a,b in timed)/seconds_ref if seconds_ref else None
        return success,reduction,seconds
    randomizer=random.Random(seed);samples=[[],[],[]]
    for _ in range(resamples):
        selected=[pair for name in randomizer.choices(names,k=len(names)) for pair in groups[name]]
        for target,value in zip(samples,values(selected)):target.append(value)
    actual=values(pairs)
    return {'parents':len(names),'pairs':len(pairs),'exclusive_success':{method:exclusive_left,reference:exclusive_right},
            'exact_mcnemar_p':pvalue,'mcnemar_unit':'one pair per independent parent' if independent else 'not applied to correlated events',
            'success_rate_difference':actual[0],'token_reduction':actual[1],'time_reduction':actual[2],
            'parent_bootstrap_95':dict(zip(('success_rate_difference','token_reduction','time_reduction'),map(interval,samples))),
            'resamples':resamples,'seed':seed}
