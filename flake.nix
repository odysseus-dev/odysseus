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
            virtualisation.podman.enable = lib.mkDefault true;
            virtualisation.podman.dockerCompat = lib.mkDefault true;

            # Fix DNS resolution inside Podman containers.
            #
            # Root cause: aardvark-dns (Podman's internal resolver) intercepts all
            # container DNS queries as the primary nameserver and forwards external
            # lookups to whatever is in the host's /etc/resolv.conf.  On NixOS with
            # systemd-resolved the host resolv.conf contains "nameserver 127.0.0.53"
            # (the stub listener), which is only reachable on the host loopback —
            # NOT from aardvark running on the Podman bridge.  aardvark therefore
            # returns SERVFAIL for every external name; glibc does not fall back to
            # secondary nameservers on SERVFAIL, only on timeout, so adding real IPs
            # to containers.conf dns_servers has no effect.
            #
            # Fix: disable the stub listener so systemd-resolved writes real upstream
            # IPs into /etc/resolv.conf.  aardvark then picks those up and external
            # DNS works both on the host and inside every container.
            networking.nameservers = lib.mkDefault [
              "1.1.1.1"
              "9.9.9.9"
            ];

            # Allow DNS from every Podman bridge network to reach aardvark-dns.
            # NixOS's nixos-fw INPUT chain has policy drop; the netavark chain
            # (which already has the right accept rules) never fires because
            # nixos-fw drops the packet first.  Podman allocates bridge
            # interfaces named podman0, podman1, … so we open port 53 on the
            # known range.  podman0 = default network (handled by the Podman
            # NixOS module), podman1 = odysseus_default.
            networking.firewall.interfaces."podman1" = lib.mkDefault {
              allowedUDPPorts = [ 53 ];
              allowedTCPPorts = [ 53 ];
            };
            services.resolved.settings = lib.mkDefault {
              Resolve = {
                DNS = "1.1.1.1 9.9.9.9";
                FallbackDNS = "8.8.8.8 8.8.4.4";
                DNSStubListener = "no";
              };
            };

            virtualisation.containers.containersConf.settings = {
              containers.dns_servers = [
                "1.1.1.1"
                "9.9.9.9"
              ];
            };

            assertions = [
              {
                assertion = cfg.searxngSecretKey != "change-me-before-exposing-to-the-network";
                message = "services.odysseus.searxngSecretKey must be changed from its insecure default value before deployment";
              }
            ];

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
                    dns:
                      - 1.1.1.1
                      - 9.9.9.9
                    environment:
                      - PUID=''${PUID:-1000}
                      - PGID=''${PGID:-1000}
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
                    dns:
                      - 1.1.1.1
                      - 9.9.9.9
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
                    dns:
                      - 1.1.1.1
                      - 9.9.9.9
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
                    dns:
                      - 1.1.1.1
                      - 9.9.9.9
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
                ExecStartPre = [
                  # Always recreate the Podman network so aardvark-dns starts fresh
                  # and reads the current /etc/resolv.conf (real upstream IPs).
                  # podman-compose down in pod mode removes the pod but NOT the
                  # network, so the stale aardvark instance persists across restarts
                  # with the old forwarding config if we only create-if-missing.
                  "${pkgs.bash}/bin/bash -c '${pkgs.podman}/bin/podman network rm odysseus_default 2>/dev/null; ${pkgs.podman}/bin/podman network create --dns=1.1.1.1 --dns=9.9.9.9 odysseus_default'"
                  "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml build"
                ];
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
