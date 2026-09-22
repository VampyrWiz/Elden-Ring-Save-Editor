This is my attempt at creating a save editor for Elden Ring. The goal is to provide a user-friendly interface for modifying save files, allowing players to customize their game experience.

## Usage (regulation 1.17.x, stdlib only)

GUI with Character / Statistics / Items tabs (open any save, save with backup + MD5):

    python3 er_save_editor.py --gui

CLI:

    python3 er_save_editor.py list
    python3 er_save_editor.py show 0
    python3 er_save_editor.py items 0 [--search kukri]
    python3 er_save_editor.py setqty 0 53 50        # held goods qty, 1..999
    python3 er_save_editor.py additem 0 kukri 99     # add goods (merge or append)
    python3 er_save_editor.py additem 0 400006c2 99  # same by hex id
    python3 er_save_editor.py set 0 vigor=50 runes=100000
    python3 er_save_editor.py updatedb               # refresh items_db/

The save is auto-detected under Wine/Steam (`--save PATH` overrides).
Edits are in-place fixed-size only: stats, quantities, and appended goods
rows (empty held slots + count bump). MD5 checksums are recalculated and
every write keeps a timestamped backup. Weapons/armor add/remove and the
storage box are out of scope — variable-length GaItem surgery is what
corrupts saves, and the chest is empty in every save on hand.

## Layout

    er_save_editor.py      thin launcher (backward compatible)
    src/er_save_editor/
      save.py              BND4 container, slots, stats, checksums
      items.py             GaItem walk, held inventory, qty edits
      itemdb.py            item name DB + updater
      cli.py               list/show/set/items/setqty/updatedb
      gui.py               Character/Statistics/Items tabs + Open/Save
    items_db/              item names (refresh with `updatedb`)
    tests/                 stdlib unittests (`python3 -m unittest discover -s tests`)

The storage box is not mapped yet: it is empty in every save on hand, so
there is no signature to validate against. Deposit one stackable item into
the chest (Site of Grace → Organize Chest), quit to title, and it can be
mapped the same way as the held inventory.

## Credits

Item names in `items_db/` come from
[darthdemono/sl2-analyzer](https://github.com/darthdemono/sl2-analyzer)
(`db_er/`), MIT licensed. Save-format knowledge (GaItem walk, inventory
layout) was validated against its published parser and against a real
1.17 save before use.