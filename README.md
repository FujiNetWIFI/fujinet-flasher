# FujiNet Flasher

FujiNet Flasher is a utility app for [FujiNet Hardware](https://fujinet.online)
and is designed to make flashing as simple as possible by:

 * Having pre-built binaries for most modern operating systems
 * Hiding all non-essential options for flashing. All necessary options for flashing
   (bootloader, flash mode) are automatically extracted from the binary.
   
This project was originally intended to be a simple command-line tool,
but then it was decided that a GUI would be nice. The GUI largely is based on the 
[NodeMCU PyFlasher](https://github.com/marcelstoer/nodemcu-pyflasher) and
[esphomeflasher](https://github.com/esphome/esphome-flasher) projects.

The flashing process is done using the [esptool](https://github.com/espressif/esptool)
library by espressif.

## Boards with cartridge firmware

Some FujiNet boards (the "Fujiversal" designs: Intellivision, CoCo bus, MSX)
pair the ESP32 with a second microcontroller on the cartridge that plugs into
the retro machine. Its firmware travels *inside* the ESP32 image rather than as
a separate file, so flashing works exactly as it does for any other board — one
zip, one run, over the same serial port.

What differs is that flashing is not finished when esptool says it is. On the
next boot the FujiNet pushes the cartridge firmware across to the second chip
itself, and reports progress on the serial log with lines beginning `PICOFW:`.
The flasher points this out after writing the ESP32 and highlights those lines.
**Leave the board powered until one of them says `OK` or `up to date`.**

This only happens where the firmware calls for it; zips for every other board
behave, and print, exactly as before.

## Installation

It doesn't have to be installed, just double-click it and it'll start.
Check the [releases section](https://github.com/FujiNetWiFi/fujinet-flasher/releases)
for downloads for your platform.

## Build it yourself

If you want to build this application yourself:

- Install Python 3.x
- See `build-instructions.md`

## License

[MIT](http://opensource.org/licenses/MIT) © Marcel Stör, Otto Winter, Joe Honold
