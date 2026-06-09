import os
import sys
import argparse
import subprocess
from dotenv import load_dotenv

load_dotenv()

parser = argparse.ArgumentParser(description="Odysseus Server Runner")

parser.add_argument(
    "--port",
    type=str,
    default=os.environ.get("APP_PORT", "7000"),
    help="Choose a port to bind the server to (defaults to .env APP_PORT or 7000)"
)
parser.add_argument(
    "--host", 
    type=str, 
    default="127.0.0.1", 
    help="Host interface to bind to (e.g., 127.0.0.1 or 0.0.0.0)"
)

args = parser.parse_args()

# Fixed: Explicitly using sys.executable guarantees it runs cross-platform
cmd = [sys.executable, "-m", "uvicorn", "app:app", "--host", args.host, "--port", args.port]

print(f"Launching Odysseus server...")
print(f"Target: http://{args.host}:{args.port}\n")

try:
    # Fixed: Removed result variable; streaming logs live to the console
    subprocess.run(cmd, check=True)
except KeyboardInterrupt:
    print(f"\n[info] Server stopped cleanly by user.")
except subprocess.CalledProcessError as e:
    print(f"\n[error] Uvicorn failed to start. Exit code: {e.returncode}")
