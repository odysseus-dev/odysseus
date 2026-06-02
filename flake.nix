{
  description = "Odysseus AI Workspace Environment";

  # Defines the source of our packages. Pinned to the 25.05 branch for stability.
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      nix-darwin,
    }:
    let
      mkRuntimeLibs =
        pkgs: with pkgs; [
          stdenv.cc.cc.lib # libstdc++.so.6, libgomp.so.1 (onnxruntime / fastembed)
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
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Shared libs needed by pip-installed native wheels.
        runtimeLibs = mkRuntimeLibs pkgs;
        # Python 3.12 and all required application dependencies
        pythonEnv =
          (pkgs.python3.override {
            packageOverrides = pyself: pysuper: {
              niquests = pysuper.niquests.overridePythonAttrs (old: {
                doCheck = !pkgs.stdenv.isDarwin;
              });
            };
          }).withPackages
            (
              ps: with ps; [
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
              ]
            );
      in
      {
        devShells.default = pkgs.mkShell {
          name = "odysseus-dev-env";

          # Dependencies that will be available in the environment.
          # These are completely isolated from the host operating system.
          buildInputs =
            with pkgs;
            [
              # System tools required for building and running the application
              git
              cmake
              nodejs
              tmux
              openssh
              curl
              gcc
              pkg-config
              process-compose
              gnumake
              pythonEnv
            ]
            ++ lib.optionals pkgs.stdenv.isLinux [
              gosu
            ]
            ++ runtimeLibs;

          # Environment variables automatically injected into the shell
          env = {
            APP_HOST = "0.0.0.0";
            APP_PORT = "7000";
            PYTHONPATH = ".";
            ODYSSEUS_DATA_DIR = "./data";
          };

          # A bash script that executes automatically when a user runs `nix develop`
          shellHook = ''
            # Fixes dynamic linking issues for Python libraries relying on C/C++ dependencies
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

            SESSION_NAME="odysseus"

            echo "Odysseus Nix shell for ${system} is loaded."

            # 1. First-time Setup Check
            # If the database directory doesn't exist, we assume this is a fresh clone.
            if [ ! -d "$ODYSSEUS_DATA_DIR" ]; then
                echo "First configuration detected. Everything is being set-up!"

                # Execute the initial setup to generate the admin account
                ${self.packages.${system}.default}/bin/odysseus-setup
                echo "-----------------------------------------------------"
                echo "Make sure you remember your admin username and temporary password!"

                # Pause the terminal so the user can copy their credentials before the UI launches
                read -p "Press enter to continue to the dashboard!" < /dev/tty
            else
                echo "Setup has already been executed... Starting application"
            fi

            # 2. Background Process Automation (Tmux)
            # We run process-compose inside a detached tmux session so the user
            # can safely close their terminal window without killing the server.
            if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
              # Reconnect to the existing background session
              tmux attach -t "$SESSION_NAME"
            else
              # Start a new background session and launch the process manager
              tmux new -s "$SESSION_NAME" "process-compose up"
            fi

            # 3. Clean Exit
            # Once the user kills the tmux session (e.g., via odysseus-down), safely exit the Nix shell
            exit
          '';
        };

        packages = {
          default = pkgs.stdenv.mkDerivation {
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

          container = pkgs.dockerTools.buildLayeredImage {
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
                "7000/tcp" = { };
              };
              WorkingDir = "/var/lib/odysseus";
            };
            extraCommands = ''
              mkdir -p var/lib/odysseus/data
            '';
          };
        };
      }
    )
    // {
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
      nixosModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.odysseus;
          runtimeLibs = mkRuntimeLibs pkgs;
          inherit (lib)
            mkEnableOption
            mkOption
            mkIf
            types
            optionalAttrs
            ;
        in
        {
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
            users.groups.${cfg.group} = { };

            systemd.services.odysseus = {
              description = "Odysseus AI assistant";
              after = [ "network.target" ];
              wantedBy = [ "multi-user.target" ];

              # Tools the app shells out to at runtime
              path =
                with pkgs;
                [
                  bash
                  nodejs # npx for optional Browser MCP server
                  tmux # Cookbook background downloads/serves
                  openssh # Cookbook remote server probes
                  curl
                  git
                ]
                ++ runtimeLibs;

              environment = {
                PYTHONUNBUFFERED = "1";
                # Route constants.py's DATA_DIR to the mutable state directory.
                ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
                LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
              };

              preStart =
                let
                  data = "${cfg.dataDir}/data";
                in
                ''
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

                  # First-time setup: create admin user.
                  # The DB itself is initialised automatically by core/database.py
                  # on the first import (init_db() runs at module load).
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
              }
              // optionalAttrs (cfg.environmentFile != null) {
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
      darwinModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.odysseus;
          runtimeLibs = mkRuntimeLibs pkgs;
          inherit (lib)
            mkEnableOption
            mkOption
            mkIf
            types
            optionalAttrs
            ;
        in
        {
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
            users.groups.${cfg.group} = { };

            launchd.daemons.odysseus = {
              command =
                let
                  data = "${cfg.dataDir}/data";
                in
                ''
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

            environment.systemPackages =
              with pkgs;
              [
                bash
                nodejs_22
                tmux
                openssh
                curl
                git
              ]
              ++ runtimeLibs;
          };
        };

      checks = {
        x86_64-linux.nixos-module =
          let
            system = "x86_64-linux";
            pkgs = import nixpkgs { inherit system; };
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
              response = machine.succeed("curl -sfL http://localhost:7000")
              assert response != "", "Expected non-empty response from Odysseus"
            '';
          };

        x86_64-linux.container =
          let
            system = "x86_64-linux";
            pkgs = import nixpkgs { inherit system; };
            image = self.packages.${system}.container;
          in
          pkgs.runCommand "odysseus-container-check"
            {
              buildInputs = [
                pkgs.gnutar
                pkgs.gzip
                pkgs.jq
              ];
            }
            ''
              # Verify the image tarball is a valid gzip archive
              file ${image} | grep -q 'gzip compressed data' || {
                echo "ERROR: ${image} is not a valid gzip archive"
                file ${image}
                exit 1
              }

              # Verify the image contains standard container layout elements
              CONTENTS=$(tar -tzf ${image})
              echo "$CONTENTS"

              echo "$CONTENTS" | grep -q 'manifest.json' || {
                echo "ERROR: missing manifest.json"
                exit 1
              }

              # Verify the entrypoint is set (odysseus binary)
              echo "$CONTENTS" | grep -q 'odysseus' || {
                echo "WARNING: odysseus binary not found in image contents"
              }

              echo "odysseus container image is valid"
              touch $out
            '';

        aarch64-darwin.darwin-module =
          let
            system = "aarch64-darwin";
            pkgs = import nixpkgs { inherit system; };
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
            pkgs = import nixpkgs { inherit system; };
            odysseus = self.packages.${system}.default;
          in
          pkgs.runCommand "odysseus-darwin-integration-test"
            {
              nativeBuildInputs = [
                odysseus
                pkgs.curl
                pkgs.python3
              ];
            }
            ''
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
