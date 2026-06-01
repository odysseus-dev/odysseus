import os
import sys
import subprocess
import time

# Set root directory relative to this script
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

PID_FILE = os.path.join(ROOT_DIR, "odysseus.pid")

def is_pid_running(pid):
    """Check if a process with the given PID is running on Windows."""
    try:
        # Use tasklist to see if PID exists
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            shell=True
        )
        return str(pid) in out.stdout
    except Exception:
        return False

def start():
    """Start the Odysseus server in a detached background process."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            if is_pid_running(pid):
                print(f"Odysseus is already running (PID: {pid}).")
                return
        except Exception:
            pass

    # Resolve pythonw.exe path
    python_exe = sys.executable
    if python_exe.lower().endswith("python.exe"):
        pythonw_exe = python_exe[:-10] + "pythonw.exe"
    elif python_exe.lower().endswith("pythonw.exe"):
        pythonw_exe = python_exe
    else:
        # Fallback to PATH resolution
        pythonw_exe = "pythonw"

    daemon_script = os.path.abspath(__file__)
    
    print("Starting Odysseus daemon in background...")
    
    # 0x00000008 is DETACHED_PROCESS creation flag for Windows
    try:
        proc = subprocess.Popen(
            [pythonw_exe, daemon_script, "run"],
            creationflags=0x00000008,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=ROOT_DIR
        )
        
        # Save PID to file
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
        
        print(f"Odysseus daemon started (PID: {proc.pid}).")
    except Exception as e:
        print(f"Failed to start Odysseus daemon: {e}")

def stop():
    """Stop the running background process tree."""
    if not os.path.exists(PID_FILE):
        print("Odysseus is not running (no PID file found).")
        return

    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
    except Exception as e:
        print(f"Error reading PID file: {e}")
        return

    if not is_pid_running(pid):
        print(f"Odysseus PID {pid} is not running. Cleaning up stale PID file.")
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        return

    print(f"Stopping Odysseus (PID: {pid}) and its sub-processes...")
    try:
        # /F forces termination, /T kills process tree (important for MCP servers/subprocesses)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], shell=True)
        print("Odysseus stopped successfully.")
    except Exception as e:
        print(f"Error stopping process: {e}")

    try:
        os.remove(PID_FILE)
    except Exception:
        pass

def status():
    """Report the current status of the daemon."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            if is_pid_running(pid):
                print(f"Odysseus is running in background (PID: {pid}).")
                return
        except Exception:
            pass
    print("Odysseus is stopped.")

def run_server():
    """Run the uvicorn server in-process."""
    import uvicorn
    from dotenv import load_dotenv
    
    load_dotenv()
    
    host = os.getenv("ODYSSEUS_HOST", "0.0.0.0")
    port = int(os.getenv("ODYSSEUS_PORT", "8000"))
    
    # Run uvicorn app:app server
    uvicorn.run("app:app", host=host, port=port, log_level="info")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python odysseus_daemon.py [start|stop|status|run]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "run":
        run_server()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
