# Win32 Calculator

A minimal native Windows calculator written in C++ with the Win32 API.

## Current build path

This repo's active artifact is produced without a C/C++ compiler, assembler,
linker, CMake, MSBuild, or Visual Studio build tools:

```powershell
python .\tools\emit_win32_calculator.py
```

That writes `build\Win32Calculator.exe` directly as a PE32 GUI executable.
The Python script emits the PE headers, import table, UTF-16 data, and
hand-encoded x86 instructions.

## Calculator behavior

- Arithmetic supports large floating-point values up to roughly `1e308`.
- Large and tiny results are shown in scientific notation, such as `1e+12`.
- Results that cannot be represented display `ERROR`.
- Division by zero displays `ERROR`.
- Pressing `=` again repeats the last completed operation.
- The display uses a larger font for better readability.

## Constraints

- Do not add libraries or dependencies.
- Do not introduce a general-purpose linker abstraction yet.
- Keep the emitter specific and explicit until the build process needs a real
  abstraction.
- The C++ source remains the behavioral reference, but the generated EXE comes
  from `tools\emit_win32_calculator.py`.

## Verification

The current generated binary has been smoke tested by launching the Win32 GUI
and invoking `9 * 9 =`. The display returned `81`.

For automated GUI checks, prefer Win32 messages over UI Automation patterns:
find the calculator window by process id, get child controls with `GetDlgItem`,
send `BM_CLICK` to the button HWNDs, and read the display with `WM_GETTEXT`.
In the current agent environment, UI Automation may expose the raw Win32
controls as panes without invoke patterns, and cross-process `GetWindowTextW`
can return stale edit text.
