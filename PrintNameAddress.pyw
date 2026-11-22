import json
import os
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from pdf_form import FORMATS, PdfForm, load_csv, load_rules, save_rules


APP_DIR = Path(__file__).resolve().parent


class App:
    def __init__(self, root):
        self.root = root
        self.form = None
        self.pdf_path = None
        self.csv_path = None
        self.rules = {}
        self.columns = []
        self.rows = []
        self.page = 0
        self.highlight = None
        self.image = None
        self.print_job = None
        root.title("帳票差し込み印刷")
        root.geometry("1200x850")
        root.minsize(790, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)

        menu = tk.Menu(root)
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="PDFファイルの読込み", command=self.open_pdf)
        files.add_command(label="CSVファイルの読込み", command=self.open_csv)
        files.add_separator()
        files.add_command(label="終了", command=self.close)
        menu.add_cascade(label="ファイル", menu=files)
        root.configure(menu=menu)

        bar = ttk.Frame(root, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="PDFを開く", command=self.open_pdf).pack(side="left")
        ttk.Button(bar, text="CSVを開く", command=self.open_csv).pack(side="left", padx=(6, 14))
        ttk.Label(bar, text="名前").pack(side="left")
        self.person = ttk.Combobox(bar, width=20, state="disabled")
        self.person.pack(side="left", padx=(6, 0))
        self.person.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        self.name_column = ttk.Combobox(bar, width=11, state="disabled")
        self.name_column.pack(side="left", padx=(6, 0))
        self.name_column.bind("<<ComboboxSelected>>", lambda _: self.update_names())
        ttk.Label(bar, text="← 名前に使う列").pack(side="left", padx=(3, 0))
        self.print_button = ttk.Button(bar, text="印刷…", command=self.print_pdf, state="disabled")
        self.print_button.pack(side="right")
        self.mapping_button = ttk.Button(bar, text="列の対応表…", command=self.edit_rules, state="disabled")
        self.mapping_button.pack(side="right", padx=8)

        nav = ttk.Frame(root, padding=(12, 0, 12, 8))
        nav.pack(fill="x")
        self.print_all_button = ttk.Button(nav, text="全員を印刷…", command=self.print_all, state="disabled")
        self.print_all_button.pack(side="right")
        self.previous = ttk.Button(nav, width=5, text="前へ", command=lambda: self.change_page(-1))
        self.previous.pack(side="left")
        self.page_label = ttk.Label(nav, width=12, anchor="center")
        self.page_label.pack(side="left")
        self.next = ttk.Button(nav, width=5, text="次へ", command=lambda: self.change_page(1))
        self.next.pack(side="left")
        self.zoom = ttk.Combobox(nav, values=("75%", "100%", "125%", "150%", "200%"),
                                 width=6, state="readonly")
        self.zoom.set("125%")
        self.zoom.pack(side="left", padx=8)
        self.zoom.bind("<<ComboboxSelected>>", lambda _: self.refresh(reset_scroll=True))

        area = ttk.Frame(root)
        area.pack(fill="both", expand=True)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(area, background="#e7ebf0", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.canvas.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-event.delta // 120, "units"))
        self.canvas.bind("<Shift-MouseWheel>", lambda event: self.canvas.xview_scroll(-event.delta // 120, "units"))
        self.status = ttk.Label(root, padding=(12, 6))
        self.status.pack(fill="x")
        self.refresh()

    @property
    def row(self):
        index = self.person.current()
        return self.rows[index] if index >= 0 else {}

    def open_pdf(self):
        selected = filedialog.askopenfilename(parent=self.root, filetypes=[("PDFファイル", "*.pdf")])
        if selected:
            self.load_pdf(Path(selected))

    def load_pdf(self, path):
        try:
            rules = load_rules(path.with_suffix(".rl"))
            form = PdfForm(path)
        except Exception as error:
            messagebox.showerror("PDFを開けません", str(error), parent=self.root)
            return
        if self.form:
            self.form.close()
        self.pdf_path, self.form, self.rules = path, form, rules
        self.page = 0
        self.refresh(reset_scroll=True)

    def open_csv(self):
        selected = filedialog.askopenfilename(parent=self.root, filetypes=[("CSVファイル", "*.csv")])
        if selected:
            self.load_people(Path(selected))

    def load_people(self, path):
        try:
            columns, rows = load_csv(path)
        except Exception as error:
            messagebox.showerror("CSVを開けません", str(error), parent=self.root)
            return
        self.csv_path, self.columns, self.rows = path, columns, rows
        candidates = ("氏名2", "氏名", "名前", "氏名1", "Name", "name")
        column = next((name for name in candidates if name in columns), columns[0])
        self.name_column.configure(values=columns, state="readonly")
        self.name_column.set(column)
        self.update_names()

    def update_names(self):
        current = min(len(self.rows) - 1, max(0, self.person.current()))
        column = self.name_column.get()
        names = [row[column].strip() for row in self.rows]
        counts = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        labels = [
            f"{name or '（空欄）'} ［{index}件目］" if not name or counts[name] > 1 else name
            for index, name in enumerate(names, 1)
        ]
        self.person.configure(values=labels, state="readonly")
        self.person.current(current)
        self.refresh()

    def change_page(self, step):
        self.page += step
        self.refresh(reset_scroll=True)

    def refresh(self, reset_scroll=False):
        count = len(self.form.doc) if self.form else 0
        self.page_label.configure(text=f"{self.page + 1} / {count}" if count else "— / —")
        self.previous.configure(state="normal" if self.page else "disabled")
        self.next.configure(state="normal" if self.page + 1 < count else "disabled")
        self.update_print_buttons()
        self.mapping_button.configure(state="normal" if self.form and self.form.fields else "disabled")
        self.canvas.delete("all")
        if not count:
            self.canvas.create_text(24, 25, anchor="nw", fill="#263648",
                                    text="PDFとCSVを開いてください。")
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
        else:
            try:
                scale = int(self.zoom.get().rstrip("%")) / 100
                image, _ = self.form.render(self.page, scale, self.rules, self.row, self.highlight)
                self.image = ImageTk.PhotoImage(image)
                self.canvas.create_image(12, 12, anchor="nw", image=self.image)
                self.canvas.configure(scrollregion=(0, 0, image.width + 24, image.height + 24))
            except Exception as error:
                self.canvas.create_text(24, 25, anchor="nw", text=f"PDFを表示できません。\n{error}")
        if reset_scroll:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        pdf_name = self.pdf_path.name if self.pdf_path else "未選択"
        csv_name = self.csv_path.name if self.csv_path else "未選択"
        label = f"PDF  {pdf_name}     ｜     CSV  {csv_name}"
        if self.form and not self.form.fields:
            label += "     ｜     入力欄がありません"
        if self.csv_path:
            missing = {rule["column"] for rule in self.rules.values() if rule["column"] not in self.columns}
            if missing:
                label += "     ｜     CSVにない列：" + "、".join(sorted(missing))
        self.status.configure(text=label)

    def edit_rules(self):
        MappingDialog(self)

    def update_print_buttons(self):
        can_print = self.form and len(self.form.doc) and not self.print_job
        self.print_button.configure(state="normal" if can_print else "disabled")
        self.print_all_button.configure(state="normal" if can_print and self.rows else "disabled")

    def print_pdf(self):
        if not self.form or self.print_job:
            return
        if not self.rows and not messagebox.askyesno(
                "印刷", "CSVが未選択です。ひな形のまま印刷しますか？", parent=self.root):
            return
        self.start_print((self.row,))

    def print_all(self):
        if not self.form or not self.rows or self.print_job:
            return
        self.start_print(self.rows)

    def start_print(self, rows):
        folder = None
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            folder = tempfile.TemporaryDirectory(prefix="PrintNameAddress-")
            pages = []
            for row in rows:
                for index in range(len(self.form.doc)):
                    image, (width, height) = self.form.render(
                        index, 300 / 72, self.rules, row, printing=True)
                    filename = f"page-{len(pages) + 1}.png"
                    image.save(Path(folder.name) / filename)
                    pages.append({"file": filename, "width": width, "height": height})
            manifest = Path(folder.name) / "pages.json"
            manifest.write_text(json.dumps(pages), encoding="utf-8")
            owner_handle = int(self.root.wm_frame(), 0)
            log = open(Path(folder.name) / "print.log", "wb")
            try:
                process = subprocess.Popen(
                    [str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                     "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                     str(APP_DIR / "print_pages.ps1"), str(manifest),
                     "-OwnerHandle", str(owner_handle)],
                    creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=log)
            finally:
                log.close()
            self.print_job = (process, folder)
            self.update_print_buttons()
            self.root.after(250, self.check_print)
        except Exception as error:
            if folder is not None:
                folder.cleanup()
            messagebox.showerror("印刷できません", str(error), parent=self.root)
        finally:
            self.root.configure(cursor="")

    def check_print(self):
        process, folder = self.print_job
        result = process.poll()
        if result is None:
            self.root.after(250, self.check_print)
            return
        error = (Path(folder.name) / "print.log").read_text(encoding="utf-8", errors="replace")
        folder.cleanup()
        self.print_job = None
        self.update_print_buttons()
        if result:
            messagebox.showerror("印刷できません", error.strip() or "プリンターを確認してください。",
                                 parent=self.root)

    def close(self):
        if self.print_job:
            messagebox.showinfo("印刷", "印刷画面を閉じてから終了してください。", parent=self.root)
            return
        if self.form:
            self.form.close()
        self.root.destroy()


class MappingDialog:
    def __init__(self, app):
        self.app = app
        self.rules = {key: rule.copy() for key, rule in app.rules.items()}
        self.original_page = app.page
        self.window = tk.Toplevel(app.root)
        self.window.title("PDF入力欄とCSV列の対応表")
        x = max(0, app.root.winfo_rootx() + app.root.winfo_width() - 660)
        y = max(0, app.root.winfo_rooty() + app.root.winfo_height() - 620)
        self.window.geometry(f"650x590+{x}+{y}")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="入力欄を選ぶと、メイン画面のPDFで位置を確認できます。").pack(anchor="w")
        table_area = ttk.Frame(body)
        table_area.pack(fill="both", expand=True, pady=8)
        self.table = ttk.Treeview(table_area, columns=("page", "column", "format"), selectmode="browse")
        for key, title, width in (("#0", "PDF入力欄", 155), ("page", "ページ", 65),
                                  ("column", "CSV列", 150), ("format", "取り出す値", 145)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, stretch=key != "page")
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_area, command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", self.select)

        controls = ttk.Frame(body)
        controls.pack(fill="x")
        ttk.Label(controls, text="CSV列").grid(row=0, column=0, sticky="w")
        self.column = ttk.Combobox(controls, values=("", *app.columns), width=20)
        self.column.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(controls, text="取り出す値").grid(row=1, column=0, sticky="w", pady=6)
        self.format = ttk.Combobox(controls, values=list(FORMATS.values()), state="readonly", width=20)
        self.format.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(controls, text="数字のマス数（0＝通常欄）").grid(row=0, column=2, sticky="w", padx=6)
        self.cells = ttk.Spinbox(controls, from_=0, to=100, width=5)
        self.cells.grid(row=0, column=3)
        self.center = tk.BooleanVar()
        ttk.Checkbutton(controls, text="中央揃え", variable=self.center).grid(row=1, column=2, sticky="w", padx=6)
        self.column.bind("<<ComboboxSelected>>", lambda _: self.update_entry())
        self.format.bind("<<ComboboxSelected>>", lambda _: self.update_entry())
        ttk.Button(controls, text="選んだ欄に適用", command=self.update_entry).grid(row=2, column=1, sticky="w", padx=6)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="キャンセル", command=self.close).pack(side="right")
        ttk.Button(actions, text="保存して適用", command=self.save).pack(side="right", padx=6)
        self.current = None
        for name in app.form.fields:
            self.table.insert("", "end", iid=name, text=name, values=self.values(name))
        initial = next((name for name in self.rules if name in app.form.fields), next(iter(app.form.fields)))
        self.table.selection_set(initial)
        self.table.see(initial)
        self.window.grab_set()

    def values(self, name):
        rule = self.rules.get(name, {})
        return self.app.form.fields[name] + 1, rule.get("column", ""), FORMATS[rule.get("format", "")]

    def select(self, _event):
        selection = self.table.selection()
        if not selection or selection[0] == self.current:
            return
        if not self.update_entry():
            self.table.selection_set(self.current)
            return
        self.current = selection[0]
        rule = self.rules.get(self.current, {})
        self.column.set(rule.get("column", ""))
        self.format.set(FORMATS[rule.get("format", "")])
        self.cells.set(rule.get("cells", 0))
        self.center.set(rule.get("center", False))
        self.app.highlight = self.current
        change = self.app.page != self.app.form.fields[self.current]
        self.app.page = self.app.form.fields[self.current]
        self.app.refresh(reset_scroll=change)

    def update_entry(self):
        if self.current is None:
            return True
        column = self.column.get().strip()
        if column:
            try:
                count = int(self.cells.get())
                if not 0 <= count <= 100:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("対応表", "マス数は0から100の整数を入力してください。",
                                     parent=self.window)
                return False
            fmt = next(key for key, value in FORMATS.items() if value == self.format.get())
            self.rules[self.current] = {"column": column, "format": fmt,
                                        "cells": count, "center": self.center.get()}
        else:
            self.rules.pop(self.current, None)
        self.table.item(self.current, values=self.values(self.current))
        return True

    def save(self):
        if not self.update_entry():
            return
        try:
            save_rules(self.app.pdf_path.with_suffix(".rl"), self.rules)
        except OSError as error:
            messagebox.showerror("対応表を保存できません", str(error), parent=self.window)
            return
        self.app.rules = self.rules
        self.close()

    def close(self):
        self.window.destroy()
        self.app.highlight = None
        self.app.page = self.original_page
        self.app.refresh(reset_scroll=True)


if __name__ == "__main__":
    window = tk.Tk()
    ttk.Style(window).theme_use("vista")
    App(window)
    window.mainloop()
