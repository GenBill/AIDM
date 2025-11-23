# app/config.py
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # 指向根目录 AI-Dungeon-Master
DATA_DIR = BASE_DIR / "data"
STORIES_DIR = DATA_DIR / "stories"
LIBRARY_DIR = DATA_DIR / "dnd_library"