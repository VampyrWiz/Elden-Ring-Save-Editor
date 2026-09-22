"""Stdlib-only unit tests (no save file needed). Run: python3 -m unittest discover -s tests"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from er_save_editor.cli import parse_set_args
from er_save_editor.itemdb import resolve_item
from er_save_editor.items import (
    find_held_base,
    handle_item_id,
    pack_qty,
    read_items,
    walk_gaitems,
)


def make_slot():
    # 64 KiB synthetic slot: GaItem table, magic window, held inventory.
    slot = bytearray(0x10000)
    m = 0x1000
    o = 0x20
    # weapon record: handle + id(0x0 nibble) + 13B tail
    struct.pack_into("<II", slot, o, 0x80800001, 0x003D0900)
    slot[o + 8:o + 21] = b"\xff" * 13
    o += 21
    # armor record: handle + id(0x1 nibble) + 8B tail
    struct.pack_into("<II", slot, o, 0x90800002, 0x10001234)
    slot[o + 8:o + 16] = b"\xff" * 8
    o += 16
    # rest of the 5120-entry table stays empty (zeros)
    assert o < m
    # held inventory past the walk's reach (5120 x 8B min) and inside the
    # find_held_base window [m + 0x6000, m + 0x12000)
    base = m + 0xC000
    struct.pack_into("<I", slot, base, 3)
    struct.pack_into("<III", slot, base + 4, 0xB0000005, 7, 12)
    struct.pack_into("<III", slot, base + 16, 0x80800001, 1, 433)
    struct.pack_into("<III", slot, base + 28, 0x90800002, 1, 434)
    return slot, m, base


class TestItems(unittest.TestCase):
    def test_gaitem_walk(self):
        slot, _, _ = make_slot()
        g = walk_gaitems(slot, 261)
        self.assertEqual(g, {0x80800001: 0x003D0900, 0x90800002: 0x10001234})

    def test_find_held_base(self):
        slot, m, base = make_slot()
        self.assertEqual(find_held_base(slot, m), base)

    def test_read_items(self):
        slot, m, base = make_slot()
        g = walk_gaitems(slot, 261)
        rows = read_items(slot, base, g)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], (0, 0xB0000005, 0x40000005, 7, 12))
        self.assertEqual(rows[1][2], 0x003D0900)
        self.assertEqual(handle_item_id(0xA0000007, {}), 0x20000007)

    def test_pack_qty(self):
        slot, m, base = make_slot()
        data = bytearray(0x20000)
        dat = 0x310
        data[dat:dat + len(slot)] = slot
        h, old = pack_qty(data, dat, base, 0, 42)
        self.assertEqual((h, old), (0xB0000005, 7))
        self.assertEqual(struct.unpack_from("<I", data, dat + base + 8)[0], 42)
        with self.assertRaises(ValueError):  # weapon row: not stackable
            pack_qty(data, dat, base, 1, 5)
        with self.assertRaises(ValueError):  # out of range
            pack_qty(data, dat, base, 0, 1000)


class TestResolve(unittest.TestCase):
    def test_direct_and_reinforced(self):
        db = {"weapons": {0x003D0900: "Moonveil"}, "goods": {0x40000064: "Finger"}}
        self.assertEqual(resolve_item(0x003D0900, db), ("Moonveil", "weapons"))
        self.assertEqual(resolve_item(0x003D0908, db), ("Moonveil +8", "weapons"))
        self.assertEqual(resolve_item(0x40000064, db), ("Finger", "goods"))
        self.assertEqual(resolve_item(0x50000001, db), (None, None))
        self.assertEqual(resolve_item(None, db), (None, None))


class TestParseSet(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_set_args(["vigor=50", "name=Abisa"]),
                         {"vigor": 50, "name": "Abisa"})

    def test_bad(self):
        with self.assertRaises(ValueError):
            parse_set_args(["str=10"])
        with self.assertRaises(ValueError):
            parse_set_args(["nope"])


if __name__ == "__main__":
    unittest.main()
