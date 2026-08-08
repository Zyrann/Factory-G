import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

def load() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def get(cfg: dict, *keys, default=None):
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val
