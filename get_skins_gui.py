import requests
import urllib3
import json
import tkinter as tk
from tkinter import messagebox
import subprocess
import re
import threading
from requests.auth import HTTPBasicAuth
import os
import time
import sys
import argparse
import logging
from security_config import SecurityConfig, RateLimiter

# Logging setup - finds a writable location for log files
def _get_log_path():
    """Returns a writable log file path, preferring user's LocalAppData on Windows"""
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
            # Last resort: system temp dir
            import tempfile
            return os.path.join(tempfile.gettempdir(), 'skin_fetcher.log')


def _get_data_dir():
    """Return a writable directory for user data (skins.json etc).
    Prefer the same per-user folder used for logs. Falls back to script dir or system temp.
    """
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
            import tempfile
            return tempfile.gettempdir()


def _get_auth_file_path():
    """Return path to persistent auth token file"""
    return os.path.join(_get_data_dir(), 'auth_token.json')


def _save_auth_token(auth_token, user_id):
    """Save auth token and user_id to file"""
    try:
        auth_file = _get_auth_file_path()
        with open(auth_file, 'w', encoding='utf-8') as f:
            json.dump({
                'auth_token': auth_token,
                'user_id': user_id,
                'saved_at': time.time()
            }, f)
    except Exception as e:
        logging.error(f"Failed to save auth token: {e}")


def _load_auth_token():
    """Load auth token and user_id from file"""
    try:
        auth_file = _get_auth_file_path()
        if os.path.exists(auth_file):
            with open(auth_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
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
            except:
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
        
        # Set icon before title/geometry (Windows needs this)
        self._set_window_icon()
        
        self.root.title("Skinergy Uploader")
        self.root.geometry("500x320")
        self.root.resizable(False, False)

        # Color scheme matching the website profile page
        self.bg_color = "#0A0C0E"  # Dark background
        self.panel_color = "#0A0C0E"  # Panel background
        self.card_bg = "#1F2937"  # Card background (dark gray)
        self.primary_color = "#9333EA"  # Purple accent
        self.success_color = "#10B981"  # Green for success states
        self.warning_color = "#F59E0B"  # Amber for warnings
        self.error_color = "#EF4444"  # Red for errors
        self.text_primary = "#E6EDF3"  # Primary text (light gray)
        self.text_secondary = "#9CA3AF"  # Secondary text (medium gray)
        self.border_color = "#1F2937"  # Border color (matches card bg)

        # Font settings
        self.title_font = ("Segoe UI", 13, "bold")
        self.body_font = ("Segoe UI", 10)
        self.small_font = ("Segoe UI", 8)

        self.root.configure(bg=self.bg_color)

        # Application state
        self.is_fetching = False
        self.auth_token = None
        self.user_id = None
        self.authorized = False
        
        # Progress tracking (0=authorize, 1=fetch, 2=upload, 3=complete)
        self.current_step = 0

        # League client status monitoring
        self.status_monitor_running = True
        self.last_status = None

        # API configuration and rate limiting
        self.api_endpoints = SecurityConfig.get_api_endpoints()
        self.rate_limiter = RateLimiter(SecurityConfig.MAX_REQUESTS_PER_MINUTE)
        
        # Register skinergy:// protocol handler (Windows only, when running as EXE)
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            _register_protocol_handler()
        
        # Initialize UI
        self.setup_gui()
        
        # Set icon again after window is created (Windows sometimes needs this)
        self._set_window_icon()

        # Load saved authentication if available
        self.load_persistent_auth()

        # Check for code from web page (legacy)
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

        # Start background thread to monitor League client status
        self.start_status_monitoring()

        self.log_message("Application started successfully")

        # Cleanup on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.root.mainloop()

    def _set_window_icon(self):
        """Set window icon for title bar and taskbar"""
        try:
            icon_path = None
            # Try multiple locations for icon file
            if getattr(sys, 'frozen', False):
                # Running as EXE - check same directory as executable
                exe_dir = os.path.dirname(sys.executable)
                icon_path = os.path.join(exe_dir, 'icon.ico')
            else:
                # Running as script - check script directory
                script_dir = os.path.dirname(os.path.abspath(__file__))
                icon_path = os.path.join(script_dir, 'icon.ico')
            
            # Fallback to current directory
            if not icon_path or not os.path.exists(icon_path):
                if os.path.exists("icon.ico"):
                    icon_path = os.path.abspath("icon.ico")
            
            if icon_path and os.path.exists(icon_path):
                # Windows needs absolute paths
                icon_path = os.path.abspath(icon_path)
                
                # Try iconbitmap first (title bar)
                try:
                    self.root.iconbitmap(icon_path)
                except Exception as e:
                    logging.debug(f"iconbitmap failed: {e}")
                
                # Also try wm_iconbitmap for taskbar (Windows)
                if os.name == 'nt':
                    try:
                        self.root.wm_iconbitmap(icon_path)
                    except Exception as e:
                        logging.debug(f"wm_iconbitmap failed: {e}")
                        # Last resort: direct tk call
                        try:
                            self.root.tk.call('wm', 'iconbitmap', self.root._w, '-default', icon_path)
                        except Exception as e:
                            logging.debug(f"tk call iconbitmap failed: {e}")
        except Exception as e:
            logging.debug(f"Failed to set window icon: {e}")

    def setup_gui(self):
        # Main window container
        main_frame = tk.Frame(self.root, bg=self.bg_color, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Window title
        title_label = tk.Label(main_frame,
                              text="Skinergy Uploader",
                              font=self.title_font,
                              fg=self.text_primary,  # Light gray text
                              bg=self.bg_color)  # Dark background
        title_label.pack(anchor=tk.W, pady=(0, 16))

        # Auth card container
        self.auth_card = tk.Frame(main_frame, 
                                  bg="#1F2937",  # Dark gray card background
                                  relief="flat",
                                  bd=1,
                                  highlightbackground=self.border_color,  # Border color
                                  highlightthickness=1)
        self.auth_card.pack(fill=tk.X, pady=(0, 12))

        # Code input section
        input_container = tk.Frame(self.auth_card, bg="#1F2937")  # Dark gray
        input_container.pack(fill=tk.X, padx=12, pady=12)

        # Label above input
        code_label = tk.Label(input_container,
                             text="Authorization Code",
                             font=self.small_font,
                             fg=self.text_secondary,  # Medium gray text
                             bg="#1F2937",  # Dark gray background
                             anchor=tk.W)
        code_label.pack(anchor=tk.W, pady=(0, 6))

        # Input field and buttons container
        input_frame = tk.Frame(input_container, bg="#1F2937")  # Dark gray
        input_frame.pack(fill=tk.X)

        # Code entry field
        self.code_entry = tk.Entry(input_frame,
                                  font=("Consolas", 12, "bold"),
                                  width=14,
                                  justify=tk.CENTER,
                                  bg="#0F172A",  # Very dark blue-gray input background
                                  fg=self.text_primary,  # Light gray text
                                  insertbackground=self.text_primary,  # Cursor color
                                  relief="flat",
                                  bd=1,
                                  highlightthickness=1,
                                  highlightbackground=self.border_color,  # Border color
                                  highlightcolor=self.primary_color)  # Focus border (purple)
        self.code_entry.pack(side=tk.LEFT, padx=(0, 8))

        # Buttons container
        btn_frame = tk.Frame(input_frame, bg="#1F2937")  # Dark gray
        btn_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Paste button
        paste_btn = tk.Button(btn_frame,
                              text="📋",
                              font=("Segoe UI", 12),
                              fg=self.text_primary,  # Light gray text
                              bg="#1F2937",  # Dark gray background
                              activebackground="#374151",  # Lighter gray on hover
                              activeforeground=self.text_primary,
                              relief="flat",
                              padx=8,
                              pady=6,
                              command=self.paste_code,
                              cursor="hand2",
                              bd=0,
                              width=2)
        paste_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Clear button
        clear_btn = tk.Button(btn_frame,
                              text="✕",
                              font=("Segoe UI", 14),
                              fg=self.text_primary,  # Light gray text
                              bg="#1F2937",  # Dark gray background
                              activebackground="#374151",  # Lighter gray on hover
                              activeforeground=self.text_primary,
                              relief="flat",
                              padx=8,
                              pady=6,
                              command=self.clear_code,
                              cursor="hand2",
                              bd=0,
                              width=2)
        clear_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Main action button (Authorize/Start Upload)
        self.auth_btn = tk.Button(btn_frame,
                                  text="Authorize",
                                  font=self.body_font,
                                  fg="white",  # White text
                                  bg=self.primary_color,  # Purple background
                                  activebackground="#A855F7",  # Lighter purple on hover
                                  activeforeground="white",
                                  relief="flat",
                                  padx=16,
                                  pady=6,
                                  command=self.handle_auth_or_upload,
                                  cursor="hand2",
                                  bd=0)
        self.auth_btn.pack(side=tk.LEFT)

        # Progress timeline container
        self.progress_container = tk.Frame(main_frame, bg=self.bg_color)  # Dark background
        self.progress_container.pack(fill=tk.X, pady=(0, 8))

        # Progress steps definition
        self.steps = [
            {"label": "Authorize"},
            {"label": "Fetch Data"},
            {"label": "Upload"},
            {"label": "Complete"}
        ]
        self.step_labels = []
        self.step_indicators = []
        self.step_connectors = []
        
        # Build horizontal progress timeline
        timeline_frame = tk.Frame(self.progress_container, bg=self.bg_color)  # Dark background
        timeline_frame.pack(fill=tk.X, pady=8)
        
        for i, step in enumerate(self.steps):
            # Each step container
            step_container = tk.Frame(timeline_frame, bg=self.bg_color)  # Dark background
            step_container.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
            
            # Step indicator (circle)
            indicator = tk.Label(step_container,
                                text="○",
                                font=("Segoe UI", 12),
                                fg=self.text_secondary,  # Medium gray (pending state)
                                bg=self.bg_color,  # Dark background
                                width=3)
            indicator.pack()
            self.step_indicators.append(indicator)
            
            # Step label text
            step_label = tk.Label(step_container,
                                 text=step["label"],
                                 font=self.small_font,
                                 fg=self.text_secondary,  # Medium gray text
                                 bg=self.bg_color)  # Dark background
            step_label.pack(pady=(4, 0))
            self.step_labels.append(step_label)
            
            # Connector line between steps
            if i < len(self.steps) - 1:
                connector = tk.Frame(timeline_frame, 
                                     bg=self.border_color,  # Dark gray line
                                     height=2,
                                     width=20)
                connector.pack(side=tk.LEFT, padx=4, pady=12)
                self.step_connectors.append(connector)

        # Status message display
        self.status_label = tk.Label(main_frame,
                                    text="",
                                    font=self.small_font,
                                    fg=self.text_secondary,  # Medium gray text
                                    bg=self.bg_color,  # Dark background
                                    anchor=tk.W,
                                    wraplength=460)
        self.status_label.pack(fill=tk.X, pady=(8, 0))

        # League client connection status
        self.client_status = tk.Label(main_frame,
                                      text="League client: Checking...",
                                      font=self.small_font,
                                      fg=self.text_secondary,  # Medium gray text
                                      bg=self.bg_color,  # Dark background
                                      anchor=tk.W)
        self.client_status.pack(fill=tk.X, pady=(4, 0))

        # Logs button (bottom-right corner)
        logs_btn = tk.Button(main_frame,
                            text="Logs",
                            font=self.small_font,
                            fg=self.text_secondary,  # Medium gray text
                            bg=self.bg_color,  # Dark background
                            activebackground=self.bg_color,
                            activeforeground=self.text_primary,  # Light gray on hover
                            relief="flat",
                            padx=4,
                            pady=0,
                            command=self.open_logs,
                            cursor="hand2",
                            bd=0)
        logs_btn.place(relx=1.0, rely=1.0, anchor=tk.SE, x=-4, y=-4)

        # Log window state
        self.log_lines = []
        self.log_window = None
        self.log_text = None

        # Input field event handlers
        self.code_entry.bind('<KeyRelease>', self.on_code_change)
        self.code_entry.bind('<Return>', lambda event: self.authorize_device())
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
            except:
                pass
        
        # Auto-submit once we have 8 chars
        if len(cleaned) == 8:
            self.root.after(100, self.authorize_device)

    def paste_code(self):
        """Paste code from clipboard"""
        try:
            clipboard_text = self.root.clipboard_get()
            cleaned = clipboard_text.strip().replace(' ', '').upper()
            self.code_entry.delete(0, tk.END)
            self.code_entry.insert(0, cleaned)
            self.code_entry.focus_set()
            # Auto-submit if 8 chars
            if len(cleaned) == 8:
                self.root.after(100, self.authorize_device)
        except:
            pass

    def clear_code(self):
        """Clear code input and refocus"""
        self.code_entry.delete(0, tk.END)
        self.code_entry.focus_set()
    
    def handle_auth_or_upload(self):
        """Authorize if needed, otherwise start upload"""
        if not self.authorized:
            # Need to authorize first
            self.authorize_device()
        else:
            # Already authorized, start upload
            if not self.is_fetching:
                self.fetch_skins_threaded()

    def load_persistent_auth(self):
        """Load saved auth token if we have one"""
        auth_token, user_id = _load_auth_token()
        if auth_token and user_id:
            try:
                self.auth_token = auth_token
                self.user_id = user_id
                self.authorized = True
                
                self.status_label.config(text="Authorization found. Click 'Authorize' to verify or enter a new code.", fg=self.text_secondary)  # Medium gray text
                self.update_step(0)
                
                self.log_message("Loaded persistent authorization")
            except Exception as e:
                self.log_message(f"Persistent auth invalid: {e}")
                _clear_auth_token()
                self.auth_token = None
                self.user_id = None
                self.authorized = False

    def log_message(self, message):
        """Add message to log buffer (sanitized)"""
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
        self.log_window.title("Logs")
        self.log_window.geometry("600x400")
        self.log_window.configure(bg=self.bg_color)

        # Controls frame
        ctrl_frame = tk.Frame(self.log_window, bg=self.bg_color)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=10)

        copy_btn = tk.Button(ctrl_frame,
                            text="Copy",
                            font=self.body_font,
                            fg=self.text_primary,
                            bg="#1F2937",
                            activebackground="#374151",
                            relief="flat",
                            padx=8,
                            pady=2,
                            command=self._copy_logs,
                            cursor="hand2")
        copy_btn.pack(side=tk.LEFT, padx=(0, 6))

        clear_btn = tk.Button(ctrl_frame,
                            text="Clear",
                            font=self.body_font,
                            fg=self.text_primary,
                            bg="#1F2937",
                            activebackground="#374151",
                            relief="flat",
                            padx=8,
                            pady=2,
                            command=self._clear_logs,
                            cursor="hand2")
        clear_btn.pack(side=tk.LEFT)

        close_btn = tk.Button(ctrl_frame,
                            text="Close",
                            font=self.body_font,
                            fg=self.text_primary,
                            bg="#1F2937",
                            activebackground="#374151",
                            relief="flat",
                            padx=8,
                            pady=2,
                            command=self.log_window.destroy,
                            cursor="hand2")
        close_btn.pack(side=tk.RIGHT)

        # Log text display area
        self.log_text = tk.Text(self.log_window,
                               bg="#1F2937",  # Dark gray background
                               fg=self.text_primary,  # Light gray text
                               font=("Consolas", 8),
                               relief="flat",
                               bd=0,
                               wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

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

    def _clear_logs(self):
        """Clear log buffer"""
        self.log_lines = []
        if getattr(self, 'log_text', None):
            try:
                self.log_text.config(state=tk.NORMAL)
                self.log_text.delete('1.0', tk.END)
                self.log_text.config(state=tk.DISABLED)
            except Exception:
                pass

    def authorize_device(self):
        """Validate and submit auth code"""
        code = self.code_entry.get().strip().replace(' ', '').upper()
        
        # Validate code format
        is_valid, validated_code = SecurityConfig.validate_auth_code(code)
        if not is_valid:
            self.status_label.config(text=validated_code, fg="#EF4444")  # Red text for error
            return
        
        # Check rate limiting
        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.time_until_next_request()
            self.status_label.config(text=f"Rate limited. Wait {wait_time}s", fg="#F59E0B")  # Amber text for warning
            self.log_message(f"Rate limited: Please wait {wait_time} seconds")
            return
        
        self.log_message(f"Attempting authorization with code: [REDACTED]")
        self.status_label.config(text="Verifying code...", fg=self.text_secondary)  # Medium gray text
        
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
                
                if self.auth_token and self.user_id:
                    self.authorized = True
                    
                    # Save persistent auth
                    _save_auth_token(self.auth_token, self.user_id)
                    
                    # Show progress, keep auth UI visible
                    self.progress_container.pack(fill=tk.X, pady=(0, 8), before=self.status_label)
                    self.update_step(0)  # Step 0: Authorize (completed)
                    
                    self.status_label.config(text="Authorization successful! Click 'Authorize' again to start upload.", fg=self.success_color)  # Green text
                    self.log_message("Device authorization successful!")
                    
                    # Update button to show ready state
                    self.auth_btn.config(text="Start Upload", bg=self.success_color, activebackground="#059669")  # Green button
                else:
                    self.status_label.config(text="Authorization failed - missing token", fg="#EF4444")  # Red text
                    self.log_message("Authorization failed: No auth token or user_id received")
                    
            elif response.status_code == 404:
                self.status_label.config(text="Invalid or expired code", fg="#EF4444")  # Red text
                self.log_message("Authorization failed: Invalid or expired code")
                _clear_auth_token()
                self.authorized = False
                self.auth_token = None
                self.user_id = None
                self.auth_btn.config(text="Authorize", bg=self.primary_color, activebackground="#A855F7")  # Purple button
            elif response.status_code == 409:
                self.status_label.config(text="Code already used", fg="#EF4444")  # Red text
                self.log_message("Authorization failed: Code already used")
                _clear_auth_token()
                self.authorized = False
                self.auth_token = None
                self.user_id = None
                self.auth_btn.config(text="Authorize", bg=self.primary_color, activebackground="#A855F7")  # Purple button
            elif response.status_code == 400:
                self.status_label.config(text="Invalid code format", fg="#EF4444")  # Red text
                self.log_message("Authorization failed: Invalid code format")
            elif response.status_code == 401:
                self.status_label.config(text="Authorization expired. Please enter a new code.", fg="#EF4444")  # Red text
                self.log_message("Authorization failed: Token expired")
                _clear_auth_token()
                self.authorized = False
                self.auth_token = None
                self.user_id = None
                self.auth_btn.config(text="Authorize", bg=self.primary_color, activebackground="#A855F7")  # Purple button
            else:
                self.status_label.config(text="Authorization failed", fg="#EF4444")  # Red text
                self.log_message(f"Authorization failed: HTTP {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            self.status_label.config(text="Server connection error", fg="#EF4444")  # Red text
            self.log_message("Connection error: Cannot reach Skinergy server")
        except requests.exceptions.Timeout:
            self.status_label.config(text="Request timeout", fg="#EF4444")  # Red text
            self.log_message("Timeout error: Server took too long to respond")
        except Exception as e:
            self.status_label.config(text="Authorization error", fg="#EF4444")  # Red text
            self.log_message(f"Authorization error: {str(e)}")

    def start_status_monitoring(self):
        """Start background thread to check League client status"""
        def monitor():
            while self.status_monitor_running:
                try:
                    is_running = self.is_league_running()
                    if is_running != self.last_status:
                        self.last_status = is_running
                        self.root.after(0, self.update_status_display, is_running)
                except:
                    pass
                time.sleep(2)

        threading.Thread(target=monitor, daemon=True).start()

    def is_league_running(self):
        """Check if League client is currently running"""
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq LeagueClientUx.exe'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return 'LeagueClientUx.exe' in result.stdout
        except:
            return False

    def update_status_display(self, is_running):
        """Update the League client status label"""
        try:
            if is_running:
                self.client_status.config(text="League client: Connected", fg="#10B981")  # Green text
            else:
                self.client_status.config(text="League client: Not detected", fg="#EF4444")  # Red text
        except:
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
        except:
            pass

        for path in possible_paths:
            try:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        content = f.read().strip()
                        parts = content.split(':')
                        if len(parts) >= 4:
                            return parts[2], parts[3]
            except:
                continue

        return None, None

    def update_progress(self, text, step=None):
        """Update status text and progress steps"""
        self.status_label.config(text=text, fg=self.text_primary)
        self.log_message(f"Progress: {text}")
        
        if step is not None:
            self.update_step(step)
        
        self.root.update_idletasks()
    
    def update_step(self, step_index):
        """Update which step we're on in the progress timeline"""
        self.current_step = step_index
        
        for i, (indicator, label) in enumerate(zip(self.step_indicators, self.step_labels)):
            if i < step_index:
                # Completed step
                indicator.config(text="✓", fg=self.success_color)  # Green checkmark
                label.config(fg=self.text_primary)  # Light gray text
                if i < len(self.step_connectors):
                    self.step_connectors[i].config(bg=self.success_color)  # Green connector line
            elif i == step_index:
                # Current step
                indicator.config(text="●", fg=self.primary_color)  # Purple dot
                label.config(fg=self.text_primary)  # Light gray text
            else:
                # Pending step
                indicator.config(text="○", fg=self.text_secondary)  # Medium gray circle
                label.config(fg=self.text_secondary)  # Medium gray text
                if i < len(self.step_connectors):
                    self.step_connectors[i].config(bg=self.border_color)  # Dark gray connector line

    def show_success_popup(self):
        """Show popup when upload completes"""
        popup = tk.Toplevel(self.root)
        popup.title("Upload Complete")
        popup.geometry("300x120")
        popup.configure(bg=self.bg_color)
        popup.resizable(False, False)
        
        # Center on parent
        popup.transient(self.root)
        popup.grab_set()
        
        # Make it modal
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 60
        popup.geometry(f"300x120+{x}+{y}")
        
        # Success message
        msg_label = tk.Label(popup,
                            text="Upload complete!",
                            font=self.title_font,
                            fg="#10B981",  # Green text
                            bg=self.bg_color)  # Dark background
        msg_label.pack(pady=(20, 10))
        
        # Close button
        close_btn = tk.Button(popup,
                              text="Close",
                              font=self.body_font,
                              fg="white",  # White text
                              bg=self.primary_color,  # Purple background
                              activebackground="#A855F7",  # Lighter purple on hover
                              activeforeground="white",
                              relief="flat",
                              padx=20,
                              pady=4,
                              command=popup.destroy,
                              cursor="hand2")
        close_btn.pack(pady=10)

    def fetch_skins_threaded(self):
        """Start skin fetch in background thread"""
        if self.is_fetching or not self.authorized:
            return

        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.time_until_next_request()
            self.log_message(f"Rate limited: Please wait {wait_time} seconds")
            return

        self.log_message("=== Starting secure skin fetch process ===")
        threading.Thread(target=self.fetch_skins, daemon=True).start()

    def fetch_skins(self):
        """Fetch skins from League client and upload to server"""
        if not self.authorized:
            self.log_message("✗ Not authorized - please enter authorization code first")
            return

        self.is_fetching = True

        try:
            # Find League client connection info
            self.update_progress("Connecting to League client...", step=1)

            port, token = self.get_league_connection_info()

            if not port or not token:
                self.log_message("✗ Could not find League client connection info")
                self.root.after(0, lambda: messagebox.showerror("Error",
                                   "League client not detected.\n\nOpen League and retry."))
                self.is_fetching = False
                return

            self.log_message(f"✓ Connected to League client on port {port}")

            # Get summoner account information
            self.update_progress("Getting account information...", step=1)

            summoner_url = f"https://127.0.0.1:{port}/lol-summoner/v1/current-summoner"
            self.log_message(f"Making request to: {summoner_url}")
            
            # League client uses self-signed localhost cert, so we skip verification
            response = requests.get(summoner_url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=10)
            self.log_message(f"Summoner API response: {response.status_code}")

            if response.status_code != 200:
                self.log_message(f"✗ Failed to get summoner info: {response.status_code}")
                self.root.after(0, lambda: messagebox.showerror("Error",
                                   "Failed to connect to League client.\n\nMake sure League is running and try again."))
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
            self.update_progress("Fetching your skin collection...", step=1)

            url = f"https://127.0.0.1:{port}/lol-champions/v1/inventories/{summoner_id}/skins-minimal"
            response = requests.get(url, auth=HTTPBasicAuth('riot', token), verify=False, timeout=15)
            self.log_message(f"Skins API response: {response.status_code}")

            if response.status_code != 200:
                self.log_message(f"✗ Failed to fetch skins: {response.status_code}")
                self.root.after(0, lambda: messagebox.showerror("Error",
                                   "Failed to fetch skins from League client.\n\nTry again later."))
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
            self.update_progress("Uploading data to server...", step=2)

            if not self.user_id:
                self.log_message("✗ User ID not found. Please re-authorize.")
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
                    self.root.after(2000, self.show_success_popup)
                else:
                    error_msg = "Upload failed after multiple attempts"
                    if api_response:
                        try:
                            error_data = api_response.json()
                            error_msg = error_data.get('error', error_msg)
                        except:
                            pass
                    self.root.after(0, lambda: messagebox.showerror("Upload Error",
                                       f"Failed to upload data.\n\n{error_msg}"))
                    
            except requests.exceptions.ConnectionError:
                self.log_message("✗ Connection error - cannot reach Skinergy servers")
                self.root.after(0, lambda: messagebox.showerror("Connection Error", 
                    "Cannot connect to Skinergy servers.\n\nCheck your internet connection."))
            except requests.exceptions.Timeout:
                self.log_message("✗ Request timeout - server took too long to respond")
                self.root.after(0, lambda: messagebox.showerror("Timeout Error", 
                    "Server took too long to respond.\n\nPlease try again."))
            except Exception as e:
                self.log_message(f"✗ API upload error: {str(e)}")
                self.root.after(0, lambda: messagebox.showerror("Upload Error", 
                    "Failed to upload data.\n\nPlease try again."))

        except Exception as e:
            error_msg = f"An unexpected error occurred: {str(e)}"
            self.log_message(f"✗ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("Error",
                               "An error occurred.\n\nPlease check the logs for details."))

        finally:
            self.is_fetching = False

    def on_closing(self):
        """Handle window close event"""
        self.status_monitor_running = False
        self.root.destroy()


if __name__ == "__main__":
    # Parse command line arguments for deep link support
    parser = argparse.ArgumentParser(description='Skinergy Desktop Uploader')
    parser.add_argument('--code', type=str, help='Authorization code to prefill')
    args = parser.parse_args()
    
    app = LeagueSkinFetcher(code_from_args=args.code if args.code else None)
