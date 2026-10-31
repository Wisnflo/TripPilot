"""Model configuration: environment variables, with an optional local .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"

MODEL_ENV_VARS = ("CHINATRAVEL_OPENAI_MODEL", "OPENAI_MODEL")
KEY_ENV_VARS = ("CHINATRAVEL_OPENAI_API_KEY", "OPENAI_API_KEY")
BASE_URL_ENV_VARS = ("CHINATRAVEL_OPENAI_BASE_URL", "OPENAI_BASE_URL")


class ConfigError(RuntimeError):
    """Raised when a required model setting is missing."""


@dataclass(frozen=True)
class ModelConfig:
    model: str
    api_key: str = field(repr=False)
    base_url: Optional[str] = None
    request_timeout_seconds: float = 90.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 2.0
    price_input_per_1m: Optional[float] = None
    price_output_per_1m: Optional[float] = None
    sources: Dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def base_url_host(self) -> str:
        if not self.base_url:
            return "default"
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc or "default"


def load_dotenv(path: Path = ENV_FILE, environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Load simple KEY=VALUE lines into environ without overriding real env vars.

    Values are never printed or logged. Returns the parsed keys/values.
    """
    env = os.environ if environ is None else environ
    if not path.is_file():
        return {}
    parsed: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        parsed[key] = value
        env.setdefault(key, value)
    return parsed


def _first_present(env: Dict[str, str], names) -> Optional[str]:
    for name in names:
        value = env.get(name)
        if value:
            return name
    return None


def resolve_model_config(
    model_override: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
    env_file: Optional[Path] = None,
) -> ModelConfig:
    env = dict(os.environ) if environ is None else environ
    load_dotenv(env_file or ENV_FILE, env)

    model = model_override or env.get("CHINATRAVEL_OPENAI_MODEL") or env.get("OPENAI_MODEL")
    if not model:
        raise ConfigError(
            "missing model name; set --model or one of %s" % ", ".join(MODEL_ENV_VARS)
        )

    key_name = _first_present(env, KEY_ENV_VARS)
    if not key_name:
        raise ConfigError(
            "missing API key; set one of %s in the environment or project .env"
            % ", ".join(KEY_ENV_VARS)
        )

    base_name = _first_present(env, BASE_URL_ENV_VARS)

    def _float(name: str, default: Optional[float]) -> Optional[float]:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfigError("invalid float for %s: %r" % (name, raw))

    def _int(name: str, default: int) -> int:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            raise ConfigError("invalid int for %s: %r" % (name, raw))

    return ModelConfig(
        model=model,
        api_key=env[key_name],
        base_url=env[base_name] if base_name else None,
        request_timeout_seconds=_float("CHINATRAVEL_REQUEST_TIMEOUT", 90.0),
        max_attempts=max(1, _int("CHINATRAVEL_MAX_ATTEMPTS", 3)),
        retry_backoff_seconds=_float("CHINATRAVEL_RETRY_BACKOFF", 2.0),
        price_input_per_1m=_float("CHINATRAVEL_PRICE_INPUT_PER_1M", None),
        price_output_per_1m=_float("CHINATRAVEL_PRICE_OUTPUT_PER_1M", None),
        sources={"model": "override" if model_override else "env", "key": key_name},
    )
