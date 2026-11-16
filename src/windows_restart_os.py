"""Small Windows primitives for local cooperative control; no termination APIs."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import socket

K = C.WinDLL('kernel32', use_last_error=True)
A = C.WinDLL('advapi32', use_last_error=True)
I = C.WinDLL('iphlpapi', use_last_error=True)


def api(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(args)
    return function


close = api(K, 'CloseHandle', W.BOOL, W.HANDLE)
_open = api(K, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
_times = api(K, 'GetProcessTimes', W.BOOL, W.HANDLE, *([C.POINTER(W.FILETIME)] * 4))
_exit = api(K, 'GetExitCodeProcess', W.BOOL, W.HANDLE, C.POINTER(W.DWORD))
_token = api(A, 'OpenProcessToken', W.BOOL, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE))
_info = api(A, 'GetTokenInformation', W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD))
_sid = api(A, 'ConvertSidToStringSidW', W.BOOL, C.c_void_p, C.POINTER(W.LPWSTR))
_free = api(K, 'LocalFree', C.c_void_p, C.c_void_p)
_sd = api(A, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', W.BOOL, W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p)
_set = api(A, 'SetFileSecurityW', W.BOOL, W.LPCWSTR, W.DWORD, C.c_void_p)


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def identity(pid):
    handle = _open(0x1000, False, int(pid))
    if not handle:
        if C.get_last_error() == 87:
            return None
        raise C.WinError(C.get_last_error())
    try:
        code = W.DWORD()
        checked(_exit(handle, C.byref(code)))
        if code.value != 259:
            return None
        values = [W.FILETIME() for _ in range(4)]
        checked(_times(handle, *(C.byref(value) for value in values)))
        return {'pid': int(pid), 'start_token': f'win32:{(values[0].dwHighDateTime << 32) | values[0].dwLowDateTime}'}
    finally:
        close(handle)


def user_sid(pid=None):
    process = checked(_open(0x1000, False, int(pid or os.getpid())))
    token = W.HANDLE()
    try:
        checked(_token(process, 8, C.byref(token)))
        size = W.DWORD()
        _info(token, 1, None, 0, C.byref(size))
        buffer = C.create_string_buffer(size.value)
        checked(_info(token, 1, buffer, size, C.byref(size)))
        string = W.LPWSTR()
        checked(_sid(C.cast(buffer, C.POINTER(C.c_void_p))[0], C.byref(string)))
        try:
            return string.value
        finally:
            _free(C.cast(string, C.c_void_p))
    finally:
        if token:
            close(token)
        close(process)


def descriptor(directory=False):
    value = C.c_void_p()
    flags = 'OICI' if directory else ''
    checked(_sd(f'D:P(A;{flags};GA;;;SY)(A;{flags};GA;;;{user_sid()})', 1, C.byref(value), None))
    return value


def protect(path):
    value = descriptor(directory=True)
    try:
        checked(_set(str(path), 0x80000004, value))
    finally:
        _free(value)


class SecurityAttributes(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', C.c_void_p), ('inherit', W.BOOL)]


def pipe_handle(address, first=False):
    create = api(K, 'CreateNamedPipeW', W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD,
                 W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.POINTER(SecurityAttributes))
    value = descriptor()
    security = SecurityAttributes(C.sizeof(SecurityAttributes), value, False)
    try:
        # Overlapped, message-mode, local clients only; protected owner ACL.
        handle = create(address, 3 | 0x40000000 | (0x80000 if first else 0),
                        6 | 8, 4, 32768, 32768, 1000, C.byref(security))
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return handle
    finally:
        _free(value)


def peer_pid(handle, server=False):
    function = api(K, 'GetNamedPipeServerProcessId' if server else 'GetNamedPipeClientProcessId',
                   W.BOOL, W.HANDLE, C.POINTER(W.ULONG))
    pid = W.ULONG()
    checked(function(handle, C.byref(pid)))
    return pid.value


def process_parents():
    class Entry(C.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]
    snapshot = api(K, 'CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    first = api(K, 'Process32FirstW', W.BOOL, W.HANDLE, C.POINTER(Entry))
    next_row = api(K, 'Process32NextW', W.BOOL, W.HANDLE, C.POINTER(Entry))
    row = Entry()
    row.size = C.sizeof(row)
    result = {}
    try:
        checked(first(snapshot, C.byref(row)))
        while True:
            result[row.pid] = row.parent
            if not next_row(snapshot, C.byref(row)):
                if C.get_last_error() != 18:
                    raise C.WinError(C.get_last_error())
                return result
    finally:
        close(snapshot)


def listener_pid(port):
    function = api(I, 'GetExtendedTcpTable', W.DWORD, C.c_void_p, C.POINTER(W.DWORD), W.BOOL, W.ULONG, C.c_int, W.ULONG)
    size = W.DWORD()
    result = function(None, C.byref(size), False, 2, 3, 0)
    if result not in (0, 122):
        raise OSError(result, 'Cannot inspect TCP listeners')
    buffer = C.create_string_buffer(size.value)
    result = function(buffer, C.byref(size), False, 2, 3, 0)
    if result:
        raise OSError(result, 'Cannot inspect TCP listeners')
    count = C.cast(buffer, C.POINTER(W.DWORD))[0]
    rows = C.cast(C.addressof(buffer) + 4, C.POINTER(W.DWORD))
    pids = {int(rows[n * 6 + 5]) for n in range(count) if socket.ntohs(rows[n * 6 + 2] & 65535) == port}
    if len(pids) != 1:
        raise RuntimeError(f'Expected exactly one IPv4 listener on {port}')
    return pids.pop()
