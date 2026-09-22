"""Item name database (items_db/*.json) with a one-command updater."""
import json
import os
import urllib.request

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "items_db")
DB_URL = "https://raw.githubusercontent.com/darthdemono/sl2-analyzer/main/db_er/{}.json"
DB_FILES = ("weapons", "armors", "talismans", "goods", "ashes")
# Item-type nibble -> db category (per ItemGib type code; cf. sl2-analyzer).
ER_CAT = {0x0: "weapons", 0x1: "armors", 0x2: "talismans", 0x4: "goods", 0x8: "ashes"}


def load_db(path=DB_DIR):
    """{category: {item_id: name}}; missing files are skipped."""
    db = {}
    for cat in DB_FILES:
        try:
            with open(os.path.join(path, cat + ".json"), encoding="utf-8") as f:
                db[cat] = {int(k, 16): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            continue
    return db


def resolve_item(iid, db):
    """(name, category) for an item id; weapon +N suffix decoded."""
    if iid is None:
        return None, None
    cat = ER_CAT.get((iid >> 28) & 0xF)
    if cat is None:
        return None, None
    table = db.get(cat, {})
    name = table.get(iid)
    if name is not None or cat != "weapons":
        return name, cat
    level = iid % 100
    if level > 25:
        level = 0
    for step in (100, 10000):
        name = table.get(iid - iid % step)
        if name is not None:
            return (f"{name} +{level}" if level else name), cat
    return None, cat


def update_db(dest=DB_DIR, src=None):
    """Refresh items_db/ from a local sl2-analyzer checkout (src dir holding
    *.json) or from GitHub raw. Returns list of files written."""
    os.makedirs(dest, exist_ok=True)
    done = []
    for cat in DB_FILES + ("classes",):
        if src:
            with open(os.path.join(src, cat + ".json"), encoding="utf-8") as f:
                data = f.read()
        else:
            with urllib.request.urlopen(DB_URL.format(cat), timeout=30) as r:
                data = r.read().decode("utf-8")
        json.loads(data)  # validate before writing
        with open(os.path.join(dest, cat + ".json"), "w", encoding="utf-8") as f:
            f.write(data)
        done.append(cat + ".json")
    return done
