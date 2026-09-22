"""Command line: list / show / set / items / setqty / additem / updatedb / --gui."""
import argparse
import os
import sys

try:
    from .itemdb import DB_DIR, find_goods, load_db, resolve_item, update_db
    from .save import SLOT_N, STAT_OFFS, Save, find_save, game_running
except ImportError:  # direct run: python3 src/er_save_editor/cli.py ...
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from er_save_editor.itemdb import DB_DIR, find_goods, load_db, resolve_item, update_db
    from er_save_editor.save import SLOT_N, STAT_OFFS, Save, find_save, game_running


def cmd_list(sv):
    act = sv.active()
    for n in range(SLOT_N):
        ver = sv.slot_version(n)
        if ver == 0:
            print(f"slot {n}: empty")
            continue
        p = sv.profile(n)
        cok = "md5-ok" if sv.checksum_ok(n) else "MD5-BAD"
        print(f"slot {n}: {'active' if act[n] else 'inactive'} ver={ver} "
              f"name={p['name']!r} level={p['level']} played={p['seconds'] // 3600}h{(p['seconds'] % 3600) // 60}m {cok}")


def cmd_show(sv, n):
    st = sv.stats(n)
    print(f"slot {n} ver={sv.slot_version(n)} md5={'ok' if sv.checksum_ok(n) else 'BAD'}")
    for k, _ in STAT_OFFS:
        print(f"  {k:12s} {st[k]}")
    for k in ("level", "runes", "soulmem", "name", "gender", "class",
              "scadu", "shadow", "seconds"):
        print(f"  {k:12s} {st[k]}")
    s = sum(st[k] for k, _ in STAT_OFFS) - 79
    if s != st["level"]:
        print(f"  note: stats sum-79={s} != stored level {st['level']}")


STAT_KEYS = {k for k, _ in STAT_OFFS} | {"level", "runes", "soulmem", "scadu", "shadow"}


def parse_set_args(items):
    changes = {}
    for it in items:
        if "=" not in it:
            raise ValueError(f"bad assignment {it!r}, use key=value")
        k, v = it.split("=", 1)
        k = k.lower()
        if k == "name":
            changes["name"] = v
        elif k in STAT_KEYS:
            changes[k] = int(v)
        elif k in ("end", "str", "dex", "int", "str_", "arc"):
            raise ValueError(f"ambiguous {k!r}; use full names: endurance strength dexterity intelligence arcane")
        else:
            raise ValueError(f"unknown key {k!r} (stats, level, runes, soulmem, scadu, shadow, name)")
    return changes


def cmd_items(sv, n, search=None):
    db = load_db()
    for row, h, iid, q, ix in sv.items(n):
        nm, cat = resolve_item(iid, db)
        label = nm or (f"unknown 0x{iid:08X}" if iid else "unknown")
        if search and search.lower() not in label.lower():
            continue
        print(f"{row:4d} x{q:<4d} [{cat or '?':9s}] {label} (handle 0x{h:08X})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Elden Ring ER0000.sl2 editor for 1.17.x")
    ap.add_argument("--save", default=None)
    ap.add_argument("--gui", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("slot", type=int)
    p = sub.add_parser("set")
    p.add_argument("slot", type=int)
    p.add_argument("items", nargs="+", help="key=value (vigor mind endurance strength dexterity intelligence faith arcane level runes soulmem scadu shadow name)")
    p.add_argument("--no-autolevel", action="store_true")
    p = sub.add_parser("items", help="list held inventory")
    p.add_argument("slot", type=int)
    p.add_argument("--search", default=None)
    p = sub.add_parser("setqty", help="set held goods quantity (1..999)")
    p.add_argument("slot", type=int)
    p.add_argument("row", type=int)
    p.add_argument("qty", type=int)
    p = sub.add_parser("additem", help="add goods 1..9999 (fills stacks, splits over 999)")
    p.add_argument("slot", type=int)
    p.add_argument("query", help="hex id (e.g. 400006c2) or unique name part")
    p.add_argument("qty", type=int)
    p = sub.add_parser("updatedb", help="refresh items_db/ from GitHub or DIR")
    p.add_argument("dir", nargs="?", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "updatedb":
        try:
            done = update_db(src=a.dir)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"updated {DB_DIR}: {', '.join(done)}")
        return 0
    path = a.save or find_save()
    if not path:
        print("save not found; pass --save PATH", file=sys.stderr)
        return 2
    if a.gui or a.cmd is None:
        from .gui import run_gui
        run_gui(path)
        return 0
    sv = Save(path)
    if a.cmd == "list":
        cmd_list(sv)
    elif a.cmd == "show":
        cmd_show(sv, a.slot)
    elif a.cmd == "items":
        try:
            cmd_items(sv, a.slot, a.search)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    elif a.cmd == "setqty":
        if game_running():
            print("warning: Elden Ring appears to be running — close it first to avoid corruption",
                  file=sys.stderr)
        try:
            sv.set_qty(a.slot, a.row, a.qty)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        bak = sv.write()
        print(f"wrote {path} (backup {bak})")
    elif a.cmd == "additem":
        if game_running():
            print("warning: Elden Ring appears to be running — close it first to avoid corruption",
                  file=sys.stderr)
        try:
            iid = find_goods(a.query, load_db())
            action, row, qty = sv.add_item(a.slot, iid, a.qty)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        bak = sv.write()
        print(f"{action} row {row} x{qty} (backup {bak})")
    elif a.cmd == "set":
        if game_running():
            print("warning: Elden Ring appears to be running — close it first to avoid corruption",
                  file=sys.stderr)
        try:
            ch = parse_set_args(a.items)
            sv.apply(a.slot, ch, autolevel=not a.no_autolevel)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        bak = sv.write()
        print(f"wrote {path} (backup {bak})")
        cmd_show(Save(path), a.slot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
