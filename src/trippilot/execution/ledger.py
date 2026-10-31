"""Request attempt journal. Optional child-local sink, no prompts/credentials."""
from decimal import Decimal
import json
import os
import time
from pathlib import Path


def emit(kind, **fields):
    target=os.environ.get('TRIPPILOT_REQUEST_JOURNAL')
    if not target:return
    path=Path(target);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as file:
        file.write(json.dumps({'kind':kind,'time':time.time(),**fields},ensure_ascii=False)+'\n')
        file.flush()


def summarize(path, rates=None):
    events=[]
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:events.append(json.loads(line))
            except json.JSONDecodeError:pass  # a killed final write is unknown
    starts={e['attempt_id'] for e in events if e['kind']=='start'}
    ends={e['attempt_id'] for e in events if e['kind'] in ('success','error')}
    ok=[e for e in events if e['kind']=='success']
    totals={k:sum(e.get(k,0) or 0 for e in ok) for k in ['prompt_tokens','completion_tokens','total_tokens']}
    unknown=bool(starts-ends) or any(e['kind']=='error' for e in events) or any(e.get('total_tokens') is None for e in ok)
    low=high=Decimal(0);known_cache=True
    money_rates={k:Decimal(str(v)) for k,v in rates.items()} if rates else None
    for event in ok:
        if not rates or event.get('prompt_tokens') is None or event.get('completion_tokens') is None:
            low=high=None;break
        prompt=event['prompt_tokens'];output=event['completion_tokens'];cached=event.get('cached_prompt_tokens')
        if cached is None:
            known_cache=False
            low+=(prompt*money_rates['cached_input']+output*money_rates['output'])/Decimal(1000000)
            high+=(prompt*money_rates['input']+output*money_rates['output'])/Decimal(1000000)
        else:
            cost=((prompt-cached)*money_rates['input']+cached*money_rates['cached_input']+output*money_rates['output'])/Decimal(1000000)
            low+=cost;high+=cost
    return {'request_attempts':len(starts),'completed_attempts':len(ends),'pending_attempts':len(starts-ends),
            'observed_usage':totals,'unobserved_billing_possible':unknown,'cache_usage_complete':known_cache,
            'observed_usage_cost_CNY':{'lower':float(low) if low is not None else None,'upper':float(high) if high is not None else None},
            'invoice_total_CNY':None,'events':events}
