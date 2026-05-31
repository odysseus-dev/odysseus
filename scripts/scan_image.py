"""Cross-platform wrapper: trivy image scan with IMAGE env var fallback."""
import os
import subprocess
import sys

image = os.environ.get("IMAGE", "odysseus:latest")
result = subprocess.run(["trivy", "image", image])
sys.exit(result.returncode)
