"""Local llama.cpp serving + model hub for Odysseus.

A real, zero-toolchain local-inference integration: provisions a prebuilt
llama-server binary (CUDA on NVIDIA, CPU fallback), downloads GGUF models from
HuggingFace, launches servers with GPU offload, tracks their lifecycle, and
exposes an OpenAI-compatible endpoint that auto-registers in the chat picker.

This replaces the old "vLLM is not supported on Windows" dead-end with a
working local LLM path that runs out-of-the-box on Windows (and Linux/macOS).
"""

from .manager import LlamaManager, get_llama_manager

__all__ = ["LlamaManager", "get_llama_manager"]
