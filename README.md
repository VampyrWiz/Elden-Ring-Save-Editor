This is my attempt at creating a save editor for Elden Ring. The goal is to provide a user-friendly interface for modifying save files, allowing players to customize their game experience.

## Usage (regulation 1.17.x, stdlib only)

GUI with Character / Statistics / Items tabs (open any save, save with backup + MD5):

    python3 er_save_editor.py --gui

CLI:

    python3 er_save_editor.py list
    python3 er_save_editor.py show 0
    python3 er_save_editor.py items 0 [--search kukri]
    python3 er_save_editor.py setqty 0 53 50        # held goods qty, 1..999
    python3 er_save_editor.py set 0 vigor=50 runes=100000
    python3 er_save_editor.py updatedb               # refresh items_db/

The save is auto-detected under Wine/Steam (`--save PATH` overrides).
Edits are in-place fixed-size only (no item adds/removes), MD5 checksums
are recalculated, and every write keeps a timestamped backup.

## Credits

Item names in `items_db/` come from
[darthdemono/sl2-analyzer](https://github.com/darthdemono/sl2-analyzer)
(`db_er/`), MIT licensed. Save-format knowledge (GaItem walk, inventory
layout) was validated against its published parser and against a real
1.17 save before use.