from __future__ import annotations

import json

from .constants import SAVE_PATH
from .models import SaveData


def load_save() -> SaveData:
    if not SAVE_PATH.exists():
        return SaveData()
    try:
        raw = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SaveData()
    data = SaveData()
    data.banked_bytes = int(raw.get("banked_bytes", 0))
    data.streak = int(raw.get("streak", 0))
    data.hardcore = bool(raw.get("hardcore", False))
    data.audio_enabled = bool(raw.get("audio_enabled", True))
    upgrades = raw.get("upgrades", {})
    for key in data.upgrades:
        data.upgrades[key] = int(upgrades.get(key, 0))
    return data


def save_save(data: SaveData) -> None:
    payload = {
        "banked_bytes": data.banked_bytes,
        "streak": data.streak,
        "hardcore": data.hardcore,
        "audio_enabled": data.audio_enabled,
        "upgrades": data.upgrades,
    }
    SAVE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
