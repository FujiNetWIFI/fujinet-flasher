# This GUI is a fork of the brilliant https://github.com/marcelstoer/nodemcu-pyflasher
import io
import re
import sys
import threading
import io

from urllib.parse import urljoin
import urllib.request
import urllib.error

import wx
import wx.adv
from wx.lib.embeddedimage import PyEmbeddedImage
import wx.lib.inspection
import wx.lib.mixins.inspection
# from wx.lib.wordwrap import wordwrap

from esphomeflasher.__main__ import run_esphomeflasher_kwargs
from esphomeflasher.helpers import list_serial_ports
from esphomeflasher.common import fujinet_version_info

from esphomeflasher.const import FUJINET_PLATFORMS_URL
from esphomeflasher.const import __version__
from esphomeflasher.const import FUJINET_FLASHER_VERSION_URL
from esphomeflasher.remoteFile import RemoteFile, RemoteFileEvent, flush_cache
import esphomeflasher.fnPlatform as fnPlatform
import esphomeflasher.fnRelease as fnRelease

from typing import Union
from typing import List


COLOR_RE = re.compile(r'(?:\033)(?:\[(.*?)[@-~]|\].*?(?:\007|\033\\))')
COLORS = {
    'black': wx.BLACK,
    'red': wx.RED,
    'green': wx.GREEN,
    'yellow': wx.YELLOW,
    'blue': wx.BLUE,
    'magenta': wx.Colour(255, 0, 255),
    'cyan': wx.CYAN,
    'white': wx.WHITE,
}
FORE_COLORS = {**COLORS, None: wx.WHITE}
BACK_COLORS = {**COLORS, None: wx.BLACK}


# See discussion at http://stackoverflow.com/q/41101897/131929
class RedirectText(io.TextIOBase):
    def __init__(self, text_ctrl):
        self._out = text_ctrl
        self._i = 0
        self._line = ''
        self._bold = False
        self._italic = False
        self._underline = False
        self._foreground = None
        self._background = None
        self._secret = False

    def _add_content(self, value):
        attr = wx.TextAttr()
        if self._bold:
            attr.SetFontWeight(wx.FONTWEIGHT_BOLD)
        attr.SetTextColour(FORE_COLORS[self._foreground])
        attr.SetBackgroundColour(BACK_COLORS[self._background])
        wx.CallAfter(self._out.SetDefaultStyle, attr)
        wx.CallAfter(self._out.AppendText, value)

    def _write_line(self):
        pos = 0
        while True:
            match = COLOR_RE.search(self._line, pos)
            if match is None:
                break

            j = match.start()
            self._add_content(self._line[pos:j])
            pos = match.end()

            for code in match.group(1).split(';'):
                code = int(code)
                if code == 0:
                    self._bold = False
                    self._italic = False
                    self._underline = False
                    self._foreground = None
                    self._background = None
                    self._secret = False
                elif code == 1:
                    self._bold = True
                elif code == 3:
                    self._italic = True
                elif code == 4:
                    self._underline = True
                elif code == 5:
                    self._secret = True
                elif code == 6:
                    self._secret = False
                elif code == 22:
                    self._bold = False
                elif code == 23:
                    self._italic = False
                elif code == 24:
                    self._underline = False
                elif code == 30:
                    self._foreground = 'black'
                elif code == 31:
                    self._foreground = 'red'
                elif code == 32:
                    self._foreground = 'green'
                elif code == 33:
                    self._foreground = 'yellow'
                elif code == 34:
                    self._foreground = 'blue'
                elif code == 35:
                    self._foreground = 'magenta'
                elif code == 36:
                    self._foreground = 'cyan'
                elif code == 37:
                    self._foreground = 'white'
                elif code == 39:
                    self._foreground = None
                elif code == 40:
                    self._background = 'black'
                elif code == 41:
                    self._background = 'red'
                elif code == 42:
                    self._background = 'green'
                elif code == 43:
                    self._background = 'yellow'
                elif code == 44:
                    self._background = 'blue'
                elif code == 45:
                    self._background = 'magenta'
                elif code == 46:
                    self._background = 'cyan'
                elif code == 47:
                    self._background = 'white'
                elif code == 49:
                    self._background = None

        self._add_content(self._line[pos:])

    def write(self, string):
        for s in string:
            if s == '\r':
                current_value = self._out.GetValue()
                last_newline = current_value.rfind("\n")
                wx.CallAfter(self._out.Remove, last_newline + 1, len(current_value))
                # self._line += '\n'
                self._write_line()
                self._line = ''
                continue
            self._line += s
            if s == '\n':
                self._write_line()
                self._line = ''
                continue
        return len(string)

    def writable(self):
        return True

    # esptool >=3 handles output differently if the output stream is a TTY
    def isatty(self):
        return False


class FlashingThread(threading.Thread):
    def __init__(self, **kwargs):
        threading.Thread.__init__(self)
        self.daemon = True
        self.kwargs = kwargs

    def run(self):
        try:
            run_esphomeflasher_kwargs(**self.kwargs)
        except Exception as e:
            print("Unexpected error: {}".format(e))
            raise


class MainFrame(wx.Frame):
    EVT_DOWNLOAD_PLATFORMS = wx.NewId()
    EVT_DOWNLOAD_RELEASES = wx.NewId()
    EVT_DOWNLOAD_FIRMWARE = wx.NewId()

    def __init__(self, parent, title):
        wx.Frame.__init__(self, parent, -1, title, style=wx.DEFAULT_FRAME_STYLE | wx.NO_FULL_REPAINT_ON_RESIZE)

        # Add menu with quit option
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        quit_item = file_menu.Append(
            wx.ID_EXIT, "Quit\tCtrl-Q" if sys.platform != "darwin" else "Quit\tCmd-Q", "Quit FujiNet-Flasher")
        menubar.Append(file_menu, "&File")
        self.SetMenuBar(menubar)
        self.Bind(wx.EVT_MENU, self._on_exit_app, quit_item)

        self._firmware = None
        self._port = None
        self._upload_baud_rate = 460800

        self.platforms: List[fnPlatform.FujiNetPlatform] = []
        self.platforms_rf = RemoteFile(FUJINET_PLATFORMS_URL, self, self.EVT_DOWNLOAD_PLATFORMS)
        self.chosen_platform: Union[None, fnPlatform.FujiNetPlatform] = None
        self.releases: List[fnRelease.FujiNetRelease] = []
        self.releases_rf: Union[None, RemoteFile] = None
        self.chosen_release: Union[None, fnRelease.FujiNetRelease] = None
        self.firmware_rf: Union[None, RemoteFile] = None

        self._init_ui()

        sys.stdout = RedirectText(self.console_ctrl)

        # HiDPI friendly attempt
        w, h = self.GetTextExtent("MMMMMMMMMM")
        if w >= 240: f = 2.0
        elif w >= 180: f = 1.5
        elif w >= 150: f = 1.25
        else: f = 1.0
        self.SetClientSize((int(725*f),int(650*f)))
        self.SetMinClientSize((int(640*f), int(480*f)))
        self.Centre(wx.BOTH)
        self.Show(True)

    def _init_ui(self):
        def on_close(event):
            # cancel threads, if any
            self.platforms_rf.cancel()
            if self.releases_rf is not None:
                self.releases_rf.cancel()
            if self.firmware_rf is not None:
                self.firmware_rf.cancel()
            self.Destroy()

        def on_reload(event):
            self.port_choice.SetItems(self._get_serial_ports())

        def on_flash_btn(event):
            self.console_ctrl.SetValue("")
            download_firmware()

        def on_logs_clicked(event):
            self.console_ctrl.SetValue("")
            worker = FlashingThread(port=self._port, upload_baud_rate=self._upload_baud_rate, show_logs=True)
            worker.start()

        def download_platforms():
            # flush cached entries
            flush_cache()
            # reset platforms
            self.platforms = []
            self.chosen_platform = None
            self.platform_info_text.SetLabel("")
            self.platform_choice.Disable()
            self.platform_choice.Set(["Loading platforms ..."])
            self.platform_choice.SetSelection(0)
            # reset releases
            self.releases = []
            self.chosen_release = None
            self.firmware_choice.Disable()
            self.firmware_choice.Set([""])
            self.firmware_choice.SetSelection(0)
            update_firmware_info_text(None)
            self.flash_btn.Disable()
            # get platforms file
            self.platforms_rf.get(use_cache=True)

        def on_platforms_downloaded(evt: RemoteFileEvent):
            # print("on_platforms_downloaded")
            if evt.remote_file.status == RemoteFile.STATUS_OK:
                self.platforms = fnPlatform.loads(evt.remote_file.data)
                self.platform_choice.Set(["-- Select Platform --"]+[p.name for p in self.platforms])
                self.platform_choice.SetSelection(0)
                self.platform_choice.Enable()

        def on_platform_selected(evt: wx.CommandEvent):
            if self._firmware is not None:
                file_picker.SetPath("")
                self._firmware = None
            s = evt.GetSelection()
            if 0 < s <= len(self.platforms):
                self.chosen_platform = self.platforms[s-1]
                # print("build platform:", self.chosen_platform.build)
                self.platform_info_text.SetLabel(self.chosen_platform.description)
                download_releases()
            else:
                self.chosen_platform = None
                self.platform_info_text.SetLabel("")
                self.firmware_choice.Disable()
                self.firmware_choice.Set([""])
                self.firmware_choice.SetSelection(0)
                update_firmware_info_text(None)
                self.flash_btn.Disable()
                self.chosen_release = None

        def download_releases():
            if self.chosen_platform is None:
                return
            self.firmware_choice.Disable()
            self.firmware_choice.Set(["Loading firmware list ..."])
            self.firmware_choice.SetSelection(0)
            update_firmware_info_text(None)
            self.flash_btn.Disable()
            self.releases = []
            self.chosen_release = None
            self.flash_btn.Disable()
            url = urljoin(FUJINET_PLATFORMS_URL, self.chosen_platform.url)
            if self.releases_rf is not None:
                self.releases_rf.cancel()
            self.releases_rf = RemoteFile(url, self, self.EVT_DOWNLOAD_RELEASES)
            self.releases_rf.get(use_cache=True)

        def on_releases_downloaded(evt: RemoteFileEvent):
            if evt.remote_file.status == RemoteFile.STATUS_OK:
                self.releases = fnRelease.loads(
                    evt.remote_file.data, self.chosen_platform.build, self.chosen_platform.name)
                choices = ["-- Select Firmware --"]
                if self.chosen_platform is not None:
                    choices.extend([
                        r.version for r in self.releases
                    ])
                self.firmware_choice.Set(choices)
                self.firmware_choice.SetSelection(0)
                self.firmware_choice.Enable()

        def on_release_selected(evt: wx.CommandEvent):
            if self._firmware is not None:
                file_picker.SetPath("")
                self._firmware = None
            s = evt.GetSelection()
            if 0 < s <= len(self.releases):
                self.chosen_release = self.releases[s-1]
                # print("firmware version:", self.chosen_release.named_version)
                update_firmware_info_text(self.chosen_release.info_text)
                self.flash_btn.Enable()
            else:
                self.chosen_release = None
                update_firmware_info_text(None)
                self.flash_btn.Disable()

        def update_firmware_info_text(text=None):
            # self.firmware_info_text.SetLabel(wordwrap("\n"*5 if text is None else text, 580, wx.ClientDC(self.firmware_info_text)))
            self.firmware_info_text.SetLabel("\n"*5 if text is None else text)
            self.firmware_info_text.Wrap(self.GetClientSize().Width - select_label.GetSize().Width - 32)

        def download_firmware():
            if self._firmware is not None:
                print("Installing Custom Firmware")
                package = open(self._firmware, "rb")
                worker = FlashingThread(port=self._port, upload_baud_rate=self._upload_baud_rate, package=package)
                worker.start()
                self._firmware = None
            else:
                if self.chosen_platform is None or self.chosen_release is None:
                    return
                print("Retrieving firmware")
                url = urljoin(self.releases_rf.url, self.chosen_release.url)
                if self.firmware_rf is not None:
                    self.firmware_rf.cancel()
                self.firmware_rf = RemoteFile(url, self, self.EVT_DOWNLOAD_FIRMWARE)
                self.firmware_rf.get()

        def on_firmware_downloaded(evt: RemoteFileEvent):
            if evt.remote_file.status == RemoteFile.STATUS_OK:
                checksum = self.firmware_rf.sha256
                ok = self.chosen_release.sha256.lower() == checksum.lower()
                print("sha256 {} {}".format(checksum, "OK" if ok else "CHECKSUM ERROR"))
                if not ok:
                    return
                package = io.BytesIO(self.firmware_rf.data)
                worker = FlashingThread(port=self._port, upload_baud_rate=self._upload_baud_rate, package=package)
                worker.start()

        def on_select_port(event):
            choice = event.GetEventObject()
            self._port = choice.GetString(choice.GetSelection())

        def on_select_baud(event):
            b = event.GetEventObject()
            self._upload_baud_rate = int(b.GetString(b.GetSelection()))

        def on_pick_file(event):
            self._firmware = event.GetPath().replace("'", "")
            self.flash_btn.Enable()
            update_firmware_info_text("Custom Firmware File Selected")
            self.platform_info_text.SetLabel("")

        def version_check():
            try:
                f = urllib.request.urlopen(FUJINET_FLASHER_VERSION_URL)
                latest_ver = f.read().decode('utf-8').strip()
            except urllib.error.URLError as e:
                print("Error getting version: {}".format(e))
                latest_ver = __version__  # Fallback to current version if there is an error

            if __version__ < latest_ver:
                self.flasher_ver_text.SetLabel("This version of FujiNet-Flasher is old, Please Update ({}->{})\n at https://fujinet.online/download".format(__version__, latest_ver))
            elif __version__ > latest_ver:
                self.flasher_ver_text.SetLabel("FujiNet-Flasher Version {} (pre-release)".format(__version__))
            else:
                self.flasher_ver_text.SetLabel("FujiNet-Flasher Version {}".format(__version__))

        panel = wx.Panel(self)

        hbox = wx.BoxSizer(wx.HORIZONTAL)

        fgs = wx.FlexGridSizer(10, 2, 10, 10)

        # Version check notification
        self.flasher_ver_text = wx.StaticText(panel)
        version_check()

        # Serial port
        port_label = wx.StaticText(panel, label="Serial port:")
        self.port_choice = wx.Choice(panel, choices=self._get_serial_ports())
        self.port_choice.Bind(wx.EVT_CHOICE, on_select_port)
        bmp = Reload.GetBitmap()
        reload_button = wx.BitmapButton(panel, id=wx.ID_ANY, bitmap=bmp)
        reload_button.Bind(wx.EVT_BUTTON, on_reload)
        reload_button.SetToolTip("Reload serial device list")

        # File Picker
        file_label = wx.StaticText(panel, label="Custom Firmware File:\n(optional)")
        file_picker = wx.FilePickerCtrl(panel, style=wx.FLP_USE_TEXTCTRL)
        file_picker.Bind(wx.EVT_FILEPICKER_CHANGED, on_pick_file)

        serial_boxsizer = wx.BoxSizer(wx.HORIZONTAL)
        serial_boxsizer.Add(self.port_choice, 1, wx.ALIGN_CENTER)
        # serial_boxsizer.AddStretchSpacer(0)
        serial_boxsizer.Add(reload_button, 0, wx.EXPAND | wx.LEFT, 4)

        # BAUD Rate
        baud_label = wx.StaticText(panel, label="Baud Rate:\n(default 460800)")
        self.baud_choice = wx.Choice(panel, choices=[
            "921600",
            "576000",
            "460800",
            "230400",
            "115200",
            "76800",
            "57600",
            "38400",
            "28800",
            "19200",
            "9600",
            "4800",
            "2400",
            "1800",
            "1200",
            "600",
            "300"
            ]
        )
        self.baud_choice.SetStringSelection("460800")
        self.baud_choice.Bind(wx.EVT_CHOICE, on_select_baud)
        baud_boxsizer = wx.BoxSizer(wx.HORIZONTAL)
        baud_boxsizer.Add(self.baud_choice, 1, wx.ALIGN_CENTER)

        # Platform selection
        self.platform_choice = wx.Choice(panel, choices=[""])
        self.platform_choice.Disable()
        self.platform_choice.Bind(wx.EVT_CHOICE, on_platform_selected)

        # Firmware selection
        select_label = wx.StaticText(panel, label="Firmware selection:\n(official releases)")
        self.firmware_choice = wx.Choice(panel, choices=[""])
        self.firmware_choice.Disable()
        self.firmware_choice.Bind(wx.EVT_CHOICE, on_release_selected)

        # Reload platforms
        platform_get_btn = wx.BitmapButton(panel, id=wx.ID_ANY, bitmap=bmp)
        platform_get_btn.SetToolTip("Reload platforms and firmware releases")
        platform_get_btn.Bind(wx.EVT_BUTTON, lambda evt: download_platforms())

        # Reload releases
        # firmware_get_btn = wx.BitmapButton(panel, id=wx.ID_ANY, bitmap=bmp,
        #                                    size=(bmp.GetWidth() + 7, bmp.GetHeight() + 7))
        # firmware_get_btn.SetToolTip("Reload firmware list")
        # firmware_get_btn.Bind(wx.EVT_BUTTON, lambda evt: download_releases())

        release_sizer = wx.BoxSizer(wx.HORIZONTAL)
        release_sizer.Add(self.platform_choice, 2, wx.ALIGN_CENTER)
        release_sizer.Add(self.firmware_choice, 3, wx.ALIGN_CENTER | wx.LEFT, 4)
        # release_sizer.Add(firmware_get_btn, 0, wx.EXPAND | wx.LEFT, 4)
        release_sizer.Add(platform_get_btn, 0, wx.EXPAND | wx.LEFT, 4)
        self.Connect(self.EVT_DOWNLOAD_PLATFORMS, -1, RemoteFileEvent.event_type, on_platforms_downloaded)
        self.Connect(self.EVT_DOWNLOAD_RELEASES, -1, RemoteFileEvent.event_type, on_releases_downloaded)
        self.Connect(self.EVT_DOWNLOAD_FIRMWARE, -1, RemoteFileEvent.event_type, on_firmware_downloaded)


        # Flash firmware
        self.flash_btn = wx.Button(panel, -1, "Flash FujiNet Firmware")
        self.flash_btn.Bind(wx.EVT_BUTTON, on_flash_btn)
        self.flash_btn.Disable()

        # Serial debug
        logs_button = wx.Button(panel, -1, "Serial Debug Output")
        logs_button.Bind(wx.EVT_BUTTON, on_logs_clicked)

        # Platform info
        # platform_info_label = wx.StaticText(panel, label="Platform:")
        self.platform_info_text = wx.StaticText(panel, label="")

        # Firmware info
        fw_info_label = wx.StaticText(panel, label="Firmware:")
        self.firmware_info_text = wx.StaticText(panel)
        update_firmware_info_text(None)

        # Log window
        console_label = wx.StaticText(panel, label="Console:")
        self.console_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        font = wx.Font()
        font.SetFamily(wx.FONTFAMILY_TELETYPE)
        font.SetPointSize(10)
        self.console_ctrl.SetFont(font)
        self.console_ctrl.SetBackgroundColour(wx.BLACK)
        self.console_ctrl.SetForegroundColour(wx.WHITE)
        self.console_ctrl.SetDefaultStyle(wx.TextAttr(wx.WHITE))

        fgs.AddMany([
            # Version check notification row
            wx.StaticText(panel, label=""), (self.flasher_ver_text, 1, wx.EXPAND),
            # Port selection row
            (port_label, 0, wx.ALIGN_CENTRE_VERTICAL), (serial_boxsizer, 1, wx.EXPAND),
            # Baud selection row
            (baud_label, 0, wx.ALIGN_CENTRE_VERTICAL), (baud_boxsizer, 1, wx.EXPAND),
            # Custom Firmware File Selection
            (file_label, 0, wx.ALIGN_CENTER_VERTICAL), (file_picker, 1, wx.EXPAND),
            # Platform / Firmware selection
            (select_label, 0, wx.ALIGN_CENTRE_VERTICAL), (release_sizer, 1, wx.EXPAND),
            # Platform information
            wx.StaticText(panel, label=""), (self.platform_info_text, 1, wx.EXPAND),
            # Firmware version information
            (fw_info_label, 0, wx.ALIGN_TOP), (self.firmware_info_text, 1, wx.EXPAND),
            # Flash ESP button
            wx.StaticText(panel, label=""), (self.flash_btn, 1, wx.EXPAND),
            # Debug output button
            wx.StaticText(panel, label=""), (logs_button, 1, wx.EXPAND),
            # Console View (growable)
            (console_label, 1, wx.EXPAND), (self.console_ctrl, 1, wx.EXPAND),
        ])
        fgs.AddGrowableRow(9, 1)
        fgs.AddGrowableCol(1, 1)
        hbox.Add(fgs, proportion=2, flag=wx.ALL | wx.EXPAND, border=15)
        panel.SetSizer(hbox)

        # window close event
        self.Bind(wx.EVT_CLOSE, on_close)

        # download list of platforms
        # TODO: better, stdout redirect to console_ctrl does not work immediately 
        wx.CallLater(200, download_platforms)

    def _get_serial_ports(self):
        ports = []
        for port, desc in list_serial_ports():
            ports.append(port)
        if not self._port and ports:
            self._port = ports[0]
        if not ports:
            ports.append("")
        return ports

    # Menu methods
    def _on_exit_app(self, event):
        self.Close(True)

    def log_message(self, message):
        self.console_ctrl.AppendText(message)


class App(wx.App, wx.lib.mixins.inspection.InspectionMixin):
    def OnInit(self):
        wx.SystemOptions.SetOption("mac.window-plain-transition", 1)
        self.SetAppName("fujinet-flasher (Based on esphome/NodeMCU PyFlasher)")

        frame = MainFrame(None, "fujinet-flasher (Based on esphome/NodeMCU PyFlasher)")
        frame.Show()

        return True


Exit = PyEmbeddedImage(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABGdBTUEAAK/INwWK6QAAABl0"
    "RVh0U29mdHdhcmUAQWRvYmUgSW1hZ2VSZWFkeXHJZTwAAAN1SURBVHjaYvz//z8DJQAggFhA"
    "xEpGRgaQMX+B+A8DgwYLM1M+r4K8P4+8vMi/P38Y3j18+O7Fs+fbvv7+0w9Uc/kHVG070HKA"
    "AGJBNg0omC5jZtynnpfHJeHkzPDmxQuGf6/eMIj+/yP+9MD+xFPrN8Reu3W3Gqi0D2IXAwNA"
    "AIEN+A/hpWuEBMwwmj6TgUVEjOHTo0cM9y9dZfj76ycDCysrg4K5FYMUvyAL7+pVnYfOXwJp"
    "6wIRAAHECAqDJYyMWpLmpmftN2/mYBEVZ3h38SLD9wcPGP6LioIN/7Z+PQM3UB3vv/8MXB/f"
    "MSzdvv3vpecvzfr+/z8HEEBMYFMYGXM0iwrAmu+sXcvw4OxZhqenTjEwAv3P9OsXw+unTxne"
    "6Osz3Ll3l+HvyzcMVlLSzMBwqgTpBQggsAG8MuKB4r9eM7zfv5PhHxMzg4qLCwPD0ycMDL9/"
    "MzD+/cvw/8kTBgUbGwbB1DSGe1cuMbD8+8EgwMPjCtILEEDgMOCSkhT+t20Nw4v7nxkkNuxm"
    "eLNmFYO0sCgDCwcHAwMzM4Pkl68MLzs7GGS6uhmOCwgxcD2+x8DLysID0gsQQGAD/gH99vPL"
    "dwZGDjaG/0An/z19goHp/z+Gn9dvgoP4/7dPDD9OnGD4+/0bA5uCAsPPW8DA5eACxxxAAIEN"
    "+PDuw/ufirJizE9fMzALCjD8efOO4dHObQx/d29k+PObgeHr268MQta2DCw8fAz/X75k+M/I"
    "xPDh1+9vIL0AAQQOg9dPX2x7w8TDwPL2FcOvI8cYxFs7GFjFpRl+PP/K8O3NVwZuIREGpe5u"
    "hp83rjF8u3iO4RsnO8OzHz8PgvQCBBA4GrsZGfUUtNXPWiuLsny59YxBch3Qdl4uhq/rNzP8"
    "BwYin58PAysbG8MFLy+Gnw9uM5xkYPp38fNX22X//x8DCCAmqD8u3bh6s+Lssy8MrCLcDC/8"
    "3Rl+LVvOwG1syMBrYcbwfetmhmsOdgy/795iuMXEwnDh89c2oJ7jIL0AAQR2wQRgXvgKNAfo"
    "qRIlJfk2NR42Rj5gEmb5+4/h35+/DJ+/fmd4DUyNN4B+v/DlWwcwcTWzA9PXQqBegACCGwAK"
    "ERD+zsBgwszOXirEwe7OzvCP5y/QCx/+/v/26vfv/R///O0GOvkII1AdKxCDDAAIIEZKszNA"
    "gAEA1sFjF+2KokIAAAAASUVORK5CYII=")

Reload = PyEmbeddedImage(
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAABGdBTUEAALGOfPtRkwAAACBj"
    "SFJNAAB6JQAAgIMAAPn/AACA6AAAdTAAAOpgAAA6lwAAF2+XqZnUAAAABmJLR0QA/wD/AP+g"
    "vaeTAAAACXBIWXMAAABIAAAASABGyWs+AAAACXZwQWcAAAAYAAAAGAB4TKWmAAACZUlEQVRI"
    "x7XVT4iXRRgH8M/8Mv9tUFgRZiBESRIhbFAo8kJ0EYoOwtJBokvTxUtBQnUokIjAoCi6+HiR"
    "CNKoU4GHOvQieygMJKRDEUiahC4UtGkb63TY+cnb6/rb3276vQwzzzPf5/9MKqW4kRj8n8s5"
    "53U55y03xEDOeRu+xe5ReqtWQDzAC3gTa3D7KP20nBrknDfhMB7vHH+Dj3AWxyPitxUZyDnv"
    "xsElPL6MT/BiRJwbaaBN6eamlH9yzmvxPp5bRibPYDIizg96pIM2pak2pSexGiLiEr7H3DIM"
    "3IMP/hNBm9It+BDzmGp6oeWcd+BIvdzFRZzGvUOnOtg6qOTrcRxP4ZVmkbxFxDQm8WVPtDMi"
    "tmIDPu7JJocpehnb8F1Tyo/XijsizmMX9teCwq1VNlvrdKFzZeOgTelOvFQPfurV5NE2pc09"
    "I/MR8TqewAxu68hmMd1RPzXAw1hXD9b3nL4bJ9qUdi0SzbF699ee6K9ObU6swoMd4Y42pYmm"
    "lNm6/91C33/RpvQG9jelzHeMnK4F7uK+ur49bNNzHeEdONSmNFH3f9R1gNdwrKZ0UeSc77fQ"
    "CCfxFqSveQA/9HTn8DM2d9I3xBk83ZQy3SNPFqb4JjwTEX9S56BN6SimjI857GtKea+ST+Cx"
    "6synETHssCuv6V5sd/UQXQur8VCb0tqmlEuYi4jPF1PsTvJGvFMjGfVPzOD5ppTPxvHkqseu"
    "Teku7MQm7MEjHfFXeLYp5ey4uRz5XLcpHbAwhH/jVbzblHJ5TG4s/aPN4BT2NKWcXA7xuBFs"
    "wS9NKRdXQr6kgeuBfwEbWdzTvan9igAAADV0RVh0Y29tbWVudABSZWZyZXNoIGZyb20gSWNv"
    "biBHYWxsZXJ5IGh0dHA6Ly9pY29uZ2FsLmNvbS/RLzdIAAAAJXRFWHRkYXRlOmNyZWF0ZQAy"
    "MDExLTA4LTIxVDE0OjAxOjU2LTA2OjAwdNJAnQAAACV0RVh0ZGF0ZTptb2RpZnkAMjAxMS0w"
    "OC0yMVQxNDowMTo1Ni0wNjowMAWP+CEAAAAASUVORK5CYII=")


def main():
    app = App(False)
    app.MainLoop()
