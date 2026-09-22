#!/usr/bin/env python3
"""Elden Ring save editor (regulation 1.17.x, e.g. 1.17.1) — stdlib only.

Edits ER0000.sl2 in place: stats, level, runes, name, DLC blessings,
held-inventory quantities (goods only). In-place fixed-size fields only
(no GaItem table rebuilds, no adds/removes), so offsets never shift.
Recalculates per-section MD5 checksums. Always backs up.

GUI:  python3 er_save_editor.py --gui
CLI:  python3 er_save_editor.py list
      python3 er_save_editor.py show 0
      python3 er_save_editor.py items 0
      python3 er_save_editor.py setqty 0 5 99
      python3 er_save_editor.py set 0 vigor=50 runes=100000 --no-autolevel
      python3 er_save_editor.py updatedb
Save auto-detected under Wine/Steam; override with --save PATH.
Item names come from items_db/ (refresh with `updatedb`).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import struct
import sys
import urllib.request

SLOT_N = 10
SLOT_DATA = 0x280000
SLOT_STEP = 0x280010
SLOT_CHK0 = 0x300
SLOT_DAT0 = 0x310
UD10CHK, UD10DAT, UD10LEN = 0x19003A0, 0x19003B0, 0x60000
CHAR_ACTIVE = 0x1901D04  # file offset, 10 bytes
SAVE_HDR = 0x1901D0E     # file offset, 10 x 0x24C profile summaries
HDR_LEN = 0x24C
NAME_LEN = 16            # max UTF-16 chars

STAT_OFFS = [("vigor", -379), ("mind", -375), ("endurance", -371),
             ("strength", -367), ("dexterity", -363), ("intelligence", -359),
             ("faith", -355), ("arcane", -351)]
OFF_LEVEL, OFF_RUNES, OFF_SOULMEM = -335, -331, -327
OFF_NAME, OFF_GENDER, OFF_CLASS = -283, -249, -248
OFF_SCADU, OFF_SHADOW = -187, -186
# 64-byte core: 4 x (0xFFFFFFFF + 12 x 0x00); MagicOffset = index of the 0x00 before it
CORE = (b"\xff\xff\xff\xff" + b"\x00" * 12) * 4

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_db")
DB_URL = "https://raw.githubusercontent.com/darthdemono/sl2-analyzer/main/db_er/{}.json"
DB_FILES = ("weapons", "armors", "talismans", "goods", "ashes")
# Item-type nibble -> db category (per ItemGib type code; cf. sl2-analyzer).
ER_CAT = {0x0: "weapons", 0x1: "armors", 0x2: "talismans", 0x4: "goods", 0x8: "ashes"}

# GaItem table: fixed entry count (slot version > 81), variable-length
# records; tail decided by the ITEM id's category nibble (validated).
GAITEM_START = 0x20
GAITEM_NEW = 0x1400
GAITEM_OLD = 0x13FE

# Held common inventory: u32 count + 2688 x 12B (handle, qty, index).
HELD_CAP = 0xA80


def find_save():
    """Newest ER0000.sl2 under known Wine/Steam locations, or None."""
    roots = [
        os.path.expanduser("~/.local/share/Steam/steamapps/compatdata/*/pfx"),
        os.path.expanduser("~/.wine"),
        os.path.expanduser("~/Games/Wine"),
        "/home/VampyrKing/Games/Wine",
    ]
    cands = []
    for r in roots:
        for base in glob.glob(r):
            cands += glob.glob(os.path.join(
                base, "drive_c/users/*/AppData/Roaming/EldenRing/*/ER0000.sl2"))
    cands = [p for p in cands if os.path.isfile(p)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def slot_offsets(n):
    return SLOT_CHK0 + n * SLOT_STEP, SLOT_DAT0 + n * SLOT_STEP


def md5(b):
    return hashlib.md5(b).digest()


class Save:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        if self.data[:4] != b"BND4":
            raise ValueError("not a PC .sl2 (missing BND4 magic; encrypted Steam-layer saves unsupported)")
        if len(self.data) < UD10DAT + UD10LEN:
            raise ValueError("file too small to be an ER0000.sl2")
        self.ud11dat, self.ud11len = UD10DAT + UD10LEN + 0x10, len(self.data) - (UD10DAT + UD10LEN + 0x10)

    def slot_data(self, n):
        _, dat = slot_offsets(n)
        return self.data[dat:dat + SLOT_DATA]

    def slot_version(self, n):
        _, dat = slot_offsets(n)
        return struct.unpack_from("<I", self.data, dat)[0]

    def checksum_ok(self, n):
        chk, dat = slot_offsets(n)
        if self.data[chk:chk + 16] == b"\x00" * 16:
            return self.slot_version(n) == 0
        return bytes(self.data[chk:chk + 16]) == md5(self.data[dat:dat + SLOT_DATA])

    def active(self):
        return [self.data[CHAR_ACTIVE + i] == 1 for i in range(SLOT_N)]

    def profile(self, n):
        off = SAVE_HDR + n * HDR_LEN
        raw = self.data[off:off + HDR_LEN]
        name = raw[:32].decode("utf-16-le", errors="replace").split("\x00")[0]
        level, secs = struct.unpack_from("<II", raw, 0x22)
        return {"name": name, "level": level, "seconds": secs}

    def magic(self, n):
        """MagicOffset of slot n; needs profile name cross-check. Raises if not found."""
        slot = self.slot_data(n)
        prof = self.profile(n)["name"]
        cands = []
        s = 0
        while True:
            i = slot.find(CORE, s)
            if i < 0:
                break
            cands.append(i - 1)  # the 0x00 byte before the core
            s = i + 1
        for m in cands:
            try:
                nm = slot[m + OFF_NAME:m + OFF_NAME + 32].decode("utf-16-le").split("\x00")[0]
            except Exception:
                continue
            if prof and nm == prof:
                return m
        if len(cands) == 1:
            return cands[0]
        # fallback: locate by profile name bytes
        if prof:
            j = slot.find(prof.encode("utf-16-le"))
            if j >= 0 and abs((j + 283) - (cands[0] if cands else j + 283)) < 0x300:
                m = j + 283
                if 0 < m < len(slot):
                    return m
        raise ValueError(f"slot {n}: stat block not found (pattern candidates: {len(cands)})")

    def stats(self, n):
        m = self.magic(n)
        slot = self.slot_data(n)
        # stats are single bytes on a 4-byte stride (u32 reads alias them)
        out = {k: slot[m + o] for k, o in STAT_OFFS}
        out.update({
            "level": struct.unpack_from("<I", slot, m + OFF_LEVEL)[0],
            "runes": struct.unpack_from("<I", slot, m + OFF_RUNES)[0],
            "soulmem": struct.unpack_from("<I", slot, m + OFF_SOULMEM)[0],
            "name": slot[m + OFF_NAME:m + OFF_NAME + 32].decode("utf-16-le", errors="replace").split("\x00")[0],
            "gender": slot[m + OFF_GENDER], "class": slot[m + OFF_CLASS],
            "scadu": slot[m + OFF_SCADU], "shadow": slot[m + OFF_SHADOW],
            "seconds": self.profile(n)["seconds"],
        })
        return out

    def apply(self, n, changes, autolevel=True):
        """changes: dict of stat/level/runes/soulmem/name/scadu/shadow. In-place, fixed sizes."""
        _, dat = slot_offsets(n)
        m = self.magic(n)
        for k, _ in STAT_OFFS:
            if k in changes:
                v = changes[k]
                if not 1 <= v <= 99:
                    raise ValueError(f"{k}={v}: must be 1..99")
                self.data[dat + m + {"vigor": -379, "mind": -375, "endurance": -371, "strength": -367,
                                     "dexterity": -363, "intelligence": -359, "faith": -355,
                                     "arcane": -351}[k]] = v
        cur = self.stats(n)
        if autolevel and any(k in changes for k, _ in STAT_OFFS) and "level" not in changes:
            changes = dict(changes, level=sum(cur[k] for k, _ in STAT_OFFS) - 79)
        if "level" in changes:
            if not 1 <= changes["level"] <= 713:
                raise ValueError("level must be 1..713")
            struct.pack_into("<I", self.data, dat + m + OFF_LEVEL, changes["level"])
            poff = SAVE_HDR + n * HDR_LEN + 0x22
            struct.pack_into("<I", self.data, poff, changes["level"])
        if "runes" in changes:
            if not 0 <= changes["runes"] <= 999999999:
                raise ValueError("runes must be 0..999999999")
            struct.pack_into("<I", self.data, dat + m + OFF_RUNES, changes["runes"])
        if "soulmem" in changes:
            if not 0 <= changes["soulmem"] <= 0xFFFFFFFF:
                raise ValueError("soulmem out of range")
            struct.pack_into("<I", self.data, dat + m + OFF_SOULMEM, changes["soulmem"])
        for k, off in (("scadu", OFF_SCADU), ("shadow", OFF_SHADOW)):
            if k in changes:
                if not 0 <= changes[k] <= 20:
                    raise ValueError(f"{k} must be 0..20")
                self.data[dat + m + off] = changes[k]
        if "name" in changes:
            enc = changes["name"].encode("utf-16-le")
            if len(enc) > 32:
                raise ValueError("name too long (max 16 chars)")
            enc = enc.ljust(32, b"\x00")
            self.data[dat + m + OFF_NAME:dat + m + OFF_NAME + 32] = enc
            poff = SAVE_HDR + n * HDR_LEN
            self.data[poff:poff + 32] = enc
            self.data[poff + 32:poff + 34] = b"\x00\x00"
        # checksums: edited slot + UD10 (profile lives there)
        chk, d = slot_offsets(n)
        self.data[chk:chk + 16] = md5(self.data[d:d + SLOT_DATA])
        self.data[UD10CHK:UD10CHK + 16] = md5(self.data[UD10DAT:UD10DAT + UD10LEN])

    def write(self):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = self.path + f".bak-{ts}"
        shutil.copy2(self.path, bak)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.data)
        os.replace(tmp, self.path)
        return bak


    def gaitems(self, n):
        """{gaitem_handle: item_id} for slot n (weapons/armor/AoW only;
        goods & talismans have no GaItem rows and live inventory-only)."""
        slot = self.slot_data(n)
        ver = self.slot_version(n)
        out = {}
        o = GAITEM_START
        for _ in range(GAITEM_NEW if ver > 81 else GAITEM_OLD):
            if o + 8 > len(slot):
                break
            h, iid = struct.unpack_from("<II", slot, o)
            o += 8
            if iid and iid != 0xFFFFFFFF:
                cat = (iid >> 28) & 0xF
                if cat == 0x0:
                    o += 13
                elif cat == 0x1:
                    o += 8
                if h:
                    out[h] = iid
        return out

    def held_base(self, n):
        """File-offset-agnostic locator for the held common inventory count
        field: scans for count + packed (handle, qty, index) records."""
        slot = self.slot_data(n)
        m = self.magic(n)
        lo, hi = m + 0x6000, min(len(slot), m + 0x12000)

        def at(c):
            cnt = struct.unpack_from("<I", slot, c)[0]
            if cnt == 0:
                return slot[c + 4:c + 4 + 48] == b"\x00" * 48
            if cnt > HELD_CAP:
                return False
            for j in range(cnt):
                h, q, ix = struct.unpack_from("<III", slot, c + 4 + j * 12)
                if h in (0, 0xFFFFFFFF) or (h >> 28) & 0xF not in (8, 9, 10, 11, 12) \
                        or q > 99999 or ix > 1000000:
                    return False
            return slot[c + 4 + cnt * 12:c + 4 + cnt * 12 + 48] == b"\x00" * 48

        for c in range(lo, hi):  # non-empty first: zero blocks match anywhere
            if struct.unpack_from("<I", slot, c)[0] >= 1 and at(c):
                return c
        for c in range(lo, hi):
            if struct.unpack_from("<I", slot, c)[0] == 0 and at(c):
                return c
        raise ValueError(f"slot {n}: held inventory not found")

    def items(self, n):
        """Held common inventory rows: (row, handle, item_id, qty, index)."""
        slot = self.slot_data(n)
        base = self.held_base(n)
        gmap = self.gaitems(n)
        cnt = struct.unpack_from("<I", slot, base)[0]
        rows = []
        for j in range(cnt):
            h, q, ix = struct.unpack_from("<III", slot, base + 4 + j * 12)
            iid = gmap.get(h)
            if iid is None:
                nib = (h >> 28) & 0xF
                if nib == 0xB:
                    iid = 0x40000000 | (h & 0xFFFFFF)
                elif nib == 0xA:
                    iid = 0x20000000 | (h & 0xFFFFFF)
            rows.append((j, h, iid, q, ix))
        return rows

    def set_qty(self, n, row, qty):
        """Set held-inventory goods quantity in place (12B record untouched
        otherwise). Goods only; equipment rows are not stackable."""
        if not 1 <= qty <= 999:
            raise ValueError("qty must be 1..999")
        slot = self.slot_data(n)
        base = self.held_base(n)
        cnt = struct.unpack_from("<I", slot, base)[0]
        if not 0 <= row < cnt:
            raise ValueError(f"row {row} out of range (0..{cnt - 1})")
        _, dat = slot_offsets(n)
        h, q, ix = struct.unpack_from("<III", slot, base + 4 + row * 12)
        if ((h >> 28) & 0xF) != 0xB:
            raise ValueError(f"row {row} is not a stackable goods item (qty edit refused)")
        struct.pack_into("<I", self.data, dat + base + 4 + row * 12 + 4, qty)
        chk, d = slot_offsets(n)
        self.data[chk:chk + 16] = md5(self.data[d:d + SLOT_DATA])


def game_running():
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-fi", "[e]ldenring"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out)
    except Exception:
        return False


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


def run_gui(path):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    sv = Save(path)
    db = load_db()

    root = tk.Tk()
    root.title("Elden Ring Save Editor (1.17.x)")

    # ---- file row: open any save, save modifications (backup + MD5) ----
    frow = ttk.Frame(root, padding=8)
    frow.grid(row=0, column=0, sticky="ew")
    ttk.Label(frow, text="File:").grid(row=0, column=0, sticky="w")
    pathvar = tk.StringVar(value=path)
    ttk.Label(frow, textvariable=pathvar, wraplength=420).grid(row=0, column=1, sticky="w")
    ttk.Label(frow, text="Slot:").grid(row=1, column=0, sticky="w")
    slotbox = ttk.Combobox(frow, state="readonly", width=30)
    slotbox.grid(row=1, column=1, sticky="w")

    nb = ttk.Notebook(root)
    nb.grid(row=1, column=0, sticky="nsew")
    tab_char = ttk.Frame(nb, padding=8)
    tab_stats = ttk.Frame(nb, padding=8)
    tab_items = ttk.Frame(nb, padding=8)
    nb.add(tab_char, text="Character")
    nb.add(tab_stats, text="Statistics")
    nb.add(tab_items, text="Items")

    # ---- Character tab ----
    cvars = {}
    fields = [k for k, _ in STAT_OFFS] + ["level", "runes", "soulmem", "scadu", "shadow"]
    for i, k in enumerate(fields):
        ttk.Label(tab_char, text=k).grid(row=i, column=0, sticky="w")
        v = tk.StringVar()
        cvars[k] = v
        ttk.Entry(tab_char, textvariable=v, width=14).grid(row=i, column=1)
    ttk.Label(tab_char, text="name").grid(row=len(fields), column=0, sticky="w")
    cvars["name"] = tk.StringVar()
    ttk.Entry(tab_char, textvariable=cvars["name"], width=20).grid(row=len(fields), column=1)
    auto = tk.BooleanVar(value=True)
    ttk.Checkbutton(tab_char, text="auto level = sum(stats)-79", variable=auto).grid(
        row=len(fields) + 1, column=0, columnspan=2, sticky="w")

    # ---- Statistics tab (read-only: every stat the editor knows) ----
    statstext = tk.Text(tab_stats, width=52, height=24, state="disabled")
    statstext.grid(row=0, column=0)

    # ---- Items tab (held inventory; qty edit for goods only) ----
    itree = ttk.Treeview(tab_items, columns=("qty", "cat", "name"), show="headings", height=16)
    itree.heading("qty", text="qty")
    itree.heading("cat", text="type")
    itree.heading("name", text="item")
    itree.column("qty", width=50)
    itree.column("cat", width=80)
    itree.column("name", width=280)
    itree.grid(row=0, column=0, columnspan=3)
    ttk.Label(tab_items, text="search").grid(row=1, column=0, sticky="w")
    qsearch = tk.StringVar()
    ttk.Entry(tab_items, textvariable=qsearch, width=20).grid(row=1, column=1, sticky="w")
    ttk.Label(tab_items, text="qty (1..999)").grid(row=2, column=0, sticky="w")
    qqty = tk.StringVar()
    ttk.Entry(tab_items, textvariable=qqty, width=10).grid(row=2, column=1, sticky="w")

    status = tk.StringVar()
    ttk.Label(root, textvariable=status, wraplength=440).grid(row=3, column=0, sticky="w")

    def slot_names():
        names = []
        for n in range(SLOT_N):
            try:
                p = sv.profile(n)
                names.append(f"{n}: {p['name'] or '(empty)'} lv{p['level']}")
            except Exception:
                names.append(f"{n}: ?")
        slotbox["values"] = names

    def cur():
        return slotbox.current()

    def refresh_stats(n):
        try:
            st = sv.stats(n)
        except Exception as e:
            body = f"slot {n}: {e}"
        else:
            s = sum(st[k] for k, _ in STAT_OFFS) - 79
            lines = [f"slot {n}  {st['name']!r}  md5={'ok' if sv.checksum_ok(n) else 'BAD'}",
                     f"level {st['level']} (stats sum-79 = {s})",
                     f"runes {st['runes']}   soul memory {st['soulmem']}",
                     f"playtime {st['seconds'] // 3600}h{(st['seconds'] % 3600) // 60}m",
                     f"gender {st['gender']}   class {st['class']}",
                     f"scadutree {st['scadu']}   shadow realm {st['shadow']}",
                     ""] + [f"{k:12s} {st[k]}" for k, _ in STAT_OFFS]
            try:
                rows = sv.items(n)
                from collections import Counter
                cats = Counter(resolve_item(i, db)[1] or "?" for _, _, i, _, _ in rows)
                lines += ["", f"held items: {len(rows)} (" +
                          ", ".join(f"{k} {v}" for k, v in sorted(cats.items())) + ")"]
            except Exception as e:
                lines += ["", f"held items: {e}"]
            body = "\n".join(lines)
        statstext["state"] = "normal"
        statstext.delete("1.0", "end")
        statstext.insert("1.0", body)
        statstext["state"] = "disabled"

    def refresh_items(n):
        itree.delete(*itree.get_children())
        try:
            rows = sv.items(n)
        except Exception as e:
            status.set(f"slot {n} items: {e}")
            return
        q = qsearch.get().lower()
        for row, h, iid, qty, ix in rows:
            nm, cat = resolve_item(iid, db)
            label = nm or (f"0x{iid:08X}" if iid else "?")
            if q and q not in label.lower():
                continue
            itree.insert("", "end", iid=str(row), values=(qty, cat or "?", label))

    def load(*_):
        n = cur()
        slot_names()
        try:
            st = sv.stats(n)
        except Exception as e:
            status.set(f"slot {n}: {e}")
            return
        for k in fields:
            cvars[k].set(str(st[k]))
        cvars["name"].set(st["name"])
        s = sum(st[k] for k, _ in STAT_OFFS) - 79
        note = "" if s == st["level"] else f" (note: sum-79={s})"
        status.set(f"slot {n} {st['name']!r} level {st['level']}{note} md5={'ok' if sv.checksum_ok(n) else 'BAD'}")
        refresh_stats(n)
        refresh_items(n)

    def do_open():
        p = filedialog.askopenfilename(
            title="Open ER0000.sl2", initialdir=os.path.dirname(pathvar.get()) or ".",
            filetypes=[("Elden Ring saves", "ER0000.sl2"), ("All files", "*")])
        if not p:
            return
        try:
            nonlocal_sv = Save(p)
            db_new = load_db()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        # swap file contents into the same object so tab closures keep working
        sv.__dict__.update(nonlocal_sv.__dict__)
        db.clear()
        db.update(db_new)
        pathvar.set(p)
        slotbox.current(0)
        load()

    def do_save():
        n = cur()
        ch = {}
        for k in fields:
            t = cvars[k].get().strip()
            if t:
                ch[k] = int(t)
        if cvars["name"].get().strip():
            ch["name"] = cvars["name"].get().strip()
        if game_running():
            if not messagebox.askyesno("Game running",
                                       "Elden Ring seems to be running. Saving now can corrupt the file. Save anyway?"):
                return
        try:
            sv.apply(n, ch, autolevel=auto.get())
            bak = sv.write()
            status.set(f"saved slot {n} to {pathvar.get()}. backup: {bak}")
            load()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def do_setqty():
        sel = itree.selection()
        if not sel:
            return
        n = cur()
        try:
            sv.set_qty(n, int(sel[0]), int(qqty.get()))
            bak = sv.write()
            status.set(f"saved. backup: {bak}")
            refresh_items(n)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def do_updatedb():
        try:
            done = update_db()
            db.clear()
            db.update(load_db())
            status.set(f"item db updated: {', '.join(done)}")
            refresh_items(cur())
        except Exception as e:
            messagebox.showerror("Error", str(e))

    slotbox.bind("<<ComboboxSelected>>", load)
    qsearch.trace_add("write", lambda *_: refresh_items(cur()))
    ttk.Button(frow, text="Open", command=do_open).grid(row=0, column=2, padx=4)
    ttk.Button(frow, text="Save (backup + MD5)", command=do_save).grid(row=1, column=2, padx=4)
    ttk.Button(tab_char, text="Reload", command=load).grid(row=len(fields) + 2, column=0, sticky="w", pady=4)
    ttk.Button(tab_items, text="Set qty", command=do_setqty).grid(row=2, column=2, padx=4)
    ttk.Button(tab_items, text="Update item DB", command=do_updatedb).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
    slot_names()
    slotbox.current(0)
    load()
    root.mainloop()


def main():
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
    p = sub.add_parser("updatedb", help="refresh items_db/ from GitHub or DIR")
    p.add_argument("dir", nargs="?", default=None)
    a = ap.parse_args()
    path = a.save or find_save()
    if not path:
        print("save not found; pass --save PATH", file=sys.stderr)
        return 2
    if a.gui or a.cmd is None:
        run_gui(path)
        return 0
    sv = Save(path)
    if a.cmd == "list":
        cmd_list(sv)
    elif a.cmd == "show":
        cmd_show(sv, a.slot)
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
    elif a.cmd == "items":
        db = load_db()
        try:
            rows = sv.items(a.slot)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for row, h, iid, q, ix in rows:
            nm, cat = resolve_item(iid, db)
            label = nm or (f"unknown 0x{iid:08X}" if iid else "unknown")
            if a.search and a.search.lower() not in label.lower():
                continue
            print(f"{row:4d} x{q:<4d} [{cat or '?':9s}] {label} (handle 0x{h:08X})")
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
    elif a.cmd == "updatedb":
        try:
            done = update_db(src=a.dir)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"updated {DB_DIR}: {', '.join(done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
