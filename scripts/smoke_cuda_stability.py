"""Bounded CUDA stability check driven by the real launch planner.

usage: smoke_cuda_stability.py <llama-server.exe> <model.gguf> [port] [rounds]

The planner picks the layer count and context (>= 1.25 GiB VRAM kept free). A watchdog
aborts and kills only the server it started if free RAM drops below 8 GiB or the process'
dedicated VRAM would leave less than 1.25 GiB free. Port 8100 (Chroma) is refused.
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from src import gpu_runtime, model_serving_control  # noqa: E402

EXE, MODEL = sys.argv[1], sys.argv[2]
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 18100
ROUNDS = int(sys.argv[4]) if len(sys.argv) > 4 else 6
assert PORT != 8100, "port 8100 belongs to Chroma"
MIN_FREE_RAM_MIB, MIN_FREE_VRAM_MIB = 8 * 1024, 1280


def free_ram():
    class Mem(ctypes.Structure):
        _fields_ = [("l", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
            (n, ctypes.c_uint64) for n in "tot avail tp ap tv av ext".split()]
    mem = Mem()
    mem.l = ctypes.sizeof(mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    return mem.avail / 2**20


gpu = gpu_runtime.probe_cuda_driver()
plan = model_serving_control.build_launch_plan(
    engine="llamacpp", executable=EXE, model_path=MODEL, port=PORT, context_length=2048,
    gpu_layers=999, available_memory_bytes=int(free_ram() * 2**20), gpu=gpu)
total_mib = gpu["total_bytes"] / 2**20
res = {"plan": {k: plan[k] for k in ("gpu_layers", "context_length", "memory_validation", "warnings")},
       "gpu_free_mib_before": round(gpu["free_bytes"] / 2**20), "ram_free_mib_before": round(free_ram())}
log = open(os.path.join(tempfile.gettempdir(), "smoke_stability.log"), "wb")
proc = subprocess.Popen(plan["argv"] + ["--parallel", "1", "--no-warmup"], stdout=log, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, cwd=os.path.dirname(EXE))
mem, util, ram, abort = [], [], [], []
stop = False


def taskkill():
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def watchdog():
    cmd = (f"$u=(Get-Counter '\\GPU Engine(*pid_{proc.pid}*)\\Utilization Percentage' -ErrorAction SilentlyContinue)"
           ".CounterSamples | Measure-Object CookedValue -Maximum | % Maximum;"
           f"$m=(Get-Counter '\\GPU Process Memory(pid_{proc.pid}*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
           ".CounterSamples | Measure-Object CookedValue -Sum | % Sum; \"$u $m\"")
    while not stop:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                 text=True, timeout=30).stdout.split()
            util.append(round(float(out[0] or 0)))
            mem.append(round(float(out[1]) / 2**20))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        ram.append(round(free_ram()))
        if ram[-1] < MIN_FREE_RAM_MIB:
            abort.append(f"free RAM {ram[-1]} MiB below {MIN_FREE_RAM_MIB}")
        if mem and total_mib - mem[-1] < MIN_FREE_VRAM_MIB:
            abort.append(f"VRAM headroom {round(total_mib - mem[-1])} MiB below {MIN_FREE_VRAM_MIB}")
        if abort:
            taskkill()
            return


thread = threading.Thread(target=watchdog)
thread.start()
rounds = []
try:
    started = time.time()
    while time.time() - started < 240 and not abort:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited {proc.returncode}")
        try:
            if json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3)).get("status") == "ok":
                break
        except OSError:
            time.sleep(2)
    res["load_seconds"] = round(time.time() - started, 1)
    filler = "The sea is wide and the sky is high. " * 40  # roughly 400 prompt tokens, below the 2048 context
    for i in range(ROUNDS):
        if abort:
            break
        prompt = (filler if i % 2 else "") + f"Round {i}: write two sentences about harbours."
        body = json.dumps({"prompt": prompt, "n_predict": 128, "temperature": 0.7, "cache_prompt": False}).encode()
        reply = json.load(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/completion", body, {"Content-Type": "application/json"}), timeout=120))
        t = reply["timings"]
        rounds.append({"prompt_n": t["prompt_n"], "gen_n": t["predicted_n"],
                       "prompt_tps": round(t["prompt_per_second"], 1), "gen_tps": round(t["predicted_per_second"], 1)})
except Exception as exc:  # noqa: BLE001 - report, then always clean up
    res["error"] = str(exc)
finally:
    stop = True
    thread.join()
    taskkill()
    time.sleep(2)
    res.update(rounds=rounds, gpu_util_pct=util, gpu_dedicated_mib=mem, ram_free_mib=ram,
               aborted_by_watchdog=abort, ram_free_mib_min=min(ram) if ram else None,
               vram_headroom_mib_min=round(total_mib - max(mem)) if mem else None,
               server_still_running=proc.poll() is None)
    print(json.dumps(res, indent=1))
