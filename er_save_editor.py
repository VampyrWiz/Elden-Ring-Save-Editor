#!/usr/bin/env python3
"""Elden Ring save editor (regulation 1.17.x, e.g. 1.17.1) — stdlib only.

Edits ER0000.sl2 in place: stats, level, runes, name, DLC blessings.
In-place fixed-size fields only (no GaItem/inventory edits), so offsets
never shift. Recalculates per-section MD5 checksums. Always backs up.

GUI:  python3 er_save_editor.py --gui
CLI:  python3 er_save_editor.py list
      python3 er_save_editor.py show 0
      python3 er_save_editor.py set 0 vigor=50 runes=100000 --no-autolevel
      python3 er_save_editor.py set 0 level=150 name=Abisa
Save auto-detected under Wine/Steam; override with --save PATH.
"""
import argparse
import datetime
import hashlib
import os
import shutil
import struct
import sys

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

CANDIDATE_SAVES = [
    "/home/VampyrKing/Games/Wine/drive_c/users/steamuser/AppData/Roaming/EldenRing/76561197960271872/ER0000.sl2",
    "/home/VampyrKing/.local/share/Steam/steamapps/compatdata/3719082347/pfx/drive_c/users/steamuser/AppData/Roaming/EldenRing/76561197960271872/ER0000.sl2",
]


def find_save():
    for p in CANDIDATE_SAVES:
        if os.path.isfile(p):
            return p
    return None


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


def run_gui(path):
    import tkinter as tk
    from tkinter import messagebox, ttk
    sv = Save(path)

    root = tk.Tk()
    root.title("Elden Ring Save Editor (1.17.x)")
    vars_ = {}

    top = ttk.Frame(root, padding=8)
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, text="File:").grid(row=0, column=0, sticky="w")
    ttk.Label(top, text=path, wraplength=420).grid(row=0, column=1, sticky="w")
    ttk.Label(top, text="Slot:").grid(row=1, column=0, sticky="w")
    slotvar = tk.IntVar(value=0)
    names = []
    for n in range(SLOT_N):
        try:
            p = sv.profile(n)
            names.append(f"{n}: {p['name'] or '(empty)'} lv{p['level']}")
        except Exception:
            names.append(f"{n}: ?")
    slotbox = ttk.Combobox(top, values=names, state="readonly", width=30)
    slotbox.current(0)
    slotbox.grid(row=1, column=1, sticky="w")

    grid = ttk.Frame(root, padding=8)
    grid.grid(row=1, column=0)
    fields = [k for k, _ in STAT_OFFS] + ["level", "runes", "soulmem", "scadu", "shadow"]
    for i, k in enumerate(fields):
        ttk.Label(grid, text=k).grid(row=i, column=0, sticky="w")
        v = tk.StringVar()
        vars_[k] = v
        ttk.Entry(grid, textvariable=v, width=14).grid(row=i, column=1)
    ttk.Label(grid, text="name").grid(row=len(fields), column=0, sticky="w")
    vars_["name"] = tk.StringVar()
    ttk.Entry(grid, textvariable=vars_["name"], width=20).grid(row=len(fields), column=1)
    auto = tk.BooleanVar(value=True)
    ttk.Checkbutton(grid, text="auto level = sum(stats)-79", variable=auto).grid(
        row=len(fields) + 1, column=0, columnspan=2, sticky="w")
    status = tk.StringVar()
    ttk.Label(root, textvariable=status, wraplength=440).grid(row=3, column=0, sticky="w")

    def load(*_):
        n = slotbox.current()
        try:
            st = sv.stats(n)
        except Exception as e:
            status.set(f"slot {n}: {e}")
            return
        for k in fields:
            vars_[k].set(str(st[k]))
        vars_["name"].set(st["name"])
        s = sum(st[k] for k, _ in STAT_OFFS) - 79
        note = "" if s == st["level"] else f" (note: sum-79={s})"
        status.set(f"slot {n} {st['name']!r} level {st['level']}{note} md5={'ok' if sv.checksum_ok(n) else 'BAD'}")

    def save():
        n = slotbox.current()
        ch = {}
        for k in fields:
            t = vars_[k].get().strip()
            if t:
                ch[k] = int(t)
        if vars_["name"].get().strip():
            ch["name"] = vars_["name"].get().strip()
        if game_running():
            if not messagebox.askyesno("Game running",
                                       "Elden Ring seems to be running. Saving now can corrupt the file. Save anyway?"):
                return
        try:
            sv.apply(n, ch, autolevel=auto.get())
            bak = sv.write()
            status.set(f"saved slot {n}. backup: {bak}")
            load()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    slotbox.bind("<<ComboboxSelected>>", load)
    ttk.Button(root, text="Load", command=load).grid(row=2, column=0, sticky="w", padx=8)
    ttk.Button(root, text="Save (backup + MD5)", command=save).grid(row=2, column=0, sticky="e", padx=8)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
