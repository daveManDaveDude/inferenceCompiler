# Direct PE Build Notes

This project has a deliberately unusual build path: the Win32 calculator is
packaged by writing a PE32 executable directly from Python.

## Why this exists

The target machine does not need C++ build tools. The executable is generated
without using `cl`, `link`, `ml`, CMake, MSBuild, MinGW, NASM, or any similar
compiler/assembler/linker tool.

## Files that matter

- `src/main.cpp` is the readable behavioral reference for the calculator.
- `tools/emit_win32_calculator.py` is the actual current build path.
- `build/Win32Calculator.exe` is generated output and is intentionally ignored.

## Build command

```powershell
python .\tools\emit_win32_calculator.py
```

Expected output:

```text
Wrote C:\work\personal_dev\calculator\build\Win32Calculator.exe (5120 bytes)
```

The exact size can change if the emitter changes.

## Design constraints

- No external libraries.
- No generated object files.
- No compiler, assembler, or linker invocation.
- No reasoning linker yet. Keep imports, RVAs, PE fields, and machine-code
  emission explicit in the script.
- Prefer small, local helper methods in the emitter over generic abstractions.
- If a helper starts looking like a linker, stop and document the need first.

## Current executable shape

- PE type: PE32, x86.
- Subsystem: Windows GUI.
- Image base: `0x00400000`.
- Single section: `.text`, containing code, mutable data, strings, and imports.
- Imports:
  - `kernel32.dll`: `ExitProcess`, `GetModuleHandleW`
  - `user32.dll`: window creation, message loop, controls, `wsprintfW`
  - `gdi32.dll`: `GetStockObject`

The program creates a classic Win32 window titled `Calculator`, a read-only edit
display, and 17 button controls.

## Behavioral coverage currently represented

The emitter mirrors the C++ calculator behavior:

- digit and decimal input
- clear
- `+`, `-`, `*`, `/`
- equals
- fixed-point arithmetic using scale `10000`
- divide-by-zero message
- overflow clamping behavior

## Suggested future iteration workflow

1. Change `src/main.cpp` first if the behavior needs to be clarified.
2. Port the behavior into `tools/emit_win32_calculator.py` explicitly.
3. Run `python .\tools\emit_win32_calculator.py`.
4. Validate PE structure if headers/imports changed.
5. Launch the generated EXE and smoke test the affected UI path.

Keep the build process boring and visible. This repo is not ready for a generic
assembler or linker layer.
