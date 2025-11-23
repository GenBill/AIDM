import json, time, sqlite3, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://api.open5e.com/"

# ---- 输出文件策略 ----
# 这三个类型合并进一个 equipment 文件
EQUIPMENT_TYPES = {"armor", "weapons", "magicitems"}
EQUIPMENT_JSONL = Path("open5e_equipment.jsonl")

# 其他类型：各自一个文件，命名为 open5e_<type>.jsonl
SINGLE_TYPE_JSONL_DIR = Path(".")  # 你也可以改为 Path("./by_type")

CATALOG_DB = Path("open5e_catalog.sqlite")

# ---- 全局 Session + 重试策略 ----
_session = requests.Session()
_retries = Retry(
    total=6,
    backoff_factor=0.7,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
)
_adapter = HTTPAdapter(max_retries=_retries, pool_connections=10, pool_maxsize=10)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

def get(url, **kw):
    kw.setdefault("timeout", 30)
    print(f"[HTTP] GET {url}")
    return _session.get(url, **kw)

def discover_resources():
    print("[STEP] discover_resources")
    r = get(BASE)
    r.raise_for_status()
    data = r.json()
    resources = {k: v for k, v in data.items() if isinstance(v, str) and v.startswith("http")}
    print(f"[DISCOVERED] {len(resources)} types: {list(resources.keys())}")
    return resources

def iter_paginated(url, limit=200):
    print(f"[STEP] iter {url} (limit={limit})")
    params = {"limit": limit}
    page = 1
    while url:
        page_url = url
        print(f"[PAGE] {page} {page_url}")
        try:
            resp = get(page_url, params=params if "?" not in page_url else None)
            resp.raise_for_status()
        except requests.exceptions.ReadTimeout:
            print("[WARN] read timeout, will retry via requests Retry() automatically next loop.")
            continue
        j = resp.json()
        results = j.get("results")
        if results is None:
            results = j if isinstance(j, list) else []
        for item in results:
            yield item
        url = j.get("next")
        page += 1

def guess_magicitem_subtype(name: str):
    """粗分类 magicitems：盔甲/武器/其他，用于 equipment 合并时的便捷过滤"""
    n = (name or "").lower()
    armor_keys = ["armor", "plate", "chain", "shield", "leather", "mail", "breastplate", "splint", "ring mail", "studded"]
    weapon_keys = ["sword", "dagger", "axe", "bow", "mace", "spear", "glaive", "halberd", "scimitar", "rapier", "club", "warhammer", "maul", "morningstar", "trident", "whip", "javelin", "pike", "crossbow"]
    if any(k in n for k in armor_keys):
        return "armor"
    if any(k in n for k in weapon_keys) or "weapon" in n:
        return "weapon"
    return "misc"

def normalize_item(res_type, item):
    name = item.get("name") or item.get("title") or item.get("full_name") or item.get("desc") or ""
    if not name:
        for k in ("index", "slug", "desc", "type", "document__title", "key"):
            if item.get(k):
                name = str(item[k])[:60]
                break
    slug_or_index = item.get("slug") or item.get("index") or item.get("key")
    if not slug_or_index and item.get("url"):
        m = re.search(r"/([^/]+)/?$", item["url"])
        if m:
            slug_or_index = m.group(1)

    api_url = item.get("url")
    document_slug = item.get("document__slug") or (item.get("document") or {}).get("key")
    document_title = item.get("document__title") or (item.get("document") or {}).get("display_name") or (item.get("document") or {}).get("name")

    row = {
        "type": res_type,
        "name": name.strip(),
        "slug_or_index": slug_or_index,
        "api_url": api_url,
        "document_slug": document_slug,
        "document_title": document_title,
        "raw": item,
    }

    # 给 equipment 三类加一个 subtype 字段（普通 armor/weapons 直接等于类型；magicitems 粗分）
    if res_type in {"armor", "weapons"}:
        row["subtype"] = res_type
    elif res_type == "magicitems":
        row["subtype"] = guess_magicitem_subtype(row["name"])
    return row

def ensure_db(conn):
    print("[STEP] ensure_db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog (
            type TEXT NOT NULL,
            name TEXT,
            slug_or_index TEXT,
            api_url TEXT,
            document_slug TEXT,
            document_title TEXT,
            raw_json TEXT,
            subtype TEXT,
            PRIMARY KEY (type, slug_or_index)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON catalog(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_type_doc ON catalog(type, document_slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_type_name ON catalog(type, name)")
    conn.commit()

def upsert_row(conn, row):
    conn.execute("""
        INSERT OR REPLACE INTO catalog
        (type, name, slug_or_index, api_url, document_slug, document_title, raw_json, subtype)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["type"], row["name"], row["slug_or_index"], row["api_url"],
        row["document_slug"], row["document_title"], json.dumps(row["raw"], ensure_ascii=False),
        row.get("subtype")
    ))

# ---- 文件 writer 管理：equipment 合并，其余各自一个 ----
class Writers:
    def __init__(self):
        self.files = {}
        self.equipment_fp = None

    def open_equipment(self):
        if self.equipment_fp is None:
            self.equipment_fp = EQUIPMENT_JSONL.open("w", encoding="utf-8")
        return self.equipment_fp

    def open_single(self, res_type: str):
        if res_type not in self.files:
            path = SINGLE_TYPE_JSONL_DIR / f"open5e_{res_type}.jsonl"
            self.files[res_type] = path.open("w", encoding="utf-8")
        return self.files[res_type]

    def write(self, row):
        if row["type"] in EQUIPMENT_TYPES:
            fp = self.open_equipment()
        else:
            fp = self.open_single(row["type"])
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close_all(self):
        if self.equipment_fp:
            self.equipment_fp.close()
        for fp in self.files.values():
            fp.close()

def main():
    print("[START] build Open5e catalog (equipment merged; others split)")
    endpoints = discover_resources()
    print("[OK] endpoints discovered")

    conn = sqlite3.connect(str(CATALOG_DB))
    ensure_db(conn)

    writers = Writers()
    per_file_counts = {"equipment": 0}  # 统计每个输出文件的写入条数

    for res_type, list_url in endpoints.items():
        print(f"[FETCH] {res_type} <- {list_url}")
        count = 0
        for item in iter_paginated(list_url):
            row = normalize_item(res_type, item)
            if not row["api_url"] and row["slug_or_index"]:
                collection = list_url if list_url.endswith("/") else list_url + "/"
                row["api_url"] = urljoin(collection, row["slug_or_index"] + "/")

            # 写入对应 JSONL
            writers.write(row)
            # 统计：equipment 合并计入一个桶，其余各自桶
            bucket = "equipment" if res_type in EQUIPMENT_TYPES else f"type:{res_type}"
            per_file_counts[bucket] = per_file_counts.get(bucket, 0) + 1

            # 写入 SQLite
            upsert_row(conn, row)
            count += 1

        conn.commit()
        print(f"[DONE] {res_type}: {count} items")
        time.sleep(0.2)

    writers.close_all()
    conn.close()

    print("\n[FILES]")
    for k, v in sorted(per_file_counts.items()):
        print(f"  {k:20s} -> {v:5d} rows")

    print("\n[END] OK")
    print(f" - Equipment: {EQUIPMENT_JSONL}")
    print(f" - Others:    ./open5e_<type>.jsonl")
    print(f" - SQLite:    {CATALOG_DB}")

if __name__ == "__main__":
    main()
