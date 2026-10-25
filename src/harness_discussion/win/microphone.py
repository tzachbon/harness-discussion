"""Windows desktop microphone privacy hints. Opening the recording stream is the final availability check."""
import winreg

CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def status():
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, CONSENT), (winreg.HKEY_CURRENT_USER, CONSENT),
                      (winreg.HKEY_CURRENT_USER, CONSENT + r"\NonPackaged")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                if winreg.QueryValueEx(handle, "Value")[0] == "Deny":
                    return "Blocked in Windows privacy settings"
        except FileNotFoundError:
            pass  # missing consent entries do not deny desktop applications
        except OSError:
            return "Could not check Windows privacy settings"
    try:
        import sounddevice
        if sounddevice.query_devices(kind="input")["max_input_channels"] > 0:
            return "Available"
    except Exception:
        pass
    return "No microphone available"
