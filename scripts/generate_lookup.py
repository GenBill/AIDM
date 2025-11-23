import json
from pathlib import Path
from collections import defaultdict

def build_lookup_tables(data_dir="."):
    data_dir = Path(data_dir)
    for file in data_dir.glob("open5e_*.jsonl"):
        if file.name.endswith("_lookupTable.json"):
            continue
        lookup = defaultdict(list)
        with file.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    name = obj.get("name")
                    slug = obj.get("slug_or_index")
                    if name and slug:
                        if slug not in lookup[name]:
                            lookup[name].append(slug)
                except Exception:
                    continue

        out_path = file.with_name(file.stem + "_lookupTable.json")
        with out_path.open("w", encoding="utf-8") as out:
            json.dump(lookup, out, ensure_ascii=False, indent=2)
        print(f"[OK] {out_path} ({len(lookup)} entries)")

if __name__ == "__main__":
    build_lookup_tables(".")
