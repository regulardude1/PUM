"""
mod_list.py

High-performance mod list widget using ttk.Treeview.
Replaces the old manual-widget grid with a native C-level component
that handles thousands of items without lag.

Features:
- Sortable columns (click header)
- Search filtering (hides items — no destroy/create)
- Checkbox toggling via click
- Favorites with star icon
- Right-click context menu
- Row hover highlighting via tags
"""

import tkinter as tk
from tkinter import ttk
import customtkinter
from pathlib import Path
from typing import List, Dict, Callable, Optional


class ModListPanel(customtkinter.CTkFrame):
    """
    A mod list panel backed by ttk.Treeview for instant rendering.

    Callbacks:
        on_select(mod_info: dict)  — called when a mod row is clicked
        on_toggle(mod_name: str, is_checked: bool) — called when checkbox toggled
        on_right_click(event, mod_info) — called on right-click
        on_favorite(mod_info) — called when star is clicked
    """

    def __init__(self, parent, accent_color="#1a9f84", font_size=12, **kwargs):
        super().__init__(parent, **kwargs)

        self.accent_color = accent_color
        self._font_size = font_size
        self._all_mods: List[Dict] = []
        self._checked_mods: set = set()
        self._sort_key = "name"
        self._sort_reverse = False
        self._search_term = ""
        self._category_filter = "All Categories"

        # Callbacks
        self.on_select: Optional[Callable] = None
        self.on_toggle: Optional[Callable] = None
        self.on_right_click: Optional[Callable] = None
        self.on_favorite: Optional[Callable] = None

        self._setup_ui()

    def _setup_ui(self):
        """Build the Treeview with custom styling."""
        # Configure ttk style for dark theme
        self._style = ttk.Style()
        self._style.theme_use("clam")

        # Treeview styling
        rh = max(24, self._font_size * 2 + 6)
        self._style.configure("ModList.Treeview",
            background="#1a1a2e",
            foreground="#e0e0e0",
            fieldbackground="#1a1a2e",
            borderwidth=0,
            font=("Segoe UI", self._font_size),
            rowheight=rh,
        )
        self._style.configure("ModList.Treeview.Heading",
            background="#16213e",
            foreground="#a0a0b8",
            font=("Segoe UI", self._font_size, "bold"),
            borderwidth=0,
            relief="flat",
        )
        self._style.map("ModList.Treeview",
            background=[("selected", "#0f3460")],
            foreground=[("selected", "#ffffff")],
        )
        self._style.map("ModList.Treeview.Heading",
            background=[("active", "#1a2744")],
        )

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Treeview
        columns = ("checked", "fav", "name", "author", "version", "category")
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="ModList.Treeview",
        )

        # Column definitions
        self.tree.heading("checked", text="✓", command=lambda: self._toggle_all())
        self.tree.heading("fav", text="★")
        self.tree.heading("name", text="Name", command=lambda: self._sort_by("name"))
        self.tree.heading("author", text="Author", command=lambda: self._sort_by("author"))
        self.tree.heading("version", text="Version", command=lambda: self._sort_by("version"))
        self.tree.heading("category", text="Category", command=lambda: self._sort_by("category"))

        self.tree.column("checked", width=36, minwidth=36, stretch=False, anchor="center")
        self.tree.column("fav", width=36, minwidth=36, stretch=False, anchor="center")
        self.tree.column("name", width=200, minwidth=120, stretch=True, anchor="w")
        self.tree.column("author", width=120, minwidth=80, stretch=True, anchor="w")
        self.tree.column("version", width=70, minwidth=50, stretch=False, anchor="center")
        self.tree.column("category", width=80, minwidth=60, stretch=False, anchor="center")

        # Tags for visual states
        self.tree.tag_configure("even", background="#1a1a2e")
        self.tree.tag_configure("odd", background="#1e1e34")
        self.tree.tag_configure("checked", foreground="#6fdfca")
        self.tree.tag_configure("favorite", foreground="#ffd700")

        # Scrollbar
        scrollbar = customtkinter.CTkScrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(0, 0))
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Bind events
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Button-3>", self._on_right_click_event)

    def set_mods(self, mods: List[Dict], checked_names: List[str]):
        """
        Set the full list of mods and which are checked.
        This replaces the entire mod list efficiently.
        """
        self._all_mods = mods
        self._checked_mods = set(checked_names)
        self._refresh_tree()

    def get_checked_names(self) -> List[str]:
        """Return list of checked mod names."""
        return list(self._checked_mods)

    def set_search(self, term: str):
        """Apply search filter — hides non-matching rows."""
        self._search_term = term.lower().strip()
        self._refresh_tree()

    def set_category(self, category: str):
        """Apply category filter."""
        self._category_filter = category
        self._refresh_tree()

    def update_accent(self, color: str):
        """Update accent color for selected row highlight."""
        self.accent_color = color
        try:
            self._style.map("ModList.Treeview",
                background=[("selected", color)],
            )
        except Exception:
            pass

    def rescale_fonts(self, size: int):
        """Dynamically update font size for the Treeview and headings."""
        size = int(size)
        rh = max(24, size * 2 + 6)
        try:
            self._style.configure("ModList.Treeview",
                font=("Segoe UI", size),
                rowheight=rh,
            )
            self._style.configure("ModList.Treeview.Heading",
                font=("Segoe UI", size, "bold"),
            )
            self.after(10, self._refresh_tree)
        except Exception:
            pass

    def _refresh_tree(self):
        """Rebuild the Treeview content. This is fast because Treeview
        handles items internally in C — no Python widget overhead."""
        # Clear
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Filter
        filtered = []
        for mod in self._all_mods:
            name = mod.get("name", "").lower()
            author = mod.get("author", "").lower()
            cat = mod.get("category", "Other")

            # Search filter
            if self._search_term and self._search_term not in name and self._search_term not in author:
                continue

            # Category filter
            if self._category_filter != "All Categories" and cat != self._category_filter:
                continue

            filtered.append(mod)

        # Sort — favorites first, then by sort key
        filtered.sort(
            key=lambda x: str(x.get(self._sort_key, "")).lower(),
            reverse=self._sort_reverse
        )
        filtered.sort(key=lambda x: x.get("is_favorite", False), reverse=True)

        # Insert
        # Treeview item IDs (iid) must be unique. Some users may have duplicate mod names
        # (e.g. multiple folders or variants with the same display name), so we generate
        # a stable unique iid using folder_path when available.
        used_iids = set()
        for i, mod in enumerate(filtered):
            name = mod.get("name", "Unknown")
            is_checked = name in self._checked_mods
            is_fav = mod.get("is_favorite", False)

            check_mark = "☑" if is_checked else "☐"
            star = "★" if is_fav else "☆"
            author = mod.get("author", "???")
            version = f"v{mod.get('version', '1.0')}"
            category = mod.get("category", "Other")

            tags = []
            tags.append("even" if i % 2 == 0 else "odd")
            if is_checked:
                tags.append("checked")
            if is_fav:
                tags.append("favorite")

            base_iid = name
            try:
                fp = mod.get("folder_path")
                if fp:
                    base_iid = f"{name}::{fp}"
            except Exception:
                base_iid = name

            iid = base_iid
            if iid in used_iids:
                # Ensure uniqueness even if folder_path is missing/duplicated
                n = 2
                while f"{base_iid}#{n}" in used_iids:
                    n += 1
                iid = f"{base_iid}#{n}"
            used_iids.add(iid)

            self.tree.insert(
                "", "end",
                iid=iid,
                values=(check_mark, star, name, author, version, category),
                tags=tuple(tags),
            )

        # Update heading sort indicator
        for col in ("name", "author", "version", "category"):
            heading_text = col.capitalize()
            if col == self._sort_key:
                heading_text += " ▼" if not self._sort_reverse else " ▲"
            self.tree.heading(col, text=heading_text)

    def _sort_by(self, key: str):
        """Sort by column — toggle direction if same column clicked again."""
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = key
            self._sort_reverse = False
        self._refresh_tree()

    def _toggle_all(self):
        """Toggle all mods on/off."""
        all_names = {m.get("name", "") for m in self._all_mods}
        if self._checked_mods >= all_names:
            self._checked_mods.clear()
        else:
            self._checked_mods = all_names.copy()
        self._refresh_tree()
        if self.on_toggle:
            self.on_toggle(None, None)  # Signal batch change

    def _on_tree_select(self, event):
        """Handle row selection — show mod details."""
        selection = self.tree.selection()
        if selection:
            item_id = selection[0]
            mod = self._find_mod(item_id)
            if mod and self.on_select:
                self.on_select(mod)

    def _on_click(self, event):
        """Handle clicks on checkbox and star columns."""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return

        # Always select the clicked row so the right preview updates reliably.
        # (Treeview selection can be skipped depending on click target/column.)
        try:
            self.tree.selection_set(item)
        except Exception:
            pass

        if col == "#1":  # Checked column
            mod = self._find_mod(item)
            mod_name = (mod or {}).get("name", item.split("::", 1)[0])
            if mod_name in self._checked_mods:
                self._checked_mods.discard(mod_name)
                is_checked = False
            else:
                self._checked_mods.add(mod_name)
                is_checked = True

            # Update just this row's display
            self._update_row_display(item)
            if self.on_toggle:
                self.on_toggle(mod_name, is_checked)

        elif col == "#2":  # Favorite column
            mod = self._find_mod(item)
            if mod and self.on_favorite:
                self.on_favorite(mod)
        else:
            # Any other column: treat as a normal selection click
            mod = self._find_mod(item)
            if mod and self.on_select:
                self.on_select(mod)

    def _on_right_click_event(self, event):
        """Handle right-click for context menu."""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            mod = self._find_mod(item)
            if mod and self.on_right_click:
                self.on_right_click(event, mod)

    def _update_row_display(self, item_id: str):
        """Update a single row's visual state without full refresh."""
        try:
            values = list(self.tree.item(item_id, "values"))
            mod = self._find_mod(item_id)
            mod_name = (mod or {}).get("name", values[2] if len(values) > 2 else item_id.split("::", 1)[0])
            is_checked = mod_name in self._checked_mods
            values[0] = "☑" if is_checked else "☐"

            # Determine tags
            idx = self.tree.index(item_id)
            tags = ["even" if idx % 2 == 0 else "odd"]
            if is_checked:
                tags.append("checked")
            if mod and mod.get("is_favorite", False):
                tags.append("favorite")

            self.tree.item(item_id, values=values, tags=tuple(tags))
        except Exception:
            pass

    def _find_mod(self, item_id: str) -> Optional[Dict]:
        """Find mod info by Treeview iid or display name."""
        # New iid format: "<name>::<folder_path>[#n]"
        name = item_id
        folder_path = None
        if "::" in item_id:
            name, rest = item_id.split("::", 1)
            folder_path = rest.split("#", 1)[0] if rest else None

        if folder_path:
            for mod in self._all_mods:
                if mod.get("folder_path") == folder_path and mod.get("name", "") == name:
                    return mod
        for mod in self._all_mods:
            if mod.get("name", "") == name:
                return mod
        return None
