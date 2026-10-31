"""Explicit, resumable consent for incompatible lodging/shorter-trip requests."""
import copy
import re
from decimal import Decimal

from .events import ChangeEvent
from .transaction import revision


def begin_revision(task, seed, event):
    text=(task.get('nature_language_en') or '')+' '+(task.get('nature_language') or '')
    has_hotel=any(a.get('type')=='accommodation' for d in seed.get('itinerary',[]) for a in d.get('activities',[]))
    explicit=bool(task.get('limit_rooms') or task.get('limits_room_type') or
                  re.search(r'hotel|rooms?|stay\s+at|酒店|住宿|双床|大床|客房',text,re.I))
    needs=event.kind=='return_earlier' and event.new_days==1 and has_hotel and explicit
    state={'status':'needs_clarification' if needs else 'ready','base_revision':revision(seed),
           'task':copy.deepcopy(task),'seed_plan':copy.deepcopy(seed),
           'event':{k:str(v) if isinstance(v,Decimal) else v for k,v in event.__dict__.items()}}
    if needs:
        state.update(question='行程改为当天返回后，原来的住宿要求如何处理？',
                     choices={'cancel_overnight':'取消住宿要求，继续安排一日行程',
                              'keep_original':'保留原住宿和原行程，暂不执行提前返程'})
    state['state_revision']=revision(state)
    return state


def resolve_revision(state, answer, current_seed=None):
    expected=state.get('state_revision');body={k:v for k,v in state.items() if k!='state_revision'}
    if expected!=revision(body):raise ValueError('clarification state changed')
    if state.get('status')!='needs_clarification':raise ValueError('state is not awaiting clarification')
    if current_seed is not None and revision(current_seed)!=state['base_revision']:raise ValueError('seed changed; start a new revision')
    if answer not in state['choices']:raise ValueError('answer must be one of the displayed choices')
    resolved=copy.deepcopy(state);resolved.pop('state_revision',None)
    resolved['answer']=answer
    if answer=='keep_original':
        resolved['status']='cancelled';resolved['plan']=resolved['seed_plan']
    else:
        resolved['status']='ready'
        note='User explicitly confirmed: cancel all lodging, hotel, room-type and room-count requirements for this one-day revision. All non-lodging requirements remain.'
        task=resolved['task']
        task['nature_language_en']=(task.get('nature_language_en') or task.get('nature_language',''))+'\n'+note
        task['nature_language']=(task.get('nature_language') or '')+'\n用户已明确确认：本次一日修订取消住宿、酒店设施、房型和房间数要求，其它需求保持。'
        task['limit_rooms']=False;task['limits_room_type']=False
        resolved['explicit_lodging_waiver']=True
    resolved['state_revision']=revision(resolved)
    return resolved


def state_event(state):
    values=dict(state['event'])
    if values.get('new_budget') is not None:values['new_budget']=Decimal(values['new_budget'])
    return ChangeEvent(**values)
