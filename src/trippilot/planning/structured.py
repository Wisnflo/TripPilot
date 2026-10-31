"""Public-fact compiler planner: intent -> tool catalog -> ID sketch -> schedule.

No benchmark labels, reference plans or offline scorer are imported here.
"""
import copy
import json
import math
import re
import time
from ..money import amount, number, multiply, plan_total, sum_cost
from datetime import datetime, timezone

from ..agent.loop import Trajectory, extract_plan, run_react
from ..models.openai_adapter import chat_step, ModelRequestError
from ..request import request_text
from ..execution.budget import count_feedback
from ..plan.checker import check_plan, Violation, _tool_city, extract_budget
from ..repair.proposals import PublicTools, transport_activity, activity_position


def minute(text):
    if not isinstance(text, str) or not re.fullmatch(r'\d{1,2}:\d{2}',text):
        raise ValueError('invalid time')
    h,m=map(int,text.split(':'))
    if not 0<=h<=24 or not 0<=m<60 or (h==24 and m):raise ValueError('invalid time')
    return h*60+m


def hhmm(value):
    value=int(value)
    if not 0<=value<1440:raise ValueError('schedule crosses midnight')
    return '%02d:%02d'%(value//60,value%60)


class Catalog:
    def __init__(self, toolbox, deadline):
        self.tools=PublicTools(toolbox,deadline)
        self.cache={}

    def fetch(self, tool, **arguments):
        key=(tool,json.dumps(arguments,sort_keys=True))
        if key in self.cache:return copy.deepcopy(self.cache[key])
        response=self.tools.call(tool,arguments)
        data=response.get('data') or {}
        if not isinstance(data,dict):return []
        rows=list(data.get('rows',[]));total=data.get('row_count',len(rows))
        while len(rows)<min(total,300):
            if self.tools.deadline is not None and time.monotonic()>=self.tools.deadline:raise TimeoutError('catalog deadline')
            more=self.tools.toolbox.call('next_page',{})
            self.tools.trace.append({'name':'next_page','arguments':{},'response':more})
            part=(more.get('data') or {}).get('rows',[])
            if not part:break
            rows.extend(part)
        self.cache[key]=rows
        return copy.deepcopy(rows)

    def select(self, kind, city, key='name', value='', op='contains'):
        name={'attraction':'attractions_select','hotel':'accommodations_select','restaurant':'restaurants_select'}[kind]
        return self.fetch(name,city=city,key=key,op=op,value=value)


FIELDS={'attraction_types':list,'attraction_names':list,'hotel_features':list,'hotel_names':list,
        'restaurant_names':list,'cuisines':list,'local_modes':list,'rooms':int,'room_type':int,
        'intercity_mode':str,'meal_price_cap':(int,float),'departure_after':str,'return_before':str,'budget':(int,float)}


def extract_requirements(task, catalogs, config, deadline, record):
    text=request_text(task)
    taxonomy={'attraction_types':sorted({str(x.get('type')) for x in catalogs['attraction']}),
              'hotel_features':sorted({str(x.get('featurehoteltype')) for x in catalogs['hotel']}),
              'cuisines':sorted({str(x.get('cuisine')) for x in catalogs['restaurant']})}
    prompt=('Extract ONLY explicitly stated user constraints. Return JSON with optional keys: '
            'attraction_types[], attraction_names[], hotel_features[], hotel_names[], restaurant_names[], '
            'cuisines[], local_modes[] (walk|taxi|metro), rooms integer, room_type (1 one bed: single/king/double, 2 two beds/twin), '
            'intercity_mode (train|airplane), budget total, meal_price_cap per person, departure_after HH:MM, return_before HH:MM. '
            'For each populated key, evidence[key] MUST be an exact substring of the request. '
            'No defaults or invented requirements. Use taxonomy values exactly when possible. '
            'Preserve specific named entities in their ORIGINAL language, suitable for contains queries; do not translate proper names. '
            'Return unsupported[] for ambiguous constraints. Empty objects/lists are allowed.')
    response=chat_step(config,[{'role':'system','content':prompt},
        {'role':'user','content':json.dumps({'request':text,'taxonomy':taxonomy},ensure_ascii=False)}],deadline=deadline)
    record(response,'extract_requirements')
    parsed=extract_plan(response.content) or {};evidence=parsed.get('evidence') or {};result={}
    if not isinstance(evidence,dict):evidence={}
    rejected=[]
    for key,typ in FIELDS.items():
        value=parsed.get(key)
        if value is None or value==[]:continue
        quote=evidence.get(key)
        if not isinstance(quote,str) or not quote or quote not in text or not isinstance(value,typ) or isinstance(value,bool):
            rejected.append(key);continue
        if typ is list and (len(value)>8 or any(not isinstance(v,str) or len(v)>150 for v in value)):
            rejected.append(key);continue
        if key=='room_type' and value not in (1,2):rejected.append(key);continue
        if key=='rooms' and not 1<=value<=20:rejected.append(key);continue
        if key=='intercity_mode' and value not in ('train','airplane'):rejected.append(key);continue
        if key=='local_modes' and any(v not in ('walk','taxi','metro') for v in value):rejected.append(key);continue
        if key in ('meal_price_cap','budget') and (not math.isfinite(value) or value<0):rejected.append(key);continue
        if key in ('departure_after','return_before'):
            try:minute(value)
            except ValueError:rejected.append(key);continue
        if key in taxonomy:
            canonical={v.lower():v for v in taxonomy[key]}
            result[key]=[canonical.get(v.lower(),v) for v in value]
        else:result[key]=value
    budget=extract_budget(text)
    if budget is not None:result['budget']=float(budget)
    # High-confidence literal Chinese constraints override translation/model
    # ambiguity, with an audit trail; they are not benchmark annotations.
    original=task.get('nature_language') or ''
    singles=re.search(r'单床|大床|一张床',original)
    twins=re.search(r'双床|两张床|二张床',original)
    corrections=[]
    if bool(singles) != bool(twins):
        value=1 if singles else 2
        if result.get('room_type')!=value:corrections.append({'field':'room_type','before':result.get('room_type'),'after':value})
        result['room_type']=value
    rooms=re.search(r'(一|两|二|三|四|五|[1-9][0-9]*)间',original)
    if rooms:
        numbers={'一':1,'两':2,'二':2,'三':3,'四':4,'五':5}
        token=rooms.group(1);value=numbers.get(token) or int(token)
        if result.get('rooms')!=value:corrections.append({'field':'rooms','before':result.get('rooms'),'after':value})
        result['rooms']=value
    result['literal_corrections']=corrections
    result['evidence']={k:evidence[k] for k in result if k in evidence}
    result['unsupported']=parsed.get('unsupported',[]) if isinstance(parsed.get('unsupported',[]),list) else []
    result['rejected_fields']=rejected
    return result


def shortlist(catalog, task, bases, req):
    city=_tool_city(task['target_city']);origin=_tool_city(task['start_city'])
    pools={kind:copy.deepcopy(values) for kind,values in bases.items()}
    specs=[('attraction','attraction_types','type','eq'),('attraction','attraction_names','name','contains'),
           ('hotel','hotel_features','featurehoteltype','contains'),('hotel','hotel_names','name','contains'),
           ('restaurant','cuisines','cuisine','eq'),('restaurant','restaurant_names','name','contains')]
    req['entity_aliases']={}
    for kind,field,key,op in specs:
        for value in req.get(field,[]):
            found=catalog.select(kind,city,key,value,op)
            pools[kind].extend(found)
            req['entity_aliases'][value]=[str(r.get(key,'')) for r in found]
    result={}
    for kind,values in pools.items():
        unique={str(row['id']):row for row in values if 'id' in row}
        values=list(unique.values())
        def matches(row):
            if kind=='hotel':
                return ((not req.get('room_type') or row.get('numbed')==req['room_type']) and
                    all(any(alias.lower() in str(row.get('featurehoteltype','')).lower() for alias in req.get('entity_aliases',{}).get(v,[v])) for v in req.get('hotel_features',[])) and
                    (not req.get('hotel_names') or any(any(alias.lower() in row['name'].lower() for alias in req.get('entity_aliases',{}).get(v,[v])) for v in req['hotel_names'])))
            if kind=='restaurant' and req.get('meal_price_cap') is not None:
                return amount(row.get('price',0))<=amount(req['meal_price_cap'])
            return True
        values=[v for v in values if matches(v)]
        values.sort(key=lambda r:(amount(r.get('price',0)),r['name']))
        picked=[]
        groups={'attraction':[('type',v) for v in req.get('attraction_types',[])]+[('name',v) for v in req.get('attraction_names',[])],
                'restaurant':[('cuisine',v) for v in req.get('cuisines',[])]+[('name',v) for v in req.get('restaurant_names',[])],
                'hotel':[]}[kind]
        for key,value in groups:
            picked.extend([r for r in values if any(alias.lower() in str(r.get(key,'')).lower() for alias in req.get('entity_aliases',{}).get(value,[value]))][:4])
        picked.extend(values[:max(10,int(task['days'])*3)])
        picked=list({str(v['id']):v for v in picked}.values())[:24]
        result[kind]={('%s%s'%(kind[0],v['id'])):v for v in picked}
    transport={}
    if origin!=city:
        for direction,src,dst in [('outbound',origin,city),('inbound',city,origin)]:
            values=[]
            for mode in [req['intercity_mode']] if req.get('intercity_mode') else ['train','airplane']:
                for row in catalog.tools.rows(src,dst,mode):
                    if minute(row['EndTime'])<minute(row['BeginTime']):continue
                    if direction=='outbound' and req.get('departure_after') and minute(row['BeginTime'])<minute(req['departure_after']):continue
                    if direction=='inbound' and req.get('return_before') and minute(row['EndTime'])>minute(req['return_before']):continue
                    values.append(dict(row,_mode=mode))
            # Mix cheapest and useful arrival/departure windows; never only the
            # first ten midnight trains from the public table.
            values=[v for v in values if isinstance(v.get('Cost'),(int,float)) and math.isfinite(v['Cost']) and isinstance(v.get('TrainID') or v.get('FlightID'),str)]
            cheap=sorted(values,key=lambda v:amount(v['Cost']))[:8]
            timed=sorted(values,key=lambda v:minute(v['EndTime']) if direction=='outbound' else -minute(v['BeginTime']))[:8]
            key=lambda v:v.get('TrainID') or v.get('FlightID')
            transport[direction]={key(v):v for v in cheap+timed}
    return {**result,**transport}


def compile_sketch(task, sketch, facts, req, tools):
    n=int(task['days']);people=int(task['people_number']);city=_tool_city(task['target_city'])
    origin=_tool_city(task['start_city']);days=sketch.get('days')
    if not isinstance(days,list) or len(days)!=n:raise ValueError('sketch must contain exactly one list per day')
    room_count=req.get('rooms',max(1,math.ceil(people/2)))
    hotel=facts['hotel'].get(sketch.get('hotel')) if n>1 else None
    if n>1 and hotel is None:raise ValueError('select an offered hotel ID')
    outbound=inbound=None
    if origin!=city:
        out=facts['outbound'].get(sketch.get('outbound'));back=facts['inbound'].get(sketch.get('inbound'))
        if out is None or back is None:raise ValueError('select offered outbound/inbound IDs')
        outbound=transport_activity(out,out['_mode'],people);inbound=transport_activity(back,back['_mode'],people)
    modes=req.get('local_modes') or ['taxi','metro','walk']
    plan={'people_number':people,'start_city':origin,'target_city':city,'itinerary':[]}
    previous=None
    for dayindex,visits in enumerate(days,1):
        if not isinstance(visits,list) or not 1<=len(visits)<=6:raise ValueError('each day needs 1..6 ID visits')
        activities=[];time_now=8*60
        if dayindex==1 and outbound:
            activities.append(copy.deepcopy(outbound));previous=activities[-1];time_now=minute(previous['end_time'])
        elif previous and previous.get('type')=='accommodation':
            previous=dict(previous,end_time='08:00')
        def attach(activity, earliest=0, duration=60, closing=1439):
            nonlocal time_now,previous
            activity=copy.deepcopy(activity)
            target=activity.get('start') or activity.get('position')
            route=[]
            if previous and activity_position(previous)!=target:
                possibilities=[]
                for mode in modes:
                    option=tools.route(city,activity_position(previous),target,hhmm(time_now),mode,people)
                    if option is not None:
                        try:arrival=minute(option[-1]['end_time']) if option else time_now
                        except ValueError:continue
                        if arrival>=time_now:possibilities.append((arrival,sum_cost(option),option))
                if not possibilities:raise ValueError('no route to '+str(target))
                # Cost/arrival are compiler objectives; user mode constraints
                # have already bounded the choices above.
                arrival,_,route=min(possibilities,key=lambda v:(v[0],v[1]))
            else:arrival=time_now
            start=max(arrival,earliest)
            if activity.get('type') in ('train','airplane'):
                start=minute(activity['start_time'])
                if arrival>start:raise ValueError('cannot reach return departure')
                end=minute(activity['end_time'])
            elif activity.get('type')=='accommodation':
                end=1439
                if start>=end:raise ValueError('hotel arrival too late')
            else:end=start+duration
            if end>closing or end>=1440:raise ValueError('activity exceeds opening/meal window')
            activity.update(start_time=hhmm(start),end_time=hhmm(end),transports=route)
            activities.append(activity);previous=activity;time_now=end
        for visit in visits:
            if not isinstance(visit,dict):raise ValueError('visit must contain kind and id')
            kind=visit.get('kind');key=visit.get('id')
            group='attraction' if kind=='attraction' else 'restaurant'
            if kind not in ('attraction','breakfast','lunch','dinner') or key not in facts[group]:raise ValueError('unknown visit ID')
            row=facts[group][key];price=number(row['price'])
            activity={'type':kind,'position':row['name'],'price':price,'cost':multiply(price,people)}
            if kind=='attraction':activity['tickets']=people
            opening=minute(row.get('opentime','00:00'));closing=minute(row.get('endtime','23:59'))
            if closing<opening:closing=1439
            duration=max(30,math.ceil(float(row.get('recommendmintime',1))*60)) if kind=='attraction' else 45
            if kind in ('breakfast','lunch','dinner'):
                lo,hi={'breakfast':(360,540),'lunch':(660,840),'dinner':(1020,1200)}[kind]
                opening=max(opening,lo);closing=min(closing,hi)
            attach(activity,opening,duration,closing)
        # Usability floor is declared, not inferred from hidden annotations.
        if not any(a['type']=='attraction' for a in activities):raise ValueError('each day needs an attraction')
        if not any(a['type'] in ('breakfast','lunch','dinner') for a in activities):raise ValueError('each day needs a meal')
        if dayindex<n:
            attach({'type':'accommodation','position':hotel['name'],'price':number(hotel['price']),
                    'cost':multiply(hotel['price'],room_count),'rooms':room_count,'room_type':int(hotel['numbed'])},18*60)
        elif inbound:attach(inbound)
        plan['itinerary'].append({'day':dayindex,'activities':activities})
    return plan


def check_requirements(task,plan,req,toolbox):
    result=check_plan(task,plan,toolbox=toolbox)
    acts=[a for d in plan.get('itinerary',[]) for a in d.get('activities',[])]
    pools={'attraction':[],'restaurant':[],'hotel':[]}
    for a in acts:
        kind=a.get('type');group='attraction' if kind=='attraction' else 'hotel' if kind=='accommodation' else 'restaurant' if kind in ('breakfast','lunch','dinner') else None
        if group:
            tool={'attraction':'attractions_select','hotel':'accommodations_select','restaurant':'restaurants_select'}[group]
            response=toolbox.call(tool,{'city':_tool_city(task['target_city']),'key':'name','op':'eq','value':a.get('position','')})
            pools[group].extend((response.get('data') or {}).get('rows',[]))
    mappings=[('attraction_types','attraction','type'),('attraction_names','attraction','name'),
              ('hotel_features','hotel','featurehoteltype'),('hotel_names','hotel','name'),
              ('restaurant_names','restaurant','name'),('cuisines','restaurant','cuisine')]
    for field,group,column in mappings:
        for wanted in req.get(field,[]):
            if not any(any(alias.lower() in str(row.get(column,'')).lower() for alias in req.get('entity_aliases',{}).get(wanted,[wanted])) for row in pools[group]):
                result.violations.append(Violation('predicted_requirement','missing %s %r (extracted from user text)'%(field,wanted)))
    for a in acts:
        if a.get('type')=='accommodation':
            for field,planfield in [('rooms','rooms'),('room_type','room_type')]:
                if req.get(field) is not None and a.get(planfield)!=req[field]:result.violations.append(Violation('predicted_room_rule',field+' mismatch'))
        if a.get('type') in ('breakfast','lunch','dinner') and req.get('meal_price_cap') is not None and amount(a.get('price',0))>amount(req['meal_price_cap']):
            result.violations.append(Violation('predicted_meal_cap','meal price above user cap'))
    intercity=[a for a in acts if a.get('type') in ('train','airplane')]
    if req.get('intercity_mode') and any(a['type']!=req['intercity_mode'] for a in intercity):
        result.violations.append(Violation('predicted_intercity_mode','wrong intercity mode'))
    if req.get('departure_after') and intercity and minute(intercity[0]['start_time'])<minute(req['departure_after']):
        result.violations.append(Violation('predicted_departure','departure too early'))
    if req.get('return_before') and intercity and minute(intercity[-1]['end_time'])>minute(req['return_before']):
        result.violations.append(Violation('predicted_return','return too late'))
    if req.get('local_modes'):
        for a in acts:
            legs=a.get('transports') or []
            if legs:
                mode='metro' if len(legs)==3 else legs[0].get('mode')
                if mode not in req['local_modes']:result.violations.append(Violation('predicted_local_mode','wrong local mode'))
    if req.get('budget') is not None:
        total=plan_total(plan)
        if total>amount(req['budget']):result.violations.append(Violation('predicted_budget','over budget'))
    result.unsupported.extend(req.get('unsupported',[]))
    return result


def run_structured(task,config,toolbox,max_steps=16,max_seconds=240,feedback=True,compile_plan=True,shared_facts=None):
    started=time.monotonic();deadline=started+max_seconds;steps=[];usage={};plan=None;terminal='max_steps'
    catalog=Catalog(toolbox,deadline);metadata={'usage_complete':True,'fallback':False};checker=None
    def record(response,phase):
        for key in ['prompt_tokens','completion_tokens','total_tokens','reasoning_tokens']:
            value=getattr(response,key,None)
            if value is not None:usage[key]=usage.get(key,0)+value
        if response.total_tokens is None:metadata['usage_complete']=False
        steps.append({'phase':phase,'content':response.content,'attempts':response.attempts,
                      'request_latencies':response.request_latencies,'request_id':response.request_id,
                      'response_model':response.model,'prompt_tokens':response.prompt_tokens,
                      'completion_tokens':response.completion_tokens})
    try:
        city=_tool_city(task['target_city'])
        bases={k:catalog.select(k,city) for k in ('attraction','hotel','restaurant')} if shared_facts is None else {k:list(shared_facts[k].values()) for k in ('attraction','hotel','restaurant')}
        req=extract_requirements(task,bases,config,deadline,record);metadata['predicted_requirements']=req
        facts=shortlist(catalog,task,bases,req) if shared_facts is None else copy.deepcopy(shared_facts)
        req.setdefault('entity_aliases',{})
        if shared_facts is not None:
            for kind,field,key,op in [('attraction','attraction_types','type','eq'),('attraction','attraction_names','name','contains'),('hotel','hotel_features','featurehoteltype','contains'),('hotel','hotel_names','name','contains'),('restaurant','cuisines','cuisine','eq'),('restaurant','restaurant_names','name','contains')]:
                for value in req.get(field,[]):
                    found=catalog.select(kind,city,key,value,op)
                    req['entity_aliases'][value]=[str(r.get(key,'')) for r in found]

        metadata['fact_catalog']=facts
        metadata['components']={'feedback':feedback,'compiler':compile_plan,'shared_context':shared_facts is not None}
        compact={kind:{key:{k:v for k,v in row.items() if k not in ('recommendedfood','hotelname_en')}
                       for key,row in rows.items()} for kind,rows in facts.items()}
        messages=[{'role':'system','content':
            'Choose an itinerary SKETCH using ONLY offered IDs. No prices, times or invented entities. '
            'Return JSON: {"outbound":"ID","inbound":"ID","hotel":"hID",'
            '"days":[[{"kind":"attraction","id":"aID"},{"kind":"lunch","id":"rID"}]]}. '
            'Exactly N days, each with at least one attraction and one lunch/dinner. No repeated attractions or restaurants. '
            'Required types/named places/cuisines must be included somewhere. Respect preferred transport, hotel and room requirements. '
            'Choose early arrival, late same-day return and geographically close places, leave ample time for ground transport. '
            'Activities use minimum recommended duration; meals 45min; lunch 11-14, dinner 17-20; every night needs hotel. '
            'For one day hotel=null. Keep visits short (2-3/day) and affordable. Reply only JSON.'},
            {'role':'user','content':json.dumps({'task':task,'requirements':req,'facts':compact},ensure_ascii=False)}]
        if not compile_plan:
            from ..plan.schema import load_plan_schema
            messages[0]['content']='Write a complete travel plan using ONLY supplied tool facts. Return JSON matching this schema: '+json.dumps(load_plan_schema(),ensure_ascii=False)
        for _ in range(min(3 if feedback else 1,max_steps-len(steps))):
            response=chat_step(config,messages,deadline=deadline);record(response,'select_sketch')
            sketch=extract_plan(response.content) or {};messages.append({'role':'assistant','content':response.content})
            try:
                if compile_plan:
                    candidate=compile_sketch(task,sketch,facts,req,catalog.tools)
                else:
                    from ..plan.normalize import normalise_plan
                    from ..plan.grounding import complete_routes
                    candidate=complete_routes(task,normalise_plan(sketch,task.get('people_number')),toolbox)
                checked=check_requirements(task,candidate,req,toolbox)
                if checked.ok:
                    plan=candidate;checker=checked.as_dict();terminal='checked_ok';break
                feedback=[v.message for v in checked.violations][:10]
            except (ValueError,KeyError,TypeError) as exc:feedback=[str(exc)]
            count_feedback()
            messages.append({'role':'user','content':'Compiler rejected sketch: '+json.dumps(feedback)+'. Revise only the sketch. Do not invent IDs.'})
        if feedback and plan is None and len(steps)<max_steps and time.monotonic()<deadline:
            metadata['fallback']=True
            revised=dict(task)
            revised['nature_language_en']=(task.get('nature_language_en') or task.get('nature_language',''))+'\nExtracted user constraints (predictions, not gold): '+json.dumps(req)
            out=run_react(revised,config,toolbox,method='b2',max_steps=max_steps-len(steps),
                max_seconds=deadline-time.monotonic(),checker=lambda _,p:check_requirements(task,p,req,toolbox))
            metadata['fallback_metadata']=out.metadata
            metadata['usage_complete'] &= out.metadata.get('usage_complete',True)
            steps.extend(dict(s,phase='react_fallback') for s in out.steps)
            for key,value in out.usage.items():usage[key]=usage.get(key,0)+(value or 0)
            plan,terminal,checker=out.plan,out.terminal_state,out.checker
    except ModelRequestError as exc:
        terminal='request_error';metadata['usage_complete']=False
        steps.append({'phase':'failed_request','attempts':exc.attempts,'request_latencies':exc.request_latencies,'error':type(exc.last_error).__name__})
    except TimeoutError:terminal='timeout'
    metadata['tool_trace']=catalog.tools.trace
    metadata['request_count']=sum(s.get('attempts',1) for s in steps)
    if time.monotonic()>=deadline:terminal='timeout'
    return Trajectory(str(task['uid']),'structured',config.model,terminal,plan,steps=steps,usage=usage,
        checker=checker,metadata=metadata,duration_seconds=time.monotonic()-started,
        finished_at=datetime.now(timezone.utc).isoformat())
