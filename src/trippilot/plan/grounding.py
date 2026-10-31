"""Shared factual route completion; never invent or change a chosen activity."""
import copy
import re
from ..request import request_text
from ..money import multiply,sum_cost
from .checker import _minutes,_tool_city


def complete_routes(task,plan,toolbox):
    if toolbox is None:return plan
    text=request_text(task)
    modes=[mode for mode,pattern in [('taxi',r'taxi|出租|打车'),('metro',r'metro|subway|地铁'),('walk',r'walk|步行')] if re.search(pattern,text,re.I)] or ['taxi','metro','walk']
    previous=None;previous_day=None
    for day in plan.get('itinerary',[]):
        for activity in day.get('activities',[]):
            position=activity.get('start') if activity.get('type') in ('train','airplane') else activity.get('position')
            old_position=(previous.get('end') if previous.get('type') in ('train','airplane') else previous.get('position')) if previous else None
            if old_position and position and old_position!=position and not activity.get('transports'):
                start=_minutes(activity.get('start_time'));earliest=_minutes(previous.get('end_time')) if previous_day==day['day'] else 0
                if start is not None and earliest is not None:
                    depart=max(earliest,start-60);when='%02d:%02d'%(depart//60,depart%60);options=[]
                    for mode in modes:
                        response=toolbox.call('goto',{'city':_tool_city(task['target_city']),'start':old_position,'end':position,'start_time':when,'transport_type':mode})
                        legs=response.get('data')
                        if not response.get('success') or not isinstance(legs,list) or not legs:continue
                        legs=copy.deepcopy(legs)
                        end=_minutes(legs[-1].get('end_time'))
                        if end is None or end>start:continue
                        for leg in legs:
                            price=leg.get('cost',0);leg['price']=price
                            if leg.get('mode')=='taxi':leg.update(cars=1,cost=multiply(price,1))
                            elif leg.get('mode')=='metro':leg.update(tickets=task['people_number'],cost=multiply(price,task['people_number']))
                            else:leg['cost']=0
                        options.append(legs)
                    if options:activity['transports']=min(options,key=sum_cost)
            previous=activity;previous_day=day['day']
    return plan
