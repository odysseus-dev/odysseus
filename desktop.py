import os
import sys
import threading
import time
import socket
import webview
import uvicorn

# We must import the app object to run it via uvicorn programmatically
from app import app

PORT = 7000
HOST = "127.0.0.1"

def is_port_in_use(port):
    """Check if the port is already bound by another process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) == 0

def start_server():
    """Run the FastAPI server."""
    # Run uvicorn without reloading to prevent subprocess issues in the wrapper
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

if __name__ == '__main__':
    # Only start the server if it isn't already running
    if not is_port_in_use(PORT):
        print(f"Starting background server on http://{HOST}:{PORT}...")
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        
        # Give the server a moment to bind to the port
        time.sleep(2)
    else:
        print(f"Server is already running on http://{HOST}:{PORT}.")

    # Redirect stdout and stderr to prevent deadlocks in pythonw.exe when the buffer fills
    log_path = os.path.join(os.path.dirname(__file__), 'crash.log')
    sys.stdout = open(log_path, 'a')
    sys.stderr = sys.stdout

    class NativeAPI:
        def __init__(self):
            self._window = None
            self._is_maximized = False
            
        def _get_hwnd(self):
            import win32gui, win32process, os
            pid = os.getpid()
            hwnds = []
            def callback(hwnd, hwnds):
                if win32gui.IsWindowVisible(hwnd):
                    _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if found_pid == pid:
                        # Filter out hidden or non-main windows
                        if win32gui.GetWindowText(hwnd) or win32gui.GetClassName(hwnd):
                            hwnds.append(hwnd)
                return True
            win32gui.EnumWindows(callback, hwnds)
            # Sort by those with a title first, as the main pywebview window usually has one
            hwnds.sort(key=lambda h: bool(win32gui.GetWindowText(h)), reverse=True)
            return hwnds[0] if hwnds else 0

        def start_drag(self):
            try:
                import win32gui, win32con
                hwnd = self._get_hwnd()
                if hwnd:
                    win32gui.ReleaseCapture()
                    win32gui.SendMessage(hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION, 0)
            except Exception:
                pass

        def start_resize(self, edge):
            try:
                import win32gui, win32con
                hwnd = self._get_hwnd()
                if hwnd:
                    mapping = {
                        'top': win32con.HTTOP, 'bottom': win32con.HTBOTTOM,
                        'left': win32con.HTLEFT, 'right': win32con.HTRIGHT,
                        'topleft': win32con.HTTOPLEFT, 'topright': win32con.HTTOPRIGHT,
                        'bottomleft': win32con.HTBOTTOMLEFT, 'bottomright': win32con.HTBOTTOMRIGHT
                    }
                    ht = mapping.get(edge)
                    if ht:
                        win32gui.ReleaseCapture()
                        win32gui.SendMessage(hwnd, win32con.WM_NCLBUTTONDOWN, ht, 0)
            except Exception:
                pass

        def minimize(self):
            if self._window:
                self._window.minimize()
                
        def maximize(self):
            if self._window:
                try:
                    if self._is_maximized:
                        self._window.restore()
                        self._is_maximized = False
                    else:
                        self._window.maximize()
                        self._is_maximized = True
                except AttributeError:
                    self._window.toggle_fullscreen()
                    
        def close(self):
            if self._window:
                self._window.destroy()

    api = NativeAPI()

    # Create and launch the native desktop window using pywebview
    window = webview.create_window(
        "Odysseus", 
        f"http://{HOST}:{PORT}",
        width=1200, 
        height=800,
        frameless=False,
        easy_drag=False,
        js_api=api
    )
    api._window = window
    
    def on_shown():
        try:
            import ctypes
            import win32gui, win32con, os
            hwnd = api._get_hwnd()
            if hwnd:
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
                value = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, ctypes.byref(value), ctypes.sizeof(value))
                
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "odysseus.ico")
                if os.path.exists(icon_path):
                    hicon = win32gui.LoadImage(0, icon_path, win32con.IMAGE_ICON, 0, 0, win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
                    if hicon:
                        win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, hicon)
                        win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, hicon)
        except Exception:
            pass
            
    window.events.shown += on_shown
    
    # This blocks until the window is closed
    webview.start()
    
    # Since the server thread is a daemon, exiting the main thread will kill the server.
    sys.exit(0)
