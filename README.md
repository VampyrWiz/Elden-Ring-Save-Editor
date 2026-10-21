# Elden Ring Save Editor (regulation 1.17.x)

A user-friendly save editor for Elden Ring on PC. Edit character stats,
runes, inventory quantities, and add consumables with automatic backups
and checksum repair on every write.

Stdlib only: no dependencies, no install. Requires Python 3.8+.

## GUI

Character / Statistics / Items tabs; open any save, save with backup + MD5:

    python3 er_save_editor.py --gui   # or: python3 src/er_save_editor --gui

The save is auto-detected under Wine/Steam (`--save PATH` overrides).

## Safety

- **Close the game before saving.** The editor warns if Elden Ring is running.
- Every write keeps a timestamped backup (`ER0000.sl2.bak-<date>`) next to the save.
- Edits are in-place fixed-size only: stats, quantities, appended goods rows
  (empty held slots + count bump). MD5 checksums are recalculated.

## Layout

    er_save_editor.py      thin launcher (backward compatible)
    src/er_save_editor/
      save.py              BND4 container, slots, stats, checksums
      items.py             GaItem walk, held inventory, qty edits
      itemdb.py            item name DB + updater
      cli.py               list/show/set/items/setqty/additem/updatedb
      gui.py               Character/Statistics/Items tabs + Open/Save
    items_db/              item names (refresh with `updatedb`)
    tests/                 stdlib unittests (`python3 -m unittest discover -s tests`)

The storage box is not mapped yet: deposit one stackable item into the
chest (Site of Grace → Organize Chest), quit to title, and it can be
mapped the same way as the held inventory.

## Credits

Item names in `items_db/` come from
[darthdemono/sl2-analyzer](https://github.com/darthdemono/sl2-analyzer)
(`db_er/`), MIT licensed. Save-format knowledge (GaItem walk, inventory
layout) was validated against its published parser and against a real
1.17 save before use.
