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
controls through UI Automation, invokes `7`, `+`, `8`, `=`, and checks that the
display reads `15`.

This requires permission to launch a GUI process from the agent environment.
