"""
preview_panel.py

Mod details panel with integrated 3D model viewer and image preview fallback.
Shows mod name, author, version, description, screenshot, and action buttons.
Integrates the OpenGL model viewer from model_viewer.py.
"""

import tkinter as tk
import customtkinter
from pathlib import Path
from PIL import Image
from typing import Optional, Callable, Dict

# Local imports
try:
    from model_viewer import ModelViewer, PreviewManager, MeshData, _GL_AVAILABLE
except ImportError:
    _GL_AVAILABLE = False
    ModelViewer = None
    PreviewManager = None

ASSETS_DIR = Path("assets")


class PreviewPanel(customtkinter.CTkFrame):
    """
    Right-side panel showing mod details and 3D/image preview.
    
    Has two display modes:
    1. 3D Model viewer (OpenGL) — when umodel + dependencies available
    2. Image preview (fallback) — always available
    """

    def __init__(self, parent, accent_color="#1a9f84", game_path="", aes_key="", **kwargs):
        super().__init__(parent, fg_color="#16213e", corner_radius=12, **kwargs)

        self.accent_color = accent_color
        self._game_path = game_path
        self._aes_key = aes_key
        self._current_mod: Optional[Dict] = None

        # Callbacks
        self.on_toggle: Optional[Callable] = None  # (mod, is_enabled)
        self.on_configure: Optional[Callable] = None  # (mod)

        # Preview manager for 3D
        self._preview_mgr = None
        if PreviewManager:
            self._preview_mgr = PreviewManager(game_path, aes_key)

        self._setup_ui()

    def _setup_ui(self):
        """Build the panel layout."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)  # Preview area gets most space

        # --- Header: Mod name + version ---
        self._header_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self._header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        self._header_frame.grid_columnconfigure(0, weight=1)

        self._title_label = customtkinter.CTkLabel(
            self._header_frame,
            text="",
            font=("Segoe UI", 20, "bold"),
            text_color="#ffffff",
            anchor="w",
        )
        self._title_label.grid(row=0, column=0, sticky="w")

        self._subtitle_label = customtkinter.CTkLabel(
            self._header_frame,
            text="",
            font=("Segoe UI", 12),
            text_color="#8888a8",
            anchor="w",
        )
        self._subtitle_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

        # --- Preview area ---
        self._preview_container = customtkinter.CTkFrame(
            self, fg_color="#0e1525", corner_radius=8
        )
        self._preview_container.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self._preview_container.grid_columnconfigure(0, weight=1)
        self._preview_container.grid_rowconfigure(0, weight=1)

        # 3D viewer (if available)
        self._3d_viewer = None
        if _GL_AVAILABLE and ModelViewer is not None:
            try:
                self._3d_viewer = ModelViewer(self._preview_container, width=400, height=300)
                self._3d_viewer.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
                self._3d_viewer.animate = 1  # Enable continuous rendering
                if self._preview_mgr:
                    self._preview_mgr.set_viewer(self._3d_viewer)
            except Exception as e:
                print(f"[PreviewPanel] Could not initialize 3D viewer: {e}")
                self._3d_viewer = None

        # Image preview label (fallback / always visible when no 3D)
        self._image_label = customtkinter.CTkLabel(
            self._preview_container,
            text="",
            fg_color="transparent",
        )
        # Will be shown/hidden dynamically

        # Placeholder for when nothing is selected
        self._placeholder_label = customtkinter.CTkLabel(
            self._preview_container,
            text="Select a mod to preview",
            font=("Segoe UI", 14, "italic"),
            text_color="#4a4a6a",
        )
        self._placeholder_label.grid(row=0, column=0, sticky="nsew")

        # --- Description ---
        self._desc_box = customtkinter.CTkTextbox(
            self,
            height=80,
            corner_radius=6,
            fg_color="#0e1525",
            text_color="#c0c0d8",
            font=("Segoe UI", 11),
            border_width=0,
        )
        self._desc_box.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
        self._desc_box.configure(state="disabled")

        # --- Action buttons ---
        self._btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self._btn_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._btn_frame.grid_columnconfigure((0, 1), weight=1)

        self._toggle_btn = customtkinter.CTkButton(
            self._btn_frame,
            text="Enable Mod",
            fg_color="#a51f45",
            hover_color="#8b132d",
            font=("Segoe UI", 12, "bold"),
            height=36,
            corner_radius=8,
            command=self._on_toggle_click,
        )
        self._toggle_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self._config_btn = customtkinter.CTkButton(
            self._btn_frame,
            text="Configure",
            fg_color=self.accent_color,
            hover_color=self._darken(self.accent_color),
            font=("Segoe UI", 12),
            height=36,
            corner_radius=8,
            command=self._on_config_click,
        )
        self._config_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # --- Link ---
        self._link_label = customtkinter.CTkLabel(
            self,
            text="",
            font=("Segoe UI", 11),
            text_color=self.accent_color,
            cursor="hand2",
        )
        self._link_label.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 12))
        self._link_label.bind("<Button-1>", self._on_link_click)

        # --- Preview mode toggle ---
        self._mode_frame = customtkinter.CTkFrame(self, fg_color="transparent", height=28)
        self._mode_frame.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 8))

        self._use_3d = _GL_AVAILABLE and self._3d_viewer is not None
        if self._3d_viewer is not None:
            self._mode_toggle = customtkinter.CTkSegmentedButton(
                self._mode_frame,
                values=["3D Preview", "Image"],
                command=self._on_mode_change,
                font=("Segoe UI", 10),
                selected_color=self.accent_color,
                selected_hover_color=self._darken(self.accent_color),
            )
            self._mode_toggle.set("3D Preview")
            self._mode_toggle.pack(side="right")

        # Show placeholder initially
        self._show_placeholder()

    def show_mod(self, mod: Dict, is_enabled: bool = False):
        """Display a mod's details and preview."""
        self._current_mod = mod
        name = mod.get("name", "Unknown")
        author = mod.get("author", "???")
        version = mod.get("version", "1.0")
        category = mod.get("category", "Other")
        description = mod.get("description", "No description available.")

        # Header
        self._title_label.configure(text=name)
        self._subtitle_label.configure(text=f"by {author}  •  v{version}  •  {category}")

        # Description
        self._desc_box.configure(state="normal")
        self._desc_box.delete("0.0", "end")
        self._desc_box.insert("0.0", description)
        self._desc_box.configure(state="disabled")

        # Toggle button state
        if is_enabled:
            self._toggle_btn.configure(
                text="✓ Enabled",
                fg_color=self.accent_color,
                hover_color=self._darken(self.accent_color),
            )
        else:
            self._toggle_btn.configure(
                text="Enable Mod",
                fg_color="#a51f45",
                hover_color="#8b132d",
            )

        # Config button visibility
        if mod.get("has_options"):
            self._config_btn.grid()
        else:
            self._config_btn.grid_remove()

        # Link
        url = mod.get("url", "")
        if url:
            self._link_label.configure(text="🔗 View mod online")
            self._link_label.grid()
        else:
            self._link_label.grid_remove()

        # Hide placeholder
        self._placeholder_label.grid_remove()

        # Show preview
        if self._use_3d and self._3d_viewer is not None:
            self._show_3d_preview(mod)
        else:
            self._show_image_preview(mod)

    def _show_3d_preview(self, mod: Dict):
        """Attempt to load and display 3D model."""
        # Show the 3D viewer
        if self._3d_viewer:
            self._3d_viewer.grid()
            self._image_label.grid_remove()

        # Try to extract and load model
        folder = mod.get("folder_path")
        if folder and self._preview_mgr:
            def on_load(success):
                if not success:
                    # Fallback to image
                    try:
                        def _do_fallback():
                            self._show_image_preview(mod)
                            if hasattr(self, '_mode_toggle'):
                                self._mode_toggle.set("Image")
                        self.after(0, _do_fallback)
                    except Exception:
                        pass

            self._preview_mgr.preview_mod(Path(folder), callback=on_load)
        else:
            # No folder or no preview manager — show image
            self._show_image_preview(mod)

    def _show_image_preview(self, mod: Dict):
        """Show the mod's screenshot as a large image."""
        if self._3d_viewer:
            self._3d_viewer.grid_remove()

        folder = mod.get("folder_path")
        img_path = None

        if folder:
            screenshot = mod.get("screenshot", "preview.png")
            candidate = Path(folder) / screenshot
            if candidate.exists() and candidate.is_file():
                img_path = candidate

        if img_path is None:
            default = ASSETS_DIR / "default_preview.png"
            if default.exists():
                img_path = default

        if img_path:
            try:
                img = Image.open(img_path)
                # Scale to fit container while maintaining ratio
                max_w, max_h = 420, 300
                img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                ctk_img = customtkinter.CTkImage(
                    light_image=img, dark_image=img,
                    size=(img.width, img.height)
                )
                self._image_label.configure(image=ctk_img, text="")
                self._image_label.image = ctk_img  # Keep reference
                self._image_label.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
            except Exception as e:
                self._image_label.configure(image=None, text="No preview available")
                self._image_label.grid(row=0, column=0, sticky="nsew")
        else:
            self._image_label.configure(image=None, text="No preview available")
            self._image_label.grid(row=0, column=0, sticky="nsew")

    def _show_placeholder(self):
        """Show the 'select a mod' placeholder."""
        if self._3d_viewer:
            self._3d_viewer.grid_remove()
        self._image_label.grid_remove()
        self._placeholder_label.grid(row=0, column=0, sticky="nsew")

        self._title_label.configure(text="")
        self._subtitle_label.configure(text="")
        self._desc_box.configure(state="normal")
        self._desc_box.delete("0.0", "end")
        self._desc_box.configure(state="disabled")
        self._toggle_btn.grid_remove()
        self._config_btn.grid_remove()
        self._link_label.grid_remove()

    def _on_toggle_click(self):
        if self._current_mod and self.on_toggle:
            self.on_toggle(self._current_mod)

    def _on_config_click(self):
        if self._current_mod and self.on_configure:
            self.on_configure(self._current_mod)

    def _on_link_click(self, event):
        if self._current_mod and self._current_mod.get("url"):
            import os
            try:
                os.startfile(self._current_mod["url"])
            except Exception:
                import webbrowser
                webbrowser.open(self._current_mod["url"])

    def _on_mode_change(self, mode: str):
        """Toggle between 3D preview and image."""
        if mode == "3D Preview":
            self._use_3d = True
            if self._current_mod:
                self._show_3d_preview(self._current_mod)
        else:
            self._use_3d = False
            if self._current_mod:
                self._show_image_preview(self._current_mod)

    def update_accent(self, color: str):
        """Update accent color."""
        self.accent_color = color
        self._toggle_btn.configure(hover_color=self._darken(color))
        self._config_btn.configure(fg_color=color, hover_color=self._darken(color))
        self._link_label.configure(text_color=color)

    def set_game_path(self, path: str):
        """Update game path for model extraction."""
        self._game_path = path
        if self._preview_mgr:
            self._preview_mgr.set_game_path(path)

    @staticmethod
    def _darken(hex_color: str, factor: float = 0.18) -> str:
        """Darken a hex color."""
        try:
            h = hex_color.lstrip("#")
            r = max(0, int(int(h[0:2], 16) * (1 - factor)))
            g = max(0, int(int(h[2:4], 16) * (1 - factor)))
            b = max(0, int(int(h[4:6], 16) * (1 - factor)))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return "#13775c"
