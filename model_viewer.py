"""
model_viewer.py

Handles 3D model extraction from .pak files (via umodel CLI) and rendering
an interactive OpenGL preview embedded in a tkinter/customtkinter window.

Pipeline:
  1. ModelExtractor  — calls umodel to export UE assets → glTF / OBJ
  2. ModelViewer     — OpenGL viewport (pyopengltk) with mouse rotation/zoom
  3. PreviewManager  — coordinates extraction + rendering, caches results
"""

import os
import sys
import math
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import numpy as np

# Lazy imports to avoid hard failures if OpenGL is missing
_GL_AVAILABLE = False
try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from pyopengltk import OpenGLFrame
    _GL_AVAILABLE = True
except ImportError:
    pass

_TRIMESH_AVAILABLE = False
try:
    import trimesh
    _TRIMESH_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CACHE_DIR = Path("cache") / "models"
TOOLS_DIR = Path("tools")
UMODEL_EXE = TOOLS_DIR / "umodel.exe"

# ---------------------------------------------------------------------------
# ModelExtractor — extracts meshes from .pak via umodel CLI
# ---------------------------------------------------------------------------
class ModelExtractor:
    """Extracts 3D meshes from Unreal Engine .pak files using umodel."""

    def __init__(self, game_paks_path: str, aes_key: str = ""):
        self.game_paks_path = Path(game_paks_path)
        self.aes_key = aes_key
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_available() -> bool:
        """Check whether umodel binary is present."""
        return UMODEL_EXE.exists()

    def extract_asset(self, asset_path: str, output_dir: Optional[Path] = None) -> Optional[Path]:
        """
        Export a specific UE asset to glTF using umodel.

        Parameters
        ----------
        asset_path : str
            Internal UE asset path (e.g., "HerovsGame/Characters/Hero01/Mesh/SK_Hero01")
        output_dir : Path, optional
            Where to write exported files. Defaults to CACHE_DIR / <asset_hash>.

        Returns
        -------
        Path or None
            Path to exported directory containing .gltf/.glb file, or None on failure.
        """
        if not self.is_available():
            print("[ModelViewer] umodel.exe not found in tools/")
            return None

        if output_dir is None:
            # Use a stable hash of the asset path for caching
            safe_name = asset_path.replace("/", "_").replace("\\", "_")
            output_dir = CACHE_DIR / safe_name

        # Check cache
        if output_dir.exists():
            gltf_files = list(output_dir.rglob("*.gltf")) + list(output_dir.rglob("*.glb"))
            if gltf_files:
                return output_dir

        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(UMODEL_EXE),
            f"-path={self.game_paks_path}",
            "-export",
            "-gltf",
            f"-out={output_dir}",
            asset_path,
        ]

        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                startupinfo=startupinfo,
            )
            if result.returncode != 0:
                print(f"[ModelViewer] umodel error: {result.stderr[:500]}")
                return None
        except subprocess.TimeoutExpired:
            print("[ModelViewer] umodel timed out")
            return None
        except Exception as e:
            print(f"[ModelViewer] umodel failed: {e}")
            return None

        gltf_files = list(output_dir.rglob("*.gltf")) + list(output_dir.rglob("*.glb"))
        if not gltf_files:
            # Fallback: look for .psk/.obj
            other = list(output_dir.rglob("*.psk")) + list(output_dir.rglob("*.obj"))
            if other:
                return output_dir
            print("[ModelViewer] No mesh files found in export")
            return None

        return output_dir

    def extract_mod_preview(self, mod_folder: Path) -> Optional[Path]:
        """
        Try to extract a 3D model from a mod's .pak file.
        Returns path to directory with exported mesh, or None.
        """
        assets_dir = mod_folder / "assets"
        if not assets_dir.exists():
            return None

        pak_files = list(assets_dir.glob("*.pak"))
        if not pak_files:
            return None

        # Use first .pak file
        pak = pak_files[0]
        safe_name = pak.stem
        output_dir = CACHE_DIR / f"mod_{safe_name}"

        if output_dir.exists():
            mesh_files = (list(output_dir.rglob("*.gltf")) +
                         list(output_dir.rglob("*.glb")) +
                         list(output_dir.rglob("*.obj")) +
                         list(output_dir.rglob("*.psk")))
            if mesh_files:
                return output_dir

        output_dir.mkdir(parents=True, exist_ok=True)

        if not self.is_available():
            return None

        # Export this specific pak
        cmd = [
            str(UMODEL_EXE),
            "-game=ue4.27",
            f"-path={pak.parent}",
            "-export",
            "-gltf",
            f"-out={output_dir}",
        ]
        if hasattr(self, 'aes_key') and self.aes_key:
            cmd.append(f"-aes={self.aes_key.strip()}")
            
        cmd.append("*")

        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                startupinfo=startupinfo,
            )
        except Exception as e:
            print(f"[ModelViewer] extract failed: {e}")
            return None

        mesh_files = (list(output_dir.rglob("*.gltf")) +
                     list(output_dir.rglob("*.glb")) +
                     list(output_dir.rglob("*.obj")) +
                     list(output_dir.rglob("*.psk")))
        return output_dir if mesh_files else None


# ---------------------------------------------------------------------------
# MeshData — lightweight container for renderable mesh geometry
# ---------------------------------------------------------------------------
class MeshData:
    """Holds vertices, faces, normals, and colors for OpenGL rendering."""
    def __init__(self):
        self.vertices: Optional[np.ndarray] = None   # (N, 3) float32
        self.faces: Optional[np.ndarray] = None       # (M, 3) int32
        self.normals: Optional[np.ndarray] = None     # (N, 3) float32
        self.uvs: Optional[np.ndarray] = None         # (N, 2) float32
        self.colors: Optional[np.ndarray] = None      # (N, 4) float32 RGBA
        self.texture_image = None                     # PIL Image
        self.center: np.ndarray = np.zeros(3, dtype=np.float32)
        self.scale: float = 1.0

    @staticmethod
    def _resolve_umodel_texture(mat_name: str, base_dir: Path):
        if not mat_name or not base_dir: return None
        import re
        try:
            from PIL import Image
        except ImportError:
            return None
            
        props_files = list(base_dir.rglob(f"{mat_name}.props.txt"))
        if not props_files: return None
        
        try:
            content = props_files[0].read_text("utf-8", errors="ignore")

            # The UE .props.txt files include multiple texture params (e.g. ColorTexture, ColorMaskTexture, TextureAO).
            # The previous implementation picked the first texture leaf that wasn't blacklisted by *texture name*,
            # which often selects masks (e.g. `CM`) instead of the actual diffuse/base-color texture.
            #
            # We instead parse (ParameterInfo.Name -> Texture leaf) pairs and prefer ColorTexture / diffuse-like params.
            entries = re.findall(
                r"ParameterInfo\s*=\s*\{\s*Name=([^}\r\n]+)\s*\}\s*[\s\S]*?ParameterValue\s*=\s*Texture2D'[^']*/([^/']+)\.[^']+'\s*",
                content,
                flags=re.S,
            )

            # Keep parameter order as it appears in the file (usually the intended priority order).
            param_tex_list: List[Tuple[str, str]] = [(p.strip(), t.strip()) for (p, t) in entries]

            def open_texture_by_leaf(tex_leaf: str) -> Optional[Image.Image]:
                tex_files = (
                    list(base_dir.rglob(f"{tex_leaf}.tga"))
                    + list(base_dir.rglob(f"{tex_leaf}.png"))
                )
                if tex_files:
                    return Image.open(tex_files[0]).convert("RGBA")
                return None

            # 1) Prefer an explicit color/albedo parameter.
            best_tex: Optional[str] = None
            priority_exact = ["ColorTexture", "BaseColorTexture", "DiffuseTexture", "AlbedoTexture"]
            for want in priority_exact:
                want_l = want.lower()
                for p, t in param_tex_list:
                    if p.lower() == want_l:
                        best_tex = t
                        break
                if best_tex:
                    break

            # 2) If no explicit color param was found, fall back to "Color*" params that aren't masks.
            if not best_tex:
                for p, t in param_tex_list:
                    pl = p.lower()
                    if "color" in pl and "mask" not in pl:
                        best_tex = t
                        break

            # 3) Last resort: old heuristic, but guided by parameter names when possible.
            if not best_tex:
                param_blacklist = ["mask", "ao", "norm", "normal", "spec", "rough", "emiss", "metal", "pbr", "trans"]
                texture_blacklist = ["mask", "ao", "norm", "spec", "rough", "emiss", "pbr", "_n", "_m", "_s", "_e", "_r"]

                if param_tex_list:
                    for p, t in param_tex_list:
                        pl = p.lower()
                        tl = t.lower()
                        if any(bad in pl for bad in param_blacklist):
                            continue
                        if any(bad in tl for bad in texture_blacklist):
                            continue
                        best_tex = t
                        break
                else:
                    # If we couldn't parse param pairs, revert to the original "first non-blacklisted texture leaf" approach.
                    tex_matches = re.findall(r"Texture2D'.*/([^/']+)\.[^']+'", content)
                    texture_blacklist = ["mask", "ao", "norm", "spec", "rough", "emiss", "pbr", "_n", "_m", "_s", "_e", "_r", "_pbr"]
                    for tm in tex_matches:
                        tml = tm.lower()
                        if not any(bad in tml for bad in texture_blacklist):
                            best_tex = tm
                            break

            if best_tex:
                return open_texture_by_leaf(best_tex)
        except Exception as e:
            print(f"[ModelViewer] Error resolving texture {mat_name}: {e}")
        return None

    @staticmethod
    def from_trimesh(mesh, base_dir: Optional[Path] = None) -> "MeshData":
        """Convert a trimesh.Trimesh into MeshData."""
        md = MeshData()
        md.vertices = np.array(mesh.vertices, dtype=np.float32)
        md.faces = np.array(mesh.faces, dtype=np.int32)

        if hasattr(mesh, 'vertex_normals') and mesh.vertex_normals is not None:
            md.normals = np.array(mesh.vertex_normals, dtype=np.float32)
        else:
            md.normals = np.zeros_like(md.vertices)

        if hasattr(mesh.visual, 'uv') and mesh.visual.uv is not None and len(mesh.visual.uv) > 0:
            md.uvs = np.array(mesh.visual.uv, dtype=np.float32)

        try:
            if hasattr(mesh.visual, 'material'):
                mat = mesh.visual.material
                if hasattr(mat, 'image') and mat.image is not None:
                    md.texture_image = mat.image.convert("RGBA")
                elif hasattr(mat, 'baseColorTexture') and mat.baseColorTexture is not None:
                    md.texture_image = mat.baseColorTexture.convert("RGBA")
                    
                # If standard glTF parse fails, try UModel fallback parsing
                if md.texture_image is None and base_dir is not None:
                    mat_name = getattr(mat, 'name', None)
                    md.texture_image = MeshData._resolve_umodel_texture(mat_name, base_dir)
        except Exception:
            pass

        # Use texture-baked colors if possible, else vertex colors, else default gray
        try:
            vc = None
            if hasattr(mesh.visual, 'to_color'):
                color_visuals = mesh.visual.to_color()
                if hasattr(color_visuals, 'vertex_colors'):
                    vc = color_visuals.vertex_colors
            elif hasattr(mesh.visual, 'vertex_colors'):
                vc = mesh.visual.vertex_colors

            if vc is not None and len(vc) > 0:
                vc_np = np.array(vc, dtype=np.float32) / 255.0
                if vc_np.shape[1] == 3:
                    alpha = np.ones((vc_np.shape[0], 1), dtype=np.float32)
                    vc_np = np.hstack([vc_np, alpha])
                md.colors = vc_np
            else:
                md.colors = np.full((len(md.vertices), 4), 0.7, dtype=np.float32)
        except Exception as e:
            print(f"[ModelViewer] Color extraction fallback: {e}")
            md.colors = np.full((len(md.vertices), 4), 0.7, dtype=np.float32)


        # Compute bounding box for centering and scaling
        bbox_min = md.vertices.min(axis=0)
        bbox_max = md.vertices.max(axis=0)
        md.center = (bbox_min + bbox_max) / 2.0
        extent = (bbox_max - bbox_min).max()
        md.scale = 2.0 / extent if extent > 0 else 1.0

        return md

    @staticmethod
    def load_from_directory(directory: Path) -> Optional[List["MeshData"]]:
        """Load a mesh from an export directory (tries glTF, OBJ, etc.)."""
        if not _TRIMESH_AVAILABLE:
            return None

        # Priority: .glb > .gltf > .obj > .psk
        search_order = ["*.glb", "*.gltf", "*.obj", "*.psk"]
        mesh_file = None
        for pattern in search_order:
            found = list(directory.rglob(pattern))
            if found:
                mesh_file = found[0]
                break

        if mesh_file is None:
            return None

        try:
            scene_or_mesh = trimesh.load(str(mesh_file), process=False)
            if isinstance(scene_or_mesh, trimesh.Scene):
                # Return list of all submeshes directly to preserve materials
                meshes = list(scene_or_mesh.geometry.values())
                if not meshes:
                    return None
                return [MeshData.from_trimesh(m, directory) for m in meshes]
            elif isinstance(scene_or_mesh, trimesh.Trimesh):
                return [MeshData.from_trimesh(scene_or_mesh, directory)]
            else:
                return None
        except Exception as e:
            print(f"[ModelViewer] Failed to load mesh: {e}")
            return None


# ---------------------------------------------------------------------------
# ModelViewer — OpenGL viewport embedded in tkinter
# ---------------------------------------------------------------------------
if _GL_AVAILABLE:
    class ModelViewer(OpenGLFrame):
        """
        Interactive OpenGL 3D model viewer that can be embedded in tkinter.
        
        Supports:
        - Left-drag to rotate
        - Scroll to zoom
        - Right-drag to pan
        - Flat/unlit shading for performance
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mesh_data: Optional[List[MeshData]] = None
            self._tex_ids: List[int] = []
            self._rot_x = 0.0
            self._rot_y = 15.0
            self._zoom = 3.0
            self._pan_x = 0.0
            self._pan_y = 0.0
            self._last_x = 0
            self._last_y = 0
            self._dragging = False
            self._panning = False
            self._initialized = False
            self._bg_color = (0.08, 0.08, 0.14, 1.0)  # Dark background matching theme

            # Bind mouse events
            self.bind("<ButtonPress-1>", self._on_left_press)
            self.bind("<ButtonRelease-1>", self._on_left_release)
            self.bind("<B1-Motion>", self._on_left_drag)
            self.bind("<ButtonPress-3>", self._on_right_press)
            self.bind("<ButtonRelease-3>", self._on_right_release)
            self.bind("<B3-Motion>", self._on_right_drag)
            self.bind("<MouseWheel>", self._on_scroll)

        def initgl(self):
            """Called once when the OpenGL context is ready."""
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_COLOR_MATERIAL)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            
            # Enable basic alpha blending/testing for hair textures and decals
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_ALPHA_TEST)
            glAlphaFunc(GL_GREATER, 0.1)

            # Bright directional light to minimize dark shadows (pseudo-unlit)
            glLightfv(GL_LIGHT0, GL_POSITION, [0.0, 0.0, 1.0, 0.0])
            glLightfv(GL_LIGHT0, GL_DIFFUSE, [1.0, 1.0, 1.0, 1.0])
            glLightfv(GL_LIGHT0, GL_AMBIENT, [0.85, 0.85, 0.85, 1.0])
            glLightfv(GL_LIGHT0, GL_SPECULAR, [0.0, 0.0, 0.0, 1.0])

            glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
            glClearColor(*self._bg_color)
            glShadeModel(GL_FLAT)

            self._initialized = True

        def redraw(self):
            """Called on each frame to render the scene."""
            if not self._initialized:
                return

            w = self.winfo_width()
            h = self.winfo_height()
            if w <= 0 or h <= 0:
                return

            glViewport(0, 0, w, h)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

            # Projection
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            aspect = w / h if h > 0 else 1.0
            gluPerspective(45.0, aspect, 0.01, 100.0)

            # Modelview
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glTranslatef(self._pan_x, self._pan_y, -self._zoom)
            glRotatef(self._rot_x, 1.0, 0.0, 0.0)
            glRotatef(self._rot_y, 0.0, 1.0, 0.0)

            if self.mesh_data is not None:
                self._draw_mesh()
            else:
                self._draw_placeholder()

        def _bind_texture(self, image) -> int:
            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            try:
                # Convert PIL Image to raw RGBA bytes for OpenGL
                from PIL import Image
                image = image.transpose(Image.FLIP_TOP_BOTTOM)
                img_data = image.tobytes("raw", "RGBA", 0, -1)
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width, image.height,
                             0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
            except Exception as e:
                print(f"[ModelViewer] Texture bind error: {e}")
            return tex_id

        def _draw_mesh(self):
            """Render the loaded mesh."""
            if not self.mesh_data:
                return

            glPushMatrix()

            # Center and scale based on global bounding box of all submeshes
            import numpy as np
            verts = []
            for md in self.mesh_data:
                verts.append(md.vertices)
            all_verts = np.vstack(verts)
            global_min = all_verts.min(axis=0)
            global_max = all_verts.max(axis=0)
            global_center = (global_min + global_max) / 2.0
            global_extent = (global_max - global_min).max()
            global_scale = 2.0 / global_extent if global_extent > 0 else 1.0

            glScalef(global_scale, global_scale, global_scale)
            glTranslatef(-global_center[0], -global_center[1], -global_center[2])

            if getattr(self, '_display_list', None) is None:
                self._display_list = glGenLists(1)
                glNewList(self._display_list, GL_COMPILE)
                
                for idx, md in enumerate(self.mesh_data):
                    # Lazily generate OpenGL Texture IDs
                    if len(self._tex_ids) <= idx:
                        if md.texture_image is not None:
                            self._tex_ids.append(self._bind_texture(md.texture_image))
                        else:
                            self._tex_ids.append(0)
                    
                    tex_id = self._tex_ids[idx]
                    if tex_id != 0:
                        glEnable(GL_TEXTURE_2D)
                        glBindTexture(GL_TEXTURE_2D, tex_id)
                        glColor4f(1.0, 1.0, 1.0, 1.0) # Base color white for texture multiplier
                    else:
                        glDisable(GL_TEXTURE_2D)

                    glBegin(GL_TRIANGLES)
                    for face in md.faces:
                        for vi in face:
                            if md.normals is not None:
                                glNormal3fv(md.normals[vi])
                            
                            has_tex = (tex_id != 0 and md.uvs is not None and len(md.uvs) > vi)
                            if has_tex:
                                glTexCoord2f(md.uvs[vi][0], md.uvs[vi][1])
                            elif md.colors is not None and len(md.colors) > vi:
                                glColor4fv(md.colors[vi])
                                
                            glVertex3fv(md.vertices[vi])
                    glEnd()

                glDisable(GL_TEXTURE_2D)
                glEndList()

            glCallList(self._display_list)

            glPopMatrix()

        def _draw_placeholder(self):
            """Draw a simple rotating cube as placeholder."""
            glPushMatrix()
            glColor3f(0.15, 0.45, 0.40)

            s = 0.3
            verts = [
                [-s, -s, -s], [s, -s, -s], [s, s, -s], [-s, s, -s],
                [-s, -s, s], [s, -s, s], [s, s, s], [-s, s, s],
            ]
            faces_idx = [
                [0,1,2,3], [4,5,6,7], [0,1,5,4],
                [2,3,7,6], [0,3,7,4], [1,2,6,5],
            ]
            normals = [
                [0,0,-1], [0,0,1], [0,-1,0],
                [0,1,0], [-1,0,0], [1,0,0],
            ]

            glBegin(GL_QUADS)
            for i, face in enumerate(faces_idx):
                glNormal3fv(normals[i])
                for vi in face:
                    glVertex3fv(verts[vi])
            glEnd()

            # Wireframe overlay
            glDisable(GL_LIGHTING)
            glColor3f(0.3, 0.8, 0.7)
            glLineWidth(2.0)
            for face in faces_idx:
                glBegin(GL_LINE_LOOP)
                for vi in face:
                    glVertex3fv(verts[vi])
                glEnd()
            glEnable(GL_LIGHTING)

            glPopMatrix()

        def set_mesh(self, mesh_data: Optional[List[MeshData]]):
            """Set the mesh to display."""
            self.mesh_data = mesh_data
            if getattr(self, '_display_list', None) is not None:
                try:
                    glDeleteLists(self._display_list, 1)
                except Exception:
                    pass
                self._display_list = None
                
            for tid in getattr(self, '_tex_ids', []):
                try:
                    if tid != 0:
                        glDeleteTextures([tid])
                except Exception:
                    pass
            self._tex_ids = []

            if self.mesh_data is not None and len(self.mesh_data) > 0:
                # Reset view to face forward (Unreal Engine models face +X)
                self._rot_x = 0.0
                self._rot_y = 15.0
                self._zoom = 3.0
                self._pan_x = 0.0
                self._pan_y = 0.0

        def clear(self):
            """Clear the displayed model."""
            if getattr(self, '_display_list', None) is not None:
                try:
                    glDeleteLists(self._display_list, 1)
                except Exception:
                    pass
                self._display_list = None
                
            for tid in getattr(self, '_tex_ids', []):
                try:
                    if tid != 0:
                        glDeleteTextures([tid])
                except Exception:
                    pass
            self._tex_ids = []
            self.mesh_data = None

        # --- Mouse interaction ---
        def _on_left_press(self, event):
            self._dragging = True
            self._last_x = event.x
            self._last_y = event.y

        def _on_left_release(self, event):
            self._dragging = False

        def _on_left_drag(self, event):
            if self._dragging:
                dx = event.x - self._last_x
                dy = event.y - self._last_y
                self._rot_y += dx * 0.5
                self._rot_x += dy * 0.5
                self._last_x = event.x
                self._last_y = event.y

        def _on_right_press(self, event):
            self._panning = True
            self._last_x = event.x
            self._last_y = event.y

        def _on_right_release(self, event):
            self._panning = False

        def _on_right_drag(self, event):
            if self._panning:
                dx = event.x - self._last_x
                dy = event.y - self._last_y
                self._pan_x += dx * 0.005
                self._pan_y -= dy * 0.005
                self._last_x = event.x
                self._last_y = event.y

        def _on_scroll(self, event):
            if event.delta > 0:
                self._zoom = max(0.5, self._zoom - 0.3)
            else:
                self._zoom = min(20.0, self._zoom + 0.3)
else:
    # Fallback stub if OpenGL is not available
    class ModelViewer:
        def __init__(self, *args, **kwargs):
            pass


# ---------------------------------------------------------------------------
# PreviewManager — coordinates async extraction + loading
# ---------------------------------------------------------------------------
class PreviewManager:
    """
    High-level manager that handles the full preview pipeline:
    extract → load → display. Runs extraction in a background thread.
    """

    def __init__(self, game_paks_path: str = "", aes_key: str = ""):
        self.extractor = ModelExtractor(game_paks_path, aes_key) if game_paks_path else None
        self.viewer: Optional[ModelViewer] = None
        self._loading = False
        self._on_load_callback = None

    def set_game_path(self, game_paks_path: str, aes_key: str = ""):
        """Update the game path for extraction."""
        self.extractor = ModelExtractor(game_paks_path, aes_key)

    def set_viewer(self, viewer):
        """Attach a ModelViewer widget."""
        self.viewer = viewer

    def can_preview_3d(self) -> bool:
        """Check if 3D preview is possible (all dependencies available)."""
        return (_GL_AVAILABLE and _TRIMESH_AVAILABLE and
                self.extractor is not None and self.extractor.is_available())

    def preview_mod(self, mod_folder: Path, callback=None):
        """
        Start async extraction and loading of a mod's model.
        Calls callback(success: bool) when done.
        """
        if self._loading:
            return

        if not self.can_preview_3d():
            if callback:
                callback(False)
            return

        self._loading = True
        self._on_load_callback = callback

        def _worker():
            try:
                export_dir = self.extractor.extract_mod_preview(mod_folder)
                if export_dir is None:
                    self._loading = False
                    if self._on_load_callback:
                        self._on_load_callback(False)
                    return

                mesh_data = MeshData.load_from_directory(export_dir)
                if mesh_data is None:
                    self._loading = False
                    if self._on_load_callback:
                        self._on_load_callback(False)
                    return

                # Set mesh on viewer (must be done from main thread context)
                if self.viewer is not None:
                    self.viewer.set_mesh(mesh_data)

                self._loading = False
                if self._on_load_callback:
                    self._on_load_callback(True)
            except Exception as e:
                print(f"[PreviewManager] Error: {e}")
                self._loading = False
                if self._on_load_callback:
                    self._on_load_callback(False)

        threading.Thread(target=_worker, daemon=True).start()

    def load_mesh_direct(self, mesh_path: Path) -> bool:
        """Load a mesh file directly (for testing)."""
        if not _TRIMESH_AVAILABLE or self.viewer is None:
            return False
        try:
            mesh_data = MeshData.load_from_directory(mesh_path.parent if mesh_path.is_file() else mesh_path)
            if mesh_data:
                self.viewer.set_mesh(mesh_data)
                return True
        except Exception as e:
            print(f"[PreviewManager] Direct load failed: {e}")
        return False
