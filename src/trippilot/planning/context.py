"""Deterministic public evidence packet shared by all compared methods."""
import copy
import time
import re
from .structured import Catalog, shortlist
from ..request import request_text
from ..plan.checker import _tool_city, CITY_ZH_TO_EN
from ..tools.agent_tools import ToolBox
from ..money import amount


def public_context(task, toolbox, deadline):
    catalog = Catalog(toolbox, deadline)
    city = _tool_city(task['target_city'])
    bases = {k: catalog.select(k, city) for k in ('attraction', 'hotel', 'restaurant')}
    facts = shortlist(catalog, task, bases, {})
    # Cover categories/bed counts and named Chinese entities independently of
    # method outcomes or hidden annotations. All queries count against budget.
    text = request_text(task)
    zh = Catalog(ToolBox(lang='zh'), deadline)
    city_zh = {v:k for k,v in CITY_ZH_TO_EN.items()}.get(city,city)
    for kind, values in bases.items():
        selected = dict(facts[kind])
        category = {'attraction':'type','restaurant':'cuisine','hotel':'numbed'}[kind]
        groups = sorted({str(v.get(category,'')) for v in values})
        for group in groups:
            rows = sorted([v for v in values if str(v.get(category,''))==group],key=lambda v:(amount(v.get('price',0)),str(v['id'])))[:2]
            selected.update({kind[0]+str(v['id']):v for v in rows})
        named = [v for v in values if v.get('name','').lower() in text.lower() and v.get('name')]
        zh_rows = zh.select(kind,city_zh)
        def mentioned(row):
            if row.get('name') and row['name'] in text:return True
            for field in ('type','cuisine','featurehoteltype'):
                for value in re.split(r'[,，;；、|/]',str(row.get(field,''))):
                    value=value.strip(' []\"\'')
                    stem=value[:-1] if value.endswith(('室','馆')) else value
                    if len(stem)>=2 and stem in text:return True
            return False
        named_ids = {str(v['id']) for v in zh_rows if mentioned(v)}
        named += [v for v in values if str(v['id']) in named_ids]
        # Required names survive the cap; deterministic generic choices follow.
        ordered = {kind[0]+str(v['id']):v for v in named}
        ordered.update({k:v for k,v in selected.items() if k not in ordered})
        facts[kind]=dict(list(ordered.items())[:32])
    return facts
