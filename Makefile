SHELL := /usr/bin/env bash

.PHONY: help start stop restart build rebuild cuda-build cuda-rebuild cuda-start cuda-enable logs ps config gpu-check gpu-enable

help:
	@printf 'Odysseus Docker targets:\n'
	@printf '  make start       Start services, building changed images if needed\n'
	@printf '  make stop        Stop services and remove orphan containers\n'
	@printf '  make restart     Stop, then start\n'
	@printf '  make build       Build the Odysseus image using Docker cache\n'
	@printf '  make rebuild     Stop, rebuild Odysseus with no cache, then start\n'
	@printf '  make cuda-build  Build the CUDA llama.cpp Odysseus image\n'
	@printf '  make cuda-rebuild Enable CUDA llama.cpp overlay, no-cache rebuild, then start\n'
	@printf '  make cuda-start  Enable CUDA llama.cpp overlay, then start\n'
	@printf '  make logs        Follow Odysseus app logs\n'
	@printf '  make ps          Show Compose service status\n'
	@printf '  make config      Render merged Compose config\n'
	@printf '  make gpu-check   Verify host Docker GPU passthrough\n'
	@printf '  make gpu-enable  Enable the NVIDIA Compose overlay in .env\n'

start:
	docker compose up -d --build

stop:
	docker compose down --remove-orphans

restart: stop start

build:
	docker compose build odysseus

rebuild:
	docker compose down --remove-orphans
	docker compose build --no-cache --pull odysseus
	docker compose up -d

cuda-build:
	COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.llamacpp.yml docker compose build odysseus

cuda-rebuild:
	scripts/check-docker-gpu.sh --enable-nvidia-overlay --yes
	sed -i 's#^COMPOSE_FILE=.*#COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.llamacpp.yml#' .env
	docker compose down --remove-orphans
	docker compose build --no-cache --pull odysseus
	docker compose up -d

cuda-start:
	scripts/check-docker-gpu.sh --enable-nvidia-overlay --yes
	sed -i 's#^COMPOSE_FILE=.*#COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.llamacpp.yml#' .env
	docker compose up -d --build

logs:
	docker compose logs -f odysseus

ps:
	docker compose ps

config:
	docker compose config

gpu-check:
	scripts/check-docker-gpu.sh

gpu-enable:
	scripts/check-docker-gpu.sh --enable-nvidia-overlay
