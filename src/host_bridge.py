"""
HostBridgeDaemon: Hardware access layer for the Odysseus platform.
Provides Wi-Fi scanning/connecting and system performance metrics.
Works on Linux (nmcli), Windows (netsh), and macOS (airport).
"""
import asyncio
import logging
import platform
import shutil

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()  # "Linux", "Windows", "Darwin"


async def _run_cmd(cmd: list, timeout: int = 15) -> tuple:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


async def scan_wifi_interfaces() -> list:
    """Scan for available Wi-Fi networks. Returns list of {ssid, signal, security}."""
    networks = []

    if _SYSTEM == "Linux":
        if not shutil.which("nmcli"):
            return [{"error": "nmcli not available on this system"}]
        code, out, err = await _run_cmd(
            ["nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"],
            timeout=20,
        )
        if code == 0:
            seen = set()
            for line in out.strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    ssid = parts[0].strip()
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                        "security": parts[2].strip() if len(parts) > 2 and parts[2].strip() else "open",
                    })
        else:
            networks.append({"error": err or "nmcli failed"})

    elif _SYSTEM == "Windows":
        code, out, err = await _run_cmd(["netsh", "wlan", "show", "networks", "mode=Bssid"])
        if code == 0:
            current = {}
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("SSID") and "BSSID" not in line:
                    if current.get("ssid"):
                        networks.append(current)
                    current = {"ssid": line.split(":", 1)[-1].strip(), "signal": 0, "security": "unknown"}
                elif "Signal" in line and current:
                    sig = line.split(":", 1)[-1].strip().replace("%", "")
                    current["signal"] = int(sig) if sig.isdigit() else 0
                elif "Authentication" in line and current:
                    current["security"] = line.split(":", 1)[-1].strip()
            if current.get("ssid"):
                networks.append(current)

    elif _SYSTEM == "Darwin":
        airport_paths = [
            "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport",
        ]
        airport_bin = next((p for p in airport_paths if shutil.which(p) or __import__("os").path.exists(p)), None)
        if airport_bin:
            code, out, _ = await _run_cmd([airport_bin, "-s"])
            for line in out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    networks.append({
                        "ssid": parts[0],
                        "signal": int(parts[2]) if len(parts) > 2 else 0,
                        "security": parts[-1] if len(parts) > 4 else "open",
                    })
        else:
            networks.append({"error": "airport utility not found"})

    return networks


async def connect_wifi(ssid: str, password: str = "") -> dict:
    """Connect to a Wi-Fi network. Returns {success: bool, message: str}."""
    if _SYSTEM == "Linux":
        if not shutil.which("nmcli"):
            return {"success": False, "message": "nmcli not available"}

        # Ensure radio is on
        code, out, _ = await _run_cmd(["nmcli", "radio", "wifi"])
        if "disabled" in out.lower():
            await _run_cmd(["nmcli", "radio", "wifi", "on"])
            await asyncio.sleep(2)

        args = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        code, out, err = await _run_cmd(args, timeout=30)
        success = "successfully activated" in out.lower() or code == 0
        return {"success": success, "message": out.strip() or err.strip()}

    elif _SYSTEM == "Windows":
        code, out, err = await _run_cmd(
            ["netsh", "wlan", "connect", f"name={ssid}"], timeout=20
        )
        return {"success": code == 0, "message": out.strip() or err.strip()}

    return {"success": False, "message": f"Wi-Fi connect not implemented for {_SYSTEM}"}


async def disconnect_wifi() -> dict:
    """Disconnect from current Wi-Fi network."""
    if _SYSTEM == "Linux":
        code, out, err = await _run_cmd(["nmcli", "dev", "disconnect", "wlan0"])
        return {"success": code == 0, "message": out.strip() or err.strip()}
    elif _SYSTEM == "Windows":
        code, out, err = await _run_cmd(["netsh", "wlan", "disconnect"])
        return {"success": code == 0, "message": out.strip() or err.strip()}
    return {"success": False, "message": f"Not implemented for {_SYSTEM}"}


def get_performance_metrics() -> dict:
    """Get CPU, RAM, disk, temperature metrics. Requires psutil."""
    try:
        import psutil
    except ImportError:
        return {
            "error": "psutil not installed",
            "cpu_percent": 0,
            "ram_percent": 0,
            "platform": _SYSTEM,
        }

    disk_path = "C:\\" if _SYSTEM == "Windows" else "/"

    metrics = {
        "platform": _SYSTEM,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "ram_percent": psutil.virtual_memory().percent,
        "ram_used_gb": round(psutil.virtual_memory().used / (1024 ** 3), 2),
        "ram_total_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
        "swap_percent": psutil.swap_memory().percent,
        "disk_percent": psutil.disk_usage(disk_path).percent,
        "disk_used_gb": round(psutil.disk_usage(disk_path).used / (1024 ** 3), 2),
        "disk_total_gb": round(psutil.disk_usage(disk_path).total / (1024 ** 3), 2),
        "temperatures": {},
        "network_io": {},
        "uptime_seconds": 0,
    }

    try:
        import time
        metrics["uptime_seconds"] = int(time.time() - psutil.boot_time())
    except Exception:
        pass

    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for chip, readings in temps.items():
                for r in readings:
                    if r.current:
                        key = f"{chip}_{r.label or 'temp'}".replace(" ", "_")
                        metrics["temperatures"][key] = round(r.current, 1)
    except (AttributeError, Exception):
        pass

    try:
        net = psutil.net_io_counters()
        metrics["network_io"] = {
            "bytes_sent_mb": round(net.bytes_sent / (1024 ** 2), 2),
            "bytes_recv_mb": round(net.bytes_recv / (1024 ** 2), 2),
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        }
    except Exception:
        pass

    return metrics


def list_network_interfaces() -> list:
    """List all network interfaces with address and status info."""
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        result = []
        for iface, addr_list in addrs.items():
            stat = stats.get(iface)
            result.append({
                "name": iface,
                "is_up": stat.isup if stat else False,
                "speed_mbps": stat.speed if stat else 0,
                "mtu": stat.mtu if stat else 0,
                "addresses": [
                    {"family": str(a.family.name if hasattr(a.family, 'name') else a.family), "address": a.address}
                    for a in addr_list if a.address
                ],
            })
        return result
    except ImportError:
        return [{"error": "psutil not installed"}]
    except Exception as e:
        return [{"error": str(e)}]


def get_wifi_status() -> dict:
    """Get current Wi-Fi connection status."""
    try:
        import subprocess
        if _SYSTEM == "Linux":
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 4 and parts[1] == "wifi":
                    return {
                        "device": parts[0],
                        "connected": parts[2] == "connected",
                        "ssid": parts[3] if parts[2] == "connected" else None,
                    }
        elif _SYSTEM == "Windows":
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=5
            )
            connected = "State" in result.stdout and "connected" in result.stdout
            ssid = None
            for line in result.stdout.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    ssid = line.split(":", 1)[-1].strip()
            return {"connected": connected, "ssid": ssid}
    except Exception:
        pass
    return {"connected": False, "ssid": None}
