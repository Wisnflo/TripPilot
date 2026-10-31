"""Parent-only evaluation inputs and version guards; never imported by workers."""
import hashlib
import json
from pathlib import Path
import re


def source_files(root):
    root=Path(root)
    files=set()
    for base in ('src','scripts','tests','eval'):
        files.update(p for p in (root/base).rglob('*') if p.is_file() and p.suffix in ('.py','.html'))
    files.update((root/'config').glob('*.json'))
    public=root/'upstream/ChinaTravel'
    files.update(p for p in public.rglob('*.py'))
    files.update(p for p in (public/'chinatravel/environment').rglob('*') if p.is_file() and p.suffix in ('.csv','.json','.jsonl'))
    files.add(public/'chinatravel/evaluation/output_schema.json')
    files.update(root/name for name in ('pyproject.toml','uv.lock','.python-version'))
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def input_files(root,events,kind):
    files={}
    for event in events:
        uid=event['uid'];split=event['split']
        if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',uid) or split not in ('easy','medium','human'):
            raise ValueError('Invalid task identity')
        paths=[root/'data'/area/split/(uid+'.json') for area in ('agent_visible','scoring')]
        if kind=='dynamic':
            paths.append(root/event['seed'])
            if event.get('solution_reference'):paths.append(root/event['solution_reference'])
        for path in paths:
            path=path.resolve()
            if not path.is_relative_to(root.resolve()):raise ValueError('Input must belong to this project')
            files[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def changed_files(root,sources,inputs):
    observed=source_files(root)
    changes={name for name in sources.keys()|observed.keys() if sources.get(name)!=observed.get(name)}
    for name,wanted in inputs.items():
        path=root/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:changes.add(name)
    return sorted(changes)


def make_job(root,event,kind,settings):
    """Copy an explicit input contract. Offline proof paths never reach a child."""
    job={key:event[key] for key in ('uid','split')}
    if kind=='dynamic':
        allowed={'kind','day','new_days','new_budget','target','description'}
        job['event']={k:v for k,v in event['event'].items() if k in allowed}
        job['clarification_answer']=event.get('clarification_answer')
        job['seed_plan']=json.loads((root/event['seed']).read_text())['plan']
    job.update(settings,kind=kind)
    job['task']=json.loads((root/'data/agent_visible'/event['split']/(event['uid']+'.json')).read_text())
    return job
