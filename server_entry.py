#!/usr/bin/env python3
"""PyInstaller entry point for the Odysseus server.

This is a self-contained entry that runs uvicorn with the app.
When packaged with PyInstaller, it bundles all Python deps + app code.
"""
import sys
import os

# Add the bundle directory to sys.path so imports work
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller --onedir bundle.
    # Layout: <dist>/odysseus-server/odysseus-server  (executable)
    #         <dist>/odysseus-server/_internal/        (deps + bundled data)
    # Bundled package dirs (core/, src/, services/, …) are placed at the root
    # of _internal by --add-data, so both the exe dir and _internal must be on
    # sys.path for `from core...` / `from src...` imports to resolve.
    bundle_dir = os.path.dirname(sys.executable)
    internal_dir = os.path.join(bundle_dir, '_internal')
    for p in (bundle_dir, internal_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
else:
    # Running in dev mode
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from app import app

if __name__ == '__main__':
    host = '127.0.0.1'
    port = 7860
    
    # Parse --host and --port from args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--host' and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == '--port' and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            i += 1
    
    print(f"[odysseus-server] starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level='info')
