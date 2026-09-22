"""Held-inventory parsing and quantity edits (pure functions over slot bytes).

Validated against a real 1.17 save (slot ver 261): GaItem walk ends at
PlayerGameData, held common inventory is u32 count + packed 12B records.
Goods/talismans have no GaItem rows (inventory-only handles).
"""
import struct

# GaItem table: fixed entry count (slot version > 81), variable-length
# records; tail decided by the ITEM id's category nibble (validated).
GAITEM_START = 0x20
GAITEM_NEW = 0x1400
GAITEM_OLD = 0x13FE

# Held common inventory: u32 count + 2688 x 12B (handle, qty, index).
HELD_CAP = 0xA80

# Upper nibbles of gaitem handles (weapons/armor/talismans/goods/AoW).
HANDLE_NIBS = (8, 9, 10, 11, 12)


def walk_gaitems(slot, ver):
    """{gaitem_handle: item_id} (weapons/armor/AoW only)."""
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


def rec_valid(h, q, ix):
    if (h, q, ix) == (0, 0, 0) or (h, q, ix) == (0xFFFFFFFF,) * 3:
        return True
    return (h >> 28) & 0xF in HANDLE_NIBS and q <= 99999 and ix <= 1000000


def _at(slot, c):
    cnt = struct.unpack_from("<I", slot, c)[0]
    if cnt == 0:
        return slot[c + 4:c + 4 + 48] == b"\x00" * 48
    if cnt > HELD_CAP:
        return False
    for j in range(cnt):
        h, q, ix = struct.unpack_from("<III", slot, c + 4 + j * 12)
        if h in (0, 0xFFFFFFFF) or not rec_valid(h, q, ix):
            return False
    return slot[c + 4 + cnt * 12:c + 4 + cnt * 12 + 48] == b"\x00" * 48


def find_held_base(slot, m):
    """Offset (in slot) of the held common inventory count field."""
    lo, hi = m + 0x6000, min(len(slot), m + 0x12000)
    for c in range(lo, hi):  # non-empty first: zero blocks match anywhere
        if struct.unpack_from("<I", slot, c)[0] >= 1 and _at(slot, c):
            return c
    for c in range(lo, hi):
        if struct.unpack_from("<I", slot, c)[0] == 0 and _at(slot, c):
            return c
    raise ValueError("held inventory not found")


def handle_item_id(h, gmap):
    """Item id for an inventory handle (GaItem row or inventory-only goods/talisman)."""
    iid = gmap.get(h)
    if iid is None:
        nib = (h >> 28) & 0xF
        if nib == 0xB:
            iid = 0x40000000 | (h & 0xFFFFFF)
        elif nib == 0xA:
            iid = 0x20000000 | (h & 0xFFFFFF)
    return iid


def read_items(slot, base, gmap):
    """[(row, handle, item_id, qty, index)] for the held common inventory."""
    cnt = struct.unpack_from("<I", slot, base)[0]
    rows = []
    for j in range(cnt):
        h, q, ix = struct.unpack_from("<III", slot, base + 4 + j * 12)
        rows.append((j, h, handle_item_id(h, gmap), q, ix))
    return rows


def pack_qty(data, dat, base, row, qty):
    """In-place qty write into file bytes; goods rows only. Returns (handle, old)."""
    if not 1 <= qty <= 999:
        raise ValueError("qty must be 1..999")
    cnt = struct.unpack_from("<I", data, dat + base)[0]
    if not 0 <= row < cnt:
        raise ValueError(f"row {row} out of range (0..{cnt - 1})")
    h, old, _ix = struct.unpack_from("<III", data, dat + base + 4 + row * 12)
    if ((h >> 28) & 0xF) != 0xB:
        raise ValueError(f"row {row} is not a stackable goods item (qty edit refused)")
    struct.pack_into("<I", data, dat + base + 4 + row * 12 + 4, qty)
    return h, old
