# Linux / macOS

 * `pip install -r requirements_build.txt`
 * `pyinstaller -F -w -n FujiNet-Flasher -i icon.icns esphomeflasher/__main__.py`

# Windows

 * `pip install pyinstaller`
 * `pip install -r requirements_build.txt`
 * `pip install -e.`
 * `python -m PyInstaller.__main__ -F -w -n FujiNet-Flasher -i icon.ico esphomeflasher\__main__.py`
