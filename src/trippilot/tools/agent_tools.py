"""Structured ChinaTravel tools exposed to the agent."""

import sys
from pathlib import Path
from typing import Any, Dict, List
from ..execution.budget import count_tool

UPSTREAM_ROOT = Path(__file__).resolve().parents[3] / "upstream" / "ChinaTravel"
if str(UPSTREAM_ROOT) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_ROOT))

from agent_env.adapter import ChinaTravelEnvAdapter  # noqa: E402


AGENT_TOOL_ALLOWLIST = frozenset(
    {
        "attractions_keys",
        "attractions_select",
        "attractions_id_is_open",
        "attractions_nearby",
        "attractions_types",
        "accommodations_keys",
        "accommodations_select",
        "accommodations_nearby",
        "restaurants_keys",
        "restaurants_select",
        "restaurants_id_is_open",
        "restaurants_nearby",
        "restaurants_with_recommended_food",
        "restaurants_cuisine",
        "goto",
        "intercity_transport_select",
        "poi_lat_lon_search",
        "next_page",
    }
)

BLOCKED_TOOLS = frozenset({"china_travel_load_query", "china_travel_world_command"})


class ToolBox:
    """Only allowlisted, read-only structured tools reach the model."""

    def __init__(self, lang: str = "en") -> None:
        self.lang = lang
        self.adapter = ChinaTravelEnvAdapter(lang=lang)
        self._zh_adapter = None
        self.validation_cache = {}
        self._intercity_cache = {}

    def _invoke(self, name, arguments, adapter=None):
        count_tool(name, arguments)
        return (adapter or self.adapter).call_tool(name, arguments)

    def openai_tools(self) -> List[Dict[str, Any]]:
        specs = self.adapter.list_tools(format="openai")
        return [
            spec
            for spec in specs
            if spec.get("function", {}).get("name") in AGENT_TOOL_ALLOWLIST
        ]

    def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name not in AGENT_TOOL_ALLOWLIST:
            return {
                "success": False,
                "error_type": "ToolNotAllowed",
                "error": "Tool %r is not exposed to the agent." % name,
            }
        arguments = dict(arguments)
        from ..plan.checker import CITY_ZH_TO_EN
        if self.lang == 'en':
            for key in ('city', 'start_city', 'end_city'):
                if key in arguments: arguments[key] = CITY_ZH_TO_EN.get(arguments[key], arguments[key])
        response = self._invoke(name, arguments)
        # Link user-spelled Chinese entity names through PUBLIC bilingual IDs.
        # This path is shared by every method, never uses scoring annotations.
        value = arguments.get('value')
        is_name_query = name in ('attractions_select', 'restaurants_select', 'accommodations_select') and arguments.get('key') in ('name','type','cuisine','featurehoteltype')
        if self.lang == 'en' and is_name_query and isinstance(value, str) and any('\u4e00' <= c <= '\u9fff' for c in value):
            if self._zh_adapter is None: self._zh_adapter = ChinaTravelEnvAdapter(lang='zh')
            zh = dict(arguments); inverse = {v:k for k,v in CITY_ZH_TO_EN.items()}
            zh['city'] = inverse.get(zh.get('city'), zh.get('city'))
            linked = self._invoke(name, zh, self._zh_adapter)
            data = linked.get('data') or {}; rows = data.get('rows', []) if isinstance(data, dict) else []
            english = []
            for row in rows[:10]:
                mapped = self._invoke(name, {'city':arguments['city'],'key':'id','op':'eq','value':int(row['id'])})
                english.extend((mapped.get('data') or {}).get('rows', []))
            if english:
                return {'success':True,'command':name,'resolved_from_language':'zh',
                        'data':{'type':'dataframe','rows':english,'row_count':len(english),'returned_rows':len(english)},
                        'text':str(english)}
        # Upstream's structured row_count describes the CURRENT page, while
        # text exposes total pages. Report the true count consistently so any
        # agent can decide whether to call next_page. Rows are already paged.
        if response.get("success") and isinstance(response.get("data"), dict) and response["data"].get("type") == "dataframe":
            env = self.adapter._get_env()
            output = env.results[-1] if env.results else None
            if output is not None and hasattr(output, "_page_idx"):
                response["data"]["row_count"] = len(output["whole_data"])
                response["data"]["page"] = output._page_idx + 1
                response["data"]["pages"] = output._page_total
        return response

    def intercity_rows(self, start_city: str, end_city: str, intercity_type: str) -> List[Dict[str, Any]]:
        """Full intercity table for runtime checking.

        The agent-facing adapter pages dataframes to 10 rows, which hides most
        train IDs. The checker needs the complete table to verify a plan.
        """
        key=(start_city,end_city,intercity_type)
        if key in self._intercity_cache:return self._intercity_cache[key]
        count_tool("intercity_validation_rows", {"start_city": start_city, "end_city": end_city, "type": intercity_type})
        env = self.adapter._get_env()
        table = env.intercitytransport.select(
            start_city=start_city,
            end_city=end_city,
            intercity_type=intercity_type,
        )
        if table is None or not hasattr(table, "to_dict"):
            return []
        self._intercity_cache[key]=table.to_dict(orient="records")
        return self._intercity_cache[key]
