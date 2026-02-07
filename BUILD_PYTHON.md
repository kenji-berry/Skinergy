# Building the Desktop App

## Requirements

- Python 3.8+
- pip

## Build

1. Install dependencies:
   ```
   pip install -r requirements-desktop.txt
   pip install pyinstaller
   ```

2. Run the build script:
   ```
   python build_exe.py
   ```

3. Find your exe in `dist/SkinergyUploader.exe`

## Notes

- The exe is around 20-50mb because it bundles Python
- Windows Defender might flag it (thats why this source code is public)
- If something breaks, look up PyInstaller docs

## Files

- `get_skins_gui.py` - main app
- `security_config.py` - security config and rate limiting
- `build_exe.py` - build script
