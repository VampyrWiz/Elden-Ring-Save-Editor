"""Tk GUI: file Open/Save plus Character / Statistics / Items tabs."""
import os

from .itemdb import load_db, resolve_item, update_db
from .save import SLOT_N, STAT_OFFS, Save, game_running


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
