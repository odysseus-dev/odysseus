"""One isolated, low-memory CUDA inference smoke test.

usage: smoke_cuda_inference.py <llama-server.exe> <model.gguf> [n_gpu_layers] [port]

Starts a private llama-server on a non-default port (never 8100), measures free VRAM/RAM
before and while loaded, GPU engine utilisation during one 96-token generation, and the
server-reported tokens/second, then force-stops only the process it started.
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


EXE, MODEL = sys.argv[1], sys.argv[2]
NGL = int(sys.argv[3]) if len(sys.argv) > 3 else 20
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 18100
assert PORT != 8100, "port 8100 belongs to Chroma"


def free_ram():
    class Mem(ctypes.Structure):
        _fields_ = [("l", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
            (n, ctypes.c_uint64) for n in "tot avail tp ap tv av ext".split()]
    mem = Mem()
    mem.l = ctypes.sizeof(mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    return mem.avail / 2**20


base_ram = free_ram()
log = open(os.path.join(tempfile.gettempdir(), "smoke_server.log"), "wb")
proc = subprocess.Popen([EXE, "-m", MODEL, "--host", "127.0.0.1", "--port", str(PORT), "-c", "2048",
                         "-ngl", str(NGL), "--parallel", "1", "--no-warmup"], stdout=log,
                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=os.path.dirname(EXE))
res = {"ngl": NGL, "base_free_ram_mib": round(base_ram)}
util, stop = [], False


mem = []


def sampler():
    # cuMemGetInfo from this process does not reflect another process' usage on WDDM,
    # so read the per-process dedicated GPU memory counter instead.
    cmd = (f"$u=(Get-Counter '\\GPU Engine(*pid_{proc.pid}*)\\Utilization Percentage' -ErrorAction SilentlyContinue)"
           ".CounterSamples | Measure-Object CookedValue -Maximum | % Maximum;"
           f"$m=(Get-Counter '\\GPU Process Memory(pid_{proc.pid}*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
           ".CounterSamples | Measure-Object CookedValue -Sum | % Sum; \"$u $m\"")
    while not stop:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                 text=True, timeout=30).stdout.split()
            util.append(float(out[0] or 0))
            mem.append(round(float(out[1]) / 2**20))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass


try:
    started = time.time()
    while time.time() - started < 240:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited {proc.returncode}")
        try:
            health = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3))
            if health.get("status") == "ok":
                break
        except OSError:
            time.sleep(2)
    else:
        raise RuntimeError("not ready in 240s")
    res["load_seconds"] = round(time.time() - started, 1)
    res["loaded_free_ram_mib"] = round(free_ram())
    thread = threading.Thread(target=sampler)
    thread.start()
    body = json.dumps({"prompt": "Write a short paragraph about the sea.", "n_predict": 96,
                       "temperature": 0.7, "cache_prompt": False}).encode()
    reply = json.load(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{PORT}/completion", body, {"Content-Type": "application/json"}), timeout=180))
    stop = True
    thread.join()
    res["timings"] = {k: round(v, 2) for k, v in reply["timings"].items() if isinstance(v, (int, float))}
    res["sample"] = reply["content"][:120]
    res["gpu_util_samples_pct"] = [round(u) for u in util]
    res["gpu_dedicated_mib_samples"] = mem
    res["min_free_ram_mib"] = round(free_ram())
finally:
    stop = True
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    time.sleep(2)
    print(json.dumps(res, indent=1))
