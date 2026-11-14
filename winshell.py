"""Windows-only touches so StudyTube looks like a real app, not "Python".

Why the Python icon shows up: the app runs inside pythonw.exe, so Windows groups the
window under Python and uses python's icon. Fix:
  1. give the process its own AppUserModelID (its own taskbar identity),
  2. set our .ico on the window (title bar, Alt+Tab, taskbar),
  3. tell the taskbar how to relaunch/pin it (command, name, icon),
  4. stamp the same ID on the Desktop/Start-menu shortcuts so pinned + running icons merge.
Every function is best-effort and silently does nothing on other systems.
"""
import ctypes
import logging
import os
import sys
from pathlib import Path

APP_ID = "CDAC.StudyTube.App"
BASE = Path(__file__).resolve().parent
ICON = BASE / "icon.ico"
IS_WIN = sys.platform == "win32"


def set_app_id():
    """Call before any window is created."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        logging.warning("AppUserModelID not set: %s", e)


def _our_windows():
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def _set_relaunch_props(hwnd):
    """So 'Pin to taskbar' on the running window pins StudyTube, not Python."""
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
    except ImportError:
        return  # pywin32 not installed: icons still work, pinning may show Python
    pyw = Path(sys.executable).with_name("pythonw.exe")
    command = f'"{pyw if pyw.exists() else sys.executable}" "{BASE / "launcher.py"}"'
    store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
    for key, value in ((pscon.PKEY_AppUserModel_ID, APP_ID),
                       (pscon.PKEY_AppUserModel_RelaunchCommand, command),
                       (pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, "StudyTube"),
                       (pscon.PKEY_AppUserModel_RelaunchIconResource, f"{ICON},0")):
        store.SetValue(key, propsys.PROPVARIANTType(value, pythoncom.VT_LPWSTR))
    store.Commit()


def apply_window_icon():
    """Call once the window is visible (pywebview 'shown' event)."""
    if not IS_WIN or not ICON.exists():
        return
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        set_class = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
        set_class.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        GCLP_HICON, GCLP_HICONSM = -14, -34

        for hwnd in _our_windows():
            try:
                dpi = user32.GetDpiForWindow(hwnd) or 96
            except Exception:
                dpi = 96
            small = user32.LoadImageW(None, str(ICON), IMAGE_ICON, round(16 * dpi / 96), round(16 * dpi / 96), LR_LOADFROMFILE)
            big = user32.LoadImageW(None, str(ICON), IMAGE_ICON, round(32 * dpi / 96), round(32 * dpi / 96), LR_LOADFROMFILE)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, 0, small)
                set_class(hwnd, GCLP_HICONSM, small)
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, 1, big)
                set_class(hwnd, GCLP_HICON, big)
            try:
                _set_relaunch_props(hwnd)
            except Exception as e:
                logging.info("relaunch properties not set: %s", e)
    except Exception as e:
        logging.warning("window icon not set: %s", e)


def stamp_shortcut(lnk_path):
    """Give a .lnk the same AppUserModelID, so pinned and running icons are one and the same."""
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shellcon
    store = propsys.SHGetPropertyStoreFromParsingName(str(lnk_path), None, shellcon.GPS_READWRITE,
                                                     propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
    store.Commit()


if __name__ == "__main__":
    # used by the installer:  python winshell.py stamp <shortcut.lnk> [...]
    if len(sys.argv) > 2 and sys.argv[1] == "stamp":
        for p in sys.argv[2:]:
            try:
                stamp_shortcut(p)
                print("   app id set:", p)
            except Exception as e:
                print("   (could not set app id on", p, "-", e, ")")
