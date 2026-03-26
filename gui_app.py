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

    # ── Resizable Main Layout ──
    paned_window = ttk.PanedWindow(app, orient="horizontal")
    paned_window.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=8)

    # ── Mod list (left) ──
    left_frame = customtkinter.CTkFrame(paned_window, fg_color=COLORS["bg_card"],
                                         corner_radius=12)
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

    # Category filter
    app.cat_canonical = ["All Categories", "Skin", "Voice", "Emote", "UI", "Music", "Other"]
    # `t()` returns the key name if missing; in that case we fall back to a literal label.
    cat_emote_display = t("cat_emote")
    if cat_emote_display == "cat_emote":
        cat_emote_display = "Emote"
    app.cat_display_values = [
        t("all_categories"),
        t("cat_skin"),
        t("cat_voice"),
        cat_emote_display,
        t("cat_ui"),
        t("cat_music"),
        t("cat_other"),
    ]
    app.cat_filter = customtkinter.CTkOptionMenu(
        filter_bar, values=app.cat_display_values,
        width=150, height=32, fg_color=accent,
        font=("Segoe UI", _fs(-1)),
        command=lambda _: app.refresh_logic(),
    )
    app.cat_filter.grid(row=0, column=1, padx=(0, 4))
    # Make dropdown wide enough for long entries (safe across customtkinter versions)
    try:
        app.cat_filter.configure(dynamic_resizing=False)
    except Exception:
        pass
    try:
        app.cat_filter.configure(dropdown_width=260)
    except Exception:
        pass

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

    # Wire callbacks
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
    def on_sash_drag(event):
        try:
            # Only save if we actually moved the sash (x coordinate interaction)
            app.app_settings["sash_pos"] = paned_window.sashpos(0)
            app._save_app_settings()
        except Exception:
            pass
    paned_window.bind("<ButtonRelease-1>", on_sash_drag)

    saved_sash = app.app_settings.get("sash_pos")
    if saved_sash is not None:
        def restore_sash():
            try:
                # Clamp to keep preview pane visible
                app.update_idletasks()
                w = app.winfo_width()
                min_preview = 360
                if w and w > min_preview + 200:
                    saved = int(saved_sash)
                    saved = max(200, min(saved, w - min_preview))
                    paned_window.sashpos(0, saved)
                else:
                    paned_window.sashpos(0, int(saved_sash))
            except Exception:
                pass
        app.after(100, restore_sash)
    else:
        # Default: make the preview panel ~30% of the window width.
        # (When starting maximized, the default sash can make the right pane huge.)
        def set_default_sash():
            try:
                app.update_idletasks()
                w = app.winfo_width()
                if w and w > 200:
                    min_preview = 360
                    sash = int(w * 0.70)
                    sash = max(200, min(sash, w - min_preview))
                    paned_window.sashpos(0, sash)
            except Exception:
                pass
        # Run a couple times to survive initial maximize/layout settling
        app.after(200, set_default_sash)
        app.after(600, set_default_sash)

        # Also run once on first real resize (some systems report width late)
        def _once_configure(event=None):
            try:
                paned_window.unbind("<Configure>", _bind_id)
            except Exception:
                pass
            set_default_sash()
        try:
            _bind_id = paned_window.bind("<Configure>", _once_configure)
        except Exception:
            pass
    app.preview_panel.on_toggle = lambda mod: _toggle_from_preview(app, mod)
    app.preview_panel.on_configure = lambda mod: app.open_config_window(mod)

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
