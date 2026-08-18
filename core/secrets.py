"""Credential encryption at rest for ~/.mapharvest/settings.json.

The settings file sits in the user's home directory in plain sight, and it now
carries Gmail app passwords and AI API keys. Everything sensitive goes through
this module first, so a settings.json that gets copied to a USB stick, synced to
OneDrive or pasted into a support ticket is not a set of live credentials.

On Windows the payload is sealed with DPAPI (`CryptProtectData`) scoped to the
logged-in user: Windows keeps the key material, and the ciphertext is inert on
another machine or under another account. Everywhere else — and on any DPAPI
failure — we fall back to XOR against a key derived from the machine name. That
fallback is **obfuscation, not security**: it keeps a password from being
grep-able or shoulder-surfable in a text editor and nothing more. Anyone with
the file and this source can undo it.

Stored form is `enc:v1:<base64>`. Values without that prefix pass through
`decrypt` untouched, which is the migration path for files written before
encryption existed.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import os
import socket
import sys

_PREFIX = "enc:v1:"

# First byte of the encoded payload records which scheme produced it, so a blob
# sealed by DPAPI is never fed to the XOR path (which would silently return
# mojibake instead of failing) and vice versa.
_SCHEME_DPAPI = 0x01
_SCHEME_XOR = 0x02


# ── DPAPI (Windows) ──────────────────────────────────────────────────────────

_CRYPTPROTECT_UI_FORBIDDEN = 0x01

_crypt32 = None
_kernel32 = None


class _DataBlob(ctypes.Structure):
    """Windows `DATA_BLOB`: a DWORD length followed by a byte pointer.

    The layout has to match exactly — a wrong field order or width is not a
    clean failure, it is a heap read at the wrong address.
    """

    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _load_dpapi():
    """Return (crypt32, kernel32) with prototypes set, or (None, None)."""
    global _crypt32, _kernel32
    if _crypt32 is not None:
        return _crypt32, _kernel32
    if not sys.platform.startswith("win"):
        return None, None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError):
        return None, None

    blob_p = ctypes.POINTER(_DataBlob)
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        # (pDataIn, szDataDescr, pOptionalEntropy, pvReserved, pPromptStruct,
        #  dwFlags, pDataOut) — identical arity for protect and unprotect.
        fn.argtypes = [blob_p, ctypes.c_wchar_p, blob_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_uint32, blob_p]
        fn.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    _crypt32, _kernel32 = crypt32, kernel32
    return _crypt32, _kernel32


def _dpapi(protect: bool, data: bytes) -> bytes:
    """Run one DPAPI round-trip. Returns b"" when DPAPI is absent or refuses."""
    if not data:
        return b""
    crypt32, kernel32 = _load_dpapi()
    if crypt32 is None:
        return b""

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    blob_out = _DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    try:
        ok = fn(ctypes.byref(blob_in), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    except OSError:
        return b""
    if not ok or not blob_out.pbData:
        return b""
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        # DPAPI allocates pDataOut with LocalAlloc; the caller owns it.
        kernel32.LocalFree(blob_out.pbData)


# ── XOR fallback (everything else) ───────────────────────────────────────────

def _xor_key() -> bytes:
    try:
        host = os.environ.get("COMPUTERNAME") or socket.gethostname()
    except OSError:
        host = ""
    return hashlib.sha256((host or "mapharvest").encode("utf-8", "replace")).digest()


def _xor(data: bytes) -> bytes:
    key = _xor_key()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


# ── Public API ───────────────────────────────────────────────────────────────

def is_encrypted(value: str) -> bool:
    """True when `value` is in this module's stored form."""
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """Seal `plaintext` into `enc:v1:<base64>`. Empty in, empty out."""
    if not plaintext:
        return ""
    raw = str(plaintext).encode("utf-8")
    sealed = _dpapi(True, raw)
    if sealed:
        payload = bytes((_SCHEME_DPAPI,)) + sealed
    else:
        payload = bytes((_SCHEME_XOR,)) + _xor(raw)
    return _PREFIX + base64.b64encode(payload).decode("ascii")


def decrypt(token: str) -> str:
    """Unseal a stored value. Anything not sealed comes back unchanged.

    A token this machine cannot open (sealed by another Windows account, or a
    corrupted file) yields "" — the caller then sees an empty credential and
    prompts for it again, which beats handing binary garbage to a mail server.
    """
    if not isinstance(token, str):
        return ""
    if not is_encrypted(token):
        return token
    try:
        payload = base64.b64decode(token[len(_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        return ""
    if not payload:
        return ""

    scheme, body = payload[0], payload[1:]
    if scheme == _SCHEME_DPAPI:
        raw = _dpapi(False, body)
    elif scheme == _SCHEME_XOR:
        raw = _xor(body)
    else:
        return ""
    return raw.decode("utf-8", "replace") if raw else ""
