{
  description = "Odysseus – local AI workspace (NixOS flake)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShell = pkgs.mkShell {
          buildInputs = [ pkgs.podman-compose ];
          shellHook = ''
            echo "Odysseus dev shell. Run 'podman-compose up --build' to start."
          '';
        };
      }
    )
    // {
      nixosModules.odysseus =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.odysseus;
        in
        {
          options.services.odysseus = {
            enable = lib.mkEnableOption "Odysseus AI workspace";

            dataDir = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/odysseus";
              description = "Directory for persistent Odysseus data (database, uploads, memory, logs).";
            };

            port = lib.mkOption {
              type = lib.types.port;
              default = 7000;
              description = "Host port for the Odysseus web interface.";
            };

            llamaServerEndpoint = lib.mkOption {
              type = lib.types.str;
              default = "http://localhost:11434";
              description = ''
                Base URL of the local LLM server (Ollama, llama.cpp, vLLM, etc.).
                On a Podman bridge network the host is reachable at the gateway
                address, e.g. http://10.89.0.1:11434.
              '';
            };

            searxngSecretKey = lib.mkOption {
              type = lib.types.str;
              default = "change-me-before-exposing-to-the-network";
              description = ''
                Secret key for SearXNG CSRF/session signing.
                Override with a random string for any non-local deployment.
                Can be managed with sops-nix or agenix.
              '';
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ pkgs.podman-compose ];

            # Create persistent data directories on first activation.
            systemd.tmpfiles.rules = [
              "d ${cfg.dataDir}                       0755 root root -"
              "d ${cfg.dataDir}/data                  0755 root root -"
              "d ${cfg.dataDir}/logs                  0755 root root -"
              "d ${cfg.dataDir}/data/ssh              0700 root root -"
              "d ${cfg.dataDir}/data/huggingface      0755 root root -"
              "d ${cfg.dataDir}/data/local            0755 root root -"
              "d ${cfg.dataDir}/chroma                0755 root root -"
            ];

            # SearXNG settings template — the entrypoint script sed-substitutes
            # __SEARXNG_SECRET__ with the NixOS-managed key at container start.
            environment.etc."odysseus/searxng-settings.yml" = {
              text = ''
                use_default_settings: true
                server:
                  secret_key: "__SEARXNG_SECRET__"
                search:
                  formats:
                    - html
                    - json
              '';
            };

            environment.etc."odysseus/docker-compose.yml" = {
              text = ''
                services:
                  odysseus:
                    image: localhost/odysseus_odysseus:latest
                    build: ${self}
                    entrypoint: []
                    command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
                    ports:
                      - "127.0.0.1:${toString cfg.port}:7000"
                    volumes:
                      - ${cfg.dataDir}/data:/app/data:z
                      - ${cfg.dataDir}/logs:/app/logs:z
                      - ${cfg.dataDir}/data/ssh:/app/.ssh:z
                      - ${cfg.dataDir}/data/huggingface:/app/.cache/huggingface:z
                      - ${cfg.dataDir}/data/local:/app/.local:z
                    extra_hosts:
                      - "host.docker.internal:host-gateway"
                    environment:
                      - LLM_HOSTS=''${LLM_HOSTS:-${cfg.llamaServerEndpoint}}
                      - OLLAMA_BASE_URL=''${OLLAMA_BASE_URL:-${cfg.llamaServerEndpoint}}
                      - SEARXNG_INSTANCE=http://searxng:8080
                      - CHROMADB_HOST=chromadb
                      - CHROMADB_PORT=8000
                      - DATABASE_URL=sqlite:///./data/app.db
                      - AUTH_ENABLED=''${AUTH_ENABLED:-true}
                      - LOCALHOST_BYPASS=''${LOCALHOST_BYPASS:-false}
                      - SECURE_COOKIES=''${SECURE_COOKIES:-false}
                    devices:
                      - nvidia.com/gpu=all
                    depends_on:
                      searxng:
                        condition: service_healthy
                      chromadb:
                        condition: service_started
                    restart: unless-stopped

                  chromadb:
                    image: docker.io/chromadb/chroma:latest
                    ports:
                      - "127.0.0.1:8100:8000"
                    volumes:
                      - ${cfg.dataDir}/chroma:/chroma/chroma:z
                    environment:
                      - ANONYMIZED_TELEMETRY=FALSE
                    restart: unless-stopped

                  searxng:
                    # Pinned — see docker-compose.yml for rationale
                    image: docker.io/searxng/searxng:2026.5.31-7159b8aed
                    entrypoint:
                      - /bin/sh
                      - -c
                      - |
                        set -eu
                        if [ ! -s /etc/searxng/settings.yml ] || grep -q '__SEARXNG_SECRET__' /etc/searxng/settings.yml; then
                          secret="${cfg.searxngSecretKey}"
                          sed "s|__SEARXNG_SECRET__|$$secret|g" /tmp/searxng-settings.yml.template > /etc/searxng/settings.yml
                        fi
                        exec /usr/local/searxng/entrypoint.sh
                    ports:
                      - "127.0.0.1:8080:8080"
                    volumes:
                      - searxng-data:/etc/searxng:z
                      - /etc/odysseus/searxng-settings.yml:/tmp/searxng-settings.yml.template:ro,z
                    environment:
                      - SEARXNG_BASE_URL=http://localhost:8080/
                    cap_drop:
                      - ALL
                    cap_add:
                      - CHOWN
                      - SETGID
                      - SETUID
                      - DAC_OVERRIDE
                    healthcheck:
                      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5).read(1)\""]
                      interval: 5s
                      timeout: 6s
                      retries: 20
                      start_period: 10s
                    restart: unless-stopped

                  ntfy:
                    image: docker.io/binwiederhier/ntfy
                    command: serve
                    ports:
                      - "127.0.0.1:8091:80"
                    volumes:
                      - ntfy-cache:/var/cache/ntfy:z
                    environment:
                      - NTFY_BASE_URL=''${NTFY_BASE_URL:-http://localhost:8091}
                    restart: unless-stopped

                volumes:
                  searxng-data:
                  ntfy-cache:
              '';
            };

            systemd.services.odysseus = {
              description = "Odysseus AI workspace";
              after = [
                "network-online.target"
                "podman.service"
              ];
              wants = [ "network-online.target" ];
              wantedBy = [ "multi-user.target" ];
              serviceConfig = {
                Type = "oneshot";
                RemainAfterExit = true;
                WorkingDirectory = "/etc/odysseus";
                Environment = "PATH=/run/current-system/sw/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin";
                ExecStartPre = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml build";
                ExecStart = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml up -d";
                ExecStop = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml down";
                ExecReload = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml restart";
                Restart = "no";
                TimeoutStartSec = 600;
              };
            };
          };
        };
    };
}
