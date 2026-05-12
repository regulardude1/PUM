"""
gui_app.py — Modern GUI shell for PUM.
Sets up the 3-panel layout and wires the new ModListPanel + PreviewPanel
into the existing App business logic from main.py.
"""
import customtkinter
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from mod_list import ModListPanel
from preview_panel import PreviewPanel
from discord_store import DiscordStorePanel
import os

ASSETS_DIR = Path("assets")

# Modern dark color palette
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_card": "#16213e",
    "bg_input": "#0e1525",
    "accent": "#1a9f84",
    "text": "#e0e0e0",
    "text_dim": "#8888a8",
    "danger": "#a51f45",
    "warning": "#da8938",
}

# Default font size
_DEFAULT_FONT_SIZE = 11
_font_size = _DEFAULT_FONT_SIZE


def _fs(base_offset=0):
    """Return current font size + optional offset."""
    return _font_size + base_offset


def live_rescale_fonts(app, new_size):
    """Update the global font size and immediately re-style every widget."""
    global _font_size
    new_size = int(new_size)
    if new_size == _font_size:
        return
    _font_size = new_size

    # Update ttk Treeview styles
    style = ttk.Style()
    style.configure(".", font=("Segoe UI", new_size))
    style.configure("Treeview", font=("Segoe UI", new_size),
                     rowheight=max(24, new_size * 2 + 4))
    style.configure("Treeview.Heading", font=("Segoe UI", new_size, "bold"))

    # Recursively update all customtkinter / tk widgets
    def _update_widget(w):
        try:
            wclass = w.winfo_class()
            # CTk widgets use .configure(font=...)
            if hasattr(w, 'configure'):
                try:
                    current = w.cget("font")
                    if current:
                        # Parse existing font tuple or string
                        if isinstance(current, tuple):
                            family = current[0]
                            styles = list(current[2:]) if len(current) > 2 else []
                        else:
                            family = "Segoe UI"
                            styles = []
                        w.configure(font=(family, new_size, *styles))
                except Exception:
                    pass
        except Exception:
            pass
        for child in w.winfo_children():
            _update_widget(child)

    _update_widget(app)

    # Update the ModListPanel Treeview specifically (uses custom style name)
    if hasattr(app, 'mod_list_panel'):
        try:
            app.mod_list_panel.rescale_fonts(new_size)
        except Exception:
            pass

    app.update_idletasks()


def apply_modern_theme():
    # ... (keeps existing theme definition)
    import tkinter.ttk as ttk
    style = ttk.Style()
    style.theme_use("clam")

def _show_local_display_settings(app):
    """Show a popup with checkboxes to toggle treeview columns."""
    popup = customtkinter.CTkToplevel(app)
    popup.title("Column Visibility")
    popup.geometry("280x280")
    popup.transient(app)
    popup.attributes("-topmost", True)
    popup.after(100, lambda: popup.attributes("-topmost", False))
    popup.configure(fg_color="#1e1e2e")

    customtkinter.CTkLabel(popup, text="⚙ Mod List Columns", font=("Segoe UI", 15, "bold")).pack(pady=(15, 10))
    customtkinter.CTkLabel(popup, text="Choose which columns to display:", 
                           font=("Segoe UI", 11), text_color="#b5bac1").pack(padx=15, pady=(0, 8))

    fields = [
        ("author", "Author"),
        ("version", "Version"),
        ("category", "Category"),
        ("character", "Character"),
    ]
    vars_ = {}
    from main import save_config as _save_cfg

    current_vis = list(app.mod_list_panel.tree.cget("displaycolumns"))
    if not current_vis or "#all" in current_vis or current_vis == ["#all"]:
        current_vis = list(app.mod_list_panel.tree["columns"])
        
    for key, label in fields:
        is_vis = key in current_vis
        var = customtkinter.BooleanVar(value=is_vis)
        vars_[key] = var
        cb = customtkinter.CTkCheckBox(popup, text=label, variable=var,
                                       font=("Segoe UI", 12), fg_color="#5865F2",
                                       hover_color="#4752C4", corner_radius=4)
        cb.pack(anchor="w", padx=25, pady=4)

    def _apply():
        new_vis = []
        for col in current_vis:
            if col in ["checked", "fav", "name"]:
                new_vis.append(col)
            elif col in vars_ and vars_[col].get():
                new_vis.append(col)
                
        # Include newly checked columns
        for key, var in vars_.items():
            if var.get() and key not in new_vis:
                new_vis.append(key)
        
        app.mod_list_panel.tree["displaycolumns"] = new_vis
        app.app_settings["local_columns"] = new_vis
        try:
            _save_cfg(app.current_path, getattr(app, 'saved_mods', []), app.mod_options, app_settings=app.app_settings)
        except Exception:
            pass
        popup.destroy()

    customtkinter.CTkButton(popup, text="Apply", fg_color="#5865F2", hover_color="#4752C4",
                            height=34, font=("Segoe UI", 13, "bold"), command=_apply).pack(pady=(12, 10))
    """Configure customtkinter and ttk for the modern dark look."""
    customtkinter.set_appearance_mode("dark")
    customtkinter.set_default_color_theme("dark-blue")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", _font_size))
    style.configure("TFrame", background=COLORS["bg_dark"])
    style.configure("TLabel", background=COLORS["bg_dark"],
                     foreground=COLORS["text"])
    style.configure("TPanedwindow", background=COLORS["bg_dark"])
    style.configure("Sash", background="#2a2a4a", sashthickness=6)


def build_layout(app):
    """
    Replace the old grid layout in `app` (an App instance from main.py)
    with the modern 3-panel layout using ModListPanel and PreviewPanel.
    """
    global _font_size
    _font_size = app.app_settings.get("font_size", _DEFAULT_FONT_SIZE)
    accent = app.app_settings.get("accent_color", COLORS["accent"])

    # ── Window setup ──
    app.configure(fg_color=COLORS["bg_dark"])
    app.geometry("1100x650")
    app.minsize(900, 500)
    # Grid the main window properly since we are using PanedWindow
    app.grid_columnconfigure(0, weight=1)
    app.grid_rowconfigure(1, weight=1)      # main content
    app.grid_rowconfigure(0, weight=0)      # top bar
    app.grid_rowconfigure(2, weight=0)      # bottom bar

    # ── Top bar ──
    from main import t, dynamic_text_color
    top = customtkinter.CTkFrame(app, height=32, corner_radius=0,
                                  fg_color=COLORS["bg_card"])
    top.grid(row=0, column=0, columnspan=2, sticky="ew")
    app.top_bar = top

    for i, (txt, cmd) in enumerate([
        (t("preferences"), app.toggle_pref_dropdown),
        (t("btn_url_download"), app.download_url_callback),
    ]):
        btn = customtkinter.CTkButton(
            top, text=txt, height=24, corner_radius=4,
            fg_color="transparent",
            hover_color="#1a2744",
            text_color=dynamic_text_color,
            font=("Segoe UI", _fs()),
            command=cmd,
        )
        btn.grid(row=0, column=i, padx=8, pady=4)

    # Keep references the old code expects
    app.pref_button = top.winfo_children()[0]
    app.download_btn = top.winfo_children()[1]
    app.credits_button = None

    # Console button (hidden until enabled)
    app.console_button = customtkinter.CTkButton(
        top, text=t("console_button"), height=24, corner_radius=4,
        fg_color="transparent", hover_color="#1a2744",
        text_color=dynamic_text_color, font=("Segoe UI", _fs()),
        command=app.open_console_window,
    )

    # ── Preferences dropdown (overlay) ──
    _build_pref_dropdown(app, accent)

    # ── Main Tabview Layout ──
    app.main_tabview = customtkinter.CTkTabview(app)
    app.main_tabview.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=8)
    
    local_tab = app.main_tabview.add("Local Mods")
    discord_tab = app.main_tabview.add("Discord Store")
    
    local_tab.grid_columnconfigure(0, weight=0) # Sidebar
    local_tab.grid_columnconfigure(1, weight=1) # PanedWindow
    local_tab.grid_rowconfigure(0, weight=1)
    discord_tab.grid_columnconfigure(0, weight=1)
    discord_tab.grid_rowconfigure(0, weight=1)

    # ── Category Sidebar (Leftmost) ──
    app.local_sidebar_frame = customtkinter.CTkFrame(local_tab, fg_color="#1a1a2e", width=170, corner_radius=12)
    app.local_sidebar_frame.grid(row=0, column=0, sticky="ns", padx=(0, 4), pady=0)
    app.local_sidebar_frame.grid_propagate(False)

    sidebar_title = customtkinter.CTkLabel(app.local_sidebar_frame, text="📂 Categories", font=("Segoe UI", _fs(1), "bold"), anchor="w")
    sidebar_title.pack(fill="x", padx=10, pady=(10, 6))

    app.local_sidebar_scroll = customtkinter.CTkScrollableFrame(app.local_sidebar_frame, fg_color="transparent")
    app.local_sidebar_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 6))

    # Keep track of the currently selected sidebar category
    app.selected_sidebar_category = "All Categories"
    app.sidebar_buttons = {}

    def _select_sidebar_cat(cat_name):
        app.selected_sidebar_category = cat_name
        for name, btn in app.sidebar_buttons.items():
            btn.configure(fg_color=accent if name == cat_name else "transparent")
        app.after(50, app.refresh_logic)
    app._select_sidebar_cat = _select_sidebar_cat

    # ── Resizable Main Layout (Local Mods) ──
    paned_window = ttk.PanedWindow(local_tab, orient="horizontal")
    paned_window.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

    # ── Mod list (center) ──
    left_frame = customtkinter.CTkFrame(paned_window, fg_color=COLORS["bg_card"], corner_radius=12)
    paned_window.add(left_frame, weight=1)
    left_frame.grid_columnconfigure(0, weight=1)
    left_frame.grid_rowconfigure(1, weight=1)

    # Search + filter bar
    filter_bar = customtkinter.CTkFrame(left_frame, fg_color="transparent")
    filter_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    filter_bar.grid_columnconfigure(0, weight=1)

    app.search_var = customtkinter.StringVar()
    search = customtkinter.CTkEntry(
        filter_bar, placeholder_text=t("search_placeholder"),
        textvariable=app.search_var, height=32,
        fg_color=COLORS["bg_input"], border_width=0,
        font=("Segoe UI", _fs()),
    )
    search.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    app.search_entry = search

    # ⚙ Settings button next to search
    app.local_settings_btn = customtkinter.CTkButton(
        filter_bar, text="⚙", width=34, height=32,
        fg_color="#36393f", hover_color="#4752C4",
        font=("Segoe UI", 16), corner_radius=8,
        command=lambda: _show_local_display_settings(app),
    )
    app.local_settings_btn.grid(row=0, column=1, padx=(0, 4))


    # Profile menu (persist last active profile)
    last_profile = "Default Profile"
    try:
        last_profile = app.app_settings.get("active_profile", "Default Profile") or "Default Profile"
    except Exception:
        last_profile = "Default Profile"
    app.profile_var = customtkinter.StringVar(value=last_profile)
    app.profile_menu = customtkinter.CTkOptionMenu(
        filter_bar, values=app.get_saved_profiles(),
        variable=app.profile_var, width=130, height=32,
        fg_color=accent, font=("Segoe UI", _fs(-1)),
        command=app.load_profile_event,
    )
    app.profile_menu.grid(row=0, column=2, padx=(0, 4))

    # Profile actions (create/delete)
    app.new_profile_btn = customtkinter.CTkButton(
        filter_bar,
        text="+",
        width=28,
        height=32,
        corner_radius=8,
        fg_color=accent,
        hover_color=PreviewPanel._darken(accent),
        font=("Segoe UI", _fs(2), "bold"),
        command=app.save_current_profile,
    )
    app.new_profile_btn.grid(row=0, column=3, padx=(0, 4))

    app.delete_profile_btn = customtkinter.CTkButton(
        filter_bar,
        text="🗑",
        width=34,
        height=32,
        corner_radius=8,
        fg_color="#a51f45",
        hover_color="#8b132d",
        font=("Segoe UI", _fs(-1)),
        command=app.delete_current_profile,
    )
    app.delete_profile_btn.grid(row=0, column=4)
    # Ensure the UI matches the selected profile on startup
    try:
        app.after(50, lambda: app.load_profile_event(app.profile_var.get()))
    except Exception:
        pass

    # Mod list panel (Treeview)
    app.mod_list_panel = ModListPanel(left_frame, accent_color=accent,
                                       font_size=_font_size, fg_color="transparent")
    app.mod_list_panel.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
    
    # Restore saved column order/visibility
    saved_cols = app.app_settings.get("local_columns")
    if saved_cols:
        try:
            app.mod_list_panel.tree["displaycolumns"] = saved_cols
        except Exception:
            pass

    def _on_columns_changed(cols):
        app.app_settings["local_columns"] = list(cols)
        from main import save_config as _save_cfg
        try:
            _save_cfg(app.current_path, getattr(app, 'saved_mods', []), app.mod_options, app_settings=app.app_settings)
        except Exception:
            pass

    # Wire callbacks
    app.mod_list_panel.on_columns_changed = _on_columns_changed
    app.mod_list_panel.on_select = lambda mod: _on_mod_select(app, mod)
    app.mod_list_panel.on_toggle = lambda name, chk: _on_mod_toggle(app, name, chk)
    app.mod_list_panel.on_right_click = lambda e, m: app.show_context_menu(e, m)
    app.mod_list_panel.on_favorite = lambda m: app.toggle_favorite(m)

    # Debounced search
    app._search_timer = None
    def _debounced_search(*args):
        if app._search_timer:
            app.after_cancel(app._search_timer)
        app._search_timer = app.after(250, lambda: app.mod_list_panel.set_search(
            app.search_var.get()))
    app.search_var.trace_add("write", _debounced_search)

    # ── Preview panel (right) ──
    app.preview_panel = PreviewPanel(
        paned_window, accent_color=accent, game_path=app.current_path,
        # AES key is required for encrypted base-game paks.
        # Don't ship a placeholder default here: a wrong key looks "set" but still fails.
        aes_key=app.app_settings.get("aes_key", "") or "",
        auto_3d_preview=bool(app.app_settings.get("auto_3d_preview", True)),
        width=420  # initial minimum visual width
    )
    # The right panel shouldn't stretch its width forever like the list
    # NOTE: ttk.PanedWindow doesn't support minsize on all Tk builds.
    paned_window.add(app.preview_panel, weight=0)

    # ── Save and Restore Sash Position ──
    from main import save_config as _save_cfg

    _sash_save_timer = [None]  # mutable container for after() id
    _sash_dragging = [False]   # track whether user is dragging the sash

    def _persist_sash():
        """Save sash_pos directly to config.json without needing settings window."""
        try:
            pos = paned_window.sashpos(0)
            app.app_settings["sash_pos"] = pos
            _save_cfg(
                app.current_path,
                app.mod_list_panel.get_checked_names() if hasattr(app, 'mod_list_panel') else app.saved_mods,
                app.mod_options,
                app_settings=app.app_settings,
            )
        except Exception:
            pass

    def _on_sash_press(event):
        """Detect if the click is near the sash (within 12px tolerance)."""
        try:
            sash_x = paned_window.sashpos(0)
            if abs(event.x - sash_x) < 12:
                _sash_dragging[0] = True
        except Exception:
            pass

    def _on_sash_release(event):
        """Only save sash position if we were actually dragging the sash."""
        if _sash_dragging[0]:
            _sash_dragging[0] = False
            # Debounce the save to avoid redundant I/O
            if _sash_save_timer[0]:
                app.after_cancel(_sash_save_timer[0])
            _sash_save_timer[0] = app.after(300, _persist_sash)

    paned_window.bind("<ButtonPress-1>", _on_sash_press)
    paned_window.bind("<ButtonRelease-1>", _on_sash_release)

    saved_sash = app.app_settings.get("sash_pos")
    def restore_sash():
        try:
            app.update_idletasks()
            w = app.winfo_width()
            min_preview = 360
            # Always ensure a minimum width for the list (200) and preview (min_preview)
            if w > 200 + min_preview:
                if saved_sash is not None:
                    saved = int(saved_sash)
                    # Clamp between 200 and (total width - preview width)
                    target = max(200, min(saved, w - min_preview))
                    paned_window.sashpos(0, target)
                else:
                    # Default: 65% for list, 35% for preview
                    paned_window.sashpos(0, int(w * 0.65))
            else:
                # Small window fallback
                paned_window.sashpos(0, max(150, w - min_preview) if w > 150 else 150)
        except Exception:
            pass
    # Run at increasing delays to catch window mapping AND maximize settling
    app.after(150, restore_sash)
    app.after(500, restore_sash)
    app.after(1000, restore_sash)  # After maximize finishes (~200ms)

    app.preview_panel.on_toggle = lambda mod: _toggle_from_preview(app, mod)
    app.preview_panel.on_configure = lambda mod: app.open_config_window(mod)

    # ── Discord Store Panel ──
    app.discord_store_panel = DiscordStorePanel(discord_tab, accent_color=accent, fg_color="transparent")
    app.discord_store_panel.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
    
    def on_tab_change():
        if app.main_tabview.get() == "Discord Store":
            if not getattr(app.discord_store_panel, "_auto_fetched", False):
                app.discord_store_panel._auto_fetched = True
                if app.discord_store_panel.api.get_token():
                    app.discord_store_panel.load_mods(use_cache=False)
                    
    app.main_tabview.configure(command=on_tab_change)

    # ── Bottom bar ──
    bot = customtkinter.CTkFrame(app, height=48, corner_radius=0,
                                  fg_color=COLORS["bg_card"])
    bot.grid(row=2, column=0, columnspan=2, sticky="ew")
    bot.grid_columnconfigure(1, weight=1)

    app.run_game = customtkinter.CTkButton(
        bot, text=t("run_game"), fg_color=accent,
        hover_color=PreviewPanel._darken(accent),
        height=36, corner_radius=8, font=("Segoe UI", _fs(2), "bold"),
        command=app.game_callback,
    )
    app.run_game.grid(row=0, column=0, padx=12, pady=6)

    btn_frame = customtkinter.CTkFrame(bot, fg_color="transparent")
    btn_frame.grid(row=0, column=1, sticky="e", padx=12)

    app.mod_folder = customtkinter.CTkButton(
        btn_frame, text=t("open_mods_folder"), height=30,
        fg_color=accent, hover_color=PreviewPanel._darken(accent),
        corner_radius=6, font=("Segoe UI", _fs(-1)),
        command=lambda: os.startfile(Path("./mods")),
    )
    app.mod_folder.pack(side="left", padx=4)

    app.select_all = customtkinter.CTkButton(
        btn_frame, text=t("select_all"), height=30,
        fg_color=accent, hover_color=PreviewPanel._darken(accent),
        corner_radius=6, font=("Segoe UI", _fs(-1)),
        command=app.toggle_all_mods,
    )
    app.select_all.pack(side="left", padx=4)

    app.stats_label = customtkinter.CTkLabel(
        bot, text="", font=("Segoe UI", _fs(), "italic"),
        text_color=COLORS["text_dim"],
    )
    app.stats_label.grid(row=0, column=2, padx=12, sticky="e")


# ══════════════════════════════════════════════
# Preferences Dropdown
# ══════════════════════════════════════════════

def _build_pref_dropdown(app, accent):
    """Create the preferences dropdown overlay (CTkToplevel)."""
    from main import t

    try:
        app.pref_dropdown_win = customtkinter.CTkToplevel(app)
        app.pref_dropdown_win.withdraw()
        app.pref_dropdown_win.overrideredirect(True)
        try:
            app.pref_dropdown_win.attributes("-topmost", True)
        except Exception:
            pass
        app.pref_dropdown_visible = False

        inner = customtkinter.CTkFrame(app.pref_dropdown_win,
                                        fg_color=COLORS["bg_card"],
                                        corner_radius=8)
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        dd_font = ("Segoe UI", _fs())

        app.pref_path = customtkinter.CTkButton(
            inner, text=t("game_path"), corner_radius=4, height=30,
            fg_color="transparent", hover_color="#1a2744",
            font=dd_font, command=app.select_path_callback)
        app.pref_path.pack(fill="x", padx=8, pady=(8, 2))

        app.pref_refresh = customtkinter.CTkButton(
            inner, text=t("refresh_mods"), corner_radius=4, height=30,
            fg_color="transparent", hover_color="#1a2744",
            font=dd_font, command=app.refresh_logic)
        app.pref_refresh.pack(fill="x", padx=8, pady=2)

        app.pref_save = customtkinter.CTkButton(
            inner, text=t("save_selected_mods"), corner_radius=4, height=30,
            fg_color="transparent", hover_color="#1a2744",
            font=dd_font, command=app.deploy_mods)
        app.pref_save.pack(fill="x", padx=8, pady=2)

        app.pref_settings = customtkinter.CTkButton(
            inner, text=t("settings_title"), corner_radius=4, height=30,
            fg_color="transparent", hover_color="#1a2744",
            font=dd_font, command=app.open_settings)
        app.pref_settings.pack(fill="x", padx=8, pady=(2, 8))

        # Hide on focus lost
        try:
            app.pref_dropdown_win.bind('<FocusOut>',
                                        lambda e: app.hide_pref_dropdown())
        except Exception:
            pass

    except Exception:
        # Fallback: in-root frame
        app.pref_dropdown_frame = customtkinter.CTkFrame(
            app, fg_color=COLORS["bg_card"])
        app.pref_dropdown_visible = False
        dd_font = ("Segoe UI", _fs())
        app.pref_path = customtkinter.CTkButton(
            app.pref_dropdown_frame, text=t("game_path"), corner_radius=4,
            height=30, fg_color="transparent", command=app.select_path_callback,
            font=dd_font)
        app.pref_path.pack(fill="x", padx=8, pady=(6, 2))
        app.pref_refresh = customtkinter.CTkButton(
            app.pref_dropdown_frame, text=t("refresh_mods"), corner_radius=4,
            height=30, fg_color="transparent", command=app.refresh_logic,
            font=dd_font)
        app.pref_refresh.pack(fill="x", padx=8, pady=2)
        app.pref_save = customtkinter.CTkButton(
            app.pref_dropdown_frame, text=t("save_selected_mods"),
            corner_radius=4, height=30, fg_color="transparent",
            command=app.deploy_mods, font=dd_font)
        app.pref_save.pack(fill="x", padx=8, pady=2)
        app.pref_settings = customtkinter.CTkButton(
            app.pref_dropdown_frame, text=t("settings_title"), corner_radius=4,
            height=30, fg_color="transparent", command=app.open_settings,
            font=dd_font)
        app.pref_settings.pack(fill="x", padx=8, pady=(2, 8))


# ══════════════════════════════════════════════
# Callback helpers
# ══════════════════════════════════════════════

def _on_mod_select(app, mod):
    """When a mod is selected in the list, show it in preview."""
    app.focused_mod = mod
    checked = app.mod_list_panel.get_checked_names()
    is_enabled = mod.get("name", "") in checked
    app.preview_panel.show_mod(mod, is_enabled=is_enabled)


def _on_mod_toggle(app, mod_name, is_checked):
    """When a mod checkbox changes, save config."""
    from main import save_config
    checked = app.mod_list_panel.get_checked_names()
    save_config(app.current_path, checked, app.mod_options)
    app.save_to_active_profile()
    app.update_stats_display()
    if app.focused_mod and app.focused_mod.get("name") == mod_name:
        app.preview_panel.show_mod(app.focused_mod,
                                    is_enabled=mod_name in checked)


def _toggle_from_preview(app, mod):
    """Toggle mod from the preview panel's enable button."""
    name = mod.get("name", "")
    checked = set(app.mod_list_panel.get_checked_names())
    if name in checked:
        checked.discard(name)
    else:
        checked.add(name)
    from main import save_config
    app.mod_list_panel._checked_mods = checked
    app.mod_list_panel._refresh_tree()
    save_config(app.current_path, list(checked), app.mod_options)
    app.save_to_active_profile()
    app.update_stats_display()
    is_enabled = name in checked
    app.preview_panel.show_mod(mod, is_enabled=is_enabled)
