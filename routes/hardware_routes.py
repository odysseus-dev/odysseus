"""Hardware control API routes — metrics, Wi-Fi, network interfaces."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user


class WifiConnectBody(BaseModel):
    ssid: str
    password: str = ""


def setup_hardware_routes():
    router = APIRouter(prefix="/api/hardware", tags=["hardware"])

    def _user(request: Request) -> str:
        owner = get_current_user(request)
        if not owner:
            raise HTTPException(status_code=401, detail="Authentication required")
        return owner

    def _admin(request: Request) -> str:
        owner = _user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Admin access required for hardware control")
        return owner

    @router.get("/metrics")
    def get_metrics(request: Request):
        """
        Get real-time CPU, RAM, disk, temperature, and network I/O metrics.
        Risk level: LOW — read-only, no side effects.
        """
        _user(request)
        from src.host_bridge import get_performance_metrics
        return get_performance_metrics()

    @router.get("/interfaces")
    def list_interfaces(request: Request):
        """List all network interfaces with address and status info."""
        _user(request)
        from src.host_bridge import list_network_interfaces
        return {"interfaces": list_network_interfaces()}

    @router.get("/wifi/status")
    def wifi_status(request: Request):
        """Get current Wi-Fi connection status."""
        _user(request)
        from src.host_bridge import get_wifi_status
        return get_wifi_status()

    @router.get("/wifi/scan")
    async def scan_wifi(request: Request):
        """
        Scan for available Wi-Fi networks.
        Risk level: MEDIUM — triggers hardware-level network scan.
        Requires admin.
        """
        _admin(request)
        from src.host_bridge import scan_wifi_interfaces
        networks = await scan_wifi_interfaces()
        return {"networks": networks, "count": len(networks)}

    @router.post("/wifi/connect")
    async def connect_wifi(body: WifiConnectBody, request: Request):
        """
        Connect to a Wi-Fi network.
        Risk level: MEDIUM — modifies host network configuration.
        Requires admin + explicit call (user must initiate from UI).
        """
        _admin(request)

        if not body.ssid.strip():
            raise HTTPException(status_code=400, detail="SSID is required")

        from src.host_bridge import connect_wifi
        result = await connect_wifi(body.ssid.strip(), body.password)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("message", "Connection failed"))
        return result

    @router.post("/wifi/disconnect")
    async def disconnect_wifi(request: Request):
        """Disconnect from current Wi-Fi network. Requires admin."""
        _admin(request)
        from src.host_bridge import disconnect_wifi
        result = await disconnect_wifi()
        return result

    return router
