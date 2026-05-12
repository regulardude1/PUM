import os
import re
import json
import time
import threading
import concurrent.futures
import requests
import customtkinter as ctk
import webbrowser
from io import BytesIO
from PIL import Image, ImageTk

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

class DiscordAPI:
    def __init__(self):
        self.cache_dir = os.path.abspath("cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.user_data_dir = os.path.join(self.cache_dir, "discord_session")
        self.token = None
        self.headers = {}
        
    def get_token(self):
        token_path = os.path.join(self.cache_dir, "discord_token.txt")
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                self.token = f.read().strip()
            if self.token:
                self.headers = {"Authorization": self.token, "Content-Type": "application/json"}
                # Test the token
                res = requests.get("https://discord.com/api/v9/users/@me", headers=self.headers)
                if res.status_code == 200:
                    return True
        return False
        
    def login_with_playwright(self):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=False,
                    no_viewport=True,
                )
                page = browser.new_page()
                
                # We need to capture the authorization header
                def handle_request(route, request):
                    if 'authorization' in request.headers and not self.token:
                        self.token = request.headers['authorization']
                    route.continue_()
                
                page.route("**/*", handle_request)
                page.goto("https://discord.com/app")
                
                # Wait for user to log in and app to load
                try:
                    page.wait_for_selector("[class^='guilds_']", timeout=120000)
                except Exception as e:
                    print("Playwright wait error:", e)
                
                # Fallback to local storage extraction if header intercept missed it
                if not self.token:
                    try:
                        token_script = """
                        (webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()
                        """
                        self.token = page.evaluate(token_script)
                    except Exception as e:
                        print("Fallback token extraction failed:", e)
                
                browser.close()
                
                if self.token:
                    self.headers = {"Authorization": self.token, "Content-Type": "application/json"}
                    with open(os.path.join(self.cache_dir, "discord_token.txt"), "w", encoding="utf-8") as f:
                        f.write(self.token)
                    return True
                return False
        except Exception as e:
            print("Login exception:", e)
            return False

    def fetch_endeavor_channels(self):
        res = requests.get("https://discord.com/api/v9/users/@me/guilds", headers=self.headers)
        if res.status_code != 200:
            return None
        
        guilds = res.json()
        target_guild = next((g for g in guilds if "Endeavor" in g.get("name", "")), None)
        if not target_guild:
            return None
            
        guild_id = target_guild["id"]
        res = requests.get(f"https://discord.com/api/v9/guilds/{guild_id}/channels", headers=self.headers)
        if res.status_code != 200:
            return None
            
        self.guild_id = guild_id
        channels = res.json()
        
        # Build tag lookup from forum channels
        self._channel_tags = {}  # channel_id -> {tag_id: tag_name}
        self._channels_info = []  # list of {id, name, nsfw}
        
        for c in channels:
            cname = c.get("name", "").lower()
            cid = c["id"]
            if "mod_archive" in cname or cid == "1452026167489200351":
                # Detect NSFW from channel properties OR name
                is_nsfw = c.get("nsfw", False) or "18+" in cname or "nsfw" in cname or "🔞" in c.get("name", "") or cid == "1452026167489200351"
                self._channels_info.append({"id": cid, "name": c["name"], "nsfw": is_nsfw})
                print(f"[Discord] Found channel: {c['name']} (id={cid}, nsfw={is_nsfw}, type={c.get('type')})")
                # Extract available tags from forum channel
                if c.get("available_tags"):
                    tag_map = {}
                    for tag in c["available_tags"]:
                        tag_map[tag["id"]] = tag["name"]
                    self._channel_tags[cid] = tag_map
                    print(f"[Discord] Tags for {c['name']}: {list(tag_map.values())}")
        
        return [c["id"] for c in self._channels_info]
        
    def fetch_threads_from_channel(self, channel_id, known_thread_ids=None):
        threads = []
        known_thread_ids = known_thread_ids or set()
        
        # Simulate Discord by querying exactly what the Discord client queries: threads/search
        for offset in range(0, 5000, 25):  # Fetch up to 5000 threads per channel (essentially removing the cap)
            try:
                url = f"https://discord.com/api/v9/channels/{channel_id}/threads/search?archived=true&archived=false&sort_by=last_message_time&sort_order=desc&limit=25&offset={offset}"
                res = requests.get(url, headers=self.headers, timeout=10)
                
                if res.status_code == 200:
                    batch = res.json().get("threads", [])
                    if not batch:
                        break
                    threads.extend(batch)
                    
                    # Optimization: if we hit threads we already know, we can optionally stop
                    # But to be safe, we'll keep going to ensure we don't miss inserted threads.
                    if len(batch) < 25:
                        break
                elif res.status_code == 429:
                    time.sleep(res.json().get("retry_after", 1))
                    # Retry once
                    res = requests.get(url, headers=self.headers, timeout=10)
                    if res.status_code == 200:
                        batch = res.json().get("threads", [])
                        threads.extend(batch)
                        if len(batch) < 25: break
                    else:
                        break
                else:
                    break
            except Exception:
                break
                    
        # Explicitly set parent_id
        for t in threads:
            t["parent_id"] = channel_id
            
        # Sort combined threads by last_message_id to mimic the search endpoint's behavior perfectly
        threads.sort(key=lambda x: int(x.get("last_message_id") or x.get("id") or 0), reverse=True)
            
        return threads

    def fetch_thread_initial_messages(self, thread_id):
        # Fetch up to 50 messages from the start of the thread
        url = f"https://discord.com/api/v9/channels/{thread_id}/messages?after={int(thread_id)-1}&limit=50"
        for _ in range(3):
            res = requests.get(url, headers=self.headers)
            if res.status_code == 200:
                msgs = res.json()
                if msgs:
                    # Ensure chronological order
                    msgs.sort(key=lambda m: int(m["id"]))
                    return msgs
                return []
            elif res.status_code == 429:
                try:
                    time.sleep(res.json().get("retry_after", 1))
                except:
                    time.sleep(1)
            else:
                break
        return []

    def get_raw_threads_from_channels(self, channel_ids, known_thread_ids=None):
        all_threads = []
        
        if hasattr(self, 'guild_id') and self.guild_id:
            try:
                res = requests.get(f"https://discord.com/api/v9/guilds/{self.guild_id}/threads/active", headers=self.headers, timeout=10)
                if res.status_code == 200:
                    active = res.json().get("threads", [])
                    all_threads.extend([t for t in active if t.get("parent_id") in channel_ids])
            except:
                pass
                
        def _fetch_channel(cid):
            return self.fetch_threads_from_channel(cid, known_thread_ids)
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_cid = {executor.submit(_fetch_channel, cid): cid for cid in channel_ids}
            for future in concurrent.futures.as_completed(future_to_cid):
                try:
                    res = future.result()
                    if res:
                        all_threads.extend(res)
                except Exception:
                    pass
            
        seen = set()
        unique_threads = []
        for t in all_threads:
            if t["id"] not in seen:
                seen.add(t["id"])
                unique_threads.append(t)
        return unique_threads
        
    def process_threads_batch(self, threads_batch):
        results = []
        def process_thread(t):
            try:
                tid = t["id"]
                msgs = self.fetch_thread_initial_messages(tid)
                if not msgs:
                    msg_t = t.get("message")
                    if msg_t: msgs = [msg_t]
                    else: return None
                    
                msg = msgs[0]
                op_id = t.get("owner_id") or msg.get("author", {}).get("id")
                    
                thumbnail = None
                for m in msgs:
                    if m.get("author", {}).get("id") != op_id: continue
                    if m.get("attachments"):
                        for att in m["attachments"]:
                            ctype = att.get("content_type", "")
                            fname = att.get("filename", "").lower()
                            if ctype.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                                thumbnail = att["url"]
                                break
                    if thumbnail: break

                if not thumbnail:
                    for m in msgs:
                        if m.get("author", {}).get("id") != op_id: continue
                        if m.get("attachments"):
                            for att in m["attachments"]:
                                ctype = att.get("content_type", "")
                                fname = att.get("filename", "").lower()
                                if ctype.startswith("video/") or fname.endswith((".mp4", ".webm", ".mov")):
                                    proxy = att.get("proxy_url") or att.get("url")
                                    if proxy:
                                        thumbnail = proxy + ("&format=jpeg" if "?" in proxy else "?format=jpeg")
                                        break
                        if thumbnail: break

                if not thumbnail:
                    for m in msgs:
                        if m.get("author", {}).get("id") != op_id: continue
                        if m.get("embeds"):
                            for embed in m["embeds"]:
                                if embed.get("image"):
                                    thumbnail = embed["image"]["url"]; break
                                elif embed.get("thumbnail"):
                                    thumbnail = embed["thumbnail"]["url"]; break
                                elif embed.get("video") and embed.get("video", {}).get("proxy_url"):
                                    proxy = embed["video"]["proxy_url"]
                                    thumbnail = proxy + ("&format=jpeg" if "?" in proxy else "?format=jpeg")
                                    break
                        if thumbnail: break
                
                if not thumbnail:
                    for m in msgs:
                        if m.get("author", {}).get("id") != op_id: continue
                        content = m.get("content", "")
                        # Try to find raw image links in text
                        match = re.search(r'(https?://\S+\.(?:png|jpg|jpeg|webp|gif))', content, re.I)
                        if match:
                            thumbnail = match.group(1); break
                        # Imgur fallback
                        match = re.search(r'https?://(?:i\.)?imgur\.com/([a-zA-Z0-9]+)', content)
                        if match:
                            thumbnail = f"https://i.imgur.com/{match.group(1)}.jpg"; break
                            
                # Ultimate fallback: check ANY of the first 5 messages regardless of author
                # (Fixes "Deleted User" / Webhook / Admin forwarded posts)
                if not thumbnail:
                    for m in msgs[:5]:
                        for att in m.get("attachments", []):
                            ctype = att.get("content_type", "")
                            fname = att.get("filename", "").lower()
                            if ctype.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                                thumbnail = att["url"]; break
                            elif ctype.startswith("video/") or fname.endswith((".mp4", ".webm", ".mov")):
                                proxy = att.get("proxy_url") or att.get("url")
                                if proxy: thumbnail = proxy + ("&format=jpeg" if "?" in proxy else "?format=jpeg"); break
                        if thumbnail: break
                        
                        for embed in m.get("embeds", []):
                            if embed.get("image"): thumbnail = embed["image"]["url"]; break
                            elif embed.get("thumbnail"): thumbnail = embed["thumbnail"]["url"]; break
                        if thumbnail: break
                
                download_links = []
                seen_links = set()
                
                def add_link(l):
                    if l not in seen_links:
                        seen_links.add(l)
                        download_links.append(l)

                for m in msgs:
                    is_op = (m.get("author", {}).get("id") == op_id)
                    content = m.get("content", "")
                    
                    # Extract URLs from text
                    raw_urls = re.findall(r'(https?://[^\s>]+)', content)
                    for u in raw_urls:
                        u = u.rstrip(')*]\'",.')
                        is_archive = u.lower().split('?')[0].endswith(('.pak', '.zip', '.rar', '.7z'))
                        is_host = any(h in u.lower() for h in ["drive.google.com", "mega.nz", "mediafire.com", "dropbox.com", "pixeldrain.com", "gofile.io"])
                        
                        if is_archive:
                            add_link(u)
                        elif is_op and is_host:
                            add_link(u)
                            
                    # Extract from attachments
                    if m.get("attachments"):
                        for att in m["attachments"]:
                            url = att["url"]
                            is_archive = url.lower().split('?')[0].endswith(('.pak', '.zip', '.rar', '.7z'))
                            
                            if is_archive:
                                add_link(url)
                                
                # We no longer drop threads without download links. 
                # This allows users to still view WIPs or mods with unparseable links.
                    
                # Resolve tags from the thread's applied_tags
                tag_names = []
                if t.get("applied_tags") and t.get("parent_id"):
                    tag_map = self._channel_tags.get(t["parent_id"], {})
                    for tag_id in t["applied_tags"]:
                        name = tag_map.get(tag_id, "")
                        if name:
                            tag_names.append(name)
                
                return {
                    "id": tid,
                    "title": t.get("name", "Unknown Mod"),
                    "thumbnail": thumbnail,
                    "links": download_links,
                    "author": msg.get("author", {}).get("username", "Unknown"),
                    "content": msg.get("content", ""),
                    "tags": tag_names,
                    "channel_id": t.get("parent_id", ""),
                }
            except Exception as e:
                print(f"Error processing thread: {e}")
                return None
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_thread = {executor.submit(process_thread, t): t for t in threads_batch}
            for future in concurrent.futures.as_completed(future_to_thread):
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                except Exception as e:
                    print(f"Thread pool error: {e}")
                    
        return results

class ImageCache:
    def __init__(self):
        self.cache = {}
        self.raw_images = {}
        self.img_dir = os.path.abspath("cache/images")
        os.makedirs(self.img_dir, exist_ok=True)
        
    def get_disk_path(self, url):
        import hashlib
        base_url = url.split("?")[0]
        h = hashlib.md5(base_url.encode()).hexdigest()
        return os.path.join(self.img_dir, f"{h}.png")
        
    def load_image(self, url, size=(200, 150)):
        cache_key = f"{url}_{size[0]}x{size[1]}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        try:
            disk_path = self.get_disk_path(url)
            if url in self.raw_images:
                img = self.raw_images[url].copy()
            elif os.path.exists(disk_path):
                img = Image.open(disk_path)
                self.raw_images[url] = img.copy()
            else:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                response = requests.get(url, headers=headers, timeout=5)
                img = Image.open(BytesIO(response.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(disk_path, "PNG")
                self.raw_images[url] = img.copy()
            
            # Crop to preserve aspect ratio
            target_ratio = size[0] / size[1]
            img_ratio = img.width / img.height
            if img_ratio > target_ratio:
                new_width = int(target_ratio * img.height)
                offset = (img.width - new_width) / 2
                img = img.crop((offset, 0, img.width - offset, img.height))
            elif img_ratio < target_ratio:
                new_height = int(img.width / target_ratio)
                offset = (img.height - new_height) / 2
                img = img.crop((0, offset, img.width, img.height - offset))
                
            img = img.resize(size, Image.Resampling.LANCZOS)
            self.cache[cache_key] = img
            return img
        except Exception as e:
            print(f"Failed to load image {url}: {e}")
            return None

class DiscordStorePanel(ctk.CTkFrame):
    def __init__(self, master, accent_color="#1a9f84", **kwargs):
        super().__init__(master, **kwargs)
        self.accent_color = accent_color
        self.api = DiscordAPI()
        self.image_cache = ImageCache()
        self.mods = []
        self._visible_widgets = {}  # row_idx -> list of (canvas_id, tk_frame)
        self._scroll_debounce = None
        self._deep_search_timer = None
        self._is_rendering = False

        self.grid_rowconfigure(0, weight=0)  # header
        self.grid_rowconfigure(1, weight=0)  # search + filters
        self.grid_rowconfigure(2, weight=1)  # canvas
        self.grid_rowconfigure(3, weight=0)  # footer
        self.grid_columnconfigure(0, weight=1)

        # ── Header ──
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(hdr, text="Endeavor Headquarters - Mod Archive", font=("Segoe UI", 18, "bold")).pack(side="left", padx=10)
        # Profile area (avatar + name shown after login)
        self._profile_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        self._profile_frame.pack(side="right", padx=10)
        self._avatar_label = None
        self._username_label = None
        self.login_btn = ctk.CTkButton(self._profile_frame, text="Login to Discord", fg_color=accent_color, command=self.do_login)
        self.login_btn.pack(side="left", padx=(0, 4))
        self.refresh_btn = ctk.CTkButton(hdr, text="Refresh", fg_color=accent_color, command=lambda: self.load_mods(use_cache=False))
        self.refresh_btn.pack(side="right", padx=10)

        self.settings_file = os.path.join(self.api.cache_dir, "discord_settings.json")
        self.card_width = 280
        # Display settings — which fields to show on cards
        self._display_settings = {
            "show_author": True,
            "show_version": False,
            "show_category": True,
            "show_character": True,
        }
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file) as f:
                    saved = json.load(f)
                    self.card_width = saved.get("card_width", 280)
                    for k in self._display_settings:
                        if k in saved:
                            self._display_settings[k] = saved[k]
        except: pass
        self.size_slider = ctk.CTkSlider(hdr, from_=150, to=700, command=self._on_size_change)
        self.size_slider.set(self.card_width)
        self.size_slider.pack(side="right", padx=10)
        # Deferred set to ensure visual position matches saved value after widget renders
        _saved_cw = self.card_width
        self.after(100, lambda: self.size_slider.set(_saved_cw))
        ctk.CTkLabel(hdr, text="Card Size:", font=("Segoe UI", 12)).pack(side="right", padx=(10,0))

        # ── Search & Filter Bar ──
        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
        filter_frame.grid_columnconfigure(1, weight=1)

        # Channel tabs (SFW / NSFW)
        self._channel_tab_frame = ctk.CTkFrame(filter_frame, fg_color="transparent")
        self._channel_tab_frame.grid(row=0, column=0, sticky="w", padx=(10, 5))
        self._active_channel = "sfw"  # "sfw" or "nsfw"
        self._channel_btns = {}
        
        sfw_btn = ctk.CTkButton(self._channel_tab_frame, text="Mod Archive", width=110, height=30,
                                fg_color="#5865F2", hover_color="#4752C4", font=("Segoe UI", 12, "bold"),
                                command=lambda: self._switch_channel("sfw"))
        sfw_btn.pack(side="left", padx=(0, 4))
        self._channel_btns["sfw"] = sfw_btn
        
        nsfw_btn = ctk.CTkButton(self._channel_tab_frame, text="🔞 18+ Archive", width=120, height=30,
                                 fg_color="#36393f", hover_color="#4752C4", font=("Segoe UI", 12, "bold"),
                                 command=lambda: self._switch_channel("nsfw"))
        nsfw_btn.pack(side="left", padx=(0, 4))
        self._channel_btns["nsfw"] = nsfw_btn
        # Hide NSFW button initially; shown only after channels are discovered
        nsfw_btn.pack_forget()
        self._nsfw_available = False

        # Search entry with label
        search_inner = ctk.CTkFrame(filter_frame, fg_color="transparent")
        search_inner.grid(row=0, column=1, sticky="ew", padx=5)
        search_inner.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(search_inner, text="🔍 Search:", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, padx=(5, 6))
        self._search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            search_inner, placeholder_text="Filter by name or author...",
            textvariable=self._search_var, height=32,
            fg_color="#0e1525", border_width=1, border_color="#2a2a4a",
            font=("Segoe UI", 13), corner_radius=8,
        )
        self._search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        self._search_timer = None
        self._search_var.trace_add("write", self._on_search_changed)
        self._all_mods = []  # unfiltered full list

        # ⚙ Settings button next to search
        self._settings_btn = ctk.CTkButton(
            search_inner, text="⚙", width=34, height=32,
            fg_color="#36393f", hover_color="#4752C4",
            font=("Segoe UI", 16), corner_radius=8,
            command=self._show_display_settings,
        )
        self._settings_btn.grid(row=0, column=2, padx=(0, 4))

        # Tag filter buttons row
        self._tag_frame = ctk.CTkFrame(filter_frame, fg_color="transparent")
        self._tag_frame.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 2))
        self._active_tags = set()
        self._tag_buttons = {}
        # Tags will be populated dynamically after channel data is fetched

        # ── Main content area: sidebar + canvas ──
        self._content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._content_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self._content_frame.grid_rowconfigure(0, weight=1)
        self._content_frame.grid_columnconfigure(0, weight=0)  # sidebar
        self._content_frame.grid_columnconfigure(1, weight=1)  # canvas

        # ── Category Sidebar (Folder Tree) ──
        self._sidebar = ctk.CTkFrame(self._content_frame, fg_color="#1a1a2e", width=170, corner_radius=8)
        self._sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        self._sidebar.grid_propagate(False)
        self._sidebar_category = "All"  # active category filter from sidebar
        self._sidebar_buttons = {}

        sidebar_title = ctk.CTkLabel(self._sidebar, text="📂 Categories", font=("Segoe UI", 13, "bold"), anchor="w")
        sidebar_title.pack(fill="x", padx=10, pady=(10, 6))

        self._sidebar_scroll = ctk.CTkScrollableFrame(self._sidebar, fg_color="transparent")
        self._sidebar_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        # Default categories — will be expanded when tags are discovered
        self._rebuild_sidebar(["All", "Skin", "Commission", "Interface", "VFX", "Animation", "Audio", "Environment", "Misc"])

        # ── Virtual-scroll canvas (lightweight) ──
        self._canvas_frame = ctk.CTkFrame(self._content_frame, fg_color="#1e1e2e")
        self._canvas_frame.grid(row=0, column=1, sticky="nsew")
        self._canvas_frame.grid_rowconfigure(0, weight=1)
        self._canvas_frame.grid_columnconfigure(0, weight=1)

        import tkinter as tk
        self._canvas = tk.Canvas(self._canvas_frame, bg="#1e1e2e", highlightthickness=0, bd=0)
        self._scrollbar = tk.Scrollbar(self._canvas_frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")

        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas_frame.bind("<MouseWheel>", self._on_mousewheel)

        # ── Footer ──
        ftr = ctk.CTkFrame(self, fg_color="transparent")
        ftr.grid(row=3, column=0, sticky="ew", padx=10, pady=(0,10))
        self.counter_label = ctk.CTkLabel(ftr, text="Discord Mods Loaded: 0", font=("Segoe UI", 12))
        self.counter_label.pack(side="right", padx=10)

        self._columns = 1
        self._row_height = 0
        self._resize_timer = None

        if self.api.get_token():
            self.login_btn.configure(text="Logged In", state="disabled")
            self._fetch_user_profile()
            self.load_mods()

    # ── Scrolling ──
    def _on_mousewheel(self, event):
        try:
            self._canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            self._schedule_render()
        except: pass

    def _schedule_render(self):
        if self._scroll_debounce:
            self.after_cancel(self._scroll_debounce)
        self._scroll_debounce = self.after(80, self._render_visible)

    # ── Search & Filtering ──
    def _on_search_changed(self, *args):
        if self._search_timer:
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(250, self._apply_filters)

    def _apply_filters(self):
        """Apply combined search text + tag + channel filter."""
        term = self._search_var.get().strip().lower()
        filtered = list(self._all_mods)
        
        # Channel filter (ignored if the user is explicitly searching by text)
        sfw_ids = getattr(self, '_sfw_channel_ids', set())
        nsfw_ids = getattr(self, '_nsfw_channel_ids', set())
        if (sfw_ids or nsfw_ids) and not term:
            if self._active_channel == "sfw":
                # Show mods from SFW channel OR mods with no channel_id (legacy cache)
                filtered = [m for m in filtered if m.get("channel_id", "") in sfw_ids or not m.get("channel_id")]
            elif self._active_channel == "nsfw":
                filtered = [m for m in filtered if m.get("channel_id", "") in nsfw_ids]
        
        # Sidebar category filter
        sidebar_cat = getattr(self, '_sidebar_category', 'All')
        if sidebar_cat != 'All' and not term:
            def match_sidebar(m):
                mod_tags = [t.lower() for t in m.get("tags", [])]
                if sidebar_cat.lower() in mod_tags:
                    return True
                # Fallback: match against title/content
                text = f"{m.get('title', '')} {m.get('content', '')}".lower()
                return sidebar_cat.lower() in text
            filtered = [m for m in filtered if match_sidebar(m)]
        
        # Tag filter (from top tag buttons)
        if self._active_tags:
            def has_tag(m):
                mod_tags = set(t.lower() for t in m.get("tags", []))
                if mod_tags:
                    return any(t.lower() in mod_tags for t in self._active_tags)
                # Fallback: match tag name against title/content
                text = f"{m.get('title', '')} {m.get('content', '')}".lower()
                return any(t.lower() in text for t in self._active_tags)
            filtered = [m for m in filtered if has_tag(m)]
        
        # Text search
        if term:
            filtered = [m for m in filtered if term in m.get("title", "").lower() or term in m.get("author", "").lower()]
        
        self.mods = filtered
        self._destroy_all_visible()
        self._calc_layout()
        self._render_visible()
        
        # --- Native API Deep Search (debounced to prevent UI flooding) ---
        if term:
            if self._deep_search_timer:
                self.after_cancel(self._deep_search_timer)
            self._deep_search_timer = self.after(800, lambda t=term: self._start_deep_search(t))

    def _toggle_tag(self, tag_name):
        """Toggle a tag filter on/off."""
        if tag_name in self._active_tags:
            self._active_tags.discard(tag_name)
        else:
            self._active_tags.add(tag_name)
        
        # Update button colors
        for name, btn in self._tag_buttons.items():
            if name in self._active_tags:
                btn.configure(fg_color="#5865F2")
            else:
                btn.configure(fg_color="#36393f")
        
        self._apply_filters()

    def _clear_tags(self):
        """Clear all tag filters."""
        self._active_tags.clear()
        for btn in self._tag_buttons.values():
            btn.configure(fg_color="#36393f")
        self._apply_filters()

    def _start_deep_search(self, term):
        """Run deep search in background thread (debounced from _apply_filters)."""
        def _native_search():
            if getattr(self, "_is_deep_searching", False):
                return
            self._is_deep_searching = True
            try:
                import urllib.parse
                c_ids = getattr(self, '_sfw_channel_ids', set()) if self._active_channel == "sfw" else getattr(self, '_nsfw_channel_ids', set())
                found_threads = []
                safe_term = urllib.parse.quote(term)
                for cid in c_ids:
                    url = f"https://discord.com/api/v9/channels/{cid}/threads/search?name={safe_term}"
                    res = requests.get(url, headers=self.api.headers, timeout=5)
                    if res.status_code == 200:
                        batch = res.json().get("threads", [])
                        for t in batch:
                            t["parent_id"] = cid
                        found_threads.extend(batch)
                unscraped = [t for t in found_threads if t["id"] not in getattr(self, "extracted_mods_map", {})]
                if unscraped:
                    extracted = self.api.process_threads_batch(unscraped)
                    ex_ids = {m["id"] for m in extracted if m}
                    for m in extracted:
                        if m:
                            self.extracted_mods_map[m["id"]] = m
                    for t in unscraped:
                        if t["id"] not in ex_ids:
                            self.extracted_mods_map[t["id"]] = None
                    final = [m for m in extracted if m is not None]
                    if final:
                        self.after(0, lambda: self._append_deep_search_mods(final))
            finally:
                self._is_deep_searching = False
        threading.Thread(target=_native_search, daemon=True).start()

    # ── Settings Persistence ──
    def _save_settings(self):
        """Save all display and card settings to disk."""
        try:
            data = {"card_width": self.card_width}
            data.update(self._display_settings)
            with open(self.settings_file, "w") as f:
                json.dump(data, f)
        except: pass

    # ── Display Settings Popup ──
    def _show_display_settings(self):
        """Show a popup with checkboxes to toggle card info field visibility."""
        popup = ctk.CTkToplevel(self)
        popup.title("Card Display Settings")
        popup.geometry("280x280")
        popup.transient(self.winfo_toplevel())
        popup.attributes("-topmost", True)
        popup.after(100, lambda: popup.attributes("-topmost", False))
        popup.configure(fg_color="#1e1e2e")

        ctk.CTkLabel(popup, text="⚙ Card Info Fields", font=("Segoe UI", 15, "bold")).pack(pady=(15, 10))
        ctk.CTkLabel(popup, text="Choose which details to show on mod cards:", 
                     font=("Segoe UI", 11), text_color="#b5bac1").pack(padx=15, pady=(0, 8))

        fields = [
            ("show_author", "Author"),
            ("show_category", "Category Tags"),
            ("show_character", "Character Tags"),
            ("show_version", "Version"),
        ]
        vars_ = {}
        for key, label in fields:
            var = ctk.BooleanVar(value=self._display_settings.get(key, True))
            vars_[key] = var
            cb = ctk.CTkCheckBox(popup, text=label, variable=var,
                                 font=("Segoe UI", 12), fg_color="#5865F2",
                                 hover_color="#4752C4", corner_radius=4)
            cb.pack(anchor="w", padx=25, pady=4)

        def _apply():
            for key, var in vars_.items():
                self._display_settings[key] = var.get()
            self._save_settings()
            self._full_refresh()
            popup.destroy()

        ctk.CTkButton(popup, text="Apply", fg_color="#5865F2", hover_color="#4752C4",
                      height=34, font=("Segoe UI", 13, "bold"), command=_apply).pack(pady=(12, 10))

    # ── Category Sidebar ──
    def _rebuild_sidebar(self, categories):
        """Rebuild the sidebar folder tree with given category names."""
        for w in self._sidebar_scroll.winfo_children():
            w.destroy()
        self._sidebar_buttons.clear()

        for cat in categories:
            is_active = cat == self._sidebar_category
            icon = "📁" if cat != "All" else "📂"
            btn = ctk.CTkButton(
                self._sidebar_scroll,
                text=f" {icon} {cat}",
                anchor="w",
                height=30,
                fg_color="#5865F2" if is_active else "transparent",
                hover_color="#4752C4",
                font=("Segoe UI", 11),
                text_color="white",
                corner_radius=6,
                command=lambda c=cat: self._select_sidebar_category(c),
            )
            btn.pack(fill="x", padx=4, pady=1)
            self._sidebar_buttons[cat] = btn

    def _select_sidebar_category(self, category):
        """Handle sidebar category selection."""
        self._sidebar_category = category
        # Update button styles
        for cat, btn in self._sidebar_buttons.items():
            if cat == category:
                btn.configure(fg_color="#5865F2")
            else:
                btn.configure(fg_color="transparent")
        # Reset scroll and apply filters
        if hasattr(self, "_canvas"):
            self._canvas.yview_moveto(0)
        self._apply_filters()

    # ── Profile System ──
    def _fetch_user_profile(self):
        """Fetch Discord user profile info (avatar + username) in background."""
        def _fetch():
            try:
                res = requests.get("https://discord.com/api/v9/users/@me", headers=self.api.headers, timeout=5)
                if res.status_code == 200:
                    user = res.json()
                    username = user.get("global_name") or user.get("username", "User")
                    avatar_hash = user.get("avatar")
                    user_id = user.get("id")
                    avatar_url = None
                    if avatar_hash and user_id:
                        ext = "gif" if avatar_hash.startswith("a_") else "png"
                        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=64"
                    self.after(0, lambda: self._show_user_profile(username, avatar_url))
            except: pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _show_user_profile(self, username, avatar_url):
        """Display user avatar and name in the header profile area."""
        # Remove login button text, show profile info
        self.login_btn.configure(text="✓", width=28, state="disabled", fg_color="#2d7d46")

        if self._username_label:
            self._username_label.destroy()
        self._username_label = ctk.CTkLabel(
            self._profile_frame, text=username, 
            font=("Segoe UI", 12, "bold"), text_color="#e0e0e0"
        )
        self._username_label.pack(side="left", padx=(0, 6))

        # Load avatar in background
        if avatar_url:
            def _load_avatar():
                try:
                    pil_img = self.image_cache.load_image(avatar_url, size=(32, 32))
                    if pil_img:
                        def _apply():
                            try:
                                tk_img = ImageTk.PhotoImage(pil_img)
                                if not hasattr(self, "_tk_images"):
                                    self._tk_images = []
                                self._tk_images.append(tk_img)
                                if self._avatar_label:
                                    self._avatar_label.destroy()
                                import tkinter as tk
                                self._avatar_label = tk.Label(self._profile_frame, image=tk_img, 
                                                              bg=self._profile_frame.cget("fg_color") if isinstance(self._profile_frame.cget("fg_color"), str) else "#2b2d31",
                                                              bd=0)
                                self._avatar_label.image = tk_img
                                self._avatar_label.pack(side="left", padx=(0, 4))
                                # Re-pack username after avatar so avatar appears first
                                self._username_label.pack_forget()
                                self._username_label.pack(side="left", padx=(0, 6))
                            except: pass
                        self.after(0, _apply)
                except: pass
            threading.Thread(target=_load_avatar, daemon=True).start()

    def _populate_tags(self, tags_set):
        """Populate tag filter buttons from discovered tags."""
        # Clear existing
        for w in self._tag_frame.winfo_children():
            w.destroy()
        self._tag_buttons.clear()
        
        if not tags_set:
            return
        
        # Sort tags with type tags first, then character tags
        type_tags = ["Commission", "Skin", "Interface", "VFX", "Animation", "Audio", "Environment", "Misc"]
        sorted_tags = []
        for t in type_tags:
            if t in tags_set:
                sorted_tags.append(t)
        for t in sorted(tags_set):
            if t not in sorted_tags:
                sorted_tags.append(t)
        
        for tag in sorted_tags:
            btn = ctk.CTkButton(self._tag_frame, text=tag, width=len(tag)*9+20, height=26,
                                fg_color="#36393f", hover_color="#4752C4",
                                font=("Segoe UI", 11), corner_radius=13,
                                command=lambda t=tag: self._toggle_tag(t))
            btn.pack(side="left", padx=2, pady=2)
            self._tag_buttons[tag] = btn
        
        # Clear all button
        clear_btn = ctk.CTkButton(self._tag_frame, text="✕ Clear", width=65, height=26,
                                  fg_color="transparent", hover_color="#4752C4",
                                  font=("Segoe UI", 11), corner_radius=13,
                                  text_color="#b5bac1", border_width=1, border_color="#3a3a5a",
                                  command=self._clear_tags)
        clear_btn.pack(side="left", padx=(8, 2), pady=2)

        # Also rebuild the sidebar with discovered categories
        sidebar_cats = ["All"]
        for t in sorted_tags:
            if t not in sidebar_cats:
                sidebar_cats.append(t)
        self._rebuild_sidebar(sidebar_cats)

    def _switch_channel(self, channel_type):
        """Switch between SFW and NSFW channel views."""
        if self._active_channel == channel_type:
            return
        self._active_channel = channel_type
        
        # Update tab button styles
        for key, btn in self._channel_btns.items():
            if key == channel_type:
                btn.configure(fg_color="#5865F2")
            else:
                btn.configure(fg_color="#36393f")
        
        # Reset scroll position when switching tabs to prevent being stranded out-of-bounds
        if hasattr(self, "_canvas"):
            self._canvas.yview_moveto(0)
            
        # Apply filters to show mods for the selected channel
        self._apply_filters()
        
        # If no mods visible and we have threads, load next page for this channel
        if len(self.mods) == 0 and self.all_threads:
            self.load_next_page()

    # ── Layout math ──
    def _calc_layout(self):
        cw = self._canvas.winfo_width()
        if cw < 100: cw = 800
        self._columns = max(1, cw // (self.card_width + 20))
        self._row_height = int(self.card_width * 1.35) + 20  # taller cards to fit text
        total_rows = (len(self.mods) + self._columns - 1) // self._columns if self.mods else 1
        total_h = total_rows * self._row_height + 80
        self._canvas.configure(scrollregion=(0, 0, cw, total_h))
        
        # Prevent being stranded out of bounds if the content shrank
        y_top = self._canvas.canvasy(0)
        if y_top > total_h and total_h > 0:
            self._canvas.yview_moveto(0)

    def _on_canvas_resize(self, event=None):
        if self._resize_timer:
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(300, self._full_refresh)

    def _full_refresh(self):
        self._destroy_all_visible()
        self._calc_layout()
        self._render_visible()

    def _on_size_change(self, val):
        self.card_width = int(val)
        self._save_settings()
        self._full_refresh()

    # ── Virtualized rendering ──
    def _render_visible(self):
        if getattr(self, '_is_rendering', False):
            return
        self._is_rendering = True
        try:
            self._render_visible_impl()
        finally:
            self._is_rendering = False

    def _render_visible_impl(self):
        if not self.mods:
            return
        
        # Always recalculate layout to stay in sync
        self._calc_layout()
        
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw < 50 or ch < 50 or self._row_height < 1:
            return

        # Figure out which pixel range is visible
        y_top = self._canvas.canvasy(0)
        y_bot = y_top + ch

        first_row = max(0, int(y_top / self._row_height) - 1)
        last_row = int(y_bot / self._row_height) + 1
        total_rows = (len(self.mods) + self._columns - 1) // self._columns

        needed = set(range(first_row, min(last_row + 1, total_rows)))

        # Destroy rows no longer visible
        for rid in list(self._visible_widgets.keys()):
            if rid not in needed:
                for item_id in self._visible_widgets[rid]:
                    self._canvas.delete(item_id)
                del self._visible_widgets[rid]

        # Create rows now visible
        card_w = self.card_width
        card_h = int(card_w * 1.35)
        pad = 10
        total_grid_w = self._columns * (card_w + pad)
        x_offset = max(0, (cw - total_grid_w) // 2)
        
        try:
            drawn_count = 0
            for rid in needed:
                if rid in self._visible_widgets:
                    continue
                items = []
                for c in range(self._columns):
                    idx = rid * self._columns + c
                    if idx >= len(self.mods):
                        break
                    mod = self.mods[idx]
                    x = x_offset + c * (card_w + pad) + pad // 2
                    y = rid * self._row_height + pad
                    try:
                        item_ids = self._draw_card_widget(x, y, mod, card_w, card_h)
                        items.extend(item_ids)
                        drawn_count += 1
                    except Exception as draw_err:
                        print(f"Error drawing card {idx}: {draw_err}")
                self._visible_widgets[rid] = items
            
            # If nothing was drawn but we have mods, put a warning message on the canvas
            drawn_any = any(len(v) > 0 for v in self._visible_widgets.values())
            if not drawn_any and self.mods:
                print(f"[Debug] _render_visible ran but drew 0 cards!")
                self._canvas.create_text(cw/2, y_top + ch/2, text=f"Error: {len(self.mods)} mods loaded but failed to render.", fill="red", font=("Segoe UI", 16))
                
        except Exception as e:
            print(f"Error in _render_visible: {e}")
            
        # Bottom Indicator
        if hasattr(self, "_bottom_indicator_id") and self._bottom_indicator_id:
            try: self._canvas.delete(self._bottom_indicator_id)
            except: pass
            
        channel_type = getattr(self, '_active_channel', 'sfw')
        if channel_type == "nsfw":
            nsfw_ids = getattr(self, '_nsfw_channel_ids', set())
            relevant_threads = [t for t in getattr(self, "all_threads", []) if t.get("parent_id") in nsfw_ids]
        else:
            sfw_ids = getattr(self, '_sfw_channel_ids', set())
            relevant_threads = [t for t in getattr(self, "all_threads", []) if t.get("parent_id") in sfw_ids or not t.get("parent_id")]

        current_page = getattr(self, 'current_pages', {}).get(channel_type, 0)
        has_more = current_page * self.page_size < len(relevant_threads)
        
        indicator_y = total_rows * self._row_height + 40
        indicator_text = "⏳ Keep scrolling... (Loading more)" if getattr(self, "is_loading_more", False) or has_more else "End of Page"
        self._bottom_indicator_id = self._canvas.create_text(cw/2, indicator_y, text=indicator_text, fill="#b5bac1", font=("Segoe UI", 14))

        # Auto-load more when near bottom
        if not getattr(self, "is_loading_more", False) and has_more:
            if last_row >= total_rows - 2:
                self.load_next_page()

    def _destroy_all_visible(self):
        for rid in list(self._visible_widgets.keys()):
            for item_id in self._visible_widgets[rid]:
                self._canvas.delete(item_id)
        self._visible_widgets.clear()
        # Free image references to prevent memory bloat
        if hasattr(self, '_tk_images'):
            self._tk_images.clear()

    def _draw_card_widget(self, x, y, mod, card_w, card_h):
        items = []
        
        # Card background
        bg = self._canvas.create_rectangle(x, y, x + card_w, y + card_h, fill="#2b2d31", outline="")
        items.append(bg)

        title_size = max(10, int(card_w / 22))
        author_size = max(8, int(card_w / 30))
        btn_size = max(9, int(card_w / 26))
        btn_h = max(28, int(card_w / 8))

        img_w = card_w - 20
        img_h = int(img_w * 0.6)

        # Image placeholder text
        img_text = self._canvas.create_text(x + card_w/2, y + 10 + img_h/2, text="Loading...", fill="#888", font=("Segoe UI", author_size))
        items.append(img_text)

        img_id = self._canvas.create_image(x + 10, y + 10, anchor="nw")
        items.append(img_id)

        cache_key = f"{mod.get('thumbnail','')}_{img_w}x{img_h}"
        if cache_key in self.image_cache.cache:
            pil_img = self.image_cache.cache[cache_key]
            tk_img = ImageTk.PhotoImage(pil_img)
            if not hasattr(self, "_tk_images"):
                self._tk_images = []
            self._tk_images.append(tk_img)
            
            self._canvas.itemconfig(img_id, image=tk_img)
            self._canvas.delete(img_text)
        elif mod.get("thumbnail"):
            def _load(cid=img_id, tid=img_text, url=mod["thumbnail"], w=img_w, h=img_h):
                pil_img = self.image_cache.load_image(url, size=(w, h))
                if pil_img:
                    def _apply():
                        try:
                            # Must create PhotoImage in the main thread
                            tk_img = ImageTk.PhotoImage(pil_img)
                            # Keep a strong reference so it isn't garbage collected
                            if not hasattr(self, "_tk_images"):
                                self._tk_images = []
                            self._tk_images.append(tk_img)
                            
                            if self._canvas.type(cid):
                                self._canvas.itemconfig(cid, image=tk_img)
                                self._canvas.delete(tid)
                        except: pass
                    self.after(0, _apply)
                else:
                    def _fail():
                        try:
                            if self._canvas.type(tid):
                                self._canvas.itemconfig(tid, text="No Image")
                        except: pass
                    self.after(0, _fail)
            threading.Thread(target=_load, daemon=True).start()
        else:
            self._canvas.itemconfig(img_text, text="No Image")

        # Title
        title_y = y + 10 + img_h + 10
        title = self._canvas.create_text(x + 10, title_y, text=mod["title"], fill="white", 
                                         font=("Segoe UI", title_size, "bold"), anchor="nw", width=card_w - 20)
        items.append(title)

        # Dynamic info lines based on display settings
        last_bbox = self._canvas.bbox(title)
        next_y = last_bbox[3] + 4 if last_bbox else title_y + 20
        info_size = max(8, int(card_w / 30))

        if self._display_settings.get("show_author", True):
            author = self._canvas.create_text(x + 10, next_y, text=f"By: {mod['author']}", fill="#b5bac1",
                                              font=("Segoe UI", info_size), anchor="nw", width=card_w - 20)
            items.append(author)
            ab = self._canvas.bbox(author)
            next_y = ab[3] + 3 if ab else next_y + 16

        if self._display_settings.get("show_category", True):
            tags = mod.get("tags", [])
            # Filter to type tags (non-character)
            type_tags = [t for t in tags if t.lower() in {"commission", "skin", "interface", "vfx", "animation", "audio", "environment", "misc"}]
            if type_tags:
                cat_text = ", ".join(type_tags)
                cat_item = self._canvas.create_text(x + 10, next_y, text=f"🎮 {cat_text}", fill="#7289da",
                                                    font=("Segoe UI", info_size), anchor="nw", width=card_w - 20)
                items.append(cat_item)
                cb = self._canvas.bbox(cat_item)
                next_y = cb[3] + 3 if cb else next_y + 16

        if self._display_settings.get("show_character", True):
            tags = mod.get("tags", [])
            # Filter to character tags (non-type)
            type_names = {"commission", "skin", "interface", "vfx", "animation", "audio", "environment", "misc"}
            char_tags = [t for t in tags if t.lower() not in type_names]
            if char_tags:
                char_text = ", ".join(char_tags)
                char_item = self._canvas.create_text(x + 10, next_y, text=f"👤 {char_text}", fill="#a0c4ff",
                                                     font=("Segoe UI", info_size), anchor="nw", width=card_w - 20)
                items.append(char_item)
                chb = self._canvas.bbox(char_item)
                next_y = chb[3] + 3 if chb else next_y + 16

        if self._display_settings.get("show_version", False):
            ver_item = self._canvas.create_text(x + 10, next_y, text="v1.0", fill="#8888a8",
                                                font=("Segoe UI", info_size), anchor="nw")
            items.append(ver_item)

        # Button
        btn_y = y + card_h - btn_h - 10
        btn_bg = self._canvas.create_rectangle(x + 10, btn_y, x + card_w - 10, btn_y + btn_h, 
                                               fill="#5865F2", outline="")
        btn_text = self._canvas.create_text(x + card_w/2, btn_y + btn_h/2, text="Download Links", 
                                            fill="white", font=("Segoe UI", btn_size, "bold"))
        items.extend([btn_bg, btn_text])

        # Bindings for button
        def on_enter(e, item=btn_bg):
            if self._canvas.type(item): self._canvas.itemconfig(item, fill="#4752C4")
            self._canvas.config(cursor="hand2")
        def on_leave(e, item=btn_bg):
            if self._canvas.type(item): self._canvas.itemconfig(item, fill="#5865F2")
            self._canvas.config(cursor="")
        def on_click(e, m=mod):
            self.show_download_links(m)

        self._canvas.tag_bind(btn_bg, "<Enter>", on_enter)
        self._canvas.tag_bind(btn_text, "<Enter>", on_enter)
        self._canvas.tag_bind(btn_bg, "<Leave>", on_leave)
        self._canvas.tag_bind(btn_text, "<Leave>", on_leave)
        self._canvas.tag_bind(btn_bg, "<Button-1>", on_click)
        self._canvas.tag_bind(btn_text, "<Button-1>", on_click)

        # Draw downloaded badge
        is_downloaded = mod["id"] in getattr(self, "downloaded_ids", set())
        
        if not is_downloaded:
            import re
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', mod.get("title", "")).strip().lower()
            if safe_title and safe_title in getattr(self, "installed_mod_names", set()):
                is_downloaded = True

        if is_downloaded:
            badge_w = 140
            badge_h = 32
            bx = x + card_w - badge_w - 10
            by = btn_y - badge_h - 8
            bg_id = self._canvas.create_rectangle(bx, by, bx + badge_w, by + badge_h, fill="#2d7d46", outline="", stipple="")
            text_id = self._canvas.create_text(bx + badge_w/2, by + badge_h/2, text="✓ Downloaded", fill="white", font=("Segoe UI", 13, "bold"))
            items.extend([bg_id, text_id])

        return items

    # ── Login ──
    def do_login(self):
        self.login_btn.configure(text="Logging in...", state="disabled")
        def _login():
            success = self.api.login_with_playwright()
            if success:
                self.after(0, lambda: self.login_btn.configure(text="Logged In", state="disabled"))
                self._fetch_user_profile()
                self.load_mods()
            else:
                self.after(0, lambda: self.login_btn.configure(text="Login Failed", state="normal"))
        threading.Thread(target=_login, daemon=True).start()

    # ── Data loading ──
    def load_mods(self, use_cache=True):
        self._destroy_all_visible()
        self.mods_cache_file = os.path.join(self.api.cache_dir, "discord_data.json")
        self.downloaded_mods_file = os.path.join(self.api.cache_dir, "downloaded_mods.json")
        self.downloaded_urls_file = os.path.join(self.api.cache_dir, "downloaded_urls.json")
        
        self.downloaded_ids = set()
        if os.path.exists(self.downloaded_mods_file):
            try:
                with open(self.downloaded_mods_file) as f:
                    self.downloaded_ids = set(json.load(f))
            except: pass
            
        self.downloaded_urls = set()
        if os.path.exists(self.downloaded_urls_file):
            try:
                with open(self.downloaded_urls_file) as f:
                    self.downloaded_urls = set(json.load(f))
            except: pass
            
        self.installed_mod_names = set()
        mods_dir = os.path.abspath("mods")
        if os.path.exists(mods_dir):
            try:
                for folder in os.listdir(mods_dir):
                    if os.path.isdir(os.path.join(mods_dir, folder)):
                        self.installed_mod_names.add(folder.lower())
            except: pass
            
        self.is_loading_more = False
        self.mods = []
        self._all_mods = []
        self.all_threads = []
        self.extracted_mods_map = {}
        self.current_page = 0
        self.current_pages = {"sfw": 0, "nsfw": 0}
        self.page_size = 30

        cached_data = None
        if os.path.exists(self.mods_cache_file):
            try:
                with open(self.mods_cache_file) as f:
                    cached_data = json.load(f)
            except: pass

        if cached_data and isinstance(cached_data, dict):
            self.all_threads = cached_data.get("all_threads", [])
            self.current_page = cached_data.get("current_page", 0)
            if use_cache:
                # Only restore extraction cache when not refreshing
                self.extracted_mods_map = cached_data.get("extracted_mods_map", {})
                if "mods" in cached_data and not self.extracted_mods_map:
                    for m in cached_data["mods"]:
                        self.extracted_mods_map[m["id"]] = m
                
                self._sfw_channel_ids = set(cached_data.get("sfw_channel_ids", []))
                self._nsfw_channel_ids = set(cached_data.get("nsfw_channel_ids", []))
                
                if self._nsfw_channel_ids:
                    def _show_nsfw():
                        self._nsfw_available = True
                        self._channel_btns["nsfw"].pack(side="left", padx=(0, 4))
                    self.after(0, _show_nsfw)
                    
                all_tags = set()
                for mod in self.extracted_mods_map.values():
                    if mod and mod.get("tags"):
                        all_tags.update(mod["tags"])
                if not all_tags:
                    all_tags = {"Commission", "Skin", "Interface", "VFX", "Animation", "Audio", "Environment", "Misc"}
                self.after(0, lambda: self._populate_tags(all_tags))

        if use_cache and self.all_threads:
            self.load_next_page(from_cache_init=True)
            return

        old_all_threads = list(self.all_threads)
        self.current_page = 0
        self.mods = []

        self._status_label = ctk.CTkLabel(self._canvas_frame, text="Fetching threads from Discord...", font=("Segoe UI", 14))
        self._status_label.place(relx=0.5, rely=0.5, anchor="center")

        def _fetch():
            try:
                channels = self.api.fetch_endeavor_channels()
                if not channels:
                    self.after(0, lambda: self._status_label.configure(text="Failed to find channels."))
                    return
                
                # Categorize channels into SFW/NSFW
                self._sfw_channel_ids = set()
                self._nsfw_channel_ids = set()
                all_tags = set()
                
                for info in getattr(self.api, '_channels_info', []):
                    cid = info["id"]
                    if info.get("nsfw"):
                        self._nsfw_channel_ids.add(cid)
                    else:
                        self._sfw_channel_ids.add(cid)
                
                # Collect all tag names from channel tags
                for tag_map in getattr(self.api, '_channel_tags', {}).values():
                    all_tags.update(tag_map.values())
                
                # Show NSFW tab if available
                if self._nsfw_channel_ids:
                    def _show_nsfw():
                        self._nsfw_available = True
                        self._channel_btns["nsfw"].pack(side="left", padx=(0, 4))
                    self.after(0, _show_nsfw)
                
                # Populate tag filter buttons
                print(f"[Discord] Discovered tags: {all_tags}")
                if not all_tags:
                    # Fallback: use known tags from the Endeavor server
                    all_tags = {"Commission", "Skin", "Interface", "VFX", "Animation", 
                                "Audio", "Environment", "Misc",
                                "Izuku / Deku", "Bakugo", "Uravity", "Momo", "Kendo",
                                "Endeavor", "Hawks", "Mirio", "Tamaki", "Nejire", "Toga", "Lady Nagant"}
                self.after(0, lambda: self._populate_tags(all_tags))
                
                known_ids = set(self.extracted_mods_map.keys())
                self.all_threads = self.api.get_raw_threads_from_channels(channels, known_ids)
                new_ids = {t["id"] for t in self.all_threads}
                for t in old_all_threads:
                    if t["id"] not in new_ids:
                        self.all_threads.append(t)
                self.after(0, self.load_next_page)
            except Exception as e:
                self.after(0, lambda: self._status_label.configure(text=f"Error: {e}"))
        threading.Thread(target=_fetch, daemon=True).start()

    def load_next_page(self, from_cache_init=False):
        if getattr(self, "is_loading_more", False):
            return
            
        self.is_loading_more = True
        channel_type = getattr(self, '_active_channel', 'sfw')
        if not hasattr(self, 'current_pages'):
            self.current_pages = {"sfw": getattr(self, 'current_page', 0), "nsfw": 0}
            
        if from_cache_init:
            self.current_pages[channel_type] = 0

        if channel_type == "nsfw":
            nsfw_ids = getattr(self, '_nsfw_channel_ids', set())
            relevant_threads = [t for t in self.all_threads if t.get("parent_id") in nsfw_ids]
        else:
            sfw_ids = getattr(self, '_sfw_channel_ids', set())
            relevant_threads = [t for t in self.all_threads if t.get("parent_id") in sfw_ids or not t.get("parent_id")]

        start_idx = self.current_pages[channel_type] * self.page_size
        end_idx = start_idx + self.page_size
        batch = relevant_threads[start_idx:end_idx]

        if not batch:
            self.is_loading_more = False
            return

        def _process():
            try:
                new_batch = [t for t in batch if t["id"] not in self.extracted_mods_map]
                if new_batch:
                    extracted = self.api.process_threads_batch(new_batch)
                    ex_ids = {m["id"] for m in extracted}
                    for m in extracted:
                        self.extracted_mods_map[m["id"]] = m
                    for t in new_batch:
                        if t["id"] not in ex_ids:
                            self.extracted_mods_map[t["id"]] = None

                final = [self.extracted_mods_map.get(t["id"]) for t in batch]
                final = [m for m in final if m is not None]
                self.after(0, lambda: self._append_mods(final, end_idx >= len(relevant_threads)))
            except Exception as e:
                print(f"Error: {e}")
                self.is_loading_more = False
        threading.Thread(target=_process, daemon=True).start()

    def _append_mods(self, new_mods, is_done):
        if hasattr(self, "_status_label"):
            try: self._status_label.destroy()
            except: pass

        self._all_mods.extend(new_mods)
        # Re-apply all filters (search + tags + channel)
        self._apply_filters()
        
        channel_type = getattr(self, '_active_channel', 'sfw')
        if not hasattr(self, 'current_pages'):
            self.current_pages = {"sfw": getattr(self, 'current_page', 0), "nsfw": 0}
            
        self.current_pages[channel_type] += 1
        self.current_page = self.current_pages[channel_type]  # For legacy save

        try:
            with open(self.mods_cache_file, "w") as f:
                json.dump({
                    "all_threads": self.all_threads, 
                    "extracted_mods_map": self.extracted_mods_map, 
                    "current_page": self.current_page,
                    "sfw_channel_ids": list(getattr(self, '_sfw_channel_ids', set())),
                    "nsfw_channel_ids": list(getattr(self, '_nsfw_channel_ids', set()))
                }, f)
        except: pass

        if channel_type == "nsfw":
            nsfw_ids = getattr(self, '_nsfw_channel_ids', set())
            relevant_threads = [t for t in self.all_threads if t.get("parent_id") in nsfw_ids]
        else:
            sfw_ids = getattr(self, '_sfw_channel_ids', set())
            relevant_threads = [t for t in self.all_threads if t.get("parent_id") in sfw_ids or not t.get("parent_id")]

        total_checked = min(self.current_pages[channel_type] * self.page_size, len(relevant_threads))
        self.counter_label.configure(text=f"Discord Mods Loaded: {len(self.mods)} / Threads Checked: {total_checked}")

        self.is_loading_more = False
        
        # Background precaching — loads next page after a delay so the user
        # doesn't hit a loading wall. Uses 2s delay to avoid UI flooding.
        if not is_done and self.current_pages[channel_type] * getattr(self, 'page_size', 30) < len(relevant_threads):
            self.after(2000, self.load_next_page)

    def _append_deep_search_mods(self, new_mods):
        """Helper to append mods found via Instant Deep Search without advancing pagination."""
        if hasattr(self, "_status_label"):
            try: self._status_label.destroy()
            except: pass
            
        self._all_mods.extend(new_mods)
        self._apply_filters()
        
        # Update the counter visually to let the user know more were loaded
        channel_type = getattr(self, '_active_channel', 'sfw')
        if channel_type == "nsfw":
            rel = [t for t in self.all_threads if t.get("parent_id") in getattr(self, '_nsfw_channel_ids', set())]
        else:
            rel = [t for t in self.all_threads if t.get("parent_id") in getattr(self, '_sfw_channel_ids', set()) or not t.get("parent_id")]
            
        total_checked = min(self.current_pages.get(channel_type, 0) * getattr(self, 'page_size', 30), len(rel))
        self.counter_label.configure(text=f"Discord Mods Loaded: {len(self.mods)} / Threads Checked: {total_checked} (+Deep Search)")

    # ── Download dialog ──
    def show_download_links(self, mod):
        import urllib.parse
        dialog = ctk.CTkToplevel(self)
        dialog.title("Download Options")
        dialog.geometry("500x400")
        dialog.transient(self.winfo_toplevel())
        dialog.grid_rowconfigure(1, weight=1)
        dialog.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(dialog, text=f"Downloads for: {mod['title']}", font=("Segoe UI", 16, "bold"), wraplength=450).grid(row=0, column=0, pady=(20,10), padx=20)
        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0,10))

        # Status label at bottom
        status_label = ctk.CTkLabel(dialog, text="", font=("Segoe UI", 12), text_color="#b5bac1")
        status_label.grid(row=2, column=0, pady=(0,15), padx=20)

        for link in mod["links"]:
            try:
                link = link.rstrip(')*]\'",.') 
                parsed = urllib.parse.urlparse(link)
                domain = parsed.netloc.lower()
                fn = urllib.parse.unquote(os.path.basename(parsed.path)).rstrip(')*]\'",.') 
                fn = re.sub(r'-[a-fA-F0-9]{24,}$', '', fn)
                
                is_direct = ("cdn.discordapp.com" in domain or 
                             "media.discordapp.net" in domain or
                             fn.lower().split('?')[0].endswith(('.pak', '.zip', '.rar', '.7z')))
                is_external_host = any(h in domain for h in ["drive.google.com", "mega.nz", "mediafire.com", "dropbox.com"])
                
                if "drive.google.com" in domain: dn = "🔗 Google Drive Link"
                elif "mega.nz" in domain: dn = "🔗 MEGA Link"
                elif "mediafire.com" in domain: dn = "🔗 MediaFire Link"
                elif "dropbox.com" in domain: dn = "🔗 Dropbox Link"
                else:
                    dn = fn if fn and fn.lower() not in ("view","download","file","open") else f"External Link ({domain})"
                if dn.lower().endswith(('.mp4','.mov','.webm','.avi')): 
                    continue  # Skip video attachments
                
                # Use install icon for downloadable files
                if is_direct and not is_external_host:
                    dn = f"⬇ Install: {dn}"
            except: 
                dn = "Download Link"
                is_direct = False
                is_external_host = False

            if link in getattr(self, "downloaded_urls", set()):
                dn = f"✓ {dn}"

            if is_direct and not is_external_host:
                btn = ctk.CTkButton(scroll, text=dn, font=("Segoe UI", 14), height=36, 
                                    fg_color="#2d7d46", hover_color="#236b38")
                btn.configure(command=lambda l=link, d=dialog, s=status_label, m=mod, b=btn: self._download_and_install(l, m, d, s, b))
            else:
                btn = ctk.CTkButton(scroll, text=dn, font=("Segoe UI", 14), height=36, 
                                    fg_color="#5865F2", hover_color="#4752C4")
                btn.configure(command=lambda l=link, b=btn: [self._mark_url_clicked(l, b), webbrowser.open(l)])
            btn.pack(fill="x", pady=6)

    def _mark_url_clicked(self, url, btn_widget):
        if not hasattr(self, "downloaded_urls"):
            self.downloaded_urls = set()
            self.downloaded_urls_file = os.path.join(self.api.cache_dir, "downloaded_urls.json")
        self.downloaded_urls.add(url)
        try:
            with open(self.downloaded_urls_file, "w") as f:
                json.dump(list(self.downloaded_urls), f)
        except: pass
        
        try:
            current_text = btn_widget.cget("text")
            if not current_text.startswith("✓"):
                btn_widget.configure(text=f"✓ {current_text}")
        except: pass

    def _download_and_install(self, url, mod, dialog, status_label, btn_widget=None):
        """Download a file and install .pak into mods folder."""
        import urllib.parse
        import zipfile
        import shutil
        
        # Sanitize mod title for folder name
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', mod["title"]).strip()
        if not safe_title:
            safe_title = "Unknown_Mod"
        
        mods_dir = os.path.abspath("mods")
        
        def update_status(text):
            try:
                dialog.after(0, lambda: status_label.configure(text=text))
            except: pass
        
        def _do_download():
            try:
                update_status("⏳ Downloading...")
                
                # Parse filename from URL
                parsed = urllib.parse.urlparse(url)
                raw_fn = urllib.parse.unquote(os.path.basename(parsed.path))
                raw_fn = raw_fn.split('?')[0]  # strip query params
                if not raw_fn:
                    raw_fn = "mod_file"
                
                # Download the file
                headers = {}
                if "cdn.discordapp.com" in url or "media.discordapp.net" in url:
                    if self.api.token:
                        headers["Authorization"] = self.api.token
                
                resp = requests.get(url, headers=headers, stream=True, timeout=120)
                resp.raise_for_status()
                
                # Check content-disposition for real filename
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    import email.utils
                    # Extract filename from header
                    fn_match = re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
                    if fn_match:
                        raw_fn = fn_match.group(1).strip()
                        
                # Create a specific folder for this file so variants don't overwrite each other
                base_fn = os.path.splitext(raw_fn)[0]
                if base_fn.lower() in safe_title.lower() or safe_title.lower() in base_fn.lower():
                    specific_title = safe_title if len(safe_title) >= len(base_fn) else base_fn
                else:
                    specific_title = f"{safe_title} - {base_fn}"
                    
                specific_title = re.sub(r'[<>:"/\\|?*]', '_', specific_title).strip()
                mod_folder = os.path.join(mods_dir, specific_title)
                assets_dir = os.path.join(mod_folder, "assets")
                os.makedirs(assets_dir, exist_ok=True)
                
                # Save to temp location first
                temp_dir = os.path.join(self.api.cache_dir, "downloads")
                os.makedirs(temp_dir, exist_ok=True)
                temp_path = os.path.join(temp_dir, raw_fn)
                
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                with open(temp_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = int(downloaded / total * 100)
                                mb = downloaded / (1024*1024)
                                total_mb = total / (1024*1024)
                                update_status(f"⏳ Downloading... {mb:.1f}/{total_mb:.1f} MB ({pct}%)")
                
                update_status("📦 Processing file...")
                
                ext = os.path.splitext(raw_fn)[1].lower().split('?')[0]
                installed_paks = []
                
                if ext == '.pak':
                    # Direct .pak - move to assets
                    dest = os.path.join(assets_dir, raw_fn.split('?')[0])
                    shutil.move(temp_path, dest)
                    installed_paks.append(os.path.basename(dest))
                    
                elif ext in ('.zip',):
                    # Extract zip and find .pak files
                    try:
                        with zipfile.ZipFile(temp_path, 'r') as zf:
                            for name in zf.namelist():
                                if name.lower().endswith('.pak'):
                                    zf.extract(name, temp_dir)
                                    extracted = os.path.join(temp_dir, name)
                                    pak_name = os.path.basename(name)
                                    dest = os.path.join(assets_dir, pak_name)
                                    shutil.move(extracted, dest)
                                    installed_paks.append(pak_name)
                        os.remove(temp_path)
                    except Exception as e:
                        update_status(f"❌ Failed to extract zip: {e}")
                        return
                        
                elif ext in ('.rar',):
                    # Try rarfile
                    try:
                        import rarfile
                        with rarfile.RarFile(temp_path, 'r') as rf:
                            for name in rf.namelist():
                                if name.lower().endswith('.pak'):
                                    rf.extract(name, temp_dir)
                                    extracted = os.path.join(temp_dir, name)
                                    pak_name = os.path.basename(name)
                                    dest = os.path.join(assets_dir, pak_name)
                                    shutil.move(extracted, dest)
                                    installed_paks.append(pak_name)
                        os.remove(temp_path)
                    except ImportError:
                        # Fallback: try 7z command
                        try:
                            import subprocess
                            result = subprocess.run(
                                ['7z', 'x', temp_path, f'-o{temp_dir}', '-y', '*.pak', '-r'],
                                capture_output=True, timeout=120
                            )
                            for root, dirs, files in os.walk(temp_dir):
                                for fname in files:
                                    if fname.lower().endswith('.pak'):
                                        src = os.path.join(root, fname)
                                        dest = os.path.join(assets_dir, fname)
                                        shutil.move(src, dest)
                                        installed_paks.append(fname)
                            os.remove(temp_path)
                        except Exception as e:
                            update_status(f"❌ Need 'rarfile' or 7-Zip to extract .rar: {e}")
                            return
                    except Exception as e:
                        update_status(f"❌ Failed to extract rar: {e}")
                        return
                        
                elif ext in ('.7z',):
                    try:
                        import subprocess
                        result = subprocess.run(
                            ['7z', 'x', temp_path, f'-o{temp_dir}', '-y', '*.pak', '-r'],
                            capture_output=True, timeout=120
                        )
                        for root, dirs, files in os.walk(temp_dir):
                            for fname in files:
                                if fname.lower().endswith('.pak'):
                                    src = os.path.join(root, fname)
                                    dest = os.path.join(assets_dir, fname)
                                    shutil.move(src, dest)
                                    installed_paks.append(fname)
                        os.remove(temp_path)
                    except Exception as e:
                        update_status(f"❌ Need 7-Zip to extract .7z: {e}")
                        return
                else:
                    # Unknown extension - just put it in the mod folder
                    dest = os.path.join(assets_dir, raw_fn.split('?')[0])
                    shutil.move(temp_path, dest)
                    installed_paks.append(os.path.basename(dest))
                
                # Create modinfo.json if it doesn't exist
                info_path = os.path.join(mod_folder, "modinfo.json")
                if not os.path.exists(info_path):
                    info = {
                        "name": specific_title,
                        "version": "1.0",
                        "author": mod.get("author", ""),
                        "screenshot": "",
                        "description": f"Downloaded from Discord Store",
                        "category": "Other",
                        "url": "",
                        "has_options": False,
                        "options": []
                    }
                    with open(info_path, "w", encoding="utf-8") as f:
                        json.dump(info, f, indent=4, ensure_ascii=False)
                
                if installed_paks:
                    names = ", ".join(installed_paks)
                    update_status(f"✅ Installed: {names}")
                    
                    if hasattr(self, "downloaded_ids"):
                        self.downloaded_ids.add(mod["id"])
                        try:
                            with open(self.downloaded_mods_file, "w") as f:
                                json.dump(list(self.downloaded_ids), f)
                        except: pass
                        try:
                            dialog.after(0, self._render_visible)
                        except: pass
                        
                    if btn_widget:
                        try:
                            dialog.after(0, lambda: self._mark_url_clicked(url, btn_widget))
                        except: pass
                else:
                    update_status("⚠ No .pak files found in archive")
                    
            except requests.exceptions.HTTPError as e:
                update_status(f"❌ Download failed: HTTP {e.response.status_code}")
            except Exception as e:
                update_status(f"❌ Error: {e}")
        
        threading.Thread(target=_do_download, daemon=True).start()

