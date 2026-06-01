{
  description = "Odysseus — self-hosted AI assistant";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
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
      in {
        # Source-only package: copies the app tree into the Nix store.
        # Python deps are NOT bundled here — the NixOS module manages them
        # via a pip-populated venv in the service's StateDirectory.
        packages.default = pkgs.stdenv.mkDerivation {
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
          ] ++ runtimeLibs;

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
