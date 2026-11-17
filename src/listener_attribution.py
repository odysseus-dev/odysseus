"""Passive Windows IPv4 listener attribution, never process-control authority."""
import ctypes as C
from ctypes import wintypes as W
import os
import socket
import struct
from urllib.parse import urlsplit

from core.platform_compat import pid_alive
from src.process_ownership import verify


def listener_matches_process(base_url: str, pid: int, token: str) -> bool:
    if os.name != 'nt' or type(pid) is not int or pid <= 0 or not token:
        return False
    try:
        origin = urlsplit(base_url)
        if (origin.scheme != 'http' or origin.hostname != '127.0.0.1' or origin.username or origin.password
                or origin.path not in ('', '/') or origin.query or origin.fragment
                or origin.port is None or not 1024 <= origin.port <= 65535):
            return False
        if not pid_alive(pid) or verify(pid, token) != 'owned':
            return False
        api = C.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
        api.argtypes = [C.c_void_p, C.POINTER(W.DWORD), W.BOOL, W.ULONG, C.c_int, W.ULONG]
        api.restype = W.DWORD
        size = W.DWORD()
        if api(None, C.byref(size), False, socket.AF_INET, 5, 0) not in (0, 122):
            return False
        class Row(C.Structure):
            _fields_ = [(name, W.DWORD) for name in ('state', 'local', 'port', 'remote', 'remote_port', 'pid')]
        for _ in range(2):
            if not 4 <= size.value <= 8 * 1024 * 1024:
                return False
            buffer = C.create_string_buffer(size.value)
            result = api(buffer, C.byref(size), False, socket.AF_INET, 5, 0)
            if result == 122:
                continue
            if result != 0:
                return False
            count = W.DWORD.from_buffer(buffer).value
            if 4 + count * C.sizeof(Row) > len(buffer):
                return False
            owners = set()
            for index in range(count):
                row = Row.from_buffer(buffer, 4 + index * C.sizeof(Row))
                address = socket.inet_ntoa(struct.pack('=I', row.local))
                if row.state == 2 and socket.ntohs(row.port & 65535) == origin.port and address in {'127.0.0.1', '0.0.0.0'}:
                    owners.add(row.pid)
            return owners == {pid} and pid_alive(pid) and verify(pid, token) == 'owned'
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return False
