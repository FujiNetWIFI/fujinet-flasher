from __future__ import print_function

import argparse
from datetime import datetime
import sys
import os
import time
import zipfile
import json

from esphomeflasher.common import open_downloadable_binary, open_binary_from_zip
from esphomeflasher.common import fujinet_version_info, is_url

import esptool
import serial

from esphomeflasher import const
from esphomeflasher.common import ESP32ChipInfo, EsphomeflasherError, chip_run_stub, \
    configure_write_flash_args, detect_chip, detect_flash_size, read_chip_info, \
    read_firmware_info, check_flash_size, MockEsptoolArgs
from esphomeflasher.const import ESP32_DEFAULT_BOOTLOADER_FORMAT, ESP32_DEFAULT_OTA_DATA, \
    ESP32_DEFAULT_PARTITIONS, ESP32_DEFAULT_FIRMWARE, ESP32_DEFAULT_SPIFFS, \
    FUJINET_VERSION_INFO, FUJINET_RELEASE_INFO
from esphomeflasher.helpers import list_serial_ports


# Set PYTHONUNBUFFERED environment variable to ensure unbuffered output
os.environ["PYTHONUNBUFFERED"] = "1"

def parse_args(argv):
    parser = argparse.ArgumentParser(prog='esphomeflasher {}'.format(const.__version__))
    parser.add_argument('-p', '--port',
                        help="Select the USB/COM port for uploading.")
    parser.add_argument('--upload-baud-rate', type=int, default=460800,
                       help="Baud rate to upload with (not for logging)")
    parser.add_argument('--no-erase',
                        help="Do not erase flash before flashing",
                        action='store_true')
    parser.add_argument('--show-logs', help="Only show logs", action='store_true')
    parser.add_argument('package', help="The package (zip file or URL) which contains files to flash.",
                        default=ESP32_DEFAULT_FIRMWARE)

    return parser.parse_args(argv[1:])

def select_port(args):
    if args.port is not None:
        print(u"Using '{}' as serial port.".format(args.port))
        return args.port
    ports = list_serial_ports()
    if not ports:
        raise EsphomeflasherError("No serial port found!")
    if len(ports) != 1:
        print("Found more than one serial port:")
        for port, desc in ports:
            print(u" * {} ({})".format(port, desc))
        print("Please choose one with the --port argument.")
        raise EsphomeflasherError
    print(u"Auto-detected serial port: {}".format(ports[0][0]))
    return ports[0][0]

def select_baud(args):
    if args.upload_baud_rate is not None:
        print(u"Using '{}' as baud rate.".format(args.upload_baud_rate))
        return args.upload_baud_rate

def show_logs(serial_port):
    print("Showing logs:")
    # close the port in case it's already open
    serial_port.close()
    # and reopen it
    serial_port.open()
    with serial_port:
        while True:
            try:
                raw = serial_port.readline()
            except serial.SerialException:
                print("Serial port closed!")
                return
            text = raw.decode(errors='ignore')
            line = text.replace('\r', '').replace('\n', '')
            time = datetime.now().time().strftime('[%H:%M:%S] ')
            message = time + line
            try:
                print(message)
            except UnicodeEncodeError:
                print(message.encode('ascii', 'backslashreplace'))

def run_esphomeflasher(argv):
    """run esphomeflasher with command line arguments"""
    # parse arguments
    args = parse_args(argv)
    # run flasher
    return run_esphomeflasher_args(args)

def run_esphomeflasher_kwargs(**kwargs):
    """run esphomeflasher with key=value,... arguments"""
    # prepare args
    args_dct = {
        'port': None,
        'upload_baud_rate': 460800,
        'no_erase': False,
        'show_logs': False,
    }
    args_dct.update(kwargs)
    args = argparse.Namespace(**args_dct)
    # run flasher
    return run_esphomeflasher_args(args)

def run_esphomeflasher_args(args):
    """run esphomeflasher with Namespace args object"""
    serial_port = None
    stub_chip = None

    try:
        port = select_port(args)
        baud = select_baud(args)

        if args.show_logs:
            serial_port = serial.Serial(port, baud)
            show_logs(serial_port)
            return

        print("Starting firmware upgrade...")
        if is_url(args.package):
            print("Getting firmware: {}".format(args.package))

        # open local file or download remote file
        package = open_downloadable_binary(args.package)

        addr_filename = []
        filecount = 0
        firmware = None
        # package is zip file
        with zipfile.ZipFile(package, 'r') as zf:
            release_info = json.load(open_binary_from_zip(zf, FUJINET_RELEASE_INFO))
            # Get all the partition files ready
            for file_entry in release_info.get('files', []):
                file_name = file_entry.get('filename')
                file_offset = file_entry.get('offset')
                if file_name is None or file_offset is None:
                    raise EsphomeflasherError("Invalid release info. Missing mandatory file attributes!")
                file_obj = open_binary_from_zip(zf, file_name)
                offset = int(file_offset, 16)
                addr_filename.append((offset, file_obj))
                if file_name.split(".", 1)[0].lower() == 'firmware':
                    firmware = file_obj
                if file_name.split(".", 1)[0].lower() == 'spiffs' or file_name.split(".", 1)[0].lower() == 'littlefs' or file_name.split(".", 1)[0].lower() == 'filesystem':
                    spiffs_start = offset
                filecount += 1
                print("File {}: {}, Offset: 0x{:04X}".format(filecount, file_name, offset))
        # Display firmware details
        print("FujiNet Version: {}".format(release_info.get('version', "")))
        print("Version Date: {}".format(release_info.get('version_date', "")))
        print("Git Commit: {}".format(release_info.get('git_commit', "")))

        # Verify "firmware" magic # and grab flash mode/frequency
        if firmware:
            flash_mode, flash_freq = read_firmware_info(firmware)
        else:
            raise EsphomeflasherError("Invalid release info. Missing firmware file!")

        chip = detect_chip(port, force_esp32=True)
        info = read_chip_info(chip)

        print()
        print("Chip Info:")
        print(" - Chip Family: {}".format(info.family))
        print(" - Chip Model: {}".format(info.model))
        if isinstance(info, ESP32ChipInfo):
            print(" - Number of Cores: {}".format(info.num_cores))
            print(" - Max CPU Frequency: {}".format(info.cpu_frequency))
            print(" - Has Bluetooth: {}".format('YES' if info.has_bluetooth else 'NO'))
            print(" - Has Embedded Flash: {}".format('YES' if info.has_embedded_flash else 'NO'))
            print(" - Has Factory-Calibrated ADC: {}".format(
                'YES' if info.has_factory_calibrated_adc else 'NO'))
        else:
            print(" - Chip ID: {:08X}".format(info.chip_id))

        print(" - MAC Address: {}".format(info.mac))

        stub_chip = chip_run_stub(chip)

        if args.upload_baud_rate != 115200:
            try:
                stub_chip.change_baud(args.upload_baud_rate)
            except esptool.FatalError as err:
                raise EsphomeflasherError("Error changing ESP upload baud rate: {}".format(err))

        flash_size = check_flash_size(stub_chip, spiffs_start)
        if not flash_size:
            raise EsphomeflasherError("Firmware larger than chip flash, stopping!")

        mock_args = MockEsptoolArgs(flash_size, addr_filename, flash_mode, flash_freq)

        print(" - Flash Mode: {}".format(mock_args.flash_mode))
        print(" - Flash Frequency: {}Hz".format(mock_args.flash_freq.upper()))

        try:
            stub_chip.flash_set_parameters(esptool.flash_size_bytes(flash_size))
        except esptool.FatalError as err:
            raise EsphomeflasherError("Error setting flash parameters: {}".format(err))

        if not args.no_erase:
            try:
                esptool.erase_flash(stub_chip, mock_args)
            except esptool.FatalError as err:
                raise EsphomeflasherError("Error while erasing flash: {}".format(err))

        try:
            esptool.write_flash(stub_chip, mock_args)
        except esptool.FatalError as err:
            raise EsphomeflasherError("Error while writing flash: {}".format(err))

        print("Hard Resetting...")
        stub_chip.hard_reset()

        print("Done! Flashing is complete!")
        print()

        time.sleep(0.05)
        stub_chip._port.flushInput()

        show_logs(stub_chip._port)
    finally:
        if serial_port:
            serial_port.close()
        if stub_chip:
            stub_chip._port.close()

def main():
    try:
        if len(sys.argv) <= 1:
            from esphomeflasher import gui

            return gui.main() or 0
        return run_esphomeflasher(sys.argv) or 0
    except EsphomeflasherError as err:
        msg = str(err)
        if msg:
            print(msg)
        return 1
    except KeyboardInterrupt:
        return 1

if __name__ == "__main__":
    sys.exit(main())
