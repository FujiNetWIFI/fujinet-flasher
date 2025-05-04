# Linux / MacOS

```
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip uninstall -y esptool || true
pip install -r requirements_build.txt
export STUBS_DIR="$(pip show esptool | grep "Location: " | cut -c 11- | tr -d '\n')/esptool/targets/stub_flasher/"
python -m PyInstaller.__main__ -F -w -n FujiNet-Flasher -i icon.icns \
    --add-data="${STUBS_DIR}/1/*.json:esptool/targets/stub_flasher/1/" \
    --add-data="${STUBS_DIR}/2/*.json:esptool/targets/stub_flasher/2/" \
    esphomeflasher/__main__.py
```

# Windows

```          
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip uninstall -y esptool
python -m pip install "esptool>=4.7"
pip install -r requirements_build.txt
pip install -e .
pip install --force-reinstall -v pyinstaller==4.10
pip install --force-reinstall -v pyinstaller-hooks-contrib==2023.2
python -m PyInstaller.__main__ -F -w -n FujiNet-Flasher -i icon.ico `
    --add-data "./.venv/lib/site-packages/esptool/targets/stub_flasher/1/*.json;esptool/targets/stub_flasher/1" `
    --add-data "./.venv/lib/site-packages/esptool/targets/stub_flasher/2/*.json;esptool/targets/stub_flasher/2" `
    --collect-submodules esptool `
    esphomeflasher\__main__.py
```