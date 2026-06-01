{
  description = "Odysseus AI Workspace Environment";

  # Defines the source of our packages. Pinned to the 25.05 branch for stability.
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Shared libs needed by pip-installed native wheels.
        runtimeLibs = with pkgs; [
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
      in {
        devShells.default = pkgs.mkShell {
          name = "odysseus-dev-env";

          # Dependencies that will be available in the environment.
          # These are completely isolated from the host operating system.
          buildInputs = with pkgs; [

            # Python 3.12 and all required application dependencies
            (python312.withPackages (ps: with ps; [
              fastapi uvicorn python-multipart python-dotenv httpx
              pydantic pydantic-settings sqlalchemy pypdf
              beautifulsoup4 charset-normalizer numpy
              chromadb fastembed youtube-transcript-api markdown
              icalendar python-dateutil caldav cryptography
              bcrypt mcp pyotp qrcode pytest pytest-asyncio
            ]))

            # System tools required for building and running the application
            git cmake nodejs tmux openssh gosu curl gcc pkg-config process-compose
            gnumake
          ] ++ runtimeLibs;

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
                python setup.py
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
            dontBuild = true;
            dontConfigure = true;
            installPhase = ''
              mkdir -p $out/share/odysseus
              cp -r . $out/share/odysseus/
            '';
          };
        };

        flake = {
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
              inherit runtimeLibs;
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
                    python312
                    nodejs_22  # npx for optional Browser MCP server
                    tmux        # Cookbook background downloads/serves
                    openssh     # Cookbook remote server probes
                    curl
                    git
                  ] ++ runtimeLibs;

                  environment = {
                    PYTHONUNBUFFERED = "1";
                    # WorkingDirectory is cfg.dataDir so all relative "data/..."
                    # paths in database.py resolve correctly. PYTHONPATH points
                    # into the Nix store so Python can find app.py and friends.
                    PYTHONPATH = "${cfg.package}/share/odysseus";
                    # Route constants.py's DATA_DIR to the mutable state directory.
                    ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
                    LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
                  };

                  preStart = let
                    src = "${cfg.package}/share/odysseus";
                    venv = "${cfg.dataDir}/.venv";
                    data = "${cfg.dataDir}/data";
                  in ''
                    # Bootstrap the venv on first deploy or after a package update
                    if [ ! -d "${venv}" ]; then
                      python -m venv "${venv}"
                    fi

                    # Re-install Python deps when requirements.txt changes
                    HASH_FILE="${venv}/.reqs_hash"
                    REQS_HASH=$(sha256sum "${src}/requirements.txt" | cut -d' ' -f1)
                    if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$REQS_HASH" ]; then
                      "${venv}/bin/pip" install -r "${src}/requirements.txt"
                      echo "$REQS_HASH" > "$HASH_FILE"
                    fi

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
                        "${venv}/bin/python" "${src}/setup.py"
                    fi
                  '';

                  serviceConfig = {
                    Type = "simple";
                    User = cfg.user;
                    Group = cfg.group;
                    # CWD is the data dir so database.py's relative "data/..." paths
                    # resolve to the mutable state directory, not the Nix store.
                    WorkingDirectory = cfg.dataDir;
                    ExecStart = "${cfg.dataDir}/.venv/bin/uvicorn app:app --host ${cfg.host} --port ${toString cfg.port}";
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
        };
      }
    );
}
