"""macOS key codes and modifier flags the shortcut code names keys by."""
import AppKit

FLAGS = {"ctrl": AppKit.NSEventModifierFlagControl, "alt": AppKit.NSEventModifierFlagOption,
         "shift": AppKit.NSEventModifierFlagShift, "cmd": AppKit.NSEventModifierFlagCommand}
# ANSI key codes, so the shortcut follows the physical key under any keyboard layout (Hebrew, Russian...)
KEY_CODES = {0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c", 9: "v", 11: "b", 12: "q",
             13: "w", 14: "e", 15: "r", 16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5",
             25: "9", 26: "7", 28: "8", 29: "0", 31: "o", 32: "u", 34: "i", 35: "p", 37: "l", 38: "j", 40: "k",
             45: "n", 46: "m", 122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7",
             100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12"}
# keys that type no character, so the layout can't name them
SPECIAL = {36: "↩", 48: "⇥", 49: "Space", 51: "⌫", 53: "⎋", 71: "⌧", 76: "⌤", 114: "Help", 115: "↖",
           116: "⇞", 117: "⌦", 119: "↘", 121: "⇟", 123: "←", 124: "→", 125: "↓", 126: "↑",
           105: "F13", 107: "F14", 113: "F15", 106: "F16", 64: "F17", 79: "F18", 80: "F19", 90: "F20"}
KEYPAD = {65, 67, 69, 75, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92}  # type the same as the main keys
# Physical modifier keycodes and NX_DEVICE* masks from Apple's IOLLEvent.h.
MOD_CODES = {59: ("ctrl", 0x1), 62: ("ctrl", 0x2000), 56: ("shift", 0x2), 60: ("shift", 0x4),
             58: ("alt", 0x20), 61: ("alt", 0x40), 55: ("cmd", 0x8), 54: ("cmd", 0x10)}
