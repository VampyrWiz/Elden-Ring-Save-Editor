#!/usr/bin/env python3
"""Show Larval Tear and Stonesword Key totals in player inventory."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from er_save_editor.save import SLOT_N, Save, find_save

TARGETS = {0x40001FF9: "Larval Tear", 0x401EA3E1: "Larval Tear", 0x40001F40: "Stonesword Key"}


def counts(sv, n):
    """{name: total qty} for slot n; empty slot -> {}."""
    if sv.slot_version(n) == 0:
        return {}
    totals = {name: 0 for name in set(TARGETS.values())}
    for _, _, iid, qty, _ in sv.items(n):
        if iid in TARGETS:
            totals[TARGETS[iid]] += qty
    return totals


def main(argv=None):
    ap = argparse.ArgumentParser(description="Show Larval Tear / Stonesword Key inventory")
    ap.add_argument("--save", default=None)
    ap.add_argument("slot", nargs="?", type=int, default=None, help="slot 0..9 (default: all)")
    a = ap.parse_args(argv)
    path = a.save or find_save()
    if not path:
        print("save not found; pass --save PATH", file=sys.stderr)
        return 2
    sv = Save(path)
    slots = [a.slot] if a.slot is not None else range(SLOT_N)
    for n in slots:
        if not 0 <= n < SLOT_N:
            print(f"slot {n}: out of range 0..{SLOT_N - 1}", file=sys.stderr)
            return 2
        try:
            name = sv.profile(n)["name"] if sv.slot_version(n) else "(empty)"
            t = counts(sv, n)
        except Exception as e:
            print(f"slot {n}: {e}", file=sys.stderr)
            continue
        if sv.slot_version(n) == 0:
            print(f"slot {n}: empty")
        else:
            print(f"slot {n} {name!r}: " + ", ".join(f"{k} x{v}" for k, v in sorted(t.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
