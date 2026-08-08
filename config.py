import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

_DEFAULT_CONFIG = {
  "api_keys": {
    "smspool": "",
    "capsolver": "",
    "okkproxy_user": "",
    "okkproxy_pass": "",
    "webshare_user": "",
    "webshare_pass": ""
  },
  "accounts": {
    "target_count": 10,
    "concurrency": 3
  },
  "smspool": {
    "country": 8,
    "service": "google",
    "max_reuse_per_number": 5,
    "poll_timeout_seconds": 150
  },
  "proxy": {
    "provider": "okkproxy",
    "country_code": "id",
    "host": "p.webshare.io",
    "port": 80
  },
  "browser": {
    "headless": True,
    "stealth": True,
    "slow_mo_ms": 80,
    "typing_delay_ms": 45,
    "page_timeout_ms": 35000
  },
  "storage": {
    "db_path": "data/accounts.db",
    "csv_path": "data/accounts.csv",
    "export_csv_on_finish": True
  }
}

def load() -> dict:
    """
    Load config.json, returning defaults if missing or invalid.
    Performs a shallow merge of user config over defaults.
    """
    # ensure parent dir exists
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if not CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(_DEFAULT_CONFIG, f, indent=2)
        except Exception:
            pass
        return dict(_DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        # back up invalid file and write defaults
        try:
            bad = CONFIG_PATH.with_suffix(".invalid.json")
            CONFIG_PATH.replace(bad)
            with open(CONFIG_PATH, "w") as f:
                json.dump(_DEFAULT_CONFIG, f, indent=2)
        except Exception:
            pass
        return dict(_DEFAULT_CONFIG)
    except Exception:
        return dict(_DEFAULT_CONFIG)

    # shallow merge defaults with user config
    merged = dict(_DEFAULT_CONFIG)
    for k, v in data.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged

def save(cfg: dict):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        # Swallow save errors to avoid crashing the UI; log elsewhere if needed
        pass

def get(cfg: dict, *keys, default=None):
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val
