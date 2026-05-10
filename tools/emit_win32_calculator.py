#!/usr/bin/env python3
"""
Emit a 32-bit Win32 calculator executable without invoking a compiler,
assembler, linker, or SDK build tool.

The generated program is a PE32 GUI executable.  This script writes the PE
headers, import table, data, and hand-encoded x86 instructions directly.
"""

from __future__ import annotations

import struct
from pathlib import Path


IMAGE_BASE = 0x00400000
SECTION_RVA = 0x1000
FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000

EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI = range(8)


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def s8(value: int) -> int:
    if value < 0:
        value += 0x100
    return value & 0xFF


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


class Emitter:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[str, int, str, int]] = []
        self.unique_id = 0

    def here(self) -> int:
        return len(self.buf)

    def unique(self, prefix: str) -> str:
        self.unique_id += 1
        return f"{prefix}_{self.unique_id}"

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = self.here()

    def emit(self, *values: int) -> None:
        self.buf.extend(v & 0xFF for v in values)

    def bytes(self, values: bytes | bytearray) -> None:
        self.buf.extend(values)

    def zbytes(self, count: int) -> None:
        self.buf.extend(b"\0" * count)

    def u16(self, value: int) -> None:
        self.bytes(struct.pack("<H", value & 0xFFFF))

    def u32(self, value: int) -> None:
        self.bytes(struct.pack("<I", value & 0xFFFFFFFF))

    def patch_u32(self, offset: int, value: int) -> None:
        self.buf[offset : offset + 4] = struct.pack("<I", value & 0xFFFFFFFF)

    def align(self, boundary: int, pad: int = 0) -> None:
        while len(self.buf) % boundary:
            self.emit(pad)

    def add_fixup(self, kind: str, label: str, addend: int = 0) -> None:
        self.fixups.append((kind, self.here(), label, addend))
        self.u32(0)

    def abs32(self, label: str, addend: int = 0) -> None:
        self.add_fixup("abs", label, addend)

    def rva32(self, label: str, addend: int = 0) -> None:
        self.add_fixup("rva", label, addend)

    def rel32(self, label: str, addend: int = 0) -> None:
        self.add_fixup("rel", label, addend)

    def resolve(self) -> None:
        for kind, pos, label, addend in self.fixups:
            if label not in self.labels:
                raise ValueError(f"missing label: {label}")
            target = self.labels[label] + addend
            if kind == "abs":
                value = IMAGE_BASE + SECTION_RVA + target
            elif kind == "rva":
                value = SECTION_RVA + target
            elif kind == "rel":
                value = target - (pos + 4)
            else:
                raise ValueError(f"unknown fixup kind: {kind}")
            self.patch_u32(pos, value)


class X86:
    JCC = {
        "o": 0x80,
        "no": 0x81,
        "b": 0x82,
        "ae": 0x83,
        "e": 0x84,
        "ne": 0x85,
        "be": 0x86,
        "a": 0x87,
        "s": 0x88,
        "ns": 0x89,
        "l": 0x8C,
        "ge": 0x8D,
        "le": 0x8E,
        "g": 0x8F,
    }

    def __init__(self, e: Emitter) -> None:
        self.e = e

    def modrm_reg_reg(self, reg_field: int, rm_reg: int) -> int:
        return 0xC0 | ((reg_field & 7) << 3) | (rm_reg & 7)

    def modrm_mem(self, reg_field: int, base: int, disp: int = 0) -> bytes:
        if base == ESP:
            raise NotImplementedError("SIB addressing is not used by this emitter")
        if base == EBP and disp == 0:
            return bytes([0x45 | ((reg_field & 7) << 3), 0])
        if -128 <= disp <= 127:
            return bytes([0x40 | ((reg_field & 7) << 3) | (base & 7), s8(disp)])
        return bytes([0x80 | ((reg_field & 7) << 3) | (base & 7)]) + struct.pack("<i", disp)

    def push_imm(self, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x6A, signed & 0xFF)
        else:
            self.e.emit(0x68)
            self.e.u32(value)

    def push_reg(self, reg: int) -> None:
        self.e.emit(0x50 + reg)

    def push_ebp(self, disp: int) -> None:
        self.e.emit(0xFF)
        self.e.bytes(self.modrm_mem(6, EBP, disp))

    def push_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xFF, 0x35)
        self.e.abs32(label, addend)

    def push_label(self, label: str, addend: int = 0) -> None:
        self.e.emit(0x68)
        self.e.abs32(label, addend)

    def pop_reg(self, reg: int) -> None:
        self.e.emit(0x58 + reg)

    def call(self, label: str) -> None:
        self.e.emit(0xE8)
        self.e.rel32(label)

    def call_import(self, name: str) -> None:
        self.e.emit(0xFF, 0x15)
        self.e.abs32(f"iat_{name}")

    def jmp(self, label: str) -> None:
        self.e.emit(0xE9)
        self.e.rel32(label)

    def jcc(self, cc: str, label: str) -> None:
        self.e.emit(0x0F, self.JCC[cc])
        self.e.rel32(label)

    def ret(self, bytes_to_pop: int = 0) -> None:
        if bytes_to_pop:
            self.e.emit(0xC2)
            self.e.u16(bytes_to_pop)
        else:
            self.e.emit(0xC3)

    def prologue(self, local_bytes: int = 0) -> None:
        self.e.emit(0x55, 0x8B, 0xEC)
        if local_bytes:
            self.sub_reg_imm(ESP, local_bytes)

    def epilogue(self, bytes_to_pop: int = 0) -> None:
        self.e.emit(0xC9)
        self.ret(bytes_to_pop)

    def mov_reg_imm(self, reg: int, value: int) -> None:
        self.e.emit(0xB8 + reg)
        self.e.u32(value)

    def mov_reg_label(self, reg: int, label: str, addend: int = 0) -> None:
        self.e.emit(0xB8 + reg)
        self.e.abs32(label, addend)

    def mov_reg_reg(self, dst: int, src: int) -> None:
        self.e.emit(0x89, self.modrm_reg_reg(src, dst))

    def mov_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x8B)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def mov_ebp_reg(self, disp: int, reg: int) -> None:
        self.e.emit(0x89)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def mov_ebp_imm(self, disp: int, value: int) -> None:
        self.e.emit(0xC7)
        self.e.bytes(self.modrm_mem(0, EBP, disp))
        self.e.u32(value)

    def mov_reg_abs(self, reg: int, label: str, addend: int = 0) -> None:
        self.e.emit(0x8B, ((reg & 7) << 3) | 0x05)
        self.e.abs32(label, addend)

    def mov_abs_reg(self, label: str, reg: int, addend: int = 0) -> None:
        self.e.emit(0x89, ((reg & 7) << 3) | 0x05)
        self.e.abs32(label, addend)

    def mov_abs_imm(self, label: str, value: int, addend: int = 0) -> None:
        self.e.emit(0xC7, 0x05)
        self.e.abs32(label, addend)
        self.e.u32(value)

    def mov_abs_label(self, label: str, value_label: str, addend: int = 0) -> None:
        self.e.emit(0xC7, 0x05)
        self.e.abs32(label, addend)
        self.e.abs32(value_label)

    def mov_word_abs_imm(self, label: str, value: int, addend: int = 0) -> None:
        self.e.emit(0x66, 0xC7, 0x05)
        self.e.abs32(label, addend)
        self.e.u16(value)

    def mov_word_abs_ax(self, label: str, addend: int = 0) -> None:
        self.e.emit(0x66, 0xA3)
        self.e.abs32(label, addend)

    def movzx_reg_word_abs(self, reg: int, label: str, addend: int = 0) -> None:
        self.e.emit(0x0F, 0xB7, ((reg & 7) << 3) | 0x05)
        self.e.abs32(label, addend)

    def movzx_reg_word_ptr(self, reg: int, base: int, disp: int = 0) -> None:
        self.e.emit(0x0F, 0xB7)
        self.e.bytes(self.modrm_mem(reg, base, disp))

    def mov_word_ptr_reg16(self, base: int, reg: int, disp: int = 0) -> None:
        self.e.emit(0x66, 0x89)
        self.e.bytes(self.modrm_mem(reg, base, disp))

    def mov_word_ptr_imm(self, base: int, disp: int, value: int) -> None:
        self.e.emit(0x66, 0xC7)
        self.e.bytes(self.modrm_mem(0, base, disp))
        self.e.u16(value)

    def cmp_reg_imm(self, reg: int, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83, 0xF8 | reg, signed & 0xFF)
        else:
            self.e.emit(0x81, 0xF8 | reg)
            self.e.u32(value)

    def cmp_reg_reg(self, left: int, right: int) -> None:
        self.e.emit(0x39, self.modrm_reg_reg(right, left))

    def cmp_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x3B)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def cmp_ebp_reg(self, disp: int, reg: int) -> None:
        self.e.emit(0x39)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def cmp_ebp_imm(self, disp: int, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83)
            self.e.bytes(self.modrm_mem(7, EBP, disp))
            self.e.emit(signed & 0xFF)
        else:
            self.e.emit(0x81)
            self.e.bytes(self.modrm_mem(7, EBP, disp))
            self.e.u32(value)

    def cmp_abs_imm(self, label: str, value: int, addend: int = 0) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83, 0x3D)
            self.e.abs32(label, addend)
            self.e.emit(signed & 0xFF)
        else:
            self.e.emit(0x81, 0x3D)
            self.e.abs32(label, addend)
            self.e.u32(value)

    def test_reg_reg(self, reg: int) -> None:
        self.e.emit(0x85, self.modrm_reg_reg(reg, reg))

    def xor_reg_reg(self, dst: int, src: int) -> None:
        self.e.emit(0x31, self.modrm_reg_reg(src, dst))

    def xor_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x33)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def and_reg_imm(self, reg: int, value: int) -> None:
        if reg == EAX:
            self.e.emit(0x25)
            self.e.u32(value)
        else:
            self.e.emit(0x81, 0xE0 | reg)
            self.e.u32(value)

    def add_reg_reg(self, dst: int, src: int) -> None:
        self.e.emit(0x01, self.modrm_reg_reg(src, dst))

    def add_reg_imm(self, reg: int, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83, 0xC0 | reg, signed & 0xFF)
        else:
            self.e.emit(0x81, 0xC0 | reg)
            self.e.u32(value)

    def add_reg_label(self, reg: int, label: str, addend: int = 0) -> None:
        self.e.emit(0x81, 0xC0 | reg)
        self.e.abs32(label, addend)

    def add_abs_imm(self, label: str, value: int, addend: int = 0) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83, 0x05)
            self.e.abs32(label, addend)
            self.e.emit(signed & 0xFF)
        else:
            self.e.emit(0x81, 0x05)
            self.e.abs32(label, addend)
            self.e.u32(value)

    def add_ebp_imm(self, disp: int, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83)
            self.e.bytes(self.modrm_mem(0, EBP, disp))
            self.e.emit(signed & 0xFF)
        else:
            self.e.emit(0x81)
            self.e.bytes(self.modrm_mem(0, EBP, disp))
            self.e.u32(value)

    def add_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x03)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def sub_reg_reg(self, dst: int, src: int) -> None:
        self.e.emit(0x29, self.modrm_reg_reg(src, dst))

    def sub_reg_imm(self, reg: int, value: int) -> None:
        signed = value if value < 0x80000000 else value - 0x100000000
        if -128 <= signed <= 127:
            self.e.emit(0x83, 0xE8 | reg, signed & 0xFF)
        else:
            self.e.emit(0x81, 0xE8 | reg)
            self.e.u32(value)

    def sub_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x2B)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def inc_reg(self, reg: int) -> None:
        self.e.emit(0x40 + reg)

    def dec_reg(self, reg: int) -> None:
        self.e.emit(0x48 + reg)

    def neg_reg(self, reg: int) -> None:
        self.e.emit(0xF7, 0xD8 | reg)

    def cdq(self) -> None:
        self.e.emit(0x99)

    def div_reg(self, reg: int) -> None:
        self.e.emit(0xF7, 0xF0 | reg)

    def idiv_reg(self, reg: int) -> None:
        self.e.emit(0xF7, 0xF8 | reg)

    def imul_eax_reg(self, reg: int) -> None:
        self.e.emit(0xF7, 0xE8 | reg)

    def imul_eax_ebp(self, disp: int) -> None:
        self.e.emit(0xF7)
        self.e.bytes(self.modrm_mem(5, EBP, disp))

    def imul_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x0F, 0xAF)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def imul_reg_imm(self, reg: int, value: int) -> None:
        self.e.emit(0x69, self.modrm_reg_reg(reg, reg))
        self.e.u32(value)

    def shl_reg_imm(self, reg: int, value: int) -> None:
        self.e.emit(0xC1, 0xE0 | reg, value & 0xFF)

    def or_reg_imm(self, reg: int, value: int) -> None:
        if reg == EAX:
            self.e.emit(0x0D)
            self.e.u32(value)
        else:
            self.e.emit(0x81, 0xC8 | reg)
            self.e.u32(value)

    def test_reg_imm(self, reg: int, value: int) -> None:
        if reg == EAX:
            self.e.emit(0xA9)
            self.e.u32(value)
        else:
            self.e.emit(0xF7, 0xC0 | reg)
            self.e.u32(value)

    def lea_reg_ebp(self, reg: int, disp: int) -> None:
        self.e.emit(0x8D)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def mov_reg_ptr(self, reg: int, base: int, disp: int = 0) -> None:
        self.e.emit(0x8B)
        self.e.bytes(self.modrm_mem(reg, base, disp))

    def mov_ptr_reg(self, base: int, reg: int, disp: int = 0) -> None:
        self.e.emit(0x89)
        self.e.bytes(self.modrm_mem(reg, base, disp))

    def mov_word_ebp_reg16(self, disp: int, reg: int) -> None:
        self.e.emit(0x66, 0x89)
        self.e.bytes(self.modrm_mem(reg, EBP, disp))

    def fnstcw_ebp(self, disp: int) -> None:
        self.e.emit(0xD9)
        self.e.bytes(self.modrm_mem(7, EBP, disp))

    def fldcw_ebp(self, disp: int) -> None:
        self.e.emit(0xD9)
        self.e.bytes(self.modrm_mem(5, EBP, disp))

    def fld_mem64_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDD, 0x05)
        self.e.abs32(label, addend)

    def fld_mem64_ebp(self, disp: int) -> None:
        self.e.emit(0xDD)
        self.e.bytes(self.modrm_mem(0, EBP, disp))

    def fstp_mem64_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDD, 0x1D)
        self.e.abs32(label, addend)

    def fstp_mem64_ebp(self, disp: int) -> None:
        self.e.emit(0xDD)
        self.e.bytes(self.modrm_mem(3, EBP, disp))

    def fild_mem32_ebp(self, disp: int) -> None:
        self.e.emit(0xDB)
        self.e.bytes(self.modrm_mem(0, EBP, disp))

    def fild_mem32_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDB, 0x05)
        self.e.abs32(label, addend)

    def fistp_mem32_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDB, 0x1D)
        self.e.abs32(label, addend)

    def fmul_mem64_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDC, 0x0D)
        self.e.abs32(label, addend)

    def fmul_mem64_ebp(self, disp: int) -> None:
        self.e.emit(0xDC)
        self.e.bytes(self.modrm_mem(1, EBP, disp))

    def fadd_mem64_ptr(self, base: int, disp: int = 0) -> None:
        self.e.emit(0xDC)
        self.e.bytes(self.modrm_mem(0, base, disp))

    def fsub_mem64_ptr(self, base: int, disp: int = 0) -> None:
        self.e.emit(0xDC)
        self.e.bytes(self.modrm_mem(4, base, disp))

    def fmul_mem64_ptr(self, base: int, disp: int = 0) -> None:
        self.e.emit(0xDC)
        self.e.bytes(self.modrm_mem(1, base, disp))

    def fdiv_mem64_ptr(self, base: int, disp: int = 0) -> None:
        self.e.emit(0xDC)
        self.e.bytes(self.modrm_mem(6, base, disp))

    def fdiv_mem64_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDC, 0x35)
        self.e.abs32(label, addend)

    def fcomp_mem64_abs(self, label: str, addend: int = 0) -> None:
        self.e.emit(0xDC, 0x1D)
        self.e.abs32(label, addend)

    def faddp_st1(self) -> None:
        self.e.emit(0xDE, 0xC1)

    def fsubp_st1(self) -> None:
        self.e.emit(0xDE, 0xE9)

    def fchs(self) -> None:
        self.e.emit(0xD9, 0xE0)

    def fnstsw_ax(self) -> None:
        self.e.emit(0xDF, 0xE0)

    def sahf(self) -> None:
        self.e.emit(0x9E)


def emit_wstr(e: Emitter, label: str, text: str) -> None:
    e.align(2)
    e.label(label)
    e.bytes(text.encode("utf-16le"))
    e.u16(0)


def emit_asciiz(e: Emitter, label: str, text: str) -> None:
    e.label(label)
    e.bytes(text.encode("ascii") + b"\0")


def emit_program(e: Emitter, a: X86) -> None:
    # Entry point: HINSTANCE instance = GetModuleHandleW(nullptr);
    # ExitProcess(RunCalculator(instance, SW_SHOWNORMAL));
    e.label("entry")
    a.push_imm(0)
    a.call_import("GetModuleHandleW")
    a.mov_abs_reg("g_instance", EAX)
    a.push_imm(1)
    a.push_reg(EAX)
    a.call("RunCalculator")
    a.push_reg(EAX)
    a.call_import("ExitProcess")

    emit_text_length(e, a)
    emit_copy_text(e, a)
    emit_update_display(e, a)
    emit_set_error_v2(e, a)
    emit_clear_calculator(e, a)
    emit_contains_decimal(e, a)
    emit_append_input(e, a)
    emit_append_current_char_v2(e, a)
    emit_append_digit_index_v2(e, a)
    emit_append_digits_range_v2(e, a)
    emit_append_zero_repeat_v2(e, a)
    emit_append_unsigned_int_v2(e, a)
    emit_trim_fraction_zeros(e, a)
    emit_try_current_value_v2(e, a)
    emit_format_number_v2(e, a)
    emit_apply_operation_v2(e, a)
    emit_input_digit_v2(e, a)
    emit_input_operator_v2(e, a)
    emit_calculate_result_v2(e, a)
    emit_create_button(e, a)
    emit_create_controls(e, a)
    emit_handle_button(e, a)
    emit_window_proc(e, a)
    emit_run_calculator(e, a)


def emit_text_length(e: Emitter, a: X86) -> None:
    e.label("TextLength")
    a.prologue()
    a.mov_reg_ebp(ECX, 8)
    a.xor_reg_reg(EAX, EAX)
    loop = e.unique("text_length_loop")
    done = e.unique("text_length_done")
    e.label(loop)
    a.cmp_reg_imm(EAX, 127)
    a.jcc("ge", done)
    a.movzx_reg_word_ptr(EDX, ECX)
    a.test_reg_reg(EDX)
    a.jcc("e", done)
    a.add_reg_imm(ECX, 2)
    a.inc_reg(EAX)
    a.jmp(loop)
    e.label(done)
    a.epilogue(4)


def emit_copy_text(e: Emitter, a: X86) -> None:
    e.label("CopyText")
    a.prologue()
    a.mov_reg_ebp(EDX, 8)
    a.mov_reg_label(ECX, "g_current")
    a.xor_reg_reg(EAX, EAX)
    loop = e.unique("copy_text_loop")
    term = e.unique("copy_text_term")
    e.label(loop)
    a.cmp_reg_imm(EAX, 127)
    a.jcc("ge", term)
    e.emit(0x66, 0x8B, 0x1A)  # mov bx, word ptr [edx]
    e.emit(0x66, 0x85, 0xDB)  # test bx, bx
    a.jcc("e", term)
    e.emit(0x66, 0x89, 0x19)  # mov word ptr [ecx], bx
    a.add_reg_imm(EDX, 2)
    a.add_reg_imm(ECX, 2)
    a.inc_reg(EAX)
    a.jmp(loop)
    e.label(term)
    a.mov_word_ptr_imm(ECX, 0, 0)
    a.epilogue(4)


def emit_update_display(e: Emitter, a: X86) -> None:
    e.label("UpdateDisplay")
    done = e.unique("update_display_done")
    a.cmp_abs_imm("g_display", 0)
    a.jcc("e", done)
    a.push_label("g_current")
    a.push_abs("g_display")
    a.call_import("SetWindowTextW")
    e.label(done)
    a.ret()


def emit_set_error_v2(e: Emitter, a: X86) -> None:
    e.label("SetError")
    a.push_label("str_error")
    a.call("CopyText")
    a.mov_abs_imm("g_stored", 0)
    a.mov_abs_imm("g_stored", 0, 4)
    a.mov_abs_imm("g_pendingOp", 0)
    a.mov_abs_imm("g_lastOperand", 0)
    a.mov_abs_imm("g_lastOperand", 0, 4)
    a.mov_abs_imm("g_lastOp", 0)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.mov_abs_imm("g_hasLastOperation", 0)
    a.mov_abs_imm("g_error", 1)
    a.call("UpdateDisplay")
    a.ret()


def emit_clear_calculator(e: Emitter, a: X86) -> None:
    e.label("ClearCalculator")
    a.push_label("str_zero")
    a.call("CopyText")
    a.mov_abs_imm("g_stored", 0)
    a.mov_abs_imm("g_stored", 0, 4)
    a.mov_abs_imm("g_pendingOp", 0)
    a.mov_abs_imm("g_lastOperand", 0)
    a.mov_abs_imm("g_lastOperand", 0, 4)
    a.mov_abs_imm("g_lastOp", 0)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.mov_abs_imm("g_hasLastOperation", 0)
    a.mov_abs_imm("g_error", 0)
    a.call("UpdateDisplay")
    a.ret()


def emit_contains_decimal(e: Emitter, a: X86) -> None:
    e.label("ContainsDecimal")
    a.mov_reg_label(ECX, "g_current")
    loop = e.unique("contains_decimal_loop")
    found = e.unique("contains_decimal_found")
    missing = e.unique("contains_decimal_missing")
    e.label(loop)
    a.movzx_reg_word_ptr(EAX, ECX)
    a.test_reg_reg(EAX)
    a.jcc("e", missing)
    a.cmp_reg_imm(EAX, ord("."))
    a.jcc("e", found)
    a.add_reg_imm(ECX, 2)
    a.jmp(loop)
    e.label(found)
    a.mov_reg_imm(EAX, 1)
    a.ret()
    e.label(missing)
    a.xor_reg_reg(EAX, EAX)
    a.ret()


def emit_append_input(e: Emitter, a: X86) -> None:
    e.label("AppendInput")
    a.prologue()
    a.push_label("g_current")
    a.call("TextLength")
    a.cmp_reg_imm(EAX, 127)
    done = e.unique("append_input_done")
    a.jcc("ge", done)
    a.mov_reg_reg(EDX, EAX)
    a.add_reg_reg(EDX, EDX)
    a.add_reg_label(EDX, "g_current")
    a.mov_reg_ebp(EAX, 8)
    a.mov_word_ptr_reg16(EDX, EAX)
    a.mov_word_ptr_imm(EDX, 2, 0)
    e.label(done)
    a.epilogue(4)


def emit_append_current_char_v2(e: Emitter, a: X86) -> None:
    e.label("AppendCurrentChar")
    a.prologue()
    done = e.unique("append_current_char_done")
    a.mov_reg_abs(EAX, "g_format_pos")
    a.cmp_reg_imm(EAX, 127)
    a.jcc("ge", done)
    a.mov_reg_reg(EDX, EAX)
    a.add_reg_reg(EDX, EDX)
    a.add_reg_label(EDX, "g_current")
    a.mov_reg_ebp(ECX, 8)
    a.mov_word_ptr_reg16(EDX, ECX)
    a.mov_word_ptr_imm(EDX, 2, 0)
    a.inc_reg(EAX)
    a.mov_abs_reg("g_format_pos", EAX)
    e.label(done)
    a.epilogue(4)


def emit_append_digit_index_v2(e: Emitter, a: X86) -> None:
    e.label("AppendDigitIndex")
    a.prologue()
    a.mov_reg_ebp(EAX, 8)
    a.shl_reg_imm(EAX, 2)
    a.add_reg_label(EAX, "g_digits")
    a.mov_reg_ptr(EAX, EAX)
    a.add_reg_imm(EAX, ord("0"))
    a.push_reg(EAX)
    a.call("AppendCurrentChar")
    a.epilogue(4)


def emit_append_digits_range_v2(e: Emitter, a: X86) -> None:
    e.label("AppendDigitsRange")
    a.prologue(4)
    a.mov_reg_ebp(EAX, 8)
    a.mov_ebp_reg(-4, EAX)
    loop = e.unique("append_digits_range_loop")
    done = e.unique("append_digits_range_done")
    e.label(loop)
    a.mov_reg_ebp(EAX, -4)
    a.cmp_reg_ebp(EAX, 12)
    a.jcc("ge", done)
    a.push_reg(EAX)
    a.call("AppendDigitIndex")
    a.add_ebp_imm(-4, 1)
    a.jmp(loop)
    e.label(done)
    a.epilogue(8)


def emit_append_zero_repeat_v2(e: Emitter, a: X86) -> None:
    e.label("AppendZeroRepeat")
    a.prologue(4)
    a.mov_reg_ebp(EAX, 8)
    a.mov_ebp_reg(-4, EAX)
    loop = e.unique("append_zero_repeat_loop")
    done = e.unique("append_zero_repeat_done")
    e.label(loop)
    a.cmp_ebp_imm(-4, 0)
    a.jcc("le", done)
    a.push_imm(ord("0"))
    a.call("AppendCurrentChar")
    a.add_ebp_imm(-4, -1)
    a.jmp(loop)
    e.label(done)
    a.epilogue(4)


def emit_append_unsigned_int_v2(e: Emitter, a: X86) -> None:
    e.label("AppendUnsignedInt")
    a.prologue(8)
    a.mov_reg_ebp(EAX, 8)
    a.mov_ebp_reg(-4, EAX)
    a.mov_ebp_imm(-8, 0)
    collect = e.unique("append_unsigned_collect")
    output = e.unique("append_unsigned_output")
    output_loop = e.unique("append_unsigned_output_loop")
    done = e.unique("append_unsigned_done")
    e.label(collect)
    a.mov_reg_ebp(EAX, -4)
    a.xor_reg_reg(EDX, EDX)
    a.mov_reg_imm(ECX, 10)
    a.div_reg(ECX)
    a.mov_ebp_reg(-4, EAX)
    a.add_reg_imm(EDX, ord("0"))
    a.mov_reg_ebp(ECX, -8)
    a.shl_reg_imm(ECX, 2)
    a.add_reg_label(ECX, "g_temp_digits")
    a.mov_ptr_reg(ECX, EDX)
    a.add_ebp_imm(-8, 1)
    a.cmp_ebp_imm(-4, 0)
    a.jcc("e", output)
    a.cmp_ebp_imm(-8, 16)
    a.jcc("l", collect)
    e.label(output)
    e.label(output_loop)
    a.cmp_ebp_imm(-8, 0)
    a.jcc("le", done)
    a.add_ebp_imm(-8, -1)
    a.mov_reg_ebp(ECX, -8)
    a.shl_reg_imm(ECX, 2)
    a.add_reg_label(ECX, "g_temp_digits")
    a.mov_reg_ptr(EAX, ECX)
    a.push_reg(EAX)
    a.call("AppendCurrentChar")
    a.jmp(output_loop)
    e.label(done)
    a.epilogue(4)


def emit_try_current_value_v2(e: Emitter, a: X86) -> None:
    e.label("TryCurrentValue")
    a.prologue(32)
    a.mov_ebp_imm(-4, 0)
    e.fixups.append(("abs_delta", e.here() - 4, "g_current", 0))
    a.mov_ebp_imm(-8, 0)
    a.mov_ebp_imm(-12, 0)
    a.mov_ebp_imm(-20, 0)
    a.mov_ebp_imm(-16, 0)

    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("-"))
    no_negative = e.unique("try_current_no_negative")
    a.jcc("ne", no_negative)
    a.mov_ebp_imm(-8, 1)
    a.add_ebp_imm(-4, 2)
    e.label(no_negative)

    int_loop = e.unique("try_current_int_loop")
    after_int = e.unique("try_current_after_int")
    e.label(int_loop)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("0"))
    a.jcc("l", after_int)
    a.cmp_reg_imm(EAX, ord("9"))
    a.jcc("g", after_int)
    a.sub_reg_imm(EAX, ord("0"))
    a.mov_ebp_reg(-32, EAX)
    a.fld_mem64_ebp(-20)
    a.fmul_mem64_abs("dbl_ten")
    a.fild_mem32_ebp(-32)
    a.faddp_st1()
    a.fstp_mem64_ebp(-20)
    a.mov_ebp_imm(-12, 1)
    a.add_ebp_imm(-4, 2)
    a.jmp(int_loop)

    e.label(after_int)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("."))
    after_frac = e.unique("try_current_after_frac")
    a.jcc("ne", after_frac)
    a.add_ebp_imm(-4, 2)
    a.fld_mem64_abs("dbl_tenth")
    a.fstp_mem64_ebp(-28)

    frac_loop = e.unique("try_current_frac_loop")
    e.label(frac_loop)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("0"))
    a.jcc("l", after_frac)
    a.cmp_reg_imm(EAX, ord("9"))
    a.jcc("g", after_frac)
    a.sub_reg_imm(EAX, ord("0"))
    a.mov_ebp_reg(-32, EAX)
    a.fld_mem64_ebp(-20)
    a.fild_mem32_ebp(-32)
    a.fmul_mem64_ebp(-28)
    a.faddp_st1()
    a.fstp_mem64_ebp(-20)
    a.fld_mem64_ebp(-28)
    a.fmul_mem64_abs("dbl_tenth")
    a.fstp_mem64_ebp(-28)
    a.mov_ebp_imm(-12, 1)
    a.add_ebp_imm(-4, 2)
    a.jmp(frac_loop)

    e.label(after_frac)
    success = e.unique("try_current_success")
    done = e.unique("try_current_done")
    a.cmp_ebp_imm(-12, 0)
    a.jcc("ne", success)
    a.xor_reg_reg(EAX, EAX)
    a.jmp(done)
    e.label(success)
    a.cmp_ebp_imm(-8, 0)
    positive = e.unique("try_current_positive")
    a.jcc("e", positive)
    a.fld_mem64_ebp(-20)
    a.fchs()
    a.fstp_mem64_ebp(-20)
    e.label(positive)
    a.mov_reg_ebp(ECX, 8)
    a.mov_reg_ebp(EAX, -20)
    a.mov_ptr_reg(ECX, EAX)
    a.mov_reg_ebp(EAX, -16)
    a.mov_ptr_reg(ECX, EAX, 4)
    a.mov_reg_imm(EAX, 1)
    e.label(done)
    a.epilogue(4)


def emit_add_scaled_digit(e: Emitter, a: X86) -> None:
    e.label("AddScaledDigit")
    a.prologue(4)
    a.mov_reg_ebp(EAX, 12)
    a.imul_reg_ebp(EAX, 16)
    a.mov_ebp_reg(-4, EAX)
    a.cmp_ebp_imm(16, 10000)
    frac = e.unique("add_scaled_frac")
    int_ok = e.unique("add_scaled_int_ok")
    frac_ok = e.unique("add_scaled_frac_ok")
    done = e.unique("add_scaled_done")
    a.jcc("ne", frac)
    a.mov_reg_imm(EAX, 0x7FFFFFFF)
    a.sub_reg_ebp(EAX, -4)
    a.xor_reg_reg(EDX, EDX)
    a.mov_reg_imm(ECX, 10)
    a.div_reg(ECX)
    a.cmp_ebp_reg(8, EAX)
    a.jcc("le", int_ok)
    a.mov_reg_imm(EAX, 0x7FFFFFFF)
    a.jmp(done)
    e.label(int_ok)
    a.mov_reg_ebp(EAX, 8)
    a.imul_reg_imm(EAX, 10)
    a.add_reg_ebp(EAX, -4)
    a.jmp(done)
    e.label(frac)
    a.mov_reg_imm(EAX, 0x7FFFFFFF)
    a.sub_reg_ebp(EAX, -4)
    a.cmp_ebp_reg(8, EAX)
    a.jcc("le", frac_ok)
    a.mov_reg_imm(EAX, 0x7FFFFFFF)
    a.jmp(done)
    e.label(frac_ok)
    a.mov_reg_ebp(EAX, 8)
    a.add_reg_ebp(EAX, -4)
    e.label(done)
    a.epilogue(12)


def emit_current_value(e: Emitter, a: X86) -> None:
    e.label("CurrentValue")
    a.prologue(20)
    a.mov_ebp_imm(-4, 0)
    e.fixups.append(("abs_delta", e.here() - 4, "g_current", 0))
    a.mov_ebp_imm(-8, 0)
    a.mov_ebp_imm(-12, 0)
    a.mov_ebp_imm(-16, 0)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("-"))
    no_neg = e.unique("current_no_neg")
    a.jcc("ne", no_neg)
    a.mov_ebp_imm(-8, 1)
    a.add_ebp_imm(-4, 2)
    e.label(no_neg)

    int_loop = e.unique("current_int_loop")
    after_int = e.unique("current_after_int")
    e.label(int_loop)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("0"))
    a.jcc("l", after_int)
    a.cmp_reg_imm(EAX, ord("9"))
    a.jcc("g", after_int)
    a.sub_reg_imm(EAX, ord("0"))
    a.push_imm(10000)
    a.push_reg(EAX)
    a.push_ebp(-12)
    a.call("AddScaledDigit")
    a.mov_ebp_reg(-12, EAX)
    a.mov_ebp_imm(-16, 1)
    a.add_ebp_imm(-4, 2)
    a.jmp(int_loop)

    e.label(after_int)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("."))
    after_frac = e.unique("current_after_frac")
    a.jcc("ne", after_frac)
    a.add_ebp_imm(-4, 2)
    a.mov_ebp_imm(-20, 1000)

    frac_loop = e.unique("current_frac_loop")
    e.label(frac_loop)
    a.cmp_ebp_imm(-20, 0)
    a.jcc("le", after_frac)
    a.mov_reg_ebp(EDX, -4)
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("0"))
    a.jcc("l", after_frac)
    a.cmp_reg_imm(EAX, ord("9"))
    a.jcc("g", after_frac)
    a.sub_reg_imm(EAX, ord("0"))
    a.push_ebp(-20)
    a.push_reg(EAX)
    a.push_ebp(-12)
    a.call("AddScaledDigit")
    a.mov_ebp_reg(-12, EAX)
    a.mov_ebp_imm(-16, 1)
    a.mov_reg_ebp(EAX, -20)
    a.cdq()
    a.mov_reg_imm(ECX, 10)
    a.idiv_reg(ECX)
    a.mov_ebp_reg(-20, EAX)
    a.add_ebp_imm(-4, 2)
    a.jmp(frac_loop)

    e.label(after_frac)
    saw = e.unique("current_saw_digit")
    done = e.unique("current_done")
    a.cmp_ebp_imm(-16, 0)
    a.jcc("ne", saw)
    a.xor_reg_reg(EAX, EAX)
    a.jmp(done)
    e.label(saw)
    a.mov_reg_ebp(EAX, -12)
    a.cmp_ebp_imm(-8, 0)
    a.jcc("e", done)
    a.neg_reg(EAX)
    e.label(done)
    a.epilogue()


def emit_trim_fraction_zeros(e: Emitter, a: X86) -> None:
    e.label("TrimFractionZeros")
    a.push_label("g_current")
    a.call("TextLength")
    a.mov_reg_reg(ECX, EAX)
    loop = e.unique("trim_loop")
    after_zeros = e.unique("trim_after_zeros")
    done = e.unique("trim_done")
    e.label(loop)
    a.cmp_reg_imm(ECX, 0)
    a.jcc("le", after_zeros)
    a.mov_reg_reg(EDX, ECX)
    a.dec_reg(EDX)
    a.add_reg_reg(EDX, EDX)
    a.add_reg_label(EDX, "g_current")
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("0"))
    a.jcc("ne", after_zeros)
    a.mov_word_ptr_imm(EDX, 0, 0)
    a.dec_reg(ECX)
    a.jmp(loop)
    e.label(after_zeros)
    a.cmp_reg_imm(ECX, 0)
    a.jcc("le", done)
    a.mov_reg_reg(EDX, ECX)
    a.dec_reg(EDX)
    a.add_reg_reg(EDX, EDX)
    a.add_reg_label(EDX, "g_current")
    a.movzx_reg_word_ptr(EAX, EDX)
    a.cmp_reg_imm(EAX, ord("."))
    a.jcc("ne", done)
    a.mov_word_ptr_imm(EDX, 0, 0)
    e.label(done)
    a.ret()


def emit_format_number_v2(e: Emitter, a: X86) -> None:
    e.label("FormatNumber")
    a.prologue(8)
    a.mov_reg_ebp(ECX, 8)
    a.mov_reg_ptr(EAX, ECX)
    a.mov_abs_reg("g_format_magnitude", EAX)
    a.mov_reg_ptr(EAX, ECX, 4)
    a.mov_abs_reg("g_format_magnitude", EAX, 4)

    not_zero = e.unique("format_number_not_zero")
    a.mov_reg_abs(EAX, "g_format_magnitude", 4)
    a.and_reg_imm(EAX, 0x7FFFFFFF)
    a.jcc("ne", not_zero)
    a.cmp_abs_imm("g_format_magnitude", 0)
    a.jcc("ne", not_zero)
    a.push_label("str_zero")
    a.call("CopyText")
    a.epilogue(4)

    e.label(not_zero)
    a.mov_abs_imm("g_format_negative", 0)
    a.mov_reg_abs(EAX, "g_format_magnitude", 4)
    sign_ready = e.unique("format_number_sign_ready")
    a.test_reg_imm(EAX, 0x80000000)
    a.jcc("e", sign_ready)
    a.mov_abs_imm("g_format_negative", 1)
    a.and_reg_imm(EAX, 0x7FFFFFFF)
    a.mov_abs_reg("g_format_magnitude", EAX, 4)
    e.label(sign_ready)

    a.mov_abs_imm("g_exponent", 0)
    large_loop = e.unique("format_number_large_loop")
    large_done = e.unique("format_number_large_done")
    e.label(large_loop)
    a.fld_mem64_abs("g_format_magnitude")
    a.fcomp_mem64_abs("dbl_ten")
    a.fnstsw_ax()
    a.sahf()
    a.jcc("b", large_done)
    a.fld_mem64_abs("g_format_magnitude")
    a.fdiv_mem64_abs("dbl_ten")
    a.fstp_mem64_abs("g_format_magnitude")
    a.add_abs_imm("g_exponent", 1)
    a.jmp(large_loop)
    e.label(large_done)

    small_loop = e.unique("format_number_small_loop")
    small_done = e.unique("format_number_small_done")
    e.label(small_loop)
    a.fld_mem64_abs("g_format_magnitude")
    a.fcomp_mem64_abs("dbl_one")
    a.fnstsw_ax()
    a.sahf()
    a.jcc("ae", small_done)
    a.fld_mem64_abs("g_format_magnitude")
    a.fmul_mem64_abs("dbl_ten")
    a.fstp_mem64_abs("g_format_magnitude")
    a.add_abs_imm("g_exponent", -1)
    a.jmp(small_loop)
    e.label(small_done)

    a.fnstcw_ebp(-4)
    a.movzx_reg_word_ptr(EAX, EBP, -4)
    a.or_reg_imm(EAX, 0x0C00)
    a.mov_word_ebp_reg16(-8, EAX)
    a.fldcw_ebp(-8)

    for i in range(13):
        digit_offset = i * 4
        non_negative = e.unique(f"format_digit_{i}_non_negative")
        clamped = e.unique(f"format_digit_{i}_clamped")
        a.fld_mem64_abs("g_format_magnitude")
        a.fistp_mem32_abs("g_digits", digit_offset)
        a.mov_reg_abs(EAX, "g_digits", digit_offset)
        a.cmp_reg_imm(EAX, 0)
        a.jcc("ge", non_negative)
        a.mov_abs_imm("g_digits", 0, digit_offset)
        e.label(non_negative)
        a.cmp_abs_imm("g_digits", 9, digit_offset)
        a.jcc("le", clamped)
        a.mov_abs_imm("g_digits", 9, digit_offset)
        e.label(clamped)
        a.fld_mem64_abs("g_format_magnitude")
        a.fild_mem32_abs("g_digits", digit_offset)
        a.fsubp_st1()
        a.fmul_mem64_abs("dbl_ten")
        a.fstp_mem64_abs("g_format_magnitude")

    a.fldcw_ebp(-4)

    after_round = e.unique("format_number_after_round")
    a.cmp_abs_imm("g_digits", 5, 12 * 4)
    a.jcc("l", after_round)
    for i in range(11, -1, -1):
        a.add_abs_imm("g_digits", 1, i * 4)
        carry = e.unique(f"format_round_carry_{i}")
        a.cmp_abs_imm("g_digits", 10, i * 4)
        a.jcc("ge", carry)
        a.jmp(after_round)
        e.label(carry)
        a.mov_abs_imm("g_digits", 0, i * 4)
        if i == 0:
            a.mov_abs_imm("g_digits", 1)
            a.add_abs_imm("g_exponent", 1)
            a.jmp(after_round)
    e.label(after_round)

    a.mov_abs_imm("g_digit_count", 1)
    digit_count_done = e.unique("format_digit_count_done")
    for i in range(11, 0, -1):
        keep_scanning = e.unique(f"format_digit_count_scan_{i}")
        a.cmp_abs_imm("g_digits", 0, i * 4)
        a.jcc("e", keep_scanning)
        a.mov_abs_imm("g_digit_count", i + 1)
        a.jmp(digit_count_done)
        e.label(keep_scanning)
    e.label(digit_count_done)

    a.mov_abs_imm("g_current", 0)
    a.mov_abs_imm("g_format_pos", 0)
    no_sign = e.unique("format_number_no_sign")
    a.cmp_abs_imm("g_format_negative", 0)
    a.jcc("e", no_sign)
    a.push_imm(ord("-"))
    a.call("AppendCurrentChar")
    e.label(no_sign)

    scientific = e.unique("format_number_scientific")
    plain = e.unique("format_number_plain")
    done = e.unique("format_number_done")
    a.mov_reg_abs(EAX, "g_exponent")
    a.cmp_reg_imm(EAX, 12)
    a.jcc("ge", scientific)
    a.cmp_reg_imm(EAX, 0xFFFFFFFB)
    a.jcc("le", scientific)
    a.jmp(plain)

    e.label(scientific)
    a.push_imm(0)
    a.call("AppendDigitIndex")
    no_scientific_fraction = e.unique("format_number_scientific_no_fraction")
    a.cmp_abs_imm("g_digit_count", 1)
    a.jcc("le", no_scientific_fraction)
    a.push_imm(ord("."))
    a.call("AppendCurrentChar")
    a.push_abs("g_digit_count")
    a.push_imm(1)
    a.call("AppendDigitsRange")
    e.label(no_scientific_fraction)
    a.push_imm(ord("e"))
    a.call("AppendCurrentChar")
    a.mov_reg_abs(EAX, "g_exponent")
    exponent_positive = e.unique("format_number_exponent_positive")
    append_exp = e.unique("format_number_append_exponent")
    a.cmp_reg_imm(EAX, 0)
    a.jcc("ge", exponent_positive)
    a.neg_reg(EAX)
    a.mov_abs_reg("g_whole_digits", EAX)
    a.push_imm(ord("-"))
    a.call("AppendCurrentChar")
    a.jmp(append_exp)
    e.label(exponent_positive)
    a.mov_abs_reg("g_whole_digits", EAX)
    a.push_imm(ord("+"))
    a.call("AppendCurrentChar")
    e.label(append_exp)
    a.push_abs("g_whole_digits")
    a.call("AppendUnsignedInt")
    a.jmp(done)

    e.label(plain)
    small_plain = e.unique("format_number_plain_small")
    a.mov_reg_abs(EAX, "g_exponent")
    a.cmp_reg_imm(EAX, 0)
    a.jcc("l", small_plain)
    a.inc_reg(EAX)
    a.mov_abs_reg("g_whole_digits", EAX)
    a.mov_reg_abs(EAX, "g_digit_count")
    a.mov_abs_reg("g_copied_digits", EAX)
    a.mov_reg_abs(ECX, "g_whole_digits")
    copied_ready = e.unique("format_number_copied_ready")
    a.cmp_reg_reg(EAX, ECX)
    a.jcc("le", copied_ready)
    a.mov_abs_reg("g_copied_digits", ECX)
    e.label(copied_ready)
    a.push_abs("g_copied_digits")
    a.push_imm(0)
    a.call("AppendDigitsRange")
    a.mov_reg_abs(EAX, "g_whole_digits")
    a.mov_reg_abs(ECX, "g_copied_digits")
    a.sub_reg_reg(EAX, ECX)
    a.push_reg(EAX)
    a.call("AppendZeroRepeat")
    no_plain_fraction = e.unique("format_number_plain_no_fraction")
    a.mov_reg_abs(EAX, "g_digit_count")
    a.mov_reg_abs(ECX, "g_whole_digits")
    a.cmp_reg_reg(EAX, ECX)
    a.jcc("le", no_plain_fraction)
    a.push_imm(ord("."))
    a.call("AppendCurrentChar")
    a.push_abs("g_digit_count")
    a.push_abs("g_whole_digits")
    a.call("AppendDigitsRange")
    e.label(no_plain_fraction)
    a.call("TrimFractionZeros")
    a.jmp(done)

    e.label(small_plain)
    a.push_imm(ord("0"))
    a.call("AppendCurrentChar")
    a.push_imm(ord("."))
    a.call("AppendCurrentChar")
    a.xor_reg_reg(EAX, EAX)
    a.mov_reg_abs(ECX, "g_exponent")
    a.sub_reg_reg(EAX, ECX)
    a.sub_reg_imm(EAX, 1)
    a.push_reg(EAX)
    a.call("AppendZeroRepeat")
    a.push_abs("g_digit_count")
    a.push_imm(0)
    a.call("AppendDigitsRange")
    a.call("TrimFractionZeros")

    e.label(done)
    a.epilogue(4)


def emit_format_number(e: Emitter, a: X86) -> None:
    e.label("FormatNumber")
    a.prologue(16)
    a.mov_reg_ebp(EAX, 8)
    a.cmp_reg_imm(EAX, 0x80000000)
    not_min = e.unique("format_not_min")
    a.jcc("ne", not_min)
    a.push_label("str_overflow")
    a.call("CopyText")
    a.epilogue(4)
    e.label(not_min)
    a.mov_ebp_imm(-4, 0)
    a.cmp_reg_imm(EAX, 0)
    mag_ready = e.unique("format_mag_ready")
    a.jcc("ge", mag_ready)
    a.mov_ebp_imm(-4, 1)
    a.neg_reg(EAX)
    e.label(mag_ready)
    a.mov_reg_imm(ECX, 10000)
    a.cdq()
    a.idiv_reg(ECX)
    a.mov_ebp_reg(-8, EAX)
    a.mov_ebp_reg(-12, EDX)
    a.cmp_reg_imm(EDX, 0)
    frac = e.unique("format_frac")
    done = e.unique("format_done")
    a.jcc("ne", frac)
    a.cmp_ebp_imm(-4, 0)
    whole_pos = e.unique("format_whole_pos")
    a.jcc("e", whole_pos)
    a.push_ebp(-8)
    a.push_label("fmt_neg_int")
    a.push_label("g_current")
    a.call_import("wsprintfW")
    a.add_reg_imm(ESP, 12)
    a.jmp(done)
    e.label(whole_pos)
    a.push_ebp(-8)
    a.push_label("fmt_pos_int")
    a.push_label("g_current")
    a.call_import("wsprintfW")
    a.add_reg_imm(ESP, 12)
    a.jmp(done)
    e.label(frac)
    a.cmp_ebp_imm(-4, 0)
    frac_pos = e.unique("format_frac_pos")
    a.jcc("e", frac_pos)
    a.push_ebp(-12)
    a.push_ebp(-8)
    a.push_label("fmt_neg_frac")
    a.push_label("g_current")
    a.call_import("wsprintfW")
    a.add_reg_imm(ESP, 16)
    a.call("TrimFractionZeros")
    a.jmp(done)
    e.label(frac_pos)
    a.push_ebp(-12)
    a.push_ebp(-8)
    a.push_label("fmt_pos_frac")
    a.push_label("g_current")
    a.call_import("wsprintfW")
    a.add_reg_imm(ESP, 16)
    a.call("TrimFractionZeros")
    e.label(done)
    a.epilogue(4)


def emit_apply_pending_operation(e: Emitter, a: X86) -> None:
    e.label("ApplyPendingOperation")
    a.prologue()
    a.mov_reg_abs(EAX, "g_pendingOp")
    a.cmp_reg_imm(EAX, ord("+"))
    add_case = e.unique("apply_add")
    sub_case = e.unique("apply_sub")
    mul_case = e.unique("apply_mul")
    div_case = e.unique("apply_div")
    default_case = e.unique("apply_default")
    store_true = e.unique("apply_store_true")
    return_false = e.unique("apply_return_false")
    overflow_max = e.unique("apply_overflow_max")
    overflow_min = e.unique("apply_overflow_min")
    a.jcc("e", add_case)
    a.cmp_reg_imm(EAX, ord("-"))
    a.jcc("e", sub_case)
    a.cmp_reg_imm(EAX, ord("*"))
    a.jcc("e", mul_case)
    a.cmp_reg_imm(EAX, ord("/"))
    a.jcc("e", div_case)
    a.jmp(default_case)

    e.label(add_case)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_reg_ebp(EDX, 8)
    a.add_reg_reg(EAX, EDX)
    add_ok = e.unique("apply_add_ok")
    a.jcc("no", add_ok)
    a.mov_reg_abs(EAX, "g_stored")
    a.test_reg_reg(EAX)
    a.jcc("l", overflow_min)
    a.jmp(overflow_max)
    e.label(add_ok)
    a.jmp(store_true)

    e.label(sub_case)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_reg_ebp(EDX, 8)
    a.sub_reg_reg(EAX, EDX)
    sub_ok = e.unique("apply_sub_ok")
    a.jcc("no", sub_ok)
    a.mov_reg_abs(EAX, "g_stored")
    a.test_reg_reg(EAX)
    a.jcc("l", overflow_min)
    a.jmp(overflow_max)
    e.label(sub_ok)
    a.jmp(store_true)

    e.label(mul_case)
    a.mov_reg_abs(EAX, "g_stored")
    a.imul_eax_ebp(8)
    a.test_reg_reg(EDX)
    mul_negative = e.unique("apply_mul_negative")
    mul_divide = e.unique("apply_mul_divide")
    a.jcc("l", mul_negative)
    a.cmp_reg_imm(EDX, 0x1387)
    a.jcc("g", overflow_max)
    a.jcc("ne", mul_divide)
    a.cmp_reg_imm(EAX, 0xFFFFD8F0)
    a.jcc("a", overflow_max)
    a.jmp(mul_divide)
    e.label(mul_negative)
    a.cmp_reg_imm(EDX, 0xFFFFEC78)
    a.jcc("l", overflow_min)
    e.label(mul_divide)
    a.mov_reg_imm(ECX, 10000)
    a.idiv_reg(ECX)
    a.jmp(store_true)

    e.label(div_case)
    a.cmp_ebp_imm(8, 0)
    div_not_zero = e.unique("apply_div_not_zero")
    a.jcc("ne", div_not_zero)
    a.push_label("str_divide_zero")
    a.call("CopyText")
    a.mov_abs_imm("g_pendingOp", 0)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.call("UpdateDisplay")
    a.jmp(return_false)
    e.label(div_not_zero)
    a.mov_reg_ebp(ECX, 8)
    a.mov_reg_reg(EAX, ECX)
    abs_rhs_ready = e.unique("apply_div_abs_rhs_ready")
    a.test_reg_reg(EAX)
    a.jcc("ge", abs_rhs_ready)
    a.neg_reg(EAX)
    e.label(abs_rhs_ready)
    a.mov_reg_reg(ECX, EAX)
    a.cmp_reg_imm(ECX, 10000)
    div_maybe_safe = e.unique("apply_div_maybe_safe")
    div_do = e.unique("apply_div_do")
    a.jcc("ge", div_maybe_safe)
    a.mov_reg_abs(EAX, "g_stored")
    abs_stored_ready = e.unique("apply_div_abs_stored_ready")
    a.test_reg_reg(EAX)
    a.jcc("ge", abs_stored_ready)
    a.neg_reg(EAX)
    e.label(abs_stored_ready)
    a.mov_reg_reg(EDX, ECX)
    a.imul_reg_imm(EDX, 214748)
    a.cmp_reg_reg(EAX, EDX)
    div_not_overflow = e.unique("apply_div_not_overflow")
    a.jcc("be", div_not_overflow)
    a.mov_reg_abs(EAX, "g_stored")
    a.xor_reg_ebp(EAX, 8)
    a.test_reg_reg(EAX)
    a.jcc("l", overflow_min)
    a.jmp(overflow_max)
    e.label(div_not_overflow)
    a.jmp(div_do)
    e.label(div_maybe_safe)
    a.cmp_abs_imm("g_stored", 0x80000000)
    a.jcc("ne", div_do)
    a.cmp_ebp_imm(8, 0xFFFFD8F0)
    a.jcc("e", overflow_max)
    e.label(div_do)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_reg_imm(ECX, 10000)
    a.imul_eax_reg(ECX)
    a.mov_reg_ebp(ECX, 8)
    a.idiv_reg(ECX)
    a.jmp(store_true)

    e.label(default_case)
    a.mov_reg_ebp(EAX, 8)
    a.jmp(store_true)

    e.label(overflow_max)
    a.mov_reg_imm(EAX, 0x7FFFFFFF)
    a.jmp(store_true)
    e.label(overflow_min)
    a.mov_reg_imm(EAX, 0x80000000)
    a.jmp(store_true)

    e.label(store_true)
    a.mov_abs_reg("g_stored", EAX)
    a.mov_reg_imm(EAX, 1)
    a.epilogue(4)
    e.label(return_false)
    a.xor_reg_reg(EAX, EAX)
    a.epilogue(4)


def emit_apply_operation_v2(e: Emitter, a: X86) -> None:
    e.label("ApplyOperation")
    a.prologue()
    a.mov_reg_ebp(EAX, 8)
    add_case = e.unique("apply_op_add")
    sub_case = e.unique("apply_op_sub")
    mul_case = e.unique("apply_op_mul")
    div_case = e.unique("apply_op_div")
    default_case = e.unique("apply_op_default")
    store_true = e.unique("apply_op_store_true")
    false_result = e.unique("apply_op_false")
    a.cmp_reg_imm(EAX, ord("+"))
    a.jcc("e", add_case)
    a.cmp_reg_imm(EAX, ord("-"))
    a.jcc("e", sub_case)
    a.cmp_reg_imm(EAX, ord("*"))
    a.jcc("e", mul_case)
    a.cmp_reg_imm(EAX, ord("/"))
    a.jcc("e", div_case)
    a.jmp(default_case)

    e.label(add_case)
    a.mov_reg_ebp(ECX, 12)
    a.fld_mem64_abs("g_stored")
    a.fadd_mem64_ptr(ECX)
    a.fstp_mem64_abs("g_stored")
    a.jmp(store_true)

    e.label(sub_case)
    a.mov_reg_ebp(ECX, 12)
    a.fld_mem64_abs("g_stored")
    a.fsub_mem64_ptr(ECX)
    a.fstp_mem64_abs("g_stored")
    a.jmp(store_true)

    e.label(mul_case)
    a.mov_reg_ebp(ECX, 12)
    a.fld_mem64_abs("g_stored")
    a.fmul_mem64_ptr(ECX)
    a.fstp_mem64_abs("g_stored")
    a.jmp(store_true)

    e.label(div_case)
    a.mov_reg_ebp(ECX, 12)
    a.mov_reg_ptr(EAX, ECX, 4)
    a.and_reg_imm(EAX, 0x7FFFFFFF)
    div_not_zero = e.unique("apply_op_div_not_zero")
    a.jcc("ne", div_not_zero)
    a.mov_reg_ptr(EAX, ECX)
    a.cmp_reg_imm(EAX, 0)
    a.jcc("e", false_result)
    e.label(div_not_zero)
    a.fld_mem64_abs("g_stored")
    a.fdiv_mem64_ptr(ECX)
    a.fstp_mem64_abs("g_stored")
    a.jmp(store_true)

    e.label(default_case)
    a.mov_reg_ebp(ECX, 12)
    a.mov_reg_ptr(EAX, ECX)
    a.mov_abs_reg("g_stored", EAX)
    a.mov_reg_ptr(EAX, ECX, 4)
    a.mov_abs_reg("g_stored", EAX, 4)

    e.label(store_true)
    a.mov_reg_imm(EAX, 1)
    a.epilogue(8)
    e.label(false_result)
    a.xor_reg_reg(EAX, EAX)
    a.epilogue(8)


def emit_input_digit_v2(e: Emitter, a: X86) -> None:
    e.label("InputDigit")
    a.prologue()
    a.cmp_abs_imm("g_startNewNumber", 0)
    not_start = e.unique("input_digit_v2_not_start")
    start_after = e.unique("input_digit_v2_start_after")
    done_update = e.unique("input_digit_v2_done_update")
    a.jcc("e", not_start)
    a.mov_abs_imm("g_error", 0)
    a.mov_abs_imm("g_hasLastOperation", 0)
    a.cmp_ebp_imm(8, ord("."))
    start_digit = e.unique("input_digit_v2_start_digit")
    a.jcc("ne", start_digit)
    a.push_label("str_zero_dot")
    a.call("CopyText")
    a.jmp(start_after)
    e.label(start_digit)
    a.mov_reg_ebp(EAX, 8)
    a.mov_word_abs_ax("g_current")
    a.mov_word_abs_imm("g_current", 0, 2)
    e.label(start_after)
    a.mov_abs_imm("g_startNewNumber", 0)
    a.call("UpdateDisplay")
    a.epilogue(4)

    e.label(not_start)
    a.cmp_ebp_imm(8, ord("."))
    not_decimal = e.unique("input_digit_v2_not_decimal")
    a.jcc("ne", not_decimal)
    a.call("ContainsDecimal")
    a.test_reg_reg(EAX)
    a.jcc("ne", done_update)
    a.push_imm(ord("."))
    a.call("AppendInput")
    a.jmp(done_update)

    e.label(not_decimal)
    a.movzx_reg_word_abs(EAX, "g_current")
    a.cmp_reg_imm(EAX, ord("0"))
    append = e.unique("input_digit_v2_append")
    a.jcc("ne", append)
    a.movzx_reg_word_abs(EAX, "g_current", 2)
    a.test_reg_reg(EAX)
    a.jcc("ne", append)
    a.mov_reg_ebp(EAX, 8)
    a.mov_word_abs_ax("g_current")
    a.jmp(done_update)
    e.label(append)
    a.push_ebp(8)
    a.call("AppendInput")
    e.label(done_update)
    a.call("UpdateDisplay")
    a.epilogue(4)


def emit_input_operator_v2(e: Emitter, a: X86) -> None:
    e.label("InputOperator")
    a.prologue(8)
    done = e.unique("input_operator_v2_done")
    set_error = e.unique("input_operator_v2_set_error")
    set_op = e.unique("input_operator_v2_set_op")
    a.cmp_abs_imm("g_error", 0)
    a.jcc("ne", done)
    a.cmp_abs_imm("g_startNewNumber", 0)
    a.jcc("ne", set_op)
    a.lea_reg_ebp(EAX, -8)
    a.push_reg(EAX)
    a.call("TryCurrentValue")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    a.lea_reg_ebp(EAX, -8)
    a.push_reg(EAX)
    a.push_abs("g_pendingOp")
    a.call("ApplyOperation")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    a.push_label("g_stored")
    a.call("FormatNumber")
    a.call("UpdateDisplay")
    a.jmp(set_op)
    e.label(set_error)
    a.call("SetError")
    a.jmp(done)
    e.label(set_op)
    a.mov_reg_ebp(EAX, 8)
    a.mov_abs_reg("g_pendingOp", EAX)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.mov_abs_imm("g_hasLastOperation", 0)
    e.label(done)
    a.epilogue(4)


def emit_calculate_result_v2(e: Emitter, a: X86) -> None:
    e.label("CalculateResult")
    a.prologue(20)
    done = e.unique("calculate_v2_done")
    set_error = e.unique("calculate_v2_set_error")
    pending = e.unique("calculate_v2_pending")
    a.cmp_abs_imm("g_error", 0)
    a.jcc("ne", done)
    a.cmp_abs_imm("g_pendingOp", 0)
    a.jcc("ne", pending)

    a.cmp_abs_imm("g_hasLastOperation", 0)
    a.jcc("e", done)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_ebp_reg(-8, EAX)
    a.mov_reg_abs(EAX, "g_stored", 4)
    a.mov_ebp_reg(-4, EAX)
    no_pending_current_ready = e.unique("calculate_v2_no_pending_current_ready")
    a.cmp_abs_imm("g_startNewNumber", 0)
    a.jcc("ne", no_pending_current_ready)
    a.lea_reg_ebp(EAX, -8)
    a.push_reg(EAX)
    a.call("TryCurrentValue")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    e.label(no_pending_current_ready)
    a.mov_reg_ebp(EAX, -8)
    a.mov_abs_reg("g_stored", EAX)
    a.mov_reg_ebp(EAX, -4)
    a.mov_abs_reg("g_stored", EAX, 4)
    a.push_label("g_lastOperand")
    a.push_abs("g_lastOp")
    a.call("ApplyOperation")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    a.push_label("g_stored")
    a.call("FormatNumber")
    a.mov_abs_imm("g_startNewNumber", 1)
    a.call("UpdateDisplay")
    a.jmp(done)

    e.label(pending)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_ebp_reg(-8, EAX)
    a.mov_reg_abs(EAX, "g_stored", 4)
    a.mov_ebp_reg(-4, EAX)
    pending_current_ready = e.unique("calculate_v2_pending_current_ready")
    a.cmp_abs_imm("g_startNewNumber", 0)
    a.jcc("ne", pending_current_ready)
    a.lea_reg_ebp(EAX, -8)
    a.push_reg(EAX)
    a.call("TryCurrentValue")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    e.label(pending_current_ready)
    a.mov_reg_abs(EAX, "g_pendingOp")
    a.mov_ebp_reg(-20, EAX)
    use_stored_operand = e.unique("calculate_v2_use_stored_operand")
    operand_ready = e.unique("calculate_v2_operand_ready")
    a.cmp_abs_imm("g_startNewNumber", 0)
    a.jcc("ne", use_stored_operand)
    a.mov_reg_ebp(EAX, -8)
    a.mov_ebp_reg(-16, EAX)
    a.mov_reg_ebp(EAX, -4)
    a.mov_ebp_reg(-12, EAX)
    a.jmp(operand_ready)
    e.label(use_stored_operand)
    a.mov_reg_abs(EAX, "g_stored")
    a.mov_ebp_reg(-16, EAX)
    a.mov_reg_abs(EAX, "g_stored", 4)
    a.mov_ebp_reg(-12, EAX)
    e.label(operand_ready)
    a.lea_reg_ebp(EAX, -16)
    a.push_reg(EAX)
    a.push_ebp(-20)
    a.call("ApplyOperation")
    a.test_reg_reg(EAX)
    a.jcc("e", set_error)
    a.push_label("g_stored")
    a.call("FormatNumber")
    a.mov_abs_imm("g_pendingOp", 0)
    a.mov_reg_ebp(EAX, -20)
    a.mov_abs_reg("g_lastOp", EAX)
    a.mov_reg_ebp(EAX, -16)
    a.mov_abs_reg("g_lastOperand", EAX)
    a.mov_reg_ebp(EAX, -12)
    a.mov_abs_reg("g_lastOperand", EAX, 4)
    a.mov_abs_imm("g_hasLastOperation", 1)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.call("UpdateDisplay")
    a.jmp(done)

    e.label(set_error)
    a.call("SetError")
    e.label(done)
    a.epilogue()


def emit_input_digit(e: Emitter, a: X86) -> None:
    e.label("InputDigit")
    a.prologue()
    a.cmp_abs_imm("g_startNewNumber", 0)
    not_start = e.unique("input_digit_not_start")
    start_after = e.unique("input_digit_start_after")
    done_update = e.unique("input_digit_done_update")
    a.jcc("e", not_start)
    a.cmp_ebp_imm(8, ord("."))
    start_digit = e.unique("input_digit_start_digit")
    a.jcc("ne", start_digit)
    a.push_label("str_zero_dot")
    a.call("CopyText")
    a.jmp(start_after)
    e.label(start_digit)
    a.mov_reg_ebp(EAX, 8)
    a.mov_word_abs_ax("g_current")
    a.mov_word_abs_imm("g_current", 0, 2)
    e.label(start_after)
    a.mov_abs_imm("g_startNewNumber", 0)
    a.call("UpdateDisplay")
    a.epilogue(4)

    e.label(not_start)
    a.cmp_ebp_imm(8, ord("."))
    not_decimal = e.unique("input_digit_not_decimal")
    a.jcc("ne", not_decimal)
    a.call("ContainsDecimal")
    a.test_reg_reg(EAX)
    a.jcc("ne", done_update)
    a.push_imm(ord("."))
    a.call("AppendInput")
    a.jmp(done_update)

    e.label(not_decimal)
    a.movzx_reg_word_abs(EAX, "g_current")
    a.cmp_reg_imm(EAX, ord("0"))
    append = e.unique("input_digit_append")
    a.jcc("ne", append)
    a.movzx_reg_word_abs(EAX, "g_current", 2)
    a.test_reg_reg(EAX)
    a.jcc("ne", append)
    a.mov_reg_ebp(EAX, 8)
    a.mov_word_abs_ax("g_current")
    a.jmp(done_update)
    e.label(append)
    a.push_ebp(8)
    a.call("AppendInput")
    e.label(done_update)
    a.call("UpdateDisplay")
    a.epilogue(4)


def emit_input_operator(e: Emitter, a: X86) -> None:
    e.label("InputOperator")
    a.prologue()
    a.cmp_abs_imm("g_startNewNumber", 0)
    start_new = e.unique("input_operator_start_new")
    set_op = e.unique("input_operator_set_op")
    a.jcc("ne", start_new)
    a.call("CurrentValue")
    a.push_reg(EAX)
    a.call("ApplyPendingOperation")
    a.test_reg_reg(EAX)
    a.jcc("e", set_op)
    a.push_abs("g_stored")
    a.call("FormatNumber")
    a.call("UpdateDisplay")
    a.jmp(set_op)
    e.label(start_new)
    a.call("CurrentValue")
    a.mov_abs_reg("g_stored", EAX)
    e.label(set_op)
    a.mov_reg_ebp(EAX, 8)
    a.mov_abs_reg("g_pendingOp", EAX)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.epilogue(4)


def emit_calculate_result(e: Emitter, a: X86) -> None:
    e.label("CalculateResult")
    a.cmp_abs_imm("g_pendingOp", 0)
    done = e.unique("calculate_done")
    a.jcc("e", done)
    a.call("CurrentValue")
    a.push_reg(EAX)
    a.call("ApplyPendingOperation")
    a.test_reg_reg(EAX)
    a.jcc("e", done)
    a.push_abs("g_stored")
    a.call("FormatNumber")
    a.mov_abs_imm("g_pendingOp", 0)
    a.mov_abs_imm("g_startNewNumber", 1)
    a.call("UpdateDisplay")
    e.label(done)
    a.ret()


def emit_create_button(e: Emitter, a: X86) -> None:
    e.label("CreateButton")
    a.prologue()
    a.push_imm(0)
    a.push_abs("g_instance")
    a.push_ebp(16)
    a.push_ebp(8)
    a.push_ebp(32)
    a.push_ebp(28)
    a.push_ebp(24)
    a.push_ebp(20)
    a.push_imm(0x50000000)
    a.push_ebp(12)
    a.push_label("str_button_class")
    a.push_imm(0)
    a.call_import("CreateWindowExW")
    a.epilogue(28)


def emit_create_controls(e: Emitter, a: X86) -> None:
    e.label("CreateControls")
    a.prologue()
    a.push_imm(0)
    a.push_abs("g_instance")
    a.push_imm(100)
    a.push_ebp(8)
    a.push_imm(45)
    a.push_imm(245)
    a.push_imm(15)
    a.push_imm(15)
    a.push_imm(0x50000802)
    a.push_label("str_zero")
    a.push_label("str_edit_class")
    a.push_imm(0x200)
    a.call_import("CreateWindowExW")
    a.mov_abs_reg("g_display", EAX)
    a.push_label("str_segoe_ui")
    a.push_imm(0x20)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(1)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(400)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(24)
    a.call_import("CreateFontW")
    a.mov_abs_reg("g_displayFont", EAX)
    have_font = e.unique("create_controls_have_font")
    a.test_reg_reg(EAX)
    a.jcc("ne", have_font)
    a.push_imm(17)
    a.call_import("GetStockObject")
    e.label(have_font)
    a.push_imm(1)
    a.push_reg(EAX)
    a.push_imm(0x30)
    a.push_abs("g_display")
    a.call_import("SendMessageW")

    buttons = [
        ("btn_c", 200, 0, 0, 1),
        ("btn_div", 201, 1, 0, 1),
        ("btn_mul", 202, 2, 0, 1),
        ("btn_sub", 203, 3, 0, 1),
        ("btn_7", 204, 0, 1, 1),
        ("btn_8", 205, 1, 1, 1),
        ("btn_9", 206, 2, 1, 1),
        ("btn_add", 207, 3, 1, 1),
        ("btn_4", 208, 0, 2, 1),
        ("btn_5", 209, 1, 2, 1),
        ("btn_6", 210, 2, 2, 1),
        ("btn_eq", 211, 3, 2, 1),
        ("btn_1", 212, 0, 3, 1),
        ("btn_2", 213, 1, 3, 1),
        ("btn_3", 214, 2, 3, 1),
        ("btn_0", 215, 0, 4, 2),
        ("btn_dot", 216, 2, 4, 1),
    ]
    for text_label, control_id, col, row, cols in buttons:
        x = 15 + col * (55 + 8)
        y = 80 + row * (45 + 8)
        width = cols * 55 + (cols - 1) * 8
        a.push_imm(45)
        a.push_imm(width)
        a.push_imm(y)
        a.push_imm(x)
        a.push_imm(control_id)
        a.push_label(text_label)
        a.push_ebp(8)
        a.call("CreateButton")
    a.epilogue(4)


def emit_handle_button(e: Emitter, a: X86) -> None:
    e.label("HandleButton")
    a.prologue()
    a.mov_reg_ebp(EAX, 8)
    a.sub_reg_imm(EAX, 200)
    cases = {0: "hb_clear", 1: "hb_div", 2: "hb_mul", 3: "hb_sub", 7: "hb_add", 11: "hb_eq", 15: "hb_zero", 16: "hb_dot"}
    labels = {name: e.unique(name) for name in set(cases.values())}
    range_7 = e.unique("hb_range_7")
    range_4 = e.unique("hb_range_4")
    range_1 = e.unique("hb_range_1")
    done = e.unique("hb_done")
    for value, name in cases.items():
        a.cmp_reg_imm(EAX, value)
        a.jcc("e", labels[name])
    a.cmp_reg_imm(EAX, 4)
    a.jcc("l", done)
    a.cmp_reg_imm(EAX, 6)
    a.jcc("le", range_7)
    a.cmp_reg_imm(EAX, 8)
    a.jcc("l", done)
    a.cmp_reg_imm(EAX, 10)
    a.jcc("le", range_4)
    a.cmp_reg_imm(EAX, 12)
    a.jcc("l", done)
    a.cmp_reg_imm(EAX, 14)
    a.jcc("le", range_1)
    a.jmp(done)

    e.label(labels["hb_clear"])
    a.call("ClearCalculator")
    a.jmp(done)
    e.label(labels["hb_div"])
    a.push_imm(ord("/"))
    a.call("InputOperator")
    a.jmp(done)
    e.label(labels["hb_mul"])
    a.push_imm(ord("*"))
    a.call("InputOperator")
    a.jmp(done)
    e.label(labels["hb_sub"])
    a.push_imm(ord("-"))
    a.call("InputOperator")
    a.jmp(done)
    e.label(labels["hb_add"])
    a.push_imm(ord("+"))
    a.call("InputOperator")
    a.jmp(done)
    e.label(labels["hb_eq"])
    a.call("CalculateResult")
    a.jmp(done)
    e.label(labels["hb_zero"])
    a.push_imm(ord("0"))
    a.call("InputDigit")
    a.jmp(done)
    e.label(labels["hb_dot"])
    a.push_imm(ord("."))
    a.call("InputDigit")
    a.jmp(done)
    e.label(range_7)
    a.add_reg_imm(EAX, ord("7") - 4)
    a.push_reg(EAX)
    a.call("InputDigit")
    a.jmp(done)
    e.label(range_4)
    a.add_reg_imm(EAX, ord("4") - 8)
    a.push_reg(EAX)
    a.call("InputDigit")
    a.jmp(done)
    e.label(range_1)
    a.add_reg_imm(EAX, ord("1") - 12)
    a.push_reg(EAX)
    a.call("InputDigit")
    e.label(done)
    a.epilogue(4)


def emit_window_proc(e: Emitter, a: X86) -> None:
    e.label("WindowProc")
    a.prologue()
    a.push_reg(EBX)
    a.push_reg(ESI)
    a.push_reg(EDI)
    ret0 = e.unique("window_proc_ret0")
    finish = e.unique("window_proc_finish")
    default = e.unique("window_proc_default")
    a.cmp_ebp_imm(12, 1)
    wm_command = e.unique("window_proc_wm_command")
    wm_destroy = e.unique("window_proc_wm_destroy")
    a.jcc("ne", wm_command)
    a.push_ebp(8)
    a.call("CreateControls")
    a.jmp(ret0)
    e.label(wm_command)
    a.cmp_ebp_imm(12, 0x111)
    a.jcc("ne", wm_destroy)
    a.mov_reg_ebp(EAX, 16)
    a.and_reg_imm(EAX, 0xFFFF)
    a.cmp_reg_imm(EAX, 200)
    a.jcc("l", ret0)
    a.push_reg(EAX)
    a.call("HandleButton")
    a.jmp(ret0)
    e.label(wm_destroy)
    a.cmp_ebp_imm(12, 2)
    a.jcc("ne", default)
    no_font = e.unique("window_proc_no_font")
    a.cmp_abs_imm("g_displayFont", 0)
    a.jcc("e", no_font)
    a.push_abs("g_displayFont")
    a.call_import("DeleteObject")
    a.mov_abs_imm("g_displayFont", 0)
    e.label(no_font)
    a.push_imm(0)
    a.call_import("PostQuitMessage")
    e.label(ret0)
    a.xor_reg_reg(EAX, EAX)
    a.jmp(finish)
    e.label(default)
    a.push_ebp(20)
    a.push_ebp(16)
    a.push_ebp(12)
    a.push_ebp(8)
    a.call_import("DefWindowProcW")
    e.label(finish)
    a.pop_reg(EDI)
    a.pop_reg(ESI)
    a.pop_reg(EBX)
    a.epilogue(16)


def emit_run_calculator(e: Emitter, a: X86) -> None:
    e.label("RunCalculator")
    a.prologue()
    a.mov_abs_label("wndclass", "WindowProc", 4)
    a.mov_reg_ebp(EAX, 8)
    a.mov_abs_reg("wndclass", EAX, 16)
    a.push_imm(0x7F00)
    a.push_imm(0)
    a.call_import("LoadCursorW")
    a.mov_abs_reg("wndclass", EAX, 24)
    a.mov_abs_imm("wndclass", 6, 28)
    a.mov_abs_label("wndclass", "str_class_name", 36)
    a.push_label("wndclass")
    a.call_import("RegisterClassW")

    a.push_imm(0)
    a.push_ebp(8)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(390)
    a.push_imm(290)
    a.push_imm(0x80000000)
    a.push_imm(0x80000000)
    a.push_imm(0x00CA0000)
    a.push_label("str_window_title")
    a.push_label("str_class_name")
    a.push_imm(0)
    a.call_import("CreateWindowExW")
    hwnd_ok = e.unique("run_hwnd_ok")
    a.test_reg_reg(EAX)
    a.jcc("ne", hwnd_ok)
    a.xor_reg_reg(EAX, EAX)
    a.epilogue(8)
    e.label(hwnd_ok)
    a.mov_abs_reg("main_hwnd", EAX)
    a.push_ebp(12)
    a.push_reg(EAX)
    a.call_import("ShowWindow")
    a.push_abs("main_hwnd")
    a.call_import("UpdateWindow")

    loop = e.unique("run_msg_loop")
    done = e.unique("run_msg_done")
    e.label(loop)
    a.push_imm(0)
    a.push_imm(0)
    a.push_imm(0)
    a.push_label("msg")
    a.call_import("GetMessageW")
    a.cmp_reg_imm(EAX, 0)
    a.jcc("le", done)
    a.push_label("msg")
    a.call_import("TranslateMessage")
    a.push_label("msg")
    a.call_import("DispatchMessageW")
    a.jmp(loop)
    e.label(done)
    a.mov_reg_abs(EAX, "msg", 8)
    a.epilogue(8)


def emit_data(e: Emitter) -> None:
    e.align(4)
    e.label("g_display")
    e.u32(0)
    e.label("g_displayFont")
    e.u32(0)
    e.label("g_instance")
    e.u32(0)
    e.label("main_hwnd")
    e.u32(0)
    e.align(8)
    e.label("g_stored")
    e.u32(0)
    e.u32(0)
    e.label("g_lastOperand")
    e.u32(0)
    e.u32(0)
    e.label("g_pendingOp")
    e.u32(0)
    e.label("g_lastOp")
    e.u32(0)
    e.label("g_startNewNumber")
    e.u32(1)
    e.label("g_hasLastOperation")
    e.u32(0)
    e.label("g_error")
    e.u32(0)
    e.align(4)
    e.label("g_current")
    e.bytes("0".encode("utf-16le"))
    e.u16(0)
    e.zbytes((128 * 2) - 4)
    e.align(4)
    e.label("wndclass")
    e.zbytes(40)
    e.label("msg")
    e.zbytes(28)
    e.align(8)
    e.label("g_format_magnitude")
    e.u32(0)
    e.u32(0)
    e.label("dbl_one")
    e.bytes(struct.pack("<d", 1.0))
    e.label("dbl_ten")
    e.bytes(struct.pack("<d", 10.0))
    e.label("dbl_tenth")
    e.bytes(struct.pack("<d", 0.1))
    e.align(4)
    e.label("g_format_negative")
    e.u32(0)
    e.label("g_exponent")
    e.u32(0)
    e.label("g_digit_count")
    e.u32(0)
    e.label("g_format_pos")
    e.u32(0)
    e.label("g_whole_digits")
    e.u32(0)
    e.label("g_copied_digits")
    e.u32(0)
    e.label("g_digits")
    e.zbytes(13 * 4)
    e.label("g_temp_digits")
    e.zbytes(16 * 4)

    emit_wstr(e, "str_zero", "0")
    emit_wstr(e, "str_zero_dot", "0.")
    emit_wstr(e, "str_error", "ERROR")
    emit_wstr(e, "str_class_name", "BasicWin32Calculator")
    emit_wstr(e, "str_window_title", "Calculator")
    emit_wstr(e, "str_edit_class", "EDIT")
    emit_wstr(e, "str_button_class", "BUTTON")
    emit_wstr(e, "str_segoe_ui", "Segoe UI")

    for label, text in [
        ("btn_c", "C"),
        ("btn_div", "/"),
        ("btn_mul", "*"),
        ("btn_sub", "-"),
        ("btn_7", "7"),
        ("btn_8", "8"),
        ("btn_9", "9"),
        ("btn_add", "+"),
        ("btn_4", "4"),
        ("btn_5", "5"),
        ("btn_6", "6"),
        ("btn_eq", "="),
        ("btn_1", "1"),
        ("btn_2", "2"),
        ("btn_3", "3"),
        ("btn_0", "0"),
        ("btn_dot", "."),
    ]:
        emit_wstr(e, label, text)


def emit_imports(e: Emitter) -> None:
    e.align(4)
    e.label("import_descriptors")
    dlls = [
        (
            "kernel32.dll",
            ["ExitProcess", "GetModuleHandleW"],
        ),
        (
            "user32.dll",
            [
                "CreateWindowExW",
                "DefWindowProcW",
                "DispatchMessageW",
                "GetMessageW",
                "LoadCursorW",
                "PostQuitMessage",
                "RegisterClassW",
                "SendMessageW",
                "SetWindowTextW",
                "ShowWindow",
                "TranslateMessage",
                "UpdateWindow",
            ],
        ),
        (
            "gdi32.dll",
            ["CreateFontW", "DeleteObject", "GetStockObject"],
        ),
    ]

    for dll, _funcs in dlls:
        stem = dll.replace(".", "_").replace("-", "_")
        e.rva32(f"ilt_{stem}")
        e.u32(0)
        e.u32(0)
        e.rva32(f"dll_name_{stem}")
        e.rva32(f"iat_{stem}")
    e.zbytes(20)

    for dll, funcs in dlls:
        stem = dll.replace(".", "_").replace("-", "_")
        e.align(4)
        e.label(f"ilt_{stem}")
        for name in funcs:
            e.rva32(f"ibn_{name}")
        e.u32(0)
        e.align(4)
        e.label(f"iat_{stem}")
        for name in funcs:
            e.label(f"iat_{name}")
            e.rva32(f"ibn_{name}")
        e.u32(0)

    for dll, funcs in dlls:
        for name in funcs:
            e.align(2)
            e.label(f"ibn_{name}")
            e.u16(0)
            e.bytes(name.encode("ascii") + b"\0")

    for dll, _funcs in dlls:
        stem = dll.replace(".", "_").replace("-", "_")
        emit_asciiz(e, f"dll_name_{stem}", dll)


def apply_special_fixups(e: Emitter) -> None:
    kept: list[tuple[str, int, str, int]] = []
    for kind, pos, label, addend in e.fixups:
        if kind == "abs_delta":
            e.patch_u32(pos, IMAGE_BASE + SECTION_RVA + e.labels[label] + addend)
        else:
            kept.append((kind, pos, label, addend))
    e.fixups = kept


def build_pe(section: bytes, import_rva: int, import_size: int, iat_rva: int, iat_size: int) -> bytes:
    raw_size = align(len(section), FILE_ALIGNMENT)
    virtual_size = len(section)
    size_of_image = align(SECTION_RVA + virtual_size, SECTION_ALIGNMENT)
    headers = bytearray(0x200)

    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, 0x80)
    pe = 0x80
    headers[pe : pe + 4] = b"PE\0\0"
    coff = pe + 4
    struct.pack_into(
        "<HHIIIHH",
        headers,
        coff,
        0x014C,
        1,
        0,
        0,
        0,
        0x00E0,
        0x0103,
    )

    opt = coff + 20
    struct.pack_into("<HBB", headers, opt, 0x010B, 0, 0)
    struct.pack_into("<III", headers, opt + 4, raw_size, 0, 0)
    struct.pack_into("<III", headers, opt + 16, SECTION_RVA, SECTION_RVA, SECTION_RVA)
    struct.pack_into("<I", headers, opt + 28, IMAGE_BASE)
    struct.pack_into("<II", headers, opt + 32, SECTION_ALIGNMENT, FILE_ALIGNMENT)
    struct.pack_into("<HHHHHH", headers, opt + 40, 4, 0, 0, 0, 4, 0)
    struct.pack_into("<I", headers, opt + 52, 0)
    struct.pack_into("<III", headers, opt + 56, size_of_image, 0x200, 0)
    struct.pack_into("<HH", headers, opt + 68, 2, 0)
    struct.pack_into("<IIIIII", headers, opt + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)

    data_dir = opt + 96
    struct.pack_into("<II", headers, data_dir + 8, import_rva, import_size)
    struct.pack_into("<II", headers, data_dir + 12 * 8, iat_rva, iat_size)

    sec = opt + 0xE0
    headers[sec : sec + 8] = b".text\0\0\0"
    struct.pack_into(
        "<IIIIIIHHI",
        headers,
        sec + 8,
        virtual_size,
        SECTION_RVA,
        raw_size,
        0x200,
        0,
        0,
        0,
        0,
        0xE0000020,
    )

    return bytes(headers) + section + (b"\0" * (raw_size - len(section)))


def main() -> None:
    e = Emitter()
    a = X86(e)
    emit_program(e, a)
    e.align(4)
    emit_data(e)
    import_start = e.here()
    emit_imports(e)
    import_end = e.here()
    apply_special_fixups(e)
    e.resolve()

    import_rva = SECTION_RVA + import_start
    import_size = import_end - import_start
    iat_rva = SECTION_RVA + e.labels["iat_kernel32_dll"]
    last_iat = e.labels["iat_gdi32_dll"] + (4 * 4)
    iat_size = last_iat - e.labels["iat_kernel32_dll"]
    pe = build_pe(bytes(e.buf), import_rva, import_size, iat_rva, iat_size)

    out = Path(__file__).resolve().parents[1] / "build" / "Win32Calculator.exe"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pe)
    print(f"Wrote {out} ({len(pe)} bytes)")


if __name__ == "__main__":
    main()
