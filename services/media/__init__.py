# services/media/__init__.py
"""Media generation provider modules (image MVP; video later).

Isolated provider clients for the media generation layer: the ComfyUI client
exposes a connection probe (S3) and text-to-image generation (S4B).
"""

from .comfyui import ComfyUIProvider, generate, probe

__all__ = ["ComfyUIProvider", "generate", "probe"]
