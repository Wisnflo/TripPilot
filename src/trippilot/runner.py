"""Run a small pilot and persist trajectories."""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent.loop import Trajectory, run_react, run_single_shot
from .models.config import ModelConfig
from .plan.checker import check_plan
from .tools.agent_tools import ToolBox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_VISIBLE_ROOT = PROJECT_ROOT / "data" / "agent_visible"


def load_task(split: str, uid: str) -> Dict[str, Any]:
    path = AGENT_VISIBLE_ROOT / split / ("%s.json" % uid)
    return json.loads(path.read_text(encoding="utf-8"))


def list_task_uids(split: str, limit: Optional[int] = None) -> List[str]:
    paths = sorted((AGENT_VISIBLE_ROOT / split).glob("*.json"))
    uids = [path.stem for path in paths]
    return uids[:limit] if limit else uids


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def compute_cost(usage: Dict[str, Any], price_input: Optional[float], price_output: Optional[float]) -> Optional[float]:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if price_input is None or price_output is None or prompt is None or completion is None:
        return None
    return (prompt * price_input + completion * price_output) / 1_000_000


def run_one(
    task: Dict[str, Any],
    method: str,
    config: ModelConfig,
    toolbox: ToolBox,
    max_steps: int = 12,
    client: Any = None,
    max_seconds: Optional[float] = None,
) -> Trajectory:
    if method == "b0":
        return run_single_shot(task, config, client=client, max_seconds=max_seconds)
    if method == "b1":
        return run_react(task, config, toolbox, method="b1", max_steps=max_steps, client=client, max_seconds=max_seconds)
    if method == "b2":
        return run_react(
            task,
            config,
            toolbox,
            method="b2",
            max_steps=max_steps,
            client=client,
            max_seconds=max_seconds,
            checker=lambda task_dict, plan: check_plan(task_dict, plan, toolbox=toolbox),
        )
    if method == "ours":
        raise NotImplementedError("Ours (dependency-driven local repair) is M2; use b0/b1/b2 for now.")
    raise ValueError("unknown method: %s" % method)


def new_run_dir(output_root: Path, config: ModelConfig) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_root / ("%s-%s" % (stamp, model_slug(config.model)))
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_trajectory(run_dir: Path, method: str, trajectory: Trajectory, pricing: Dict[str, Any]) -> None:
    method_dir = run_dir / method
    method_dir.mkdir(parents=True, exist_ok=True)
    payload = trajectory.as_dict()
    payload["cost"] = compute_cost(
        trajectory.usage,
        pricing.get("input_per_1m"),
        pricing.get("output_per_1m"),
    )
    payload["pricing"] = pricing
    path = method_dir / ("%s.json" % trajectory.task_uid)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pricing_from_env(config: Optional[ModelConfig] = None) -> Dict[str, Any]:
    def _float(name):
        value = os.environ.get(name)
        try:
            return float(value) if value not in (None, "") else None
        except ValueError:
            return None

    input_price = _float("PRICE_INPUT_PER_1M")
    output_price = _float("PRICE_OUTPUT_PER_1M")
    if input_price is None and config is not None:
        input_price = config.price_input_per_1m
    if output_price is None and config is not None:
        output_price = config.price_output_per_1m
    return {
        "currency": os.environ.get("PRICE_CURRENCY", ""),
        "input_per_1m": input_price,
        "output_per_1m": output_price,
    }
