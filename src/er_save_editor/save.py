"""Save container: BND4 parsing, slots, stats, checksums."""
import datetime
import glob
import hashlib
import os
import shutil
import struct

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


def find_save():
    """Newest ER0000.sl2 under known Wine/Steam locations, or None."""
    roots = [
        os.path.expanduser("~/.local/share/Steam/steamapps/compatdata/*/pfx"),
        os.path.expanduser("~/.wine"),
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


def game_running():
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-fi", "[e]ldenring"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out)
    except Exception:
        return False


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

    def gaitems(self, n):
        """{gaitem_handle: item_id} for slot n (weapons/armor/AoW only;
        goods & talismans have no GaItem rows and live inventory-only)."""
        from .items import walk_gaitems
        return walk_gaitems(self.slot_data(n), self.slot_version(n))

    def held_base(self, n):
        """Slot offset of the held common inventory count field."""
        from .items import find_held_base
        return find_held_base(self.slot_data(n), self.magic(n))

    def items(self, n):
        """Held common inventory rows: (row, handle, item_id, qty, index)."""
        from .items import read_items
        slot = self.slot_data(n)
        base = self.held_base(n)
        return read_items(slot, base, self.gaitems(n))

    def set_qty(self, n, row, qty):
        """Set held-inventory goods quantity in place (12B record untouched
        otherwise). Goods only; equipment rows are not stackable."""
        from .items import pack_qty
        _, dat = slot_offsets(n)
        base = self.held_base(n)
        pack_qty(self.data, dat, base, row, qty)
        chk, d = slot_offsets(n)
        self.data[chk:chk + 16] = md5(self.data[d:d + SLOT_DATA])

    def add_item(self, n, iid, qty):
        """Add goods to held inventory: fill existing same-name stacks to
        999 first, then append new rows. Returns (action, row, total held).
        Weapons/armor/AoW refused: variable-length GaItem rows are unsafe."""
        from .items import goods_handle, pack_add, pack_qty, read_items
        if not 1 <= qty <= 9999:
            raise ValueError("qty must be 1..9999")
        _, dat = slot_offsets(n)
        handle = goods_handle(iid)
        base = self.held_base(n)
        first, touched_new = None, False

        def rows():
            slot = self.slot_data(n)
            return read_items(slot, base, self.gaitems(n))

        for row, h, _i, q, _ix in rows():
            if h == handle and qty > 0 and q < 999:
                take = min(999 - q, qty)
                pack_qty(self.data, dat, base, row, q + take)
                qty -= take
                if first is None:
                    first = row
        while qty > 0:
            index = max((_ix for _, _, _, _, _ix in rows()), default=432) + 1
            chunk = min(999, qty)
            row = pack_add(self.data, dat, base, handle, chunk, index)
            qty -= chunk
            if first is None:
                first = row
            touched_new = True
        chk, d = slot_offsets(n)
        self.data[chk:chk + 16] = md5(self.data[d:d + SLOT_DATA])
        total = sum(q for _, h, _, q, _ in rows() if h == handle)
        return ("added" if touched_new else "merged"), first, total

    def write(self):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = self.path + f".bak-{ts}"
        shutil.copy2(self.path, bak)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.data)
        os.replace(tmp, self.path)
        return bak
