# catalog.py
"""
All system-implemented tool functions live here.
These tools are imported and invoked by the workflow/orchestrator.

Responsibilities:
- Load local lookup tables (name -> [slugs...])
- Resolve a concrete item to query (name or slug) using local JSONL/SQLite
- Fetch details from Open5e API with on-disk caching
- (Optional) regenerate lookup tables from JSONL
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests

# -------------------- Paths & Files --------------------
# 1. 获取当前文件 (catalog.py) 所在的目录绝对路径
# 结果例如: /Users/you/AI-Dungeon-Master/app/engine
CURRENT_DIR = Path(__file__).resolve().parent

# 2. 往上退两层，找到项目根目录
# parent -> app
# parent.parent -> AI-Dungeon-Master
PROJECT_ROOT = CURRENT_DIR.parent.parent

# 3. 指向 data/library
# 最终结果: /Users/you/AI-Dungeon-Master/data/library
BASE = PROJECT_ROOT / "data" / "dnd_library"

# 确保目录存在 (可选，视情况而定)
if not BASE.exists():
    print(f"Warning: Library path does not exist: {BASE}")

CACHE_DIR = BASE / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Lookup tables (name -> [slugs...])  ← 加全目录
LOOKUP_FILES = {
    "monsters":    BASE / "open5e_monsters_lookupTable.json",
    "equipment":   BASE / "open5e_equipment_lookupTable.json",
    "spells":      BASE / "open5e_spells_lookupTable.json",
    "backgrounds": BASE / "open5e_backgrounds_lookupTable.json",
    "classes":     BASE / "open5e_classes_lookupTable.json",
    "conditions":  BASE / "open5e_conditions_lookupTable.json",
    "documents":   BASE / "open5e_documents_lookupTable.json",
    "feats":       BASE / "open5e_feats_lookupTable.json",
    "planes":      BASE / "open5e_planes_lookupTable.json",
    "races":       BASE / "open5e_races_lookupTable.json",
    "sections":    BASE / "open5e_sections_lookupTable.json",
    "spelllist":   BASE / "open5e_spelllist_lookupTable.json",
}

# JSONL catalogs (each line is a normalized entry with {type, name, slug_or_index, api_url, ...})
JSONL_FILES = {
    "monsters":    BASE / "open5e_monsters.jsonl",
    "equipment":   BASE / "open5e_equipment.jsonl",
    "spells":      BASE / "open5e_spells.jsonl",
    "backgrounds": BASE / "open5e_backgrounds.jsonl",
    "classes":     BASE / "open5e_classes.jsonl",
    "conditions":  BASE / "open5e_conditions.jsonl",
    "documents":   BASE / "open5e_documents.jsonl",
    "feats":       BASE / "open5e_feats.jsonl",
    "planes":      BASE / "open5e_planes.jsonl",
    "races":       BASE / "open5e_races.jsonl",
    "sections":    BASE / "open5e_sections.jsonl",
    "spelllist":   BASE / "open5e_spelllist.jsonl",
}


# Unified SQLite catalog created by your builder (optional but recommended for fast lookups)
SQLITE_PATH = BASE / "open5e_catalog.sqlite"


# -------------------- Internal helpers --------------------
def _load_lookup(kind: str) -> Dict[str, List[str]]:
    """Load a name->slugs lookup table for a given kind (e.g., 'monsters')."""
    path = LOOKUP_FILES.get(kind)
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sqlite_get_api_url(res_type: str, slug: str) -> Optional[str]:
    """Fetch the api_url for (type, slug) from SQLite if available."""
    if not SQLITE_PATH.exists():
        return None
    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT api_url FROM catalog WHERE type=? AND slug_or_index=?",
            (res_type, slug),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def _jsonl_find_by_slug_or_name(res_type: str, name_or_slug: str) -> Optional[Dict[str, Any]]:
    """
    Fallback scan within the JSONL for the first row where:
      - slug_or_index equals the key, OR
      - name equals the key (exact match, case-insensitive).
    Returns a small metadata dict or None.
    """
    f = JSONL_FILES.get(res_type)
    if not f or not f.exists():
        return None
    key = name_or_slug.strip().lower()
    with f.open("r", encoding="utf-8") as fr:
        for line in fr:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            name = (obj.get("name") or "").strip().lower()
            slug = (obj.get("slug_or_index") or "").strip().lower()
            if key == slug or key == name:
                return {
                    "type": obj.get("type"),
                    "name": obj.get("name"),
                    "slug": obj.get("slug_or_index"),
                    "api_url": obj.get("api_url"),
                    "document_slug": obj.get("document_slug"),
                    "document_title": obj.get("document_title"),
                }
    return None


# -------------------- Public tool functions --------------------
def look_table(res_type: str, query: str, limit: int = 20) -> Dict[str, Any]:
    """
    Generic name->slugs fuzzy lookup for ANY supported type in LOOKUP_FILES.
    Returns: {"matches": [{"name": str, "slugs": [str, ...]} ...]}
    """
    path = LOOKUP_FILES.get(res_type)
    if not path or not path.exists():
        return {"error": f"lookup table not found for type: {res_type}"}
    data = json.loads(path.read_text(encoding="utf-8"))
    q = (query or "").lower().strip()
    out = []
    for name, slugs in data.items():
        if q in name.lower():
            out.append({"name": name, "slugs": slugs})
            if len(out) >= limit:
                break
    return {"matches": out}

def look_monster_table(query: str, limit: int = 20) -> Dict[str, Any]:
    """Backward-compatible alias for monsters lookup."""
    return look_table("monsters", query, limit)


def search_table(res_type: str, name_or_slug: str, prefer_doc: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolve a unique entry (name or slug) and return {chosen_name, chosen_slug, api_url}.
    Strategy:
      1) Treat input as a slug and try SQLite first (fast).
      2) Otherwise scan JSONL for exact slug/name equality.
      3) If input is a name and a lookup table exists for this type,
         use the lookup's slug candidates and then pick the best match from JSONL.
         If prefer_doc (e.g. 'srd-2024' / 'srd-2014') is provided, prefer that doc.
    """
    # 1) Try slug in SQLite
    api_url = _sqlite_get_api_url(res_type, name_or_slug)
    if api_url:
        return {"chosen_name": name_or_slug, "chosen_slug": name_or_slug, "api_url": api_url}

    # 2) Try exact match in JSONL by slug or name
    hit = _jsonl_find_by_slug_or_name(res_type, name_or_slug)
    if hit:
        return {"chosen_name": hit["name"], "chosen_slug": hit["slug"], "api_url": hit["api_url"]}

    # 3) Disambiguate by name using lookup (if any)
    kind_for_lookup = res_type if res_type in LOOKUP_FILES else None
    if kind_for_lookup:
        lookup = _load_lookup(kind_for_lookup)
        for name, slugs in lookup.items():
            if name.strip().lower() == name_or_slug.strip().lower() and slugs:
                best = None
                for s in slugs:
                    meta = _jsonl_find_by_slug_or_name(res_type, s)
                    if not meta:
                        continue
                    if not best:
                        best = meta
                    if prefer_doc and meta.get("document_slug") == prefer_doc:
                        best = meta
                        break
                if best:
                    return {"chosen_name": best["name"], "chosen_slug": best["slug"], "api_url": best["api_url"]}

    return {"error": f"not found: {res_type} / {name_or_slug}"}


def fetch_and_cache(res_type: str, slug: str) -> Dict[str, Any]:
    """
    Given a (type, slug), locate the api_url and GET the detailed JSON, with on-disk caching.
    Cache path: cache/<type>/<slug>.json
    Returns: {"slug": slug, "api_url": api_url, "data": <json>}
    """
    cache_path = CACHE_DIR / res_type / f"{slug}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    # Resolve api_url from SQLite; fallback to JSONL if needed
    api_url = _sqlite_get_api_url(res_type, slug)
    if not api_url:
        meta = _jsonl_find_by_slug_or_name(res_type, slug)
        if not meta or not meta.get("api_url"):
            return {"error": f"no api_url for {res_type}/{slug}"}
        api_url = meta["api_url"]

    r = requests.get(api_url, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = {"slug": slug, "api_url": api_url, "data": data}
    cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_lookup_from_jsonl(jsonl_path: Path, out_path: Path):
    """
    Optional helper: rebuild a lookup table (name -> [slugs...]) from a JSONL file.
    Useful if you want to regenerate lookup tables later.
    """
    from collections import defaultdict

    table = defaultdict(list)
    with jsonl_path.open("r", encoding="utf-8") as fr:
        for line in fr:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            name = row.get("name")
            slug = row.get("slug_or_index")
            if name and slug and slug not in table[name]:
                table[name].append(slug)
    out_path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
