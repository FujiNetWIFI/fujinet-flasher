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
