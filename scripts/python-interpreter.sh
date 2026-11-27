#!/bin/bash
# Select an executable interpreter without changing the caller's environment.

find_python_interpreter() {
    local machine_arch="$1" candidate resolved
    shift
    for candidate in "$@"; do
        resolved="$(command -v "$candidate" 2>/dev/null)" || continue
        if "$resolved" -c '
import platform
import sys
supported = sys.version_info[:2] >= (3, 11)
native = sys.argv[1] != "arm64" or platform.machine() == "arm64"
raise SystemExit(0 if supported and native else 1)
' "$machine_arch" >/dev/null 2>&1; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}
