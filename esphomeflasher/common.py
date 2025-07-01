import io
import struct
import json
import esptool

# For MacOS so it finds the certificates
import os
import sys
os.environ['SSL_CERT_FILE'] = os.path.join(sys._MEIPASS, 'certifi', 'cacert.pem')

from esphomeflasher.const import HTTP_REGEX
from esphomeflasher.const import ESP32_DEFAULT_FIRMWARE
from esphomeflasher.const import FUJINET_VERSION_URL
from esphomeflasher.helpers import prevent_print

class EsphomeflasherError(Exception):
    pass


class MockEsptoolArgs(object):
    def __init__(self, flash_size, addr_filename, flash_mode, flash_freq, chip):
        self.chip = chip
        self.compress = True
        self.no_compress = False
        self.flash_size = flash_size
        self.addr_filename = addr_filename
        self.flash_mode = flash_mode
        self.flash_freq = flash_freq
        self.no_stub = False
        self.verify = False
        self.erase_all = False
        self.encrypt = False
        self.force = True
        self.encrypt = False
        self.encrypt_files = None

class ChipInfo(object):
    def __init__(self, family, model, mac):
        self.family = family
        self.model = model
        self.mac = mac
        self.is_esp32 = None

    def as_dict(self):
        return {
            'family': self.family,
            'model': self.model,
            'mac': self.mac,
            'is_esp32': self.is_esp32,
        }


class ESP32ChipInfo(ChipInfo):
    def __init__(self, model, mac, num_cores, cpu_frequency, has_bluetooth, has_embedded_flash,
                 has_factory_calibrated_adc):
        super(ESP32ChipInfo, self).__init__("ESP32", model, mac)
        self.num_cores = num_cores
        self.cpu_frequency = cpu_frequency
        self.has_bluetooth = has_bluetooth
        self.has_embedded_flash = has_embedded_flash
        self.has_factory_calibrated_adc = has_factory_calibrated_adc

    def as_dict(self):
        data = ChipInfo.as_dict(self)
        data.update({
            'num_cores': self.num_cores,
            'cpu_frequency': self.cpu_frequency,
            'has_bluetooth': self.has_bluetooth,
            'has_embedded_flash': self.has_embedded_flash,
            'has_factory_calibrated_adc': self.has_factory_calibrated_adc,
        })
        return data


class ESP8266ChipInfo(ChipInfo):
    def __init__(self, model, mac, chip_id):
        super(ESP8266ChipInfo, self).__init__("ESP8266", model, mac)
        self.chip_id = chip_id

    def as_dict(self):
        data = ChipInfo.as_dict(self)
        data.update({
            'chip_id': self.chip_id,
        })
        return data


def read_chip_property(func, *args, **kwargs):
    try:
        return prevent_print(func, *args, **kwargs)
    except esptool.FatalError as err:
        raise EsphomeflasherError("Reading chip details failed: {}".format(err))


def read_chip_info(chip):
    mac = ':'.join('{:02X}'.format(x) for x in read_chip_property(chip.read_mac))
    if isinstance(chip, esptool.ESP32ROM):
        model = read_chip_property(chip.get_chip_description)
        features = read_chip_property(chip.get_chip_features)
        num_cores = 2 if 'Dual Core' in features else 1
        frequency = next((x for x in ('160MHz', '240MHz') if x in features), '80MHz')
        has_bluetooth = 'BT' in features
        has_embedded_flash = 'Embedded Flash' in features
        has_factory_calibrated_adc = 'VRef calibration in efuse' in features
        return ESP32ChipInfo(model, mac, num_cores, frequency, has_bluetooth,
                             has_embedded_flash, has_factory_calibrated_adc)
    elif isinstance(chip, esptool.ESP32S3ROM):
        model = read_chip_property(chip.get_chip_description)
        features = read_chip_property(chip.get_chip_features)
        num_cores = 2 if 'Dual Core' in features else 1
        frequency = next((x for x in ('160MHz', '240MHz') if x in features), '80MHz')
        has_bluetooth = 'BT' in features
        has_embedded_flash = 'Embedded Flash' in features
        has_factory_calibrated_adc = 'VRef calibration in efuse' in features
        return ESP32ChipInfo(model, mac, num_cores, frequency, has_bluetooth,
                             has_embedded_flash, has_factory_calibrated_adc)
    elif isinstance(chip, esptool.ESP8266ROM):
        model = read_chip_property(chip.get_chip_description)
        chip_id = read_chip_property(chip.chip_id)
        return ESP8266ChipInfo(model, mac, chip_id)
    raise EsphomeflasherError("Unknown chip type {}".format(type(chip)))


def chip_run_stub(chip):
    try:
        return chip.run_stub()
    except esptool.FatalError as err:
        raise EsphomeflasherError("Error putting ESP in stub flash mode: {}".format(err))


def detect_flash_size(stub_chip):
    flash_id = read_chip_property(stub_chip.flash_id)
    return esptool.DETECTED_FLASH_SIZES.get(flash_id >> 16, '4MB')

def check_flash_size(stub_chip, offset):
    spiffs_offset = round(offset / 1024)
    flash_id = read_chip_property(stub_chip.flash_id)
    counter = 1
    for f in range(18, 25):
        if f == (flash_id >> 16):
            size = 256 * counter
            print("Flash Size: {}KB, SPIFFS Offset: {}KB".format(size, spiffs_offset))
            if size < spiffs_offset:
                return False
            else:
                return esptool.DETECTED_FLASH_SIZES.get(flash_id >> 16, '4MB')
        counter = counter * 2
        f += 1

def read_firmware_info(firmware):
    header = firmware.read(4)
    firmware.seek(0)

    magic, _, flash_mode_raw, flash_size_freq = struct.unpack("BBBB", header)
    if magic != esptool.ESPLoader.ESP_IMAGE_MAGIC:
        raise EsphomeflasherError(
            "The firmware binary is invalid (magic byte={:02X}, should be {:02X})"
            "".format(magic, esptool.ESPLoader.ESP_IMAGE_MAGIC))
    flash_freq_raw = flash_size_freq & 0x0F
    flash_mode = {0: 'qio', 1: 'qout', 2: 'dio', 3: 'dout'}.get(flash_mode_raw)
    flash_freq = {0: '40m', 1: '26m', 2: '20m', 0xF: '80m'}.get(flash_freq_raw)
    return flash_mode, flash_freq


def is_url(path):
    if isinstance(path, str):
        return HTTP_REGEX.match(path) is not None
    else:
        return False


def open_downloadable_binary(path):
    if hasattr(path, 'seek'):
        path.seek(0)
        return path

    if is_url(path):
        import requests

        try:
            response = requests.get(path)
            response.raise_for_status()
        except requests.exceptions.Timeout as err:
            raise EsphomeflasherError(
                "Timeout while retrieving firmware file '{}': {}".format(path, err))
        except requests.exceptions.RequestException as err:
            raise EsphomeflasherError(
                "Error while retrieving firmware file '{}': {}".format(path, err))

        binary = io.BytesIO(response.content)
        return binary

    try:
        return open(path, 'rb')
    except IOError as err:
        raise EsphomeflasherError("Error opening binary '{}': {}".format(path, err))


def open_binary_from_zip(zipfile, path):
    with zipfile.open(path, 'r') as f:
        binary = io.BytesIO(f.read())
    return binary


def fujinet_version_info(path):
    f = open_downloadable_binary(path)
    info = ''.join([ln.decode('us-ascii') for ln in f.readlines()[:3]])
    return info

def format_bootloader_path(path, flash_mode, flash_freq):
    return path.replace('$FLASH_MODE$', flash_mode).replace('$FLASH_FREQ$', flash_freq)

# No longer used
def configure_write_flash_args(info, firmware_path, flash_size,
                               bootloader_path, partitions_path,
                               otadata_path, spiffs_path):
    return 0
