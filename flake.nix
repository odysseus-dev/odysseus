{
  description = "Odysseus — self-hosted AI assistant";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, nix-darwin }:
    let
      # Shared libs needed by pip-installed native wheels.
      # Defined here so both the per-system devShell and the NixOS module
      # (which receives its own pkgs) can derive the list.
      mkRuntimeLibs = pkgs: with pkgs; [
        stdenv.cc.cc.lib  # libstdc++.so.6, libgomp.so.1 (onnxruntime / fastembed)
        zlib
        openssl
        libffi
        bzip2
        xz
        sqlite
        ncurses
        readline
      ];

    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        runtimeLibs = mkRuntimeLibs pkgs;
        pythonEnv = (pkgs.python3.override {
          packageOverrides = pyself: pysuper: {
            niquests = pysuper.niquests.overridePythonAttrs (old: {
              doCheck = !pkgs.stdenv.isDarwin;
            });
          };
        }).withPackages (ps: with ps; [
          fastapi
          uvicorn
          python-multipart
          python-dotenv
          httpx
          pydantic
          pydantic-settings
          sqlalchemy
          pypdf
          beautifulsoup4
          charset-normalizer
          numpy
          chromadb
          fastembed
          youtube-transcript-api
          markdown
          icalendar
          python-dateutil
          caldav
          cryptography
          bcrypt
          mcp
          pyotp
          qrcode
          pillow
          croniter
          pytest
          pytest-asyncio
        ]);
      in {
        packages.default = pkgs.stdenv.mkDerivation {
          pname = "odysseus";
          version = "0.9.1";
          src = pkgs.lib.cleanSource ./.;

          nativeBuildInputs = [ pkgs.makeWrapper ];

          dontBuild = true;
          dontConfigure = true;

          installPhase = ''
            mkdir -p $out/share/odysseus
            cp -r . $out/share/odysseus/

            mkdir -p $out/bin
            makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/odysseus \
              --set PYTHONUNBUFFERED "1" \
              --set PYTHONPATH "$out/share/odysseus" \
              --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
              --add-flags "app:app"

            makeWrapper ${pythonEnv}/bin/python $out/bin/odysseus-setup \
              --set PYTHONPATH "$out/share/odysseus" \
              --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
              --add-flags "$out/share/odysseus/setup.py"
          '';
        };

        packages.container = pkgs.dockerTools.buildLayeredImage {
          name = "odysseus";
          tag = "latest";
          contents = [ self.packages.${system}.default ];
          config = {
            Entrypoint = [ "${self.packages.${system}.default}/bin/odysseus" ];
            Env = [
              "ODYSSEUS_DATA_DIR=/var/lib/odysseus/data"
              "PYTHONUNBUFFERED=1"
            ];
            ExposedPorts = {
              "7000/tcp" = {};
            };
            WorkingDir = "/var/lib/odysseus";
          };
          extraCommands = ''
            mkdir -p var/lib/odysseus/data
          '';
        };

        devShells.default = pkgs.mkShell {
          name = "odysseus";

          packages = with pkgs; [
            python312
            gcc
            gnumake
            cmake
            pkg-config
            git
            nodejs_22
            tmux
            openssh
            curl
          ] ++ runtimeLibs ++ [ pythonEnv ];

          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            export PYTHONPATH=""
            export PYTHONUNBUFFERED=1

            if [ ! -d .venv ]; then
              python -m venv .venv
              echo "[odysseus] Created .venv"
            fi
            source .venv/bin/activate

            HASH_FILE=.venv/.reqs_hash
            REQS_HASH=$(sha256sum requirements.txt | cut -d' ' -f1)
            if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$REQS_HASH" ]; then
              echo "[odysseus] Installing Python dependencies..."
              pip install -r requirements.txt \
                && echo "$REQS_HASH" > "$HASH_FILE" \
                && echo "[odysseus] Dependencies installed."
            fi

            echo ""
            echo "  Odysseus dev shell ready."
            echo "  First run:  python setup.py"
            echo "  Start:      uvicorn app:app --host 0.0.0.0 --port 7000"
            echo ""
          '';
        };
      }
    ) // {

      # NixOS module — system-independent.  Add to your NixOS config with:
      #
      #   inputs.odysseus.url = "path:/path/to/this/repo";
      #   imports = [ inputs.odysseus.nixosModules.default ];
      #   services.odysseus = {
      #     enable = true;
      #     environmentFile = "/run/secrets/odysseus-env";
      #   };
      #
      # The environmentFile must export LLM_HOST (and optionally OPENAI_API_KEY,
      # ODYSSEUS_ADMIN_USER, ODYSSEUS_ADMIN_PASSWORD, etc.).
      # See .env.example in the source for the full list.
      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.odysseus;
          runtimeLibs = mkRuntimeLibs pkgs;
          inherit (lib) mkEnableOption mkOption mkIf types optionalAttrs;
        in {
          options.services.odysseus = {
            enable = mkEnableOption "Odysseus AI assistant";

            package = mkOption {
              type = types.package;
              default = self.packages.${pkgs.system}.default;
              description = "The odysseus package to use.";
            };

            port = mkOption {
              type = types.port;
              default = 7000;
              description = "Port to listen on.";
            };

            host = mkOption {
              type = types.str;
              default = "0.0.0.0";
              description = "Interface to bind.";
            };

            dataDir = mkOption {
              type = types.path;
              default = "/var/lib/odysseus";
              description = "Root directory for all persistent app data (DB, uploads, vectors, etc.).";
            };

            user = mkOption {
              type = types.str;
              default = "odysseus";
            };

            group = mkOption {
              type = types.str;
              default = "odysseus";
            };

            environmentFile = mkOption {
              type = types.nullOr types.path;
              default = null;
              description = ''
                Path to a file of KEY=VALUE environment variables — API keys,
                LLM_HOST, ODYSSEUS_ADMIN_USER / ODYSSEUS_ADMIN_PASSWORD, etc.
                See .env.example in the source for all available variables.
                Use a path under /run/secrets or similar; the file must NOT be
                world-readable.
              '';
            };
          };

          config = mkIf cfg.enable {
            users.users.${cfg.user} = {
              isSystemUser = true;
              group = cfg.group;
              home = cfg.dataDir;
              createHome = true;
              description = "Odysseus service user";
            };
            users.groups.${cfg.group} = {};

            systemd.services.odysseus = {
              description = "Odysseus AI assistant";
              after = [ "network.target" ];
              wantedBy = [ "multi-user.target" ];

              # Tools the app shells out to at runtime
              path = with pkgs; [
                bash
                nodejs_22  # npx for optional Browser MCP server
                tmux        # Cookbook background downloads/serves
                openssh     # Cookbook remote server probes
                curl
                git
              ] ++ runtimeLibs;

              environment = {
                PYTHONUNBUFFERED = "1";
                ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
                LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
              };

              preStart = let
                data = "${cfg.dataDir}/data";
              in ''
                # Create data subdirectories (StateDirectory creates the root)
                for d in "${data}" \
                          "${data}/uploads" \
                          "${data}/personal_docs" \
                          "${data}/personal_docs/runbook" \
                          "${data}/tts_cache" \
                          "${data}/generated_images" \
                          "${data}/deep_research" \
                          "${data}/chroma" \
                          "${data}/rag" \
                          "${data}/memory_vectors" \
                          "${data}/logs"; do
                  mkdir -p "$d"
                done

                # First-time setup: create admin user
                if [ ! -f "${data}/auth.json" ]; then
                  ODYSSEUS_DATA_DIR="${data}" \
                    ${cfg.package}/bin/odysseus-setup
                fi
              '';

              serviceConfig = {
                Type = "simple";
                User = cfg.user;
                Group = cfg.group;
                # CWD is the data dir so database.py's relative "data/..." paths
                # resolve to the mutable state directory, not the Nix store.
                WorkingDirectory = cfg.dataDir;
                ExecStart = "${cfg.package}/bin/odysseus --host ${cfg.host} --port ${toString cfg.port}";
                StateDirectory = "odysseus";
                StateDirectoryMode = "0750";
                Restart = "on-failure";
                RestartSec = "3s";
              } // optionalAttrs (cfg.environmentFile != null) {
                EnvironmentFile = "-${cfg.environmentFile}";
              };
            };
          };
        };

      # nix-darwin module — system-independent. Add to your darwin config with:
      #
      #   inputs.odysseus.url = "path:/path/to/this/repo";
      #   imports = [ inputs.odysseus.darwinModules.default ];
      #   services.odysseus = {
      #     enable = true;
      #     environmentFile = "/run/secrets/odysseus-env";
      #   };
      #
      darwinModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.odysseus;
          runtimeLibs = mkRuntimeLibs pkgs;
          inherit (lib) mkEnableOption mkOption mkIf types optionalAttrs;
        in {
          options.services.odysseus = {
            enable = mkEnableOption "Odysseus AI assistant";

            package = mkOption {
              type = types.package;
              default = self.packages.${pkgs.system}.default;
              description = "The odysseus package to use.";
            };

            port = mkOption {
              type = types.port;
              default = 7000;
              description = "Port to listen on.";
            };

            host = mkOption {
              type = types.str;
              default = "0.0.0.0";
              description = "Interface to bind.";
            };

            dataDir = mkOption {
              type = types.path;
              default = "/var/lib/odysseus";
              description = "Root directory for all persistent app data (DB, uploads, vectors, etc.).";
            };

            user = mkOption {
              type = types.str;
              default = "odysseus";
            };

            group = mkOption {
              type = types.str;
              default = "odysseus";
            };

            environmentFile = mkOption {
              type = types.nullOr types.path;
              default = null;
              description = ''
                Path to a file of KEY=VALUE environment variables — API keys,
                LLM_HOST, ODYSSEUS_ADMIN_USER / ODYSSEUS_ADMIN_PASSWORD, etc.
                See .env.example in the source for all available variables.
                Use a path under /run/secrets or similar; the file must NOT be
                world-readable.
              '';
            };
          };

          config = mkIf cfg.enable {
            users.users.${cfg.user} = {
              gid = config.users.groups.${cfg.group}.gid or null;
              home = cfg.dataDir;
              createHome = true;
              description = "Odysseus service user";
            };
            users.groups.${cfg.group} = {};

            launchd.daemons.odysseus = {
              command = let
                data = "${cfg.dataDir}/data";
              in ''
                #!/bin/sh
                # Create data subdirectories
                for d in "${data}" \
                          "${data}/uploads" \
                          "${data}/personal_docs" \
                          "${data}/personal_docs/runbook" \
                          "${data}/tts_cache" \
                          "${data}/generated_images" \
                          "${data}/deep_research" \
                          "${data}/chroma" \
                          "${data}/rag" \
                          "${data}/memory_vectors" \
                          "${data}/logs"; do
                  mkdir -p "$d"
                done

                # First-time setup: create admin user
                if [ ! -f "${data}/auth.json" ]; then
                  ODYSSEUS_DATA_DIR="${data}" \
                    ${cfg.package}/bin/odysseus-setup
                fi

                # Start the server
                exec ${cfg.package}/bin/odysseus --host ${cfg.host} --port ${toString cfg.port}
              '';

              serviceConfig = {
                KeepAlive = true;
                RunAtLoad = true;
                StandardOutPath = "${cfg.dataDir}/logs/launchd.out.log";
                StandardErrorPath = "${cfg.dataDir}/logs/launchd.err.log";
                EnvironmentVariables = {
                  PYTHONUNBUFFERED = "1";
                  ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
                  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
                };
              };
            };

            environment.systemPackages = with pkgs; [
              bash
              nodejs_22
              tmux
              openssh
              curl
              git
            ] ++ runtimeLibs;
          };
        };

      checks = {
        x86_64-linux.nixos-module =
          let
            system = "x86_64-linux";
            pkgs = nixpkgs.legacyPackages.${system};
          in
            pkgs.testers.nixosTest {
              name = "odysseus-nixos-module";
              nodes.machine = {
                imports = [ self.nixosModules.default ];
                services.odysseus = {
                  enable = true;
                  host = "0.0.0.0";
                };
              };
              testScript = ''
                machine.wait_for_unit("odysseus.service")
                machine.wait_for_open_port(7000)
                response = machine.succeed("curl -sf http://localhost:7000")
                assert response != "", "Expected non-empty response from Odysseus"
              '';
            };

        x86_64-linux.container =
          let
            system = "x86_64-linux";
            pkgs = nixpkgs.legacyPackages.${system};
            image = self.packages.${system}.container;
          in
            pkgs.testers.nixosTest {
              name = "odysseus-container";
              nodes.machine = {
                virtualisation.podman.enable = true;
                virtualisation.diskSize = 8192;
                virtualisation.memorySize = 4096;
                users.users.test.isNormalUser = true;
              };
              testScript = ''
                machine.wait_for_unit("sockets.target")
                # Ensure podman storage is initialised before loading
                machine.succeed("podman info")
                machine.succeed("podman load -i ${image}")
                machine.succeed("podman run -d --name odysseus -p 7000:7000 odysseus:latest")
                machine.wait_for_open_port(7000)
                response = machine.succeed("curl -sf http://localhost:7000")
                assert response != "", "Expected non-empty response from Odysseus container"
              '';
            };

        aarch64-darwin.darwin-module =
          let
            system = "aarch64-darwin";
            pkgs = nixpkgs.legacyPackages.${system};
            darwinConfig = nix-darwin.lib.darwinSystem {
              inherit system;
              modules = [
                self.darwinModules.default
                {
                  services.odysseus.enable = true;
                  system.stateVersion = 5;
                }
              ];
            };
          in
            darwinConfig.system;

        aarch64-darwin.integration-test =
          let
            system = "aarch64-darwin";
            pkgs = nixpkgs.legacyPackages.${system};
            odysseus = self.packages.${system}.default;
          in
            pkgs.runCommand "odysseus-darwin-integration-test" {
              nativeBuildInputs = [ odysseus pkgs.curl pkgs.python3 ];
            } ''
              set -euo pipefail

              DATA_DIR=$(mktemp -d)
              export ODYSSEUS_DATA_DIR="$DATA_DIR/data"
              mkdir -p "$ODYSSEUS_DATA_DIR"

              # Set up runtime library path (same as the darwin module)
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath (mkRuntimeLibs pkgs)}"

              # Database path must be exported so core/database.py sees it
              # in the server process (odysseus-setup sets it internally only)
              export DATABASE_URL="sqlite:///$ODYSSEUS_DATA_DIR/app.db"

              # SSL_CERT_FILE may point to a missing path in the build env;
              # unset it so httpx falls back to system certs
              unset SSL_CERT_FILE

              # Create admin user
              ${odysseus}/bin/odysseus-setup

              # Find an ephemeral port
              PORT=$(python3 -c "import socket; s=socket.socket(); s.bind((\"\", 0)); print(s.getsockname()[1]); s.close()")

              # Start server in background (redirect stdout to avoid BrokenPipe in build)
              ${odysseus}/bin/odysseus --host 127.0.0.1 --port "$PORT" > "$DATA_DIR/server.log" 2>&1 &
              SERVER_PID=$!

              # Wait for server with 30s timeout
              i=0
              while [ $i -lt 30 ]; do
                if curl -sf -o /dev/null "http://127.0.0.1:$PORT" > /dev/null 2>&1; then
                  break
                fi
                if ! kill -0 $SERVER_PID 2>/dev/null; then
                  echo "FAIL: server exited early"
                  echo "--- server.log ---"
                  tail -40 "$DATA_DIR/server.log" || true
                  exit 1
                fi
                i=$((i + 1))
                sleep 1
              done

              if [ $i -eq 30 ]; then
                echo "FAIL: timed out waiting for Odysseus"
                echo "--- server.log ---"
                tail -40 "$DATA_DIR/server.log" || true
                kill $SERVER_PID 2>/dev/null || true
                exit 1
              fi

              # Verify response (check HTTP status, not body)
              if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT" > /dev/null 2>&1; then
                echo "FAIL: no response from Odysseus on port $PORT"
                echo "--- server.log ---"
                tail -40 "$DATA_DIR/server.log" || true
                kill $SERVER_PID 2>/dev/null || true
                exit 1
              fi

              echo "PASS: got response from Odysseus on port $PORT"

              # Clean up
              kill $SERVER_PID 2>/dev/null || true
              wait $SERVER_PID 2>/dev/null || true

              touch $out
            '';
      };
    };
}
