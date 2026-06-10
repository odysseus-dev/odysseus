import sys
import subprocess
import threading
import webbrowser
import time
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QBrush
from PyQt6.QtCore import QThread, pyqtSignal, QObject, Qt, QTimer

ODYSSEUS_DIR = Path.home() / "odysseus"
URL = "http://localhost:7000"
procs = {}

def make_pixmap(color):
    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QBrush(QColor(30, 30, 30)))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    return px

def get_ollama_models():
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        return [l.split()[0] for l in lines[1:] if l.strip()]
    except:
        return []

def get_running_models():
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        return [l.split()[0] for l in lines[1:] if l.strip()]
    except:
        return []

class Worker(QObject):
    done = pyqtSignal(bool)
    def __init__(self, action):
        super().__init__()
        self.action = action
    def run(self):
        if self.action == "start":
            procs["chroma"] = subprocess.Popen(
                ["chroma", "run", "--host", "localhost", "--port", "8100"],
                cwd=ODYSSEUS_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            procs["ollama"] = subprocess.Popen(
                ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**__import__("os").environ, "OLLAMA_KEEP_ALIVE": "0"})
            time.sleep(2)
            venv_python = ODYSSEUS_DIR / "venv" / "bin" / "python"
            procs["odysseus"] = subprocess.Popen(
                [str(venv_python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "7000"],
                cwd=ODYSSEUS_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import urllib.request
            for _ in range(30):
                try:
                    urllib.request.urlopen(URL, timeout=1)
                    break
                except:
                    time.sleep(1)
            webbrowser.open(URL)
            self.done.emit(True)
        elif self.action == "stop":
            for name, proc in list(procs.items()):
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except:
                    try: proc.kill()
                    except: pass
                procs.pop(name, None)
            self.done.emit(False)

class TrayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(QIcon(make_pixmap("#787878")))
        self.tray.setToolTip("Odysseus - stopped")
        self.tray.setVisible(True)
        self.running = False
        self.thread = None
        self.worker = None
        self.tray.activated.connect(self.on_click)
        self.build_menu()

    def on_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.running:
                webbrowser.open(URL)
            else:
                self.tray.contextMenu().popup(self.tray.geometry().center())

    def build_menu(self):
        menu = QMenu()
        if self.running:
            a = menu.addAction("Open Odysseus")
            a.triggered.connect(lambda: webbrowser.open(URL))
        else:
            a = menu.addAction("Start Odysseus")
            a.triggered.connect(self.start)

        load_menu = menu.addMenu("Load Model")
        models = get_ollama_models()
        if models:
            for m in models:
                ma = load_menu.addAction(m)
                ma.triggered.connect(lambda checked, n=m: self.switch_model(n))
        else:
            na = load_menu.addAction("No models downloaded")
            na.setEnabled(False)

        kill_menu = menu.addMenu("Running Models")
        running_models = get_running_models()
        if running_models:
            for m in running_models:
                ra = kill_menu.addAction(m + " (running)")
                ra.setEnabled(False)
            kill_menu.addSeparator()
            ka = kill_menu.addAction("Kill Model - Free VRAM")
            ka.triggered.connect(self.do_kill_ollama)
        else:
            na2 = kill_menu.addAction("None loaded")
            na2.setEnabled(False)

        menu.addSeparator()
        if self.running:
            sa = menu.addAction("Stop Odysseus")
            sa.triggered.connect(self.stop)
        qa = menu.addAction("Quit")
        qa.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)

    def switch_model(self, name):
        def run():
            for m in get_running_models():
                subprocess.run(["ollama", "stop", m], capture_output=True)
            time.sleep(1)
            subprocess.Popen(["ollama", "run", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(0, self.build_menu)
        threading.Thread(target=run, daemon=True).start()

    def do_kill_ollama(self):
        def run():
            for m in get_running_models():
                subprocess.run(["ollama", "stop", m], capture_output=True)
            time.sleep(1)
            subprocess.run(["pkill", "-9", "-f", "llama-server"], capture_output=True)
            subprocess.run(["pkill", "-9", "-f", "ollama"], capture_output=True)
            time.sleep(1)
            QTimer.singleShot(0, self.build_menu)
        threading.Thread(target=run, daemon=True).start()

    def start(self):
        self.tray.setIcon(QIcon(make_pixmap("#FFB400")))
        self.tray.setToolTip("Odysseus - starting...")
        self._run_worker("start", self.on_started)

    def on_started(self, _):
        self.running = True
        self.tray.setIcon(QIcon(make_pixmap("#00C850")))
        self.tray.setToolTip("Odysseus - running")
        self.build_menu()

    def stop(self):
        self.tray.setIcon(QIcon(make_pixmap("#FF5050")))
        self.tray.setToolTip("Odysseus - stopping...")
        self._run_worker("stop", self.on_stopped)

    def on_stopped(self, _):
        self.running = False
        self.tray.setIcon(QIcon(make_pixmap("#787878")))
        self.tray.setToolTip("Odysseus - stopped")
        self.build_menu()

    def _run_worker(self, action, callback):
        self.thread = QThread()
        self.worker = Worker(action)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(callback)
        self.worker.done.connect(self.thread.quit)
        self.thread.start()

    def quit(self):
        if self.running:
            for name, proc in list(procs.items()):
                try: proc.terminate()
                except: pass
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())

if __name__ == "__main__":
    tray = TrayApp()
    tray.run()
