"""Turn a conversational revision into a typed event, with a bounded LLM fallback.

Simple explicit changes use deterministic parsing. Ambiguous language is routed
to the model; unknown/missing fields are rejected rather than guessed.
"""
import json
import re
from decimal import Decimal

from ..agent.loop import extract_plan
from ..models.openai_adapter import chat_step
from .events import ChangeEvent


def parse_change(text, seed, config=None, deadline=None):
    normalized = text.replace(',', '')
    days = len(seed.get('itinerary', []))
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    calls = []
    # Multiple explicit edits are not silently collapsed to one event.
    matches = []
    direct = re.search(r'(?:改为|改成|缩短到|缩短为|now\s+)(\d+)\s*(?:天|days?)', normalized, re.I)
    earlier = re.search(r'提前\s*(\d+|一)\s*天', normalized)
    if direct:
        matches.append(dict(kind='return_earlier', new_days=int(direct.group(1))))
    elif earlier:
        count = 1 if earlier.group(1) == '一' else int(earlier.group(1))
        matches.append(dict(kind='return_earlier', new_days=days-count))
    budget = re.search(r'(?:预算(?:降到|降至|改为|改成|为|到)|budget(?:\s+(?:to|is))?)\s*[:：]?\s*(\d+(?:\.\d+)?)', normalized, re.I)
    if budget:
        matches.append(dict(kind='budget_cut', new_budget=budget.group(1)))
    transport = re.search(r'\b([GDKZTFC]\d{1,6}|FL\d{1,6})\b', text, re.I)
    if transport and re.search(r'停运|取消|不可用|unavailable|cancel', text, re.I):
        matches.append(dict(kind='transport_unavailable', target=transport.group(1).upper()))
    if len(matches) > 1:
        raise ValueError('Multiple changes need separate revisions; no change was applied.')
    parsed = matches[0] if matches else None
    source = 'explicit_parser'
    ids = [a.get('TrainID') or a.get('FlightID') for d in seed.get('itinerary', [])
           for a in d.get('activities', []) if a.get('type') in ('train', 'airplane')]
    if parsed is None:
        if config is None:
            raise ValueError('Ambiguous change: provide explicit days, budget or transport ID.')
        source = 'model_parser'
        response = chat_step(config, [
            {'role':'system','content': 'Extract EXACTLY ONE travel revision. Return JSON with kind '
             '(return_earlier|budget_cut|transport_unavailable), and new_days integer OR new_budget number '
             'OR target existing ID. Do not guess missing values. For ambiguous/multiple events return {}.'},
            {'role':'user','content':json.dumps({'change':text,'original_days':days,'existing_transport_ids':ids},ensure_ascii=False)}
        ], deadline=deadline)
        parsed = extract_plan(response.content) or {}
        usage = {key:getattr(response,key,None) for key in usage}
        calls.append({'request_id':response.request_id,'attempts':response.attempts,
                      'request_latencies':response.request_latencies,'response_model':response.model})
    kind = parsed.get('kind')
    if kind == 'return_earlier':
        n = parsed.get('new_days')
        if type(n) is not int or not 1 <= n < days:
            raise ValueError('Return change must specify fewer positive days than the seed.')
        event = ChangeEvent(kind,new_days=n)
    elif kind == 'budget_cut':
        value = Decimal(str(parsed.get('new_budget')))
        if not value.is_finite() or value < 0:
            raise ValueError('Budget must be finite and nonnegative.')
        event = ChangeEvent(kind,new_budget=value)
    elif kind == 'transport_unavailable':
        target = parsed.get('target')
        if target not in ids:
            raise ValueError('Transport ID is absent from the current plan.')
        event = ChangeEvent(kind,target=target)
    else:
        raise ValueError('Unsupported or ambiguous revision; no change was applied.')
    return event, {'source':source,'usage':usage,'requests':calls}
