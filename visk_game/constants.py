from __future__ import annotations

from pathlib import Path


SAVE_PATH = Path(__file__).resolve().parent.parent / "visk_save.json"
CHUNK_SIZE = 28
GENERATION_RADIUS = 2
MAX_ENEMY_LENGTH = 200

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

ABILITY_NAMES = ("zap", "bomb", "mine", "silence", "ping", "dash")
SECTOR_NAMES = ("MAINFRAME", "ARCHIVE", "SUBROUTINE", "BLACKSITE", "NULLNET", "SECTOR-7")
THEMES = (
    {
        "name": "noir",
        "bg": (10, 10, 12),
        "bg_alt": (14, 14, 17),
        "wall": (78, 79, 88),
        "floor": (12, 12, 14),
        "player": (235, 236, 241),
        "player_pending": (208, 212, 222),
        "enemy": (205, 108, 120),
        "enemy_alt": (143, 121, 212),
        "pickup": (171, 227, 146),
        "bytes": (130, 171, 244),
        "accent": (196, 132, 255),
        "muted": (82, 84, 92),
        "ping": (169, 150, 255),
        "blind": (8, 8, 10),
    },
)
NOISE_GLYPHS = "  .`:"
RUN_ART = (
    "██╗   ██╗██╗███████╗██╗  ██╗",
    "██║   ██║██║██╔════╝██║ ██╔╝",
    "██║   ██║██║███████╗█████╔╝ ",
    "╚██╗ ██╔╝██║╚════██║██╔═██╗ ",
    " ╚████╔╝ ██║███████║██║  ██╗",
    "  ╚═══╝  ╚═╝╚══════╝╚═╝  ╚═╝",
)

SHOP_ITEMS = (
    {
        "id": "dash_cache",
        "name": "DASH CACHE",
        "description": "Start each run with +1 DASH charge.",
        "base_cost": 140,
    },
    {
        "id": "ping_cache",
        "name": "PING CACHE",
        "description": "Start each run with +1 PING charge.",
        "base_cost": 120,
    },
    {
        "id": "magnet",
        "name": "BYTE MAGNET",
        "description": "Increase shard value by 20% per level.",
        "base_cost": 160,
    },
    {
        "id": "focus",
        "name": "FOCUS MASK",
        "description": "Reduce BLINDER duration by 3 turns per level.",
        "base_cost": 130,
    },
)
