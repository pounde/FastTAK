"""Load monitoring configuration from thresholds.yml with .env overrides."""

import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "thresholds.yml"

# Prefix for env var overrides — scoped to avoid collisions with
# unrelated env vars (PATH, HOME, etc.)
_ENV_PREFIX = "FASTAK_MON_"


def load_config(path: Path | None = None) -> dict:
    """Load thresholds.yml and apply environment variable overrides.

    Env var convention: FASTAK_MON_<SERVICE>__<KEY> or
    FASTAK_MON_<SERVICE>__THRESHOLDS__<METRIC>__<LEVEL>
    e.g., FASTAK_MON_DATABASE__INTERVAL=30
          FASTAK_MON_DISK__THRESHOLDS__PERCENT__WARNING=85
    """
    config_path = path or _CONFIG_PATH
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Apply env overrides — only vars with our prefix
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        remainder = key[len(_ENV_PREFIX) :]
        parts = remainder.split("__")
        if len(parts) < 2:
            continue
        service = parts[0].lower()
        if service not in config:
            continue
        _apply_override(config[service], parts[1:], value)

    return config


def _apply_override(obj: dict, path: list[str], value: str) -> None:
    """Apply a single env var override to a nested dict path."""
    for part in path[:-1]:
        key = part.lower()
        if key not in obj:
            return
        obj = obj[key]
    final_key = path[-1].lower()
    if final_key in obj:
        # Coerce to match existing type
        existing = obj[final_key]
        if isinstance(existing, int):
            obj[final_key] = int(value)
        elif isinstance(existing, float):
            obj[final_key] = float(value)
        else:
            obj[final_key] = value
