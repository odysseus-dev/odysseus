# JUNIPERUS102 - Local Ollama Model Importer

This package imports every locally installed Ollama model into Juniperus as a governed local model endpoint.

## Endpoint registered

- Name: Local Ollama (All Models)
- Base URL: http://127.0.0.1:11434/v1
- Provider shape: Ollama/OpenAI-compatible chat endpoint
- Cached models: discovered from local Ollama
- Scope: local/shared endpoint
- Secrets stored: none

## Boundary

- No external connector calls
- No secrets stored
- No package installs
- No model pulls
- No cloud/API model routing
- No production mutation outside the Juniperus model registry

## Why one endpoint?

Juniperus model discovery already treats an endpoint as a host with a cached model list. One Ollama endpoint with all models is cleaner than one endpoint per local model.
