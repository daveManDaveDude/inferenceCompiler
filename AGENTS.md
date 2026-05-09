# Codex Agent Notes

This repo builds a native Win32 calculator by emitting a PE32 executable
directly. Treat that as the primary project constraint.

## Build Rules

- Do not use a C/C++ compiler, assembler, linker, CMake, MSBuild, Visual Studio,
  MinGW, NASM, or SDK build tools to produce the active executable.
- Do not add libraries or package dependencies.
- Do not introduce a general-purpose linker or "reasoning linker" abstraction
  yet.
- Keep PE fields, RVAs, imports, data layout, and x86 instruction emission
  explicit in `tools/emit_win32_calculator.py`.
- Small local helpers are fine when they keep repeated byte emission readable.
  Avoid broad abstractions that hide the executable layout.

## Important Files

- `tools/emit_win32_calculator.py` is the actual build path.
- `src/main.cpp` is the readable behavioral reference.
- `docs/direct-pe-build.md` records the build constraints and executable shape.
- `docs/verification.md` records validation and smoke-test commands.
- `build/Win32Calculator.exe` is generated output and should remain untracked.

## Standard Workflow

1. Read `docs/direct-pe-build.md` before changing build behavior.
2. Clarify behavior in `src/main.cpp` if needed.
3. Port behavior explicitly into `tools/emit_win32_calculator.py`.
4. Run:

   ```powershell
   python .\tools\emit_win32_calculator.py
   ```

5. If PE headers or imports changed, validate the PE structure.
6. If UI behavior changed, launch the generated EXE and smoke test the affected
   path.

## Current Baseline

- Target: PE32 x86 Windows GUI executable.
- Image base: `0x00400000`.
- Single section: `.text`.
- Imports: `kernel32.dll`, `user32.dll`, `gdi32.dll`.
- Known smoke test: invoke `7 + 8 =`; display should read `15`.

Keep future changes conservative and explicit. This project values a transparent
manual build process over generality.
