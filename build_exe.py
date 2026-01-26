"""Build script for Skinergy Desktop Uploader"""

import subprocess
import sys
import os
import shutil


def build_exe():
    """Build the Windows executable"""
    
    # Clean old builds
    for folder in ['dist', 'build']:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
            except PermissionError:
                print(f"Can't delete {folder} - close the exe if its running")
                sys.exit(1)
    
    # Build command
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--windowed',
        '--noconfirm',
        '--clean',
        '--add-data', 'security_config.py;.',
        '--hidden-import', 'tkinter',
        '--hidden-import', 'requests',
        '--hidden-import', 'urllib3',
        '--exclude-module', 'PIL',
        '--exclude-module', 'matplotlib',
        '--name=SkinergyDesktop',
        '--distpath=dist',
        'get_skins_gui.py'
    ]
    
    # Add icon if we have one
    if os.path.exists('icon.ico'):
        cmd.insert(-1, '--icon=icon.ico')
    
    print("Building Skinergy Desktop...")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            exe_path = os.path.join('dist', 'SkinergyDesktop.exe')
            if os.path.exists(exe_path):
                size_mb = os.path.getsize(exe_path) / (1024 * 1024)
                print(f"Done! {exe_path} ({size_mb:.1f} MB)")
            else:
                print("Build finished but exe not found?")
        else:
            print("Build failed:")
            print(result.stderr)
            
    except subprocess.TimeoutExpired:
        print("Build timed out")
    except Exception as e:
        print(f"Error: {e}")


def check_deps():
    """Check if we have what we need"""
    try:
        import PyInstaller
        import requests
        return True
    except ImportError as e:
        print(f"Missing: {e.name}")
        print("Run: pip install pyinstaller requests")
        return False


if __name__ == "__main__":
    print("=== Skinergy Desktop Build ===")
    if check_deps():
        build_exe()
