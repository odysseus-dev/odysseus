import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

port = os.environ.get("APP_PORT", "7000")

cmd = ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", port]

try:
    result = subprocess.run(cmd, check=True)
    print(f"Captured text: {result.stdout.strip()}")
except KeyboardInterrupt:
    print(f"\n[info] Server stopped by user.")
except subprocess.CalledProcessError as e:
    print(f"\n[error] Uvicorn failed to start. Exit code: {e.returncode}")
