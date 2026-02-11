import requests
import urllib3
import json
import tkinter as tk
# messagebox removed — custom _show_popup used instead to avoid freeze with overrideredirect windows
import subprocess
import re
import threading
from requests.auth import HTTPBasicAuth
import os
import time
import sys
import argparse
import logging
import tempfile
from security_config import SecurityConfig, RateLimiter

def _get_log_path():
    """Find a writable log file path, preferring LocalAppData on Windows"""
    try:
        # Prefer Windows LocalAppData for per-user logs
        if os.name == 'nt':
            local_appdata = os.getenv('LOCALAPPDATA') or os.path.expanduser('~')
            log_dir = os.path.join(local_appdata, 'Skinergy')
        else:
            # Non-Windows: prefer a .skinergy folder in the user's home
            log_dir = os.path.join(os.path.expanduser('~'), '.skinergy')

        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, 'skin_fetcher.log')
    except Exception:
        # Fallback to the script directory if possible
        try:
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            return os.path.join(base, 'skin_fetcher.log')
        except Exception:
            return os.path.join(tempfile.gettempdir(), 'skin_fetcher.log')


def _get_data_dir():
    """Return a writable directory for user data like skins.json"""
    try:
        data_dir = os.path.dirname(_get_log_path())
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    except Exception:
        try:
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            return base
        except Exception:
            return tempfile.gettempdir()


def _get_auth_file_path():
    """Return path to persistent auth token file"""
    return os.path.join(_get_data_dir(), 'auth_token.json')


def _save_auth_token(auth_token, user_id, expires_in=86400):
    """Save auth token and user_id to disk with expiry"""
    try:
        auth_file = _get_auth_file_path()
        with open(auth_file, 'w', encoding='utf-8') as f:
            json.dump({
                'auth_token': auth_token,
                'user_id': user_id,
                'saved_at': time.time(),
                'expires_at': time.time() + expires_in
            }, f)
    except Exception as e:
        logging.error(f"Failed to save auth token: {e}")


def _load_auth_token():
    """Load auth token and user_id from file, checking expiry"""
    try:
        auth_file = _get_auth_file_path()
        if os.path.exists(auth_file):
            with open(auth_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Check if token has expired locally
                expires_at = data.get('expires_at')
                if expires_at:
                    if time.time() > expires_at:
                        logging.info("Auth token expired locally, clearing")
                        _clear_auth_token()
                        return None, None
                else:
                    # Legacy format without expires_at - check saved_at + 24 hours
                    saved_at = data.get('saved_at', 0)
                    if time.time() > saved_at + 86400:  # 24 hours
                        logging.info("Auth token expired (legacy check), clearing")
                        _clear_auth_token()
                        return None, None
                
                return data.get('auth_token'), data.get('user_id')
    except Exception as e:
        logging.error(f"Failed to load auth token: {e}")
    return None, None


def _clear_auth_token():
    """Clear saved auth token"""
    try:
        auth_file = _get_auth_file_path()
        if os.path.exists(auth_file):
            os.remove(auth_file)
    except Exception:
        pass


def _get_pending_code_file():
    """Return path to pending code file (written by web page)"""
    return os.path.join(_get_data_dir(), 'pending_code.txt')


def _load_pending_code():
    """Load pending code from file (written by web page) and delete file"""
    try:
        code_file = _get_pending_code_file()
        if os.path.exists(code_file):
            with open(code_file, 'r', encoding='utf-8') as f:
                code = f.read().strip()
            # Delete file after reading
            try:
                os.remove(code_file)
            except Exception:
                pass
            return code
    except Exception:
        pass
    return None


def _register_protocol_handler():
    """Register skinergy:// protocol handler in Windows registry"""
    if os.name != 'nt':
        return  # Only Windows
    
    try:
        import winreg
        
        # Get the path to this executable
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            exe_path = os.path.abspath(__file__)
            # Can't register protocol handler when running as script
            return
        
        protocol_key = r"Software\Classes\skinergy"
        command_key = r"Software\Classes\skinergy\shell\open\command"
        
        # Create protocol key
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, protocol_key)
        winreg.SetValue(key, "", winreg.REG_SZ, "URL:Skinergy Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        key.Close()
        
        # Create command key
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_key)
        command = f'"{exe_path}" --code "%1"'
        winreg.SetValue(key, "", winreg.REG_SZ, command)
        key.Close()
        
        logging.info("Protocol handler registered successfully")
    except Exception as e:
        logging.error(f"Failed to register protocol handler: {e}")


log_path = _get_log_path()
logging.basicConfig(level=logging.DEBUG, filename=log_path, filemode='w',
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class LeagueSkinFetcher:
    def __init__(self, code_from_args=None):
        self.root = tk.Tk()
        
        # Set title BEFORE overrideredirect so taskbar shows the correct name
        self.root.title("Skinergy Uploader")
        
        # Remove default Windows title bar for custom one
        self.root.overrideredirect(True)
        
        # Window size and position (center on screen)
        self.win_width = 480
        self.win_height = 460
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - self.win_width) // 2
        y = (screen_h - self.win_height) // 2
        self.root.geometry(f"{self.win_width}x{self.win_height}+{x}+{y}")
        self.root.resizable(False, False)
        self.root.attributes('-topmost', False)

        # Colors
        self.bg_color = "#111318"
        self.surface = "#181B22"
        self.card_bg = "#1C1F27"
        self.card_border = "#282D38"
        self.titlebar_bg = "#13151B"
        self.purple = "#8B5CF6"
        self.purple_dim = "#7C3AED"
        self.emerald = "#34D399"
        self.emerald_dim = "#10B981"
        self.error_color = "#F87171"
        self.warning_color = "#FBBF24"
        self.text_primary = "#F3F4F6"
        self.text_secondary = "#9CA3AF"
        self.text_muted = "#6B7280"
        self.input_bg = "#181B22"
        self.input_border = "#313845"
        self.btn_primary = "#8B5CF6"
        self.btn_primary_hover = "#7C3AED"
        self.btn_primary_text = "#FFFFFF"
        self.divider = "#252830"
        self.hover_bg = "#242830"

        # Fonts
        self.title_font = ("Bahnschrift SemiBold", 13)
        self.heading_font = ("Bahnschrift SemiBold", 10)
        self.body_font = ("Bahnschrift", 9)
        self.small_font = ("Bahnschrift Light", 8)
        self.mono_font = ("Consolas", 14)
        self.label_font = ("Bahnschrift SemiBold", 8)

        self.root.configure(bg=self.bg_color)

        # App state
        self.is_fetching = False
        self.is_authorizing = False
        self.auth_token = None
        self.user_id = None
        self.authorized = False
        self.current_step = 0
        self.status_monitor_running = True
        self.last_status = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._spinner_running = False
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_index = 0
        self._spinner_base_text = ""

        self.api_endpoints = SecurityConfig.get_api_endpoints()
        self.rate_limiter = RateLimiter(SecurityConfig.MAX_REQUESTS_PER_MINUTE)
        
        self._load_logo()
        self._create_and_set_icon()
        
        # Register skinergy:// protocol handler (Windows EXE only)
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            _register_protocol_handler()
        
        self.setup_gui()
        self.load_persistent_auth()

        # Check for code from web or protocol handler
        pending_code = _load_pending_code()
        if pending_code:
            code_from_args = pending_code

        # Handle code from command line or protocol handler
        if code_from_args:
            code = str(code_from_args).strip()
            
            # Parse protocol handler URL (skinergy://code=ABC12345 or skinergy://ABC12345)
            if 'skinergy://code=' in code:
                code = code.split('code=')[-1]
                code = code.rstrip('/').strip('"').strip("'").strip()
            elif code.startswith('skinergy://'):
                code = code.replace('skinergy://', '').strip()
            
            # Only keep alphanumeric
            code = ''.join(c for c in code if c.isalnum()).upper()
            
            # Prefill if we have a valid code
            if len(code) == 8:
                self.code_entry.delete(0, tk.END)
                self.code_entry.insert(0, code)
            elif code:
                # Partial code, just show first 8 chars
                self.code_entry.delete(0, tk.END)
                self.code_entry.insert(0, code[:8].upper())

        self.start_status_monitoring()
        self.log_message("Application started successfully")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.after(10, self._setup_taskbar_presence)

        self.root.mainloop()

    def _load_logo(self):
        """Load logo PNGs for the titlebar"""
        self._logo_photo_small = None     # Square logo fallback (22px)
        self._logo_photo_long_tb = None   # Wide logo for titlebar (18px tall)
        
        try:
            from PIL import Image, ImageTk
            
            search_dirs = self._get_asset_search_dirs()
            
            # Square logo (frag-logo.png) as fallback
            square_path = self._find_file(search_dirs, 'frag-logo.png')
            if square_path:
                img = Image.open(square_path).convert("RGBA")
                small = img.resize((22, 22), Image.LANCZOS)
                self._logo_photo_small = ImageTk.PhotoImage(small)
            
            # Wide logo (frag-logo-long.png) for titlebar
            long_path = self._find_file(search_dirs, 'frag-logo-long.png')
            if long_path:
                img_long = Image.open(long_path).convert("RGBA")
                aspect = img_long.width / img_long.height
                tb_h = 18
                tb_w = int(tb_h * aspect)
                resized_tb = img_long.resize((tb_w, tb_h), Image.LANCZOS)
                self._logo_photo_long_tb = ImageTk.PhotoImage(resized_tb)
                
        except ImportError:
            logging.warning("Pillow not installed, no logo available")
        except Exception as e:
            logging.warning(f"Failed to load logos: {e}")

    def _get_asset_search_dirs(self):
        """Return list of directories to search for bundled assets"""
        dirs = []
        if getattr(sys, 'frozen', False):
            dirs.append(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)))
            dirs.append(os.path.dirname(sys.executable))
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
        dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public'))
        return dirs

    @staticmethod
    def _find_file(search_dirs, filename):
        """Find a file across multiple directories"""
        for d in search_dirs:
            path = os.path.join(d, filename)
            if os.path.exists(path):
                return path
        return None

    def _setup_taskbar_presence(self):
        """Make overrideredirect window appear in taskbar on Windows with correct icon"""
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = style & ~WS_EX_TOOLWINDOW | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            # Re-show to apply
            self.root.withdraw()
            self.root.after(10, self._finish_taskbar_setup)
        except Exception:
            pass

    def _finish_taskbar_setup(self):
        """Finish taskbar setup: re-apply icon and show window"""
        self.root.deiconify()
        # Re-apply icon after style change (Windows can lose it)
        self._create_and_set_icon()

    def _create_and_set_icon(self):
        """Set the window/taskbar icon from icon.ico"""
        try:
            icon_path = self._find_file(self._get_asset_search_dirs(), 'icon.ico')
            if icon_path:
                self.root.iconbitmap(os.path.abspath(icon_path))
                if os.name == 'nt':
                    self.root.wm_iconbitmap(os.path.abspath(icon_path))
        except Exception as e:
            logging.warning(f"Failed to load icon.ico: {e}")

    def _start_drag(self, event):
        """Start dragging the window"""
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag(self, event):
        """Handle window drag movement"""
        x = self.root.winfo_x() + event.x - self._drag_start_x
        y = self.root.winfo_y() + event.y - self._drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def _minimize_window(self):
        """Minimize the window"""
        self.root.withdraw()
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            self.root.iconify()

    def setup_gui(self):
        # Outer border
        outer = tk.Frame(self.root, bg=self.card_border, bd=0)
        outer.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(outer, bg=self.bg_color)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Titlebar
        titlebar = tk.Frame(inner, bg=self.titlebar_bg, height=44)
        titlebar.pack(fill=tk.X, side=tk.TOP)
        titlebar.pack_propagate(False)

        # Make titlebar draggable
        titlebar.bind("<Button-1>", self._start_drag)
        titlebar.bind("<B1-Motion>", self._on_drag)

        # Logo + brand group (left side)
        brand_frame = tk.Frame(titlebar, bg=self.titlebar_bg)
        brand_frame.pack(side=tk.LEFT, padx=(16, 0), fill=tk.Y)
        brand_frame.bind("<Button-1>", self._start_drag)
        brand_frame.bind("<B1-Motion>", self._on_drag)

        if self._logo_photo_long_tb:
            logo_label = tk.Label(brand_frame, image=self._logo_photo_long_tb, 
                                 bg=self.titlebar_bg, bd=0)
            logo_label.pack(side=tk.LEFT, pady=13)
            logo_label.bind("<Button-1>", self._start_drag)
            logo_label.bind("<B1-Motion>", self._on_drag)
        elif self._logo_photo_small:
            logo_label = tk.Label(brand_frame, image=self._logo_photo_small, 
                                 bg=self.titlebar_bg, bd=0)
            logo_label.pack(side=tk.LEFT, pady=11)
            logo_label.bind("<Button-1>", self._start_drag)
            logo_label.bind("<B1-Motion>", self._on_drag)

        # Window controls
        controls_frame = tk.Frame(titlebar, bg=self.titlebar_bg)
        controls_frame.pack(side=tk.RIGHT, fill=tk.Y)

        # Minimize button
        min_btn = tk.Label(controls_frame, text="─", font=("Bahnschrift Light", 10),
                          fg=self.text_muted, bg=self.titlebar_bg, 
                          width=5, cursor="hand2")
        min_btn.pack(side=tk.LEFT, fill=tk.Y)
        min_btn.bind("<Enter>", lambda e: min_btn.config(bg=self.hover_bg, fg=self.text_secondary))
        min_btn.bind("<Leave>", lambda e: min_btn.config(bg=self.titlebar_bg, fg=self.text_muted))
        min_btn.bind("<Button-1>", lambda e: self._minimize_window())

        # Close button
        close_btn = tk.Label(controls_frame, text="✕", font=("Bahnschrift Light", 10),
                            fg=self.text_muted, bg=self.titlebar_bg,
                            width=5, cursor="hand2")
        close_btn.pack(side=tk.LEFT, fill=tk.Y)
        close_btn.bind("<Enter>", lambda e: close_btn.config(bg="#DC2626", fg="white"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(bg=self.titlebar_bg, fg=self.text_muted))
        close_btn.bind("<Button-1>", lambda e: self.on_closing())

        tk.Frame(inner, bg=self.divider, height=1).pack(fill=tk.X)

        # Main content
        main_frame = tk.Frame(inner, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=28, pady=(20, 18))

        # Hero section
        hero_frame = tk.Frame(main_frame, bg=self.bg_color)
        hero_frame.pack(fill=tk.X, pady=(0, 18))

        hero_title = tk.Label(hero_frame, text="Skin Data Uploader",
                             font=("Bahnschrift SemiBold", 14),
                             fg=self.text_primary, bg=self.bg_color)
        hero_title.pack(anchor=tk.W)

        hero_subtitle = tk.Label(hero_frame, text="Sync your League collection with Skinergy",
                                font=("Bahnschrift Light", 9),
                                fg=self.text_secondary, bg=self.bg_color)
        hero_subtitle.pack(anchor=tk.W, pady=(4, 0))

        # Auth card
        self.auth_card = tk.Frame(main_frame, bg=self.card_bg,
                                  highlightthickness=1,
                                  highlightbackground=self.card_border,
                                  highlightcolor=self.card_border)
        self.auth_card.pack(fill=tk.X, pady=(0, 14))

        card_inner = tk.Frame(self.auth_card, bg=self.card_bg)
        card_inner.pack(fill=tk.X, padx=18, pady=16)

        section_label = tk.Label(card_inner, text="AUTHORIZATION CODE",
                                font=self.label_font,
                                fg=self.text_muted, bg=self.card_bg)
        section_label.pack(anchor=tk.W, pady=(0, 10))

        input_row = tk.Frame(card_inner, bg=self.card_bg)
        input_row.pack(fill=tk.X)

        # Code entry
        entry_frame = tk.Frame(input_row, bg=self.input_border, 
                              highlightthickness=0)
        entry_frame.pack(side=tk.LEFT, padx=(0, 10))
        
        entry_inner = tk.Frame(entry_frame, bg=self.input_bg)
        entry_inner.pack(padx=1, pady=1)

        self.code_entry = tk.Entry(entry_inner,
                                  font=self.mono_font,
                                  width=10,
                                  justify=tk.CENTER,
                                  bg=self.input_bg,
                                  fg=self.text_primary,
                                  insertbackground='#FFFFFF',
                                  relief="flat",
                                  bd=8,
                                  highlightthickness=0)
        self.code_entry.pack()

        # Clear button
        clear_btn = tk.Label(input_row, text="✕", font=("Bahnschrift", 8),
                            fg=self.text_muted, bg=self.card_bg,
                            cursor="hand2", padx=2)
        clear_btn.pack(side=tk.LEFT, padx=(0, 8))
        clear_btn.bind("<Enter>", lambda e: clear_btn.config(fg=self.text_secondary))
        clear_btn.bind("<Leave>", lambda e: clear_btn.config(fg=self.text_muted))
        clear_btn.bind("<Button-1>", lambda e: self.clear_code())

        # Button group
        btn_group = tk.Frame(input_row, bg=self.card_bg)
        btn_group.pack(side=tk.LEFT, fill=tk.X, expand=True)

        paste_btn = tk.Button(btn_group, text="Paste",
                              font=("Bahnschrift", 8),
                              fg=self.text_secondary, bg=self.surface,
                              activebackground=self.hover_bg,
                              activeforeground=self.text_primary,
                              relief="flat", padx=12, pady=7,
                              command=self.paste_code, cursor="hand2",
                              bd=0, highlightthickness=1,
                              highlightbackground=self.card_border,
                              highlightcolor=self.card_border)
        paste_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.auth_btn = tk.Button(btn_group, text="Start Upload",
                                  font=("Bahnschrift SemiBold", 9),
                                  fg=self.btn_primary_text,
                                  bg=self.btn_primary,
                                  activebackground=self.btn_primary_hover,
                                  activeforeground=self.btn_primary_text,
                                  disabledforeground=self.text_primary,
                                  relief="flat", padx=20, pady=7,
                                  command=self.handle_auth_or_upload,
                                  cursor="hand2", bd=0, highlightthickness=0)
        self.auth_btn.pack(side=tk.LEFT)

        tk.Frame(card_inner, bg=self.divider, height=1).pack(fill=tk.X, pady=(12, 10))

        # League client status indicator
        status_row = tk.Frame(card_inner, bg=self.card_bg)
        status_row.pack(fill=tk.X)

        status_dot_frame = tk.Frame(status_row, bg=self.card_bg)
        status_dot_frame.pack(side=tk.LEFT)

        self.status_dot = tk.Canvas(status_dot_frame, width=8, height=8, 
                                    bg=self.card_bg, highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, pady=2)
        self.status_dot.create_oval(1, 1, 7, 7, fill=self.text_muted, outline="")

        self.client_status = tk.Label(status_row,
                                      text="  League Client: Checking...",
                                      font=("Bahnschrift Light", 8),
                                      fg=self.text_muted, bg=self.card_bg,
                                      anchor=tk.W)
        self.client_status.pack(side=tk.LEFT)

        # Progress stepper
        self.progress_container = tk.Frame(main_frame, bg=self.bg_color)
        self.progress_container.pack(fill=tk.X, pady=(0, 14))

        progress_header = tk.Label(self.progress_container, text="PROGRESS",
                                  font=self.label_font,
                                  fg=self.text_muted, bg=self.bg_color)
        progress_header.pack(anchor=tk.W, pady=(0, 12))

        # Progress steps
        self.steps = [
            {"label": "Authorize", "num": "1"},
            {"label": "Fetch", "num": "2"},
            {"label": "Upload", "num": "3"},
            {"label": "Done", "num": "4"}
        ]
        self.step_labels = []
        self.step_numbers = []
        self.step_connectors = []

        timeline_frame = tk.Frame(self.progress_container, bg=self.bg_color)
        timeline_frame.pack(fill=tk.X)

        for i, step in enumerate(self.steps):
            # Connector line BEFORE each step (except the first)
            if i > 0:
                connector = tk.Frame(timeline_frame, bg=self.card_border, height=2)
                connector.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(0, 16))
                self.step_connectors.append(connector)

            step_container = tk.Frame(timeline_frame, bg=self.bg_color)
            step_container.pack(side=tk.LEFT)

            # Number badge (text-based, crisp at any size)
            num_label = tk.Label(step_container, text=step["num"],
                                font=("Bahnschrift SemiBold", 9),
                                fg=self.text_muted, bg=self.card_border,
                                width=3, height=1,
                                relief="flat", bd=0)
            num_label.pack()
            self.step_numbers.append(num_label)

            # Step label below
            step_label = tk.Label(step_container, text=step["label"],
                                 font=("Bahnschrift Light", 7),
                                 fg=self.text_muted, bg=self.bg_color)
            step_label.pack(pady=(4, 0))
            self.step_labels.append(step_label)

        # Status message
        self.status_label = tk.Label(main_frame, text="",
                                    font=("Bahnschrift", 9),
                                    fg=self.emerald, bg=self.bg_color,
                                    anchor=tk.W, wraplength=400)
        self.status_label.pack(fill=tk.X, pady=(6, 0))

        # Bottom bar
        bottom_bar = tk.Frame(main_frame, bg=self.bg_color)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        logs_btn = tk.Label(bottom_bar, text="View Logs",
                           font=("Bahnschrift Light", 8),
                           fg=self.text_muted, bg=self.bg_color,
                           cursor="hand2")
        logs_btn.pack(side=tk.RIGHT)
        logs_btn.bind("<Enter>", lambda e: logs_btn.config(fg=self.text_secondary))
        logs_btn.bind("<Leave>", lambda e: logs_btn.config(fg=self.text_muted))
        logs_btn.bind("<Button-1>", lambda e: self.open_logs())

        version_label = tk.Label(bottom_bar, text="v2.0",
                                font=("Bahnschrift Light", 7),
                                fg=self.text_muted, bg=self.bg_color)
        version_label.pack(side=tk.LEFT)

        self.log_lines = []
        self.log_window = None
        self.log_text = None

        # Input handlers
        self.code_entry.bind('<KeyRelease>', self.on_code_change)
        self.code_entry.bind('<Return>', lambda event: self.handle_auth_or_upload())
        self.code_entry.focus_set()

        # Initialize progress display
        self.update_step(0)

    def on_code_change(self, event):
        """Auto-uppercase and remove spaces from code input"""
        current = self.code_entry.get()
        cleaned = current.replace(' ', '').upper()
        
        if cleaned != current:
            # Preserve cursor position when cleaning
            pos = self.code_entry.index(tk.INSERT)
            self.code_entry.delete(0, tk.END)
            self.code_entry.insert(0, cleaned)
            try:
                new_pos = min(pos, len(cleaned))
                self.code_entry.icursor(new_pos)
            except Exception:
                pass
        
        # No auto-submit — user must press the button manually

    def _start_spinner(self, base_text="Uploading"):
        """Start an animated spinner on the auth button"""
        self._spinner_running = True
        self._spinner_base_text = base_text
        self._spinner_index = 0
        self._tick_spinner()

    def _tick_spinner(self):
        """Advance the spinner animation by one frame"""
        if not self._spinner_running:
            return
        frame = self._spinner_frames[self._spinner_index % len(self._spinner_frames)]
        try:
            self.auth_btn.config(text=f"{frame} {self._spinner_base_text}")
        except Exception:
            pass
        self._spinner_index += 1
        self.root.after(100, self._tick_spinner)

    def _stop_spinner(self, final_text="Start Upload"):
        """Stop the spinner and set final button text"""
        self._spinner_running = False
        try:
            self.auth_btn.config(text=final_text)
        except Exception:
            pass

    def _safe_update_progress(self, text, step=None, fg=None):
        """Thread-safe progress update - schedules UI work on the main thread"""
        def _do():
            try:
                self.status_label.config(text=text, fg=fg or self.text_primary)
                if step is not None:
                    self.update_step(step)
            except Exception:
                pass
        self.root.after(0, _do)
        self.log_message(f"Progress: {text}")

    def _safe_update_spinner_text(self, text):
        """Thread-safe update of the spinner base text (keeps animation going)"""
        self._spinner_base_text = text

    def paste_code(self):
        """Paste code from clipboard"""
        try:
            clipboard_text = self.root.clipboard_get()
            cleaned = clipboard_text.strip().replace(' ', '').upper()
            self.code_entry.delete(0, tk.END)
            self.code_entry.insert(0, cleaned)
            self.code_entry.focus_set()
            # No auto-submit — user must press the button manually
        except Exception:
            pass

    def clear_code(self):
        """Clear code input and refocus"""
        self.code_entry.delete(0, tk.END)
        self.code_entry.focus_set()
    
    def handle_auth_or_upload(self):
        """Single button: authorize if needed, then start upload automatically"""
        if not self.authorized:
            self.authorize_and_upload()
        elif not self.is_fetching:
            self.fetch_skins_threaded()

    def authorize_and_upload(self):
        """Authorize in a thread, then automatically start upload on success"""
        # Prevent concurrent auth attempts (button spam protection)
        if self.is_authorizing:
            return

        code = self.code_entry.get().strip().replace(' ', '').upper()
        is_valid, validated_code = SecurityConfig.validate_auth_code(code)
        if not is_valid:
            self.status_label.config(text=validated_code, fg=self.error_color)
            return

        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.time_until_next_request()
            self.status_label.config(text=f"Rate limited. Wait {wait_time}s", fg=self.warning_color)
            return

        # Lock the button immediately and start spinner
        self.is_authorizing = True
        self.auth_btn.config(state='disabled',
                            disabledforeground=self.text_primary)
        self._start_spinner("Verifying")

        def _auth_then_upload():
            # Run authorization
            self.root.after(0, lambda: self.status_label.config(text="Verifying code...", fg=self.text_secondary))
            self.log_message(f"Attempting authorization with code: [REDACTED]")

            try:
                response = requests.post(
                    self.api_endpoints['auth_verify'],
                    json={"code": validated_code},
                    headers={"Content-Type": "application/json"},
                    timeout=SecurityConfig.REQUEST_TIMEOUT,
                    verify=SecurityConfig.SSL_VERIFY
                )
                self.log_message(f"Verification response status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    self.auth_token = data.get('auth_token')
                    self.user_id = data.get('user_id')
                    expires_in = data.get('expires_in', 86400)

                    if self.auth_token and self.user_id:
                        self.authorized = True
                        _save_auth_token(self.auth_token, self.user_id, expires_in)
                        self.log_message("Device authorization successful!")

                        # Update UI and immediately start upload
                        def _start_upload():
                            self.progress_container.pack(fill=tk.X, pady=(0, 8), before=self.status_label)
                            self.update_step(0)
                            self.status_label.config(text="Authorized! Starting upload...", fg=self.emerald)
                            self.auth_btn.config(bg=self.emerald,
                                                activebackground=self.emerald_dim, activeforeground="white",
                                                disabledforeground="white")
                            self._stop_spinner()
                            self._start_spinner("Uploading")
                            # Auto-start upload
                            self.root.after(300, self.fetch_skins_threaded)
                        self.root.after(0, _start_upload)
                    else:
                        self.root.after(0, lambda: self.status_label.config(text="Authorization failed - missing token", fg=self.error_color))
                        self.log_message("Authorization failed: No auth token or user_id received")
                        self.root.after(0, self._unlock_auth_btn)
                else:
                    # Handle all error codes on the main thread
                    self._handle_auth_error(response)

            except requests.exceptions.ConnectionError:
                self.root.after(0, lambda: self.status_label.config(text="Server connection error", fg=self.error_color))
                self.log_message("Connection error: Cannot reach Skinergy server")
                self.root.after(0, self._unlock_auth_btn)
            except requests.exceptions.Timeout:
                self.root.after(0, lambda: self.status_label.config(text="Request timeout", fg=self.error_color))
                self.log_message("Timeout error: Server took too long to respond")
                self.root.after(0, self._unlock_auth_btn)
            except Exception as e:
                self.root.after(0, lambda: self.status_label.config(text="Authorization error", fg=self.error_color))
                self.log_message(f"Authorization error: {str(e)}")
                self.root.after(0, self._unlock_auth_btn)

        threading.Thread(target=_auth_then_upload, daemon=True).start()

    def _unlock_auth_btn(self):
        """Re-enable the auth button after an auth attempt finishes"""
        self.is_authorizing = False
        self._stop_spinner("Start Upload")
        self.auth_btn.config(state='normal', text='Start Upload', bg=self.btn_primary, fg=self.btn_primary_text,
                            activebackground=self.btn_primary_hover, activeforeground=self.btn_primary_text)

    def _start_rate_limit_countdown(self, seconds):
        """Show a countdown on the button while rate-limited"""
        if seconds <= 0:
            self._unlock_auth_btn()
            self.status_label.config(text="Ready — generate a new code and try again", fg=self.text_secondary)
            return
        self.auth_btn.config(text=f"Wait {seconds}s...", state='disabled',
                            disabledforeground=self.text_primary)
        self.root.after(1000, lambda: self._start_rate_limit_countdown(seconds - 1))

    def _handle_auth_error(self, response):
        """Handle non-200 auth responses on the main thread"""
        status = response.status_code
        if status == 429:
            # Rate limited by server — show cooldown with countdown
            retry_after = 60  # default 60s
            try:
                retry_after = int(response.headers.get('Retry-After', 60))
            except (ValueError, TypeError):
                pass
            self.root.after(0, lambda: self.status_label.config(
                text=f"Too many attempts. Please wait...", fg=self.warning_color))
            self.log_message(f"Rate limited by server (429). Retry after {retry_after}s")
            # Start a visual countdown on the button
            self.root.after(0, lambda: self._start_rate_limit_countdown(retry_after))
            return  # Don't reset auth state or call _unlock — countdown handles it
        elif status == 404:
            self.root.after(0, lambda: self.status_label.config(text="Invalid or expired code. Generate a new one.", fg=self.error_color))
            self.log_message("Authorization failed: Invalid or expired code")
        elif status == 409:
            self.root.after(0, lambda: self.status_label.config(text="Code already used. Generate a new one.", fg=self.error_color))
            self.log_message("Authorization failed: Code already used")
        elif status == 400:
            self.root.after(0, lambda: self.status_label.config(text="Invalid code format", fg=self.error_color))
            self.log_message("Authorization failed: Invalid code format")
        elif status == 401:
            self.root.after(0, lambda: self.status_label.config(text="Authorization expired. Enter a new code.", fg=self.error_color))
            self.log_message("Authorization failed: Token expired")
        else:
            self.root.after(0, lambda: self.status_label.config(text=f"Authorization failed (error {status})", fg=self.error_color))
            self.log_message(f"Authorization failed: HTTP {status}")

        # Reset auth state for all errors
        _clear_auth_token()
        self.authorized = False
        self.auth_token = None
        self.user_id = None
        self.root.after(0, self._unlock_auth_btn)

    def load_persistent_auth(self):
        """Load saved auth token if available"""
        auth_token, user_id = _load_auth_token()
        if auth_token and user_id:
            try:
                self.auth_token = auth_token
                self.user_id = user_id
                self.authorized = True
                
                self.status_label.config(text="Ready to upload! Click 'Start Upload' to sync your skins.", fg=self.emerald)
                self.auth_btn.config(text="▶  Start Upload", bg=self.emerald, fg="white",
                                    activebackground=self.emerald_dim, activeforeground="white")
                self.update_step(0)
                self.log_message("Loaded persistent authorization")
            except Exception as e:
                self.log_message(f"Persistent auth invalid: {e}")
                _clear_auth_token()
                self.auth_token = None
                self.user_id = None
                self.authorized = False
        else:
            self.authorized = False
            self.auth_token = None
            self.user_id = None

    def log_message(self, message):
        """Add a sanitized message to the log buffer"""
        sanitized_message = SecurityConfig.sanitize_log_message(message)
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {sanitized_message}"
        try:
            self.log_lines.append(entry)
            # Keep only last 1000 lines
            if len(self.log_lines) > 1000:
                self.log_lines = self.log_lines[-1000:]
        except Exception:
            self.log_lines = [entry]

        # Update log window if it's open
        def update_log_widget():
            if getattr(self, 'log_text', None):
                try:
                    self.log_text.config(state=tk.NORMAL)
                    self.log_text.insert(tk.END, entry + "\n")
                    self.log_text.see(tk.END)
                    self.log_text.config(state=tk.DISABLED)
                except Exception:
                    pass

        try:
            self.root.after(0, update_log_widget)
        except Exception:
            pass

    def open_logs(self):
        """Open logs window"""
        if getattr(self, 'log_window', None) and tk.Toplevel.winfo_exists(self.log_window):
            try:
                self.log_window.lift()
            except Exception:
                pass
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("Application Logs")
        self.log_window.geometry("640x480")
        self.log_window.configure(bg=self.bg_color)
        self.log_window.overrideredirect(True)
        
        # Outer border frame
        self.log_window.config(highlightthickness=1, highlightbackground=self.card_border, highlightcolor=self.card_border)
        
        # Custom titlebar - matches main app
        titlebar = tk.Frame(self.log_window, bg=self.titlebar_bg, height=44)
        titlebar.pack(fill=tk.X, side=tk.TOP)
        titlebar.pack_propagate(False)
        
        # Titlebar drag bindings
        titlebar.bind("<Button-1>", lambda e: self._start_log_drag(e))
        titlebar.bind("<B1-Motion>", lambda e: self._on_log_drag(e))
        
        # Logo in titlebar (left side) - matches main app
        brand_frame = tk.Frame(titlebar, bg=self.titlebar_bg)
        brand_frame.pack(side=tk.LEFT, padx=(16, 0), fill=tk.Y)
        brand_frame.bind("<Button-1>", lambda e: self._start_log_drag(e))
        brand_frame.bind("<B1-Motion>", lambda e: self._on_log_drag(e))
        
        if self._logo_photo_long_tb:
            logo_label = tk.Label(brand_frame, image=self._logo_photo_long_tb, 
                                 bg=self.titlebar_bg, bd=0)
            logo_label.pack(side=tk.LEFT, pady=13)
            logo_label.bind("<Button-1>", lambda e: self._start_log_drag(e))
            logo_label.bind("<B1-Motion>", lambda e: self._on_log_drag(e))
        elif self._logo_photo_small:
            logo_label = tk.Label(brand_frame, image=self._logo_photo_small, 
                                 bg=self.titlebar_bg, bd=0)
            logo_label.pack(side=tk.LEFT, pady=11)
            logo_label.bind("<Button-1>", lambda e: self._start_log_drag(e))
            logo_label.bind("<B1-Motion>", lambda e: self._on_log_drag(e))
        
        # Close button (X) in titlebar - matches main app style
        close_x = tk.Label(titlebar,
                          text="✕",
                          font=("Bahnschrift Light", 10),
                          fg=self.text_muted,
                          bg=self.titlebar_bg,
                          width=5,
                          cursor="hand2")
        close_x.pack(side=tk.RIGHT, fill=tk.Y)
        close_x.bind("<Button-1>", lambda e: self.log_window.destroy())
        close_x.bind("<Enter>", lambda e: close_x.config(bg="#DC2626", fg="white"))
        close_x.bind("<Leave>", lambda e: close_x.config(bg=self.titlebar_bg, fg=self.text_muted))
        
        # Divider under titlebar
        tk.Frame(self.log_window, bg=self.divider, height=1).pack(fill=tk.X)
        
        # Main content area
        content_frame = tk.Frame(self.log_window, bg=self.bg_color)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)
        
        # "Application Logs" title below titlebar
        title_label = tk.Label(content_frame,
                              text="Application Logs",
                              font=("Bahnschrift SemiBold", 13),
                              fg=self.text_primary,
                              bg=self.bg_color)
        title_label.pack(anchor=tk.W, pady=(0, 12))
        
        # Log text area with scrollbar
        text_container = tk.Frame(content_frame, bg=self.card_bg,
                                  highlightthickness=1,
                                  highlightbackground=self.card_border,
                                  highlightcolor=self.card_border)
        text_container.pack(fill=tk.BOTH, expand=True)
        
        # Scrollbar
        scrollbar = tk.Scrollbar(text_container, bg=self.surface, troughcolor=self.card_bg,
                                 activebackground=self.hover_bg, highlightthickness=0, bd=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.log_text = tk.Text(text_container,
                               bg=self.card_bg,
                               fg=self.text_secondary,
                               font=("Consolas", 9),
                               relief="flat",
                               bd=0,
                               wrap=tk.WORD,
                               padx=12,
                               pady=12,
                               highlightthickness=0,
                               yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)

        # Copy All button at bottom
        bottom_frame = tk.Frame(content_frame, bg=self.bg_color)
        bottom_frame.pack(fill=tk.X, pady=(16, 0))
        
        copy_btn = tk.Button(bottom_frame,
                            text="Copy All",
                            font=("Bahnschrift SemiBold", 9),
                            fg=self.btn_primary_text,
                            bg=self.btn_primary,
                            activebackground=self.btn_primary_hover,
                            activeforeground=self.btn_primary_text,
                            relief="flat",
                            padx=16,
                            pady=8,
                            command=self._copy_logs,
                            cursor="hand2",
                            bd=0,
                            highlightthickness=0)
        copy_btn.pack(fill=tk.X)

        # Populate existing logs
        for line in getattr(self, 'log_lines', []):
            try:
                self.log_text.insert(tk.END, line + "\n")
            except Exception:
                pass
        try:
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def _copy_logs(self):
        """Copy all logs to clipboard"""
        try:
            if self.log_text:
                content = self.log_text.get('1.0', tk.END)
                self.root.clipboard_clear()
                self.root.clipboard_append(content)
        except Exception:
            pass

    def _start_log_drag(self, event):
        """Start dragging the log window"""
        self._log_drag_start_x = event.x
        self._log_drag_start_y = event.y

    def _on_log_drag(self, event):
        """Handle dragging the log window"""
        try:
            x = self.log_window.winfo_x() + event.x - self._log_drag_start_x
            y = self.log_window.winfo_y() + event.y - self._log_drag_start_y
            self.log_window.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def authorize_device(self):
        """Validate and submit auth code"""
        code = self.code_entry.get().strip().replace(' ', '').upper()
        
        is_valid, validated_code = SecurityConfig.validate_auth_code(code)
        if not is_valid:
            self.status_label.config(text=validated_code, fg=self.error_color)
            return
        
        # Check rate limiting
        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.time_until_next_request()
            self.status_label.config(text=f"Rate limited. Wait {wait_time}s", fg=self.warning_color)
            self.log_message(f"Rate limited: Please wait {wait_time} seconds")
            return
        
        self.log_message(f"Attempting authorization with code: [REDACTED]")
        self.status_label.config(text="Verifying code...", fg=self.text_secondary)
        
        try:
            response = requests.post(
                self.api_endpoints['auth_verify'],
                json={"code": validated_code},
                headers={"Content-Type": "application/json"},
                timeout=SecurityConfig.REQUEST_TIMEOUT,
                verify=SecurityConfig.SSL_VERIFY
            )
            
            self.log_message(f"Verification response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                self.auth_token = data.get('auth_token')
                self.user_id = data.get('user_id')
                expires_in = data.get('expires_in', 86400)
                
                if self.auth_token and self.user_id:
                    self.authorized = True
                    _save_auth_token(self.auth_token, self.user_id, expires_in)
                    
                    self.progress_container.pack(fill=tk.X, pady=(0, 8), before=self.status_label)
                    self.update_step(0)
                    
                    self.status_label.config(text="Authorization successful! Click 'Start Upload' to sync your skins.", fg=self.emerald)
                    self.log_message("Device authorization successful!")
                    self.auth_btn.config(text="Start Upload", bg=self.emerald, fg="white",
                                        activebackground=self.emerald_dim, activeforeground="white")
                else:
                    self.status_label.config(text="Authorization failed - missing token", fg=self.error_color)
                    self.log_message("Authorization failed: No auth token or user_id received")
            else:
                self._handle_auth_error(response)
                
        except requests.exceptions.ConnectionError:
            self.status_label.config(text="Server connection error", fg=self.error_color)
            self.log_message("Connection error: Cannot reach Skinergy server")
        except requests.exceptions.Timeout:
            self.status_label.config(text="Request timeout", fg=self.error_color)
            self.log_message("Timeout error: Server took too long to respond")
        except Exception as e:
            self.status_label.config(text="Authorization error", fg=self.error_color)
            self.log_message(f"Authorization error: {str(e)}")

    def start_status_monitoring(self):
        """Start background thread to check League client status"""
        def monitor():
            while self.status_monitor_running:
                try:
                    is_running = self.is_league_running()
                    if is_running != self.last_status:
                        self.last_status = is_running
                        summoner_name = None
                        if is_running:
                            summoner_name = self._get_summoner_name_quick()
                        self.root.after(0, self.update_status_display, is_running, summoner_name)
                except Exception:
                    pass
                time.sleep(2)

        threading.Thread(target=monitor, daemon=True).start()

    def _get_summoner_name_quick(self):
        """Try to get the logged-in summoner name from the League client API."""
        try:
            port, token = self.get_league_connection_info()
            if not port or not token:
                return None
            url = f"https://127.0.0.1:{port}/lol-summoner/v1/current-summoner"
            resp = requests.get(url, auth=HTTPBasicAuth('riot', token),
                                verify=False, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                name = data.get('gameName') or data.get('displayName') or ''
                tag = data.get('tagLine', '')
                if name and tag:
                    return f"{name}#{tag}"
                return name or None
        except Exception:
            pass
        return None

    def is_league_running(self):
        """Check if League client is currently running"""
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq LeagueClientUx.exe'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return 'LeagueClientUx.exe' in result.stdout
        except Exception:
            return False

    def update_status_display(self, is_running, summoner_name=None):
        """Update the League client status label"""
        try:
            self.status_dot.delete("all")
            if is_running:
                self.status_dot.create_oval(1, 1, 7, 7, fill=self.emerald, outline="")
                if summoner_name:
                    self.client_status.config(
                        text=f"  League Client: Connected - {summoner_name}",
                        fg=self.emerald)
                else:
                    self.client_status.config(
                        text="  League Client: Connected", fg=self.emerald)
            else:
                self.status_dot.create_oval(1, 1, 7, 7, fill=self.error_color, outline="")
                self.client_status.config(text="  League Client: Not detected", fg=self.error_color)
        except Exception:
            pass

    def get_league_connection_info(self):
        """Get League client connection details"""
        self.log_message("Attempting to find League client connection info...")
        
        methods = [
            ("WMIC", self.try_wmic),
            ("PowerShell", self.try_powershell),
            ("Lockfile", self.try_lockfile)
        ]

        for method_name, method in methods:
            try:
                self.log_message(f"Trying {method_name} method...")
                port, token = method()
                if port and token:
                    self.log_message(f"✓ Found connection via {method_name}: port {port}")
                    return port, token
                else:
                    self.log_message(f"✗ {method_name} method failed")
            except Exception as e:
                self.log_message(f"✗ {method_name} method error: {str(e)}")
                continue

        self.log_message("✗ All methods failed to find League connection")
        return None, None

    def try_wmic(self):
        """Try to get info using wmic"""
        cmd = 'wmic PROCESS WHERE "name=\'LeagueClientUx.exe\'" GET commandline /format:list'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)

        if result.returncode == 0 and result.stdout:
            port_match = re.search(r'--app-port=(\d+)', result.stdout)
            token_match = re.search(r'--remoting-auth-token=([\w-]+)', result.stdout)

            if port_match and token_match:
                return port_match.group(1), token_match.group(1)

        return None, None

    def try_powershell(self):
        """Try to get info using PowerShell"""
        ps_cmd = '''
        $process = Get-Process LeagueClientUx -ErrorAction SilentlyContinue
        if ($process) {
            $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)").CommandLine
            Write-Output $commandLine
        }
        '''

        result = subprocess.run(['powershell', '-Command', ps_cmd],
                              capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)

        if result.returncode == 0 and result.stdout:
            port_match = re.search(r'--app-port=(\d+)', result.stdout)
            token_match = re.search(r'--remoting-auth-token=([\w-]+)', result.stdout)

            if port_match and token_match:
                return port_match.group(1), token_match.group(1)

        return None, None

    def try_lockfile(self):
        """Try to read League's lockfile"""
        possible_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Riot Games\League of Legends\lockfile"),
            r"C:\Riot Games\League of Legends\lockfile",
        ]

        try:
            result = subprocess.run(['wmic', 'process', 'where', 'name="LeagueClientUx.exe"', 'get', 'ExecutablePath'],
                                  capture_output=True, text=True, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)

            if result.returncode == 0:
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'League of Legends' in line and '.exe' in line:
                        league_dir = os.path.dirname(line.strip())
                        lockfile_path = os.path.join(league_dir, 'lockfile')
                        possible_paths.insert(0, lockfile_path)
                        break
        except Exception:
            pass

        for path in possible_paths:
            try:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        content = f.read().strip()
                        parts = content.split(':')
                        if len(parts) >= 4:
                            return parts[2], parts[3]
            except Exception:
                continue

        return None, None

    def update_progress(self, text, step=None):
        """Update status text and progress steps (THREAD-SAFE)
        
        Schedules all UI work on the main thread via root.after().
        Safe to call from any thread.
        """
        def _do():
            try:
                self.status_label.config(text=text, fg=self.text_primary)
                if step is not None:
                    self.update_step(step)
            except Exception:
                pass
        self.root.after(0, _do)
        self.log_message(f"Progress: {text}")
    
    def update_step(self, step_index):
        """Update which step we're on in the progress stepper"""
        self.current_step = step_index
        
        for i, (num_label, label) in enumerate(zip(self.step_numbers, self.step_labels)):
            if i < step_index:
                # Completed step - emerald badge with checkmark
                num_label.config(text="✓", bg=self.emerald_dim, fg="white",
                                font=("Bahnschrift SemiBold", 9))
                label.config(fg=self.text_primary)
                if i < len(self.step_connectors):
                    self.step_connectors[i].config(bg=self.emerald_dim)
            elif i == step_index:
                # Current step - purple badge
                num_label.config(text=self.steps[i]["num"], bg=self.purple, fg="white",
                                font=("Bahnschrift SemiBold", 9))
                label.config(fg=self.purple)
            else:
                # Pending step - dim badge
                num_label.config(text=self.steps[i]["num"], bg=self.card_border, fg=self.text_muted,
                                font=("Bahnschrift SemiBold", 9))
                label.config(fg=self.text_muted)
                if i > 0 and (i - 1) < len(self.step_connectors):
                    self.step_connectors[i - 1].config(bg=self.card_border)

    def _show_popup(self, title, message, icon_text="!", icon_color=None, btn_text="OK"):
        """Show a custom popup that works reliably with overrideredirect windows.
        
        Unlike messagebox which can appear behind the main window and cause freezes,
        this popup uses the same overrideredirect approach as the main app and is
        forced to the top with -topmost.
        """
        if icon_color is None:
            icon_color = self.error_color

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=self.card_border)
        popup.resizable(False, False)
        popup.transient(self.root)

        pw, ph = 340, 190
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (pw // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (ph // 2)
        popup.geometry(f"{pw}x{ph}+{x}+{y}")

        popup.attributes('-topmost', True)
        popup.lift()
        popup.after(50, popup.focus_force)

        frame = tk.Frame(popup, bg=self.card_bg)
        frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        content = tk.Frame(frame, bg=self.card_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)

        icon_label = tk.Label(content, text=icon_text,
                             font=("Bahnschrift", 18, "bold"),
                             fg=icon_color, bg=self.card_bg)
        icon_label.pack(pady=(0, 6))

        title_label = tk.Label(content, text=title,
                              font=("Bahnschrift SemiBold", 11),
                              fg=self.text_primary, bg=self.card_bg)
        title_label.pack(pady=(0, 4))

        msg_label = tk.Label(content, text=message,
                            font=("Bahnschrift", 9),
                            fg=self.text_secondary, bg=self.card_bg,
                            wraplength=290, justify=tk.CENTER)
        msg_label.pack(pady=(0, 14))

        close_btn = tk.Button(content, text=btn_text,
                              font=("Bahnschrift SemiBold", 9),
                              fg=self.btn_primary_text, bg=self.btn_primary,
                              activebackground=self.btn_primary_hover,
                              activeforeground=self.btn_primary_text,
                              relief="flat", padx=24, pady=5,
                              command=popup.destroy,
                              cursor="hand2", bd=0, highlightthickness=0)
        close_btn.pack()

    def show_success_popup(self):
        """Show popup when upload completes"""
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=self.card_border)
        popup.resizable(False, False)
        
        # Center on parent — do NOT use grab_set() as it deadlocks with overrideredirect windows
        popup.transient(self.root)
        
        pw, ph = 320, 180
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (pw // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (ph // 2)
        popup.geometry(f"{pw}x{ph}+{x}+{y}")

        # Ensure popup is visible on top (critical for overrideredirect windows)
        popup.attributes('-topmost', True)
        popup.lift()
        popup.after(50, popup.focus_force)

        def _close_popup():
            try:
                popup.destroy()
            except Exception:
                pass
            # Reset button to allow re-uploading
            self.auth_btn.config(state='normal', text="▶  Start Upload", bg=self.emerald, fg="white",
                                activebackground=self.emerald_dim, activeforeground="white")
        
        # Inner frame with card background
        frame = tk.Frame(popup, bg=self.card_bg)
        frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        
        content = tk.Frame(frame, bg=self.card_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)
        
        # Success icon
        icon_label = tk.Label(content, text="✓", 
                             font=("Bahnschrift", 20, "bold"),
                             fg=self.emerald, bg=self.card_bg)
        icon_label.pack(pady=(0, 8))
        
        # Success message
        msg_label = tk.Label(content, text="Upload Complete",
                            font=("Bahnschrift SemiBold", 12),
                            fg=self.text_primary, bg=self.card_bg)
        msg_label.pack(pady=(0, 4))
        
        # Description
        desc_label = tk.Label(content,
                             text="Your skin data has been synced.",
                             font=("Bahnschrift", 9),
                             fg=self.text_secondary, bg=self.card_bg)
        desc_label.pack(pady=(0, 16))
        
        # Close button
        close_btn = tk.Button(content, text="Done",
                              font=("Bahnschrift SemiBold", 9),
                              fg=self.btn_primary_text, bg=self.btn_primary,
                              activebackground=self.btn_primary_hover,
                              activeforeground=self.btn_primary_text,
                              relief="flat", padx=28, pady=6,
                              command=_close_popup,
                              cursor="hand2", bd=0, highlightthickness=0)
        close_btn.pack()

    def fetch_skins_threaded(self):
        """Start skin fetch in background thread"""
        if self.is_fetching or not self.authorized:
            return

        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.time_until_next_request()
            self.log_message(f"Rate limited: Please wait {wait_time} seconds")
            return

        # Disable button and start spinner
        self.auth_btn.config(state='disabled',
                            disabledforeground="white")
        self._start_spinner("Uploading")

        self.log_message("=== Starting secure skin fetch process ===")
        threading.Thread(target=self.fetch_skins, daemon=True).start()

    def fetch_skins(self):
        """Fetch skins from League client and upload to server"""
        if not self.authorized:
            self.log_message("✗ Not authorized - please enter authorization code first")
            return

        self.is_fetching = True

        def _on_error(msg, popup_title="Error", popup_msg=None):
            """Helper to handle errors: log, show popup, reset button state"""
            self.log_message(f"✗ {msg}")
            def _do():
                self._stop_spinner("▶  Start Upload")
                self.auth_btn.config(state='normal', text="▶  Start Upload", bg=self.emerald, fg="white",
                                    activebackground=self.emerald_dim, activeforeground="white")
                if popup_msg:
                    self._show_popup(popup_title, popup_msg, icon_text="✕", icon_color=self.error_color)
            self.root.after(0, _do)

        try:
            # Find League client connection info
            self._safe_update_spinner_text("Connecting")
            self.update_progress("Connecting to League client...", step=1)

            port, token = self.get_league_connection_info()

            if not port or not token:
                _on_error("Could not find League client connection info",
                         popup_msg="League client not detected.\n\nOpen League and retry.")
                self.is_fetching = False
                return

            self.log_message(f"✓ Connected to League client on port {port}")

            # Get summoner account information
            self._safe_update_spinner_text("Fetching account")
            self.update_progress("Getting account information...", step=1)

            summoner_url = f"https://127.0.0.1:{port}/lol-summoner/v1/current-summoner"
            self.log_message(f"Making request to: {summoner_url}")
            
            # League client uses self-signed localhost cert, so we skip verification
            response = requests.get(summoner_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
            self.log_message(f"Summoner API response: {response.status_code}")

            if response.status_code != 200:
                _on_error(f"Failed to get summoner info: {response.status_code}",
                         popup_msg="Failed to connect to League client.\n\nMake sure League is running and try again.")
                self.is_fetching = False
                return

            summoner_data = response.json()
            summoner_id = summoner_data.get('summonerId')
            initial_game_name = summoner_data.get('displayName', '').strip() 
            profile_icon_id = summoner_data.get('profileIconId', 0)
            
            self.log_message(f"✓ Base summoner info: '{initial_game_name}' (ID: {summoner_id}, IconID: {profile_icon_id})")

            # Get Riot ID (game name and tagline)
            self.update_progress("Fetching Riot ID...")
            
            final_game_name = initial_game_name
            tagline = "N/A"
            platform_id = "N/A"

            # Try a few different endpoints to get Riot ID
            chat_me_url = f"https://127.0.0.1:{port}/lol-chat/v1/me"
            try:
                chat_me_response = requests.get(chat_me_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
                if chat_me_response.status_code == 200:
                    chat_data = chat_me_response.json()
                    fetched_game_name_from_chat = chat_data.get('gameName', '').strip()
                    fetched_tagline_from_chat = chat_data.get('gameTag', '').strip()
                    fetched_platform_id_from_chat = chat_data.get('platformId', '').strip()

                    if fetched_game_name_from_chat:
                        final_game_name = fetched_game_name_from_chat
                    if fetched_tagline_from_chat:
                        tagline = fetched_tagline_from_chat
                    if fetched_platform_id_from_chat:
                        platform_id = fetched_platform_id_from_chat
            except Exception:
                pass

            # Try account endpoint as fallback
            if not final_game_name.strip() or final_game_name == initial_game_name or tagline == "N/A" or platform_id == "N/A":
                active_account_url = f"https://127.0.0.1:{port}/lol-account/v1/active-account"
                try:
                    active_account_response = requests.get(active_account_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
                    if active_account_response.status_code == 200:
                        account_data = active_account_response.json()
                        fetched_game_name_from_account = account_data.get('gameName', '').strip()
                        fetched_tagline_from_account = account_data.get('tagLine', '').strip()
                        fetched_platform_id_from_account = account_data.get('platformId', '').strip()

                        if fetched_game_name_from_account and (not final_game_name.strip() or final_game_name == initial_game_name):
                            final_game_name = fetched_game_name_from_account
                        if fetched_tagline_from_account and tagline == "N/A":
                            tagline = fetched_tagline_from_account
                        if fetched_platform_id_from_account and platform_id == "N/A":
                            platform_id = fetched_platform_id_from_account
                except Exception:
                    pass

            # Last resort: summoner Riot ID endpoint
            if not final_game_name.strip() or final_game_name == initial_game_name or tagline == "N/A":
                riot_id_url = f"https://127.0.0.1:{port}/lol-summoner/v1/current-summoner/riot-id"
                try:
                    riot_id_response = requests.get(riot_id_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
                    if riot_id_response.status_code == 200:
                        riot_id_data = riot_id_response.json()
                        fetched_game_name_from_summoner_riot_id = riot_id_data.get('gameName', '').strip()
                        fetched_tagline_from_summoner_riot_id = riot_id_data.get('tagLine', '').strip()

                        if fetched_game_name_from_summoner_riot_id and (not final_game_name.strip() or final_game_name == initial_game_name):
                            final_game_name = fetched_game_name_from_summoner_riot_id
                        if fetched_tagline_from_summoner_riot_id and tagline == "N/A":
                            tagline = fetched_tagline_from_summoner_riot_id
                except Exception:
                    pass

            # Make sure we have something
            if not final_game_name.strip() or (final_game_name == initial_game_name and not initial_game_name.strip()):
                final_game_name = "Player"
            
            if not tagline or tagline == "N/A": 
                tagline = "N/A" 
            if not platform_id or platform_id == "N/A":
                platform_id = "UNKNOWN"

            self.log_message(f"✓ Connected as: {final_game_name}#{tagline} (Region: {platform_id})")

            # Fetch skin collection
            self._safe_update_spinner_text("Fetching skins")
            self.update_progress("Fetching your skin collection...", step=1)

            url = f"https://127.0.0.1:{port}/lol-champions/v1/inventories/{summoner_id}/skins-minimal"
            response = requests.get(url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=15)
            self.log_message(f"Skins API response: {response.status_code}")

            if response.status_code != 200:
                _on_error(f"Failed to fetch skins: {response.status_code}",
                         popup_msg="Failed to fetch skins from League client.\n\nTry again later.")
                self.is_fetching = False
                return

            skins_data = response.json()
            skin_count = len(skins_data) if isinstance(skins_data, list) else 0
            self.log_message(f"✓ Fetched {skin_count} skins")

            # Save skins.json
            try:
                data_dir = _get_data_dir()
                tmp_path = os.path.join(data_dir, f"skins.json.tmp.{int(time.time())}")
                final_path = os.path.join(data_dir, "skins.json")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(skins_data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, final_path)
                self.log_message(f"✓ Saved skins.json to: {final_path}")
            except Exception as e:
                self.log_message(f"✗ Failed to save skins.json: {e}")

            # Fetch loot items
            self._safe_update_spinner_text("Fetching loot")
            self.update_progress("Fetching loot data...", step=1)

            loot_data = []
            try:
                url = f"https://127.0.0.1:{port}/lol-loot/v1/player-loot"
                response = requests.get(url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=15)
                self.log_message(f"Loot API response: {response.status_code}")

                if response.status_code == 200:
                    loot_data = response.json()
                    loot_count = len(loot_data) if isinstance(loot_data, list) else 0
                    self.log_message(f"✓ Fetched {loot_count} loot items")
                    
                    # Save skinsLoot.json
                    try:
                        data_dir = _get_data_dir()
                        tmp_path = os.path.join(data_dir, f"skinsLoot.json.tmp.{int(time.time())}")
                        final_path = os.path.join(data_dir, "skinsLoot.json")
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            json.dump(loot_data, f, indent=2, ensure_ascii=False)
                        os.replace(tmp_path, final_path)
                        self.log_message(f"✓ Saved skinsLoot.json to: {final_path}")
                    except Exception as e:
                        self.log_message(f"✗ Failed to save skinsLoot.json: {e}")
            except Exception as e:
                self.log_message(f"⚠ Loot fetch error: {str(e)}")

            # Get friends list for auto-friending
            friends_data = []
            try:
                friends_url = f"https://127.0.0.1:{port}/lol-chat/v1/friends"
                friends_response = requests.get(friends_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
                self.log_message(f"Friends API response: {friends_response.status_code}")
                
                if friends_response.status_code == 200:
                    all_friends = friends_response.json()
                    friends_data = all_friends if isinstance(all_friends, list) else []
                    friends_count = len(friends_data)
                    self.log_message(f"✓ Fetched {friends_count} friends from League client")
                else:
                    self.log_message(f"⚠ Friends API returned status {friends_response.status_code}")
            except Exception as e:
                self.log_message(f"⚠ Friends fetch error: {str(e)}")

            # Upload data to server
            self._safe_update_spinner_text("Uploading")
            self.update_progress("Uploading data to server...", step=2)

            if not self.user_id:
                _on_error("User ID not found. Please re-authorize.")
                self.is_fetching = False
                return
            
            payload = {
                "user_id": self.user_id,
                "summoner_name": final_game_name,
                "summoner_tag": tagline,
                "icon": profile_icon_id,
                "region": platform_id,
                "summoner_id": summoner_id,
                "skins": skins_data,
                "loot": loot_data,
                "friends": friends_data
            }

            self.log_message(f"Preparing to upload {len(payload.get('skins', []))} skins and {len(payload.get('loot', []))} loot items")
            
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.auth_token}"
                }
                
                self.log_message("Uploading data to Skinergy servers...")

                max_attempts = 3
                backoff_base = 2
                success = False
                api_response = None

                for attempt in range(1, max_attempts + 1):
                    try:
                        timeout_val = max(30, SecurityConfig.REQUEST_TIMEOUT)
                        api_response = requests.post(
                            self.api_endpoints['upload_data'],
                            json=payload,
                            headers=headers,
                            timeout=timeout_val,
                            verify=SecurityConfig.SSL_VERIFY
                        )

                        self.log_message(f"API response status: {getattr(api_response, 'status_code', 'NO_RESPONSE')}")

                        if api_response.status_code in (200, 201):
                            self.log_message("✓ Data uploaded successfully!")
                            success = True
                            break
                        elif api_response.status_code == 401:
                            # Authorization token is invalid/expired - need to re-authorize
                            error_msg = "Authorization expired"
                            try:
                                error_data = api_response.json()
                                error_msg = error_data.get('error', error_msg)
                            except Exception:
                                pass
                            
                            self.log_message(f"⚠ {error_msg} - please re-authorize")
                            
                            # Clear saved auth and reset state
                            _clear_auth_token()
                            self.authorized = False
                            self.auth_token = None
                            self.user_id = None
                            
                            # Update UI on main thread
                            def prompt_reauth():
                                self._stop_spinner("Start Upload")
                                self.status_label.config(text="Authorization expired. Please enter a new code.", fg=self.error_color)
                                self.auth_btn.config(state='normal', text="Start Upload", bg=self.btn_primary, fg=self.btn_primary_text,
                                                    activebackground=self.btn_primary_hover, activeforeground=self.btn_primary_text)
                                self.update_step(0)
                                self._show_popup("Re-authorization Required",
                                    "Your authorization has expired.\n\nPlease get a new code from the Skinergy website and try again.",
                                    icon_text="⚠", icon_color=self.warning_color)
                            self.root.after(0, prompt_reauth)
                            return  # Exit the upload flow entirely
                        elif api_response.status_code >= 500:
                            if attempt < max_attempts:
                                wait = backoff_base ** attempt
                                self.log_message(f"Retrying upload in {wait}s (attempt {attempt + 1}/{max_attempts})")
                                time.sleep(wait)
                                continue
                            else:
                                break
                        else:
                            error_msg = "Upload failed"
                            try:
                                error_data = api_response.json()
                                error_msg = error_data.get('error', error_msg)
                            except Exception:
                                error_msg = getattr(api_response, 'text', error_msg)

                            self.log_message(f"⚠ API upload failed: {error_msg}")
                            break

                    except requests.exceptions.RequestException as rexc:
                        self.log_message(f"✗ Request exception during upload: {str(rexc)}")
                        if attempt < max_attempts:
                            wait = backoff_base ** attempt
                            self.log_message(f"Retrying upload in {wait}s (attempt {attempt + 1}/{max_attempts})")
                            time.sleep(wait)
                            continue
                        else:
                            break

                if success:
                    self.update_progress("Upload complete! Your skins are now synced.", step=3)
                    def _on_success():
                        self._stop_spinner("Done ✓")
                        self.auth_btn.config(state='normal', text="Done ✓", bg=self.emerald, fg="white",
                                            activebackground=self.emerald_dim, activeforeground="white")
                    self.root.after(0, _on_success)
                    self.root.after(2000, self.show_success_popup)
                else:
                    error_msg = "Upload failed after multiple attempts"
                    if api_response:
                        try:
                            error_data = api_response.json()
                            error_msg = error_data.get('error', error_msg)
                        except Exception:
                            pass
                    _on_error(f"Upload failed: {error_msg}",
                             popup_title="Upload Error",
                             popup_msg=f"Failed to upload data.\n\n{error_msg}")
                    
            except requests.exceptions.ConnectionError:
                _on_error("Connection error - cannot reach Skinergy servers",
                         popup_title="Connection Error",
                         popup_msg="Cannot connect to Skinergy servers.\n\nCheck your internet connection.")
            except requests.exceptions.Timeout:
                _on_error("Request timeout - server took too long to respond",
                         popup_title="Timeout Error",
                         popup_msg="Server took too long to respond.\n\nPlease try again.")
            except Exception as e:
                _on_error(f"API upload error: {str(e)}",
                         popup_title="Upload Error",
                         popup_msg="Failed to upload data.\n\nPlease try again.")

        except Exception as e:
            error_msg = f"An unexpected error occurred: {str(e)}"
            _on_error(error_msg,
                     popup_msg="An error occurred.\n\nPlease check the logs for details.")

        finally:
            self.is_fetching = False

    def on_closing(self):
        """Handle window close event - ensure full cleanup"""
        self.status_monitor_running = False
        self.is_fetching = False

        # Close the log window if open
        try:
            if getattr(self, 'log_window', None) and self.log_window.winfo_exists():
                self.log_window.destroy()
        except Exception:
            pass

        # Shut down all logging handlers so the log file is released
        try:
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                try:
                    handler.flush()
                    handler.close()
                except Exception:
                    pass
                root_logger.removeHandler(handler)
        except Exception:
            pass

        # Destroy the tkinter window
        try:
            self.root.destroy()
        except Exception:
            pass

        # Force exit the process to ensure nothing hangs
        # (background threads blocked on network requests can keep the process alive)
        os._exit(0)


if __name__ == "__main__":
    # Parse command line arguments for deep link support
    parser = argparse.ArgumentParser(description='Skinergy Desktop Uploader')
    parser.add_argument('--code', type=str, help='Authorization code to prefill')
    args = parser.parse_args()
    
    app = LeagueSkinFetcher(code_from_args=args.code if args.code else None)
