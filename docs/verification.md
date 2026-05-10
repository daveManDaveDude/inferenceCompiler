# Verification

## PE structure check

Useful PowerShell command:

```powershell
@'
from pathlib import Path
import struct

b = Path("build/Win32Calculator.exe").read_bytes()
pe = struct.unpack_from("<I", b, 0x3c)[0]
coff = pe + 4
machine, sections, _, _, _, opth, chars = struct.unpack_from("<HHIIIHH", b, coff)
opt = coff + 20
magic = struct.unpack_from("<H", b, opt)[0]
entry = struct.unpack_from("<I", b, opt + 16)[0]
image = struct.unpack_from("<I", b, opt + 28)[0]
subsystem = struct.unpack_from("<H", b, opt + 68)[0]
imports = struct.unpack_from("<II", b, opt + 96 + 8)

print("MZ", b[:2])
print("PE", b[pe:pe+4])
print("machine", hex(machine), "sections", sections, "chars", hex(chars))
print("magic", hex(magic), "entry", hex(entry), "image", hex(image))
print("subsystem", subsystem, "imports", tuple(hex(x) for x in imports))
'@ | python -
```

Expected high-level values:

- `MZ`
- `PE\0\0`
- machine `0x14c`
- optional-header magic `0x10b`
- subsystem `2`

## GUI smoke test

The current smoke test launches the generated executable, finds the Win32
controls, invokes `9`, `*`, `9`, `=`, and checks with `WM_GETTEXT` that the
display reads `81`.

This requires permission to launch a GUI process from the agent environment.

Useful PowerShell command:

```powershell
@'
import ctypes
import subprocess
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

p = subprocess.Popen([r"build\Win32Calculator.exe"])
main_hwnd = 0
try:
    deadline = time.time() + 5
    while time.time() < deadline and not main_hwnd:
        found = []

        @EnumWindowsProc
        def enum_proc(hwnd, _):
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == p.pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(enum_proc, 0)
        if found:
            main_hwnd = found[0]
        time.sleep(0.1)

    if not main_hwnd:
        raise RuntimeError("Calculator window not found")

    for control_id in (206, 202, 206, 211):  # 9, *, 9, =
        child = user32.GetDlgItem(main_hwnd, control_id)
        if not child:
            raise RuntimeError(f"Control {control_id} not found")
        user32.SendMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
        time.sleep(0.08)

    display = user32.GetDlgItem(main_hwnd, 100)
    buf = ctypes.create_unicode_buffer(128)
    user32.SendMessageW(display, 0x000D, len(buf), ctypes.cast(buf, ctypes.c_void_p).value)  # WM_GETTEXT
    if buf.value != "81":
        raise RuntimeError(f"Expected 81, got {buf.value!r}")
    print(f"Smoke result: 9 * 9 = {buf.value}")
finally:
    if main_hwnd:
        user32.PostMessageW(main_hwnd, 0x0010, 0, 0)  # WM_CLOSE
    try:
        p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=2)
'@ | python -
```

Notes learned from the GUI test:

- UI Automation can see these raw Win32 `Button` and `Edit` controls as panes
  without invoke patterns.
- `BM_CLICK` on each button HWND exercises the real button path.
- Read the display with `WM_GETTEXT`; cross-process `GetWindowTextW` can return
  stale edit text in this environment.
