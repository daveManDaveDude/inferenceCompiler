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

## Constraints

- Do not add libraries or dependencies.
- Do not introduce a general-purpose linker abstraction yet.
- Keep the emitter specific and explicit until the build process needs a real
  abstraction.
- The C++ source remains the behavioral reference, but the generated EXE comes
  from `tools\emit_win32_calculator.py`.

## Verification

The current generated binary has been smoke tested by launching the Win32 GUI
and invoking `7 + 8 =` through UI Automation. The display returned `15`.
