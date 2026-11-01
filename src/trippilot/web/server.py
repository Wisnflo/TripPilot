"""Loopback-only interactive travel demo; credentials remain in parent memory."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import csv
import mimetypes
from urllib.parse import unquote,urlsplit
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid

from ..money import plan_total
from ..execution.budget import read_resources

ROOT=Path(__file__).resolve().parents[3]


def load(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (FileNotFoundError,json.JSONDecodeError):return default


def view(record,before=None,task=None,replay=False):
    result=record.get('result',{});trajectory=result.get('result',result)
    plan=result.get('plan') or trajectory.get('plan')
    metadata=trajectory.get('metadata',{})
    ledger=record.get('ledger',{})
    preferences=(metadata.get('predicted_requirements') or {}).get('unsupported',[])
    if not isinstance(preferences,list):preferences=[]
    preferences=list(dict.fromkeys(value.strip() for value in preferences if isinstance(value,str) and value.strip()))
    return {'status':result.get('status') or result.get('terminal_state'),
            'task':result.get('task') or task,'plan':plan,'before':before,'replay':replay,
            'question':result.get('state',{}).get('question'),'choices':result.get('state',{}).get('choices'),
            'total':str(plan_total(plan)) if plan else None,
            'seconds':record.get('supervision',{}).get('wall_seconds'),
            'tokens':ledger.get('observed_usage',{}).get('total_tokens'),
            'billing_unknown':ledger.get('unobserved_billing_possible',False),
            'violations':(trajectory.get('checker') or {}).get('violations',[]),
            'unverified_preferences':preferences,
            'sources':metadata.get('fact_catalog',{}),'tools':metadata.get('tool_trace',[])[:80],
            'resources':result.get('resources') or record.get('resources',{}),
            'requests':ledger.get('events',[])[-80:]}


def display_names():
    base=ROOT/'upstream/ChinaTravel/chinatravel/environment'
    result={}
    for en in (base/'database_en').rglob('*.csv'):
        zh=base/'database'/en.relative_to(base/'database_en')
        if not zh.exists():continue
        with en.open() as e,zh.open() as z:
            left=list(csv.DictReader(e));right=list(csv.DictReader(z))
        if not left or not right:continue
        identity=next((k for k in ('id','TrainID','FlightID') if k in left[0] and k in right[0]),None)
        if not identity:continue
        by_id={v[identity]:v for v in right}
        for row in left:
            match=by_id.get(row[identity],{})
            for field in ('name','From','To'):
                if row.get(field) and match.get(field):result[row[field]]=match[field]
    return result


def examples():
    initial=ROOT/'evaluation/runs/v3-static-test-01'
    dynamic=ROOT/'evaluation/runs/v3-dynamic-test-01'
    scores=load(dynamic/'scores.json',{}).get('rows',[])
    output={}
    for name,kind in [('transport','transport_unavailable'),('clarification','return_earlier')]:
        for row in scores:
            if row['method']!='patch' or not row.get('all_pass') or not row['key'].endswith(kind):continue
            record=load(dynamic/'patch'/row['key']/'record.json')
            if not record:continue
            seed=load(initial/'structured'/row['uid']/'record.json')
            if not seed:continue
            if name=='clarification' and not record['result'].get('clarification',{}).get('explicit_lodging_waiver'):continue
            before=seed['result']['plan'];task=load(ROOT/'data/agent_visible'/row['split']/(row['uid']+'.json'))
            item=view(record,before,task,replay=True)
            if name=='clarification':
                state=record['result'].get('clarification',{})
                item.update(demo_question=state.get('question'),demo_choices=state.get('choices'),demo_answer=state.get('answer'))
            output[name]=item;break
    for row in load(initial/'scores.json',{}).get('rows',[]):
        if row['method']=='structured' and row.get('all_pass'):
            record=load(initial/'structured'/row['uid']/'record.json')
            task=load(ROOT/'data/agent_visible'/row['split']/(row['uid']+'.json'))
            output['planning']=view(record,task=task,replay=True);break
    return output


class Jobs:
    def __init__(self,directory):
        self.root=Path(directory).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.items={};self.lock=threading.Lock()
        for folder in self.root.iterdir():
            meta=load(folder/'meta.json') if folder.is_dir() else None
            if meta:self.items[folder.name]={'folder':folder,'process':None,'before':meta.get('before'),'task':meta.get('task'),'started':time.monotonic(),'cancel_requested':bool(meta.get('cancel_requested'))}

    def create(self,data):
        if not isinstance(data,dict):raise ValueError('请求必须是一个对象')
        command=data.get('command','plan');model=data.get('model','qwen3.8-reasoner')
        if command not in ('plan','revise','answer') or not isinstance(model,str) or len(model)>150:raise ValueError('请求类型或模型无效')
        key=uuid.uuid4().hex;folder=self.root/key;folder.mkdir();output=folder/'run'
        args=[sys.executable,str(ROOT/'scripts/trip.py'),command,'--model',model,'--output-dir',str(output)]
        before=None;task=None
        if command=='plan':
            request=data.get('request','');days=int(data.get('days',2));people=int(data.get('people',2))
            if not isinstance(request,str) or not 0<len(request)<=10000 or not 1<=days<=7 or not 1<=people<=20:raise ValueError('请填写合理的需求、天数和人数')
            args+=['--from-city',str(data.get('from_city','上海')),'--to-city',str(data.get('to_city','杭州')),'--days',str(days),'--people',str(people),'--request',request]
        else:
            previous=self.items.get(data.get('previous'))
            if not previous:raise ValueError('请先生成一个行程')
            seed=previous['folder']/'run';record=load(seed/'record.json',{})
            before=record.get('result',{}).get('plan');task=load(seed/'task.json')
            if command=='revise':
                change=data.get('change','')
                if not before or not (seed/'plan.json').exists() or not isinstance(change,str) or not 0<len(change)<=10000:raise ValueError('缺少行程或修改要求')
                args+=['--task',str(seed/'task.json'),'--seed',str(seed/'plan.json'),'--change',change]
            else:
                if data.get('answer') not in ('cancel_overnight','keep_original') or not (seed/'state.json').exists():raise ValueError('无待确认的问题')
                state=load(seed/'state.json',{});before=state.get('seed_plan');task=state.get('task')
                args+=['--state',str(seed/'state.json'),'--answer',data['answer']]
        (folder/'meta.json').write_text(json.dumps({'before':before,'task':task,'command':command},ensure_ascii=False))
        log=(folder/'console.log').open('w')
        process=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        log.close()
        self.items[key]={'folder':folder,'process':process,'before':before,'task':task,'started':time.monotonic()}
        return key

    def get(self,key):
        if key not in self.items:raise KeyError(key)
        item=self.items[key];folder=item['folder']/'run';record=load(folder/'record.json')
        if record:return dict(view(record,item['before'],item['task']),id=key)
        running=item['process'] is not None and item['process'].poll() is None
        cancelled=bool(item.get('cancel_requested')) and not running
        return {'id':key,'status':'running' if running else 'cancelled' if cancelled else 'worker_error','seconds':time.monotonic()-item['started'],
                'resources':read_resources(folder/'resources.jsonl'),'requests':[],
                'message':None if running else '已取消本次操作。' if cancelled else '未生成行程，请检查模型配置或稍后重试。'}

    def cancel(self,key):
        item=self.items.get(key)
        if item and item['process'] is not None and item['process'].poll() is None:
            item['cancel_requested']=True
            metadata=load(item['folder']/'meta.json',{});metadata['cancel_requested']=True
            temporary=item['folder']/'meta.tmp';temporary.write_text(json.dumps(metadata,ensure_ascii=False));temporary.replace(item['folder']/'meta.json')
            item['process'].send_signal(signal.SIGINT)

    def close(self):
        for key in self.items:self.cancel(key)
        for item in self.items.values():
            if item['process'] is None:continue
            try:item['process'].wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(item['process'].pid,signal.SIGKILL)


def application(directory):
    jobs=Jobs(directory);token=secrets.token_urlsafe(24);demo=examples();labels=display_names()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,data,kind='application/json'):
            content=json.dumps(data,ensure_ascii=False).encode() if kind=='application/json' else data
            self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(content)
        def trusted(self,write=False):
            host=self.headers.get('Host','');allowed={'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
            if host not in allowed:return False
            origin=self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in allowed}:return False
            return not write or self.headers.get('X-TripPilot-Session')==token
        def do_GET(self):
            if not self.trusted():self.reply(403,{'error':'仅允许本机访问'});return
            if self.path=='/':
                page=(ROOT/'src/trippilot/web/index.html').read_text().replace('__SESSION_TOKEN__',token)
                self.reply(200,page.encode(),'text/html');return
            if self.path.startswith('/assets/'):
                directory=(ROOT/'src/trippilot/web/assets').resolve()
                try:path=(directory/unquote(urlsplit(self.path).path.removeprefix('/assets/'))).resolve()
                except (ValueError,OSError):self.reply(404,{'error':'未找到'});return
                if not path.is_relative_to(directory) or not path.is_file():self.reply(404,{'error':'未找到'});return
                self.reply(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream');return
            if self.path=='/api/examples':self.reply(200,demo);return
            if self.path=='/api/config':self.reply(200,{'model':os.environ.get('CHINATRAVEL_OPENAI_MODEL','qwen3.8-reasoner'),'names':labels,'ready':bool(os.environ.get('CHINATRAVEL_OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY'))});return
            if self.path=='/api/jobs':self.reply(200,{'jobs':list(jobs.items)});return
            if re.fullmatch(r'/api/jobs/[a-f0-9]{32}',self.path):
                try:self.reply(200,jobs.get(self.path.rsplit('/',1)[1]))
                except KeyError:self.reply(404,{'error':'任务不存在'})
                return
            self.reply(404,{'error':'未找到'})
        def do_POST(self):
            if not self.trusted(write=True):self.reply(403,{'error':'会话无效'});return
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<=20000:raise ValueError('请求过长')
                data=json.loads(self.rfile.read(length))
                if self.path=='/api/jobs':self.reply(201,{'id':jobs.create(data)});return
                if re.fullmatch(r'/api/jobs/[a-f0-9]{32}/cancel',self.path):
                    jobs.cancel(self.path.split('/')[3]);self.reply(200,{'status':'cancelling'});return
                self.reply(404,{'error':'未找到'})
            except (ValueError,TypeError,KeyError):self.reply(400,{'error':'请检查输入。创建行程后再执行修改或确认。'})
    return Handler,jobs
