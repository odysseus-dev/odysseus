# NixOS and nix-darwin service modules for Odysseus.
#
# `src` is the repo root, passed by the flake so package builds resolve it
# from this nested module file. Exposes { nixosModule, darwinModule }.
{ src }:
let
  inherit (import ../overlays/python.nix) mkRuntimeLibs;
  inherit (import ../packages/packages.nix) mkOdysseusPackage;
  # Tools the app shells out to / probes with shutil.which at runtime
  # (Cookbook background jobs, the Browser MCP via npx, remote server probes).
  mkServiceTools =
    pkgs: with pkgs; [
      bash
      nodejs
      tmux
      openssh
      curl
      git
    ];
in
{
  nixosModule =
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

        extraPythonPackages = mkOption {
          type = with lib.types; functionTo (listOf package);
          default = ps: [ ];
          example = lib.literalExpression "ps: [ ps.hf-transfer ps.rembg ps.diffusers ]";
          description = ''
            Extra Python packages merged into the app environment, in
            withPackages form (ps: [ ps.hf-transfer ps.rembg ]). Lets the app
            import deps the Cookbook would otherwise pip-install, which fails
            on the read-only Nix store. Setting this rebuilds the bundled
            package; ignored if `package` is set explicitly.
          '';
        };

        package = mkOption {
          type = types.package;
          default = mkOdysseusPackage pkgs src cfg.extraPythonPackages;
          defaultText = lib.literalExpression "odysseus built with config.services.odysseus.extraPythonPackages";
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

        chromaPort = mkOption {
          type = types.port;
          default = 8100;
          description = ''
            Port for the bundled ChromaDB vector database server. Bound to
            loopback only; the app connects to it over HTTP.
          '';
        };

        searxng = {
          enable = mkEnableOption "bundled SearXNG metasearch (web search / deep research)";
          port = mkOption {
            type = types.port;
            default = 8888;
            description = ''
              Port for the bundled SearXNG instance. Bound to loopback only; the
              app reaches it via SEARXNG_INSTANCE.
            '';
          };
          secretKey = mkOption {
            type = types.str;
            default = "change-me-before-exposing-to-the-network";
            description = ''
              SearXNG secret_key (CSRF / session signing). MUST be changed from the
              default before enabling. Note: this value lands in the world-readable
              Nix store — for a hardened setup, set it out-of-band instead.
            '';
          };
        };

        extraEnvironmentVariables = mkOption {
          type = with lib.types; attrsOf str;
          default = { };
          example = {
            SEARXNG_GENERAL_ENGINES = "bing,mojeek";
            LLM_HOST = "http://10.0.0.5:11434";
          };
          description = ''
            Extra environment variables for the app service. Merged last, so they
            override the module's own derived values (SEARXNG_INSTANCE, etc.) — the
            escape hatch for app settings without a dedicated option.
          '';
        };

        llamaCpp = {
          enable = mkEnableOption "bundling llama.cpp (llama-server) for Cookbook GGUF serving";
          package = mkOption {
            type = types.package;
            default = pkgs.llama-cpp;
            example = lib.literalExpression "pkgs.llama-cpp-rocm";
            description = ''
              llama.cpp build providing `llama-server`, put on the service
              PATH so the Cookbook detects llama.cpp and the serve fallback
              has a real binary (no runtime cmake build). Override for a GPU
              backend, e.g. pkgs.llama-cpp-rocm, pkgs.llama-cpp-vulkan, or
              pkgs.llama-cpp.override { cudaSupport = true; }.
            '';
          };
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

      imports = [ ../security/firewall.nix ];

      config = mkIf cfg.enable {
        users.users.${cfg.user} = {
          isSystemUser = true;
          group = cfg.group;
          home = cfg.dataDir;
          createHome = true;
          description = "Odysseus service user";
        };
        users.groups.${cfg.group} = { };

        assertions = [
          {
            assertion =
              !cfg.searxng.enable || cfg.searxng.secretKey != "change-me-before-exposing-to-the-network";
            message = "services.odysseus.searxng.secretKey must be changed from its default before enabling SearXNG.";
          }
        ];

        # Bundled SearXNG metasearch (web search / deep research). Loopback-only;
        # the app reaches it via SEARXNG_INSTANCE (set on the app service below).
        services.searx = mkIf cfg.searxng.enable {
          enable = true;
          settings = {
            server = {
              port = cfg.searxng.port;
              bind_address = "127.0.0.1";
              secret_key = cfg.searxng.secretKey;
              limiter = false;
            };
            # Odysseus queries the JSON API, which SearXNG disables by default.
            search.formats = [
              "html"
              "json"
            ];
          };
        };

        # ChromaDB vector database server. The app talks to it over HTTP, so
        # it must be running for RAG / vector memory to work.
        systemd.services.odysseus-chroma = {
          description = "Odysseus ChromaDB vector database";
          after = [ "network.target" ];
          wantedBy = [ "multi-user.target" ];

          path = with pkgs; [ bash ] ++ runtimeLibs;

          environment = {
            PYTHONUNBUFFERED = "1";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
          };

          preStart = ''
            mkdir -p "${cfg.dataDir}/data/chroma"
          '';

          serviceConfig = {
            Type = "simple";
            User = cfg.user;
            Group = cfg.group;
            WorkingDirectory = cfg.dataDir;
            ExecStart = ''
              ${cfg.package}/bin/odysseus-chroma run \
                --path ${cfg.dataDir}/data/chroma \
                --host 127.0.0.1 \
                --port ${toString cfg.chromaPort}
            '';
            StateDirectory = "odysseus";
            StateDirectoryMode = "0750";
            Restart = "on-failure";
            RestartSec = "3s";
          };
        };

        systemd.services.odysseus = {
          description = "Odysseus AI assistant";
          after = [
            "network.target"
            "odysseus-chroma.service"
          ];
          wants = [ "odysseus-chroma.service" ];
          wantedBy = [ "multi-user.target" ];

          # Tools the app shells out to at runtime (see mkServiceTools).
          # llama-server is added only when services.odysseus.llamaCpp is on.
          path = mkServiceTools pkgs ++ lib.optional cfg.llamaCpp.enable cfg.llamaCpp.package ++ runtimeLibs;

          environment = {
            PYTHONUNBUFFERED = "1";
            # Route constants.py's DATA_DIR to the mutable state directory.
            ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
            # Connect to the bundled ChromaDB server (odysseus-chroma.service).
            CHROMADB_HOST = "127.0.0.1";
            CHROMADB_PORT = toString cfg.chromaPort;
          }
          // optionalAttrs cfg.searxng.enable {
            # Connect to the bundled SearXNG instance (services.searx).
            SEARXNG_INSTANCE = "http://127.0.0.1:${toString cfg.searxng.port}";
          }
          // cfg.extraEnvironmentVariables;

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

  darwinModule =
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

        extraPythonPackages = mkOption {
          type = with lib.types; functionTo (listOf package);
          default = ps: [ ];
          example = lib.literalExpression "ps: [ ps.hf-transfer ps.rembg ps.diffusers ]";
          description = ''
            Extra Python packages merged into the app environment, in
            withPackages form (ps: [ ps.hf-transfer ps.rembg ]). Lets the app
            import deps the Cookbook would otherwise pip-install, which fails
            on the read-only Nix store. Setting this rebuilds the bundled
            package; ignored if `package` is set explicitly.
          '';
        };

        package = mkOption {
          type = types.package;
          default = mkOdysseusPackage pkgs src cfg.extraPythonPackages;
          defaultText = lib.literalExpression "odysseus built with config.services.odysseus.extraPythonPackages";
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

        chromaPort = mkOption {
          type = types.port;
          default = 8100;
          description = ''
            Port for the bundled ChromaDB vector database server. Bound to
            loopback only; the app connects to it over HTTP.
          '';
        };

        searxng = {
          enable = mkEnableOption "bundled SearXNG metasearch (web search / deep research)";
          port = mkOption {
            type = types.port;
            default = 8888;
            description = ''
              Port for the bundled SearXNG instance. Bound to loopback only; the
              app reaches it via SEARXNG_INSTANCE.
            '';
          };
          secretKey = mkOption {
            type = types.str;
            default = "change-me-before-exposing-to-the-network";
            description = ''
              SearXNG secret_key (CSRF / session signing). MUST be changed from the
              default before enabling. Note: this value lands in the world-readable
              Nix store — for a hardened setup, set it out-of-band instead.
            '';
          };
        };

        extraEnvironmentVariables = mkOption {
          type = with lib.types; attrsOf str;
          default = { };
          example = {
            SEARXNG_GENERAL_ENGINES = "bing,mojeek";
            LLM_HOST = "http://10.0.0.5:11434";
          };
          description = ''
            Extra environment variables for the app service. Merged last, so they
            override the module's own derived values (SEARXNG_INSTANCE, etc.) — the
            escape hatch for app settings without a dedicated option.
          '';
        };

        llamaCpp = {
          enable = mkEnableOption "bundling llama.cpp (llama-server) for Cookbook GGUF serving";
          package = mkOption {
            type = types.package;
            default = pkgs.llama-cpp;
            example = lib.literalExpression "pkgs.llama-cpp-rocm";
            description = ''
              llama.cpp build providing `llama-server`, put on the service
              PATH so the Cookbook detects llama.cpp and the serve fallback
              has a real binary (no runtime cmake build). Override for a GPU
              backend, e.g. pkgs.llama-cpp-rocm, pkgs.llama-cpp-vulkan, or
              pkgs.llama-cpp.override { cudaSupport = true; }.
            '';
          };
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

        assertions = [
          {
            assertion =
              !cfg.searxng.enable || cfg.searxng.secretKey != "change-me-before-exposing-to-the-network";
            message = "services.odysseus.searxng.secretKey must be changed from its default before enabling SearXNG.";
          }
        ];

        # Bundled SearXNG metasearch. launchd has no inter-daemon ordering, but
        # the app reaches it lazily via SEARXNG_INSTANCE.
        launchd.daemons.odysseus-searxng = mkIf cfg.searxng.enable {
          command =
            let
              settings = pkgs.writeText "searxng-settings.yml" ''
                use_default_settings: true
                server:
                  port: ${toString cfg.searxng.port}
                  bind_address: "127.0.0.1"
                  secret_key: "${cfg.searxng.secretKey}"
                  limiter: false
                search:
                  formats:
                    - html
                    - json
              '';
            in
            ''
              #!/bin/sh
              mkdir -p "${cfg.dataDir}/logs"
              export SEARXNG_SETTINGS_PATH=${settings}
              exec ${pkgs.searxng}/bin/searxng-run
            '';

          serviceConfig = {
            KeepAlive = true;
            RunAtLoad = true;
            StandardOutPath = "${cfg.dataDir}/logs/searxng.out.log";
            StandardErrorPath = "${cfg.dataDir}/logs/searxng.err.log";
          };
        };

        # ChromaDB vector database server. The app talks to it over HTTP, so
        # it must be running for RAG / vector memory to work. launchd has no
        # ordering between daemons, but the app retries the connection lazily.
        launchd.daemons.odysseus-chroma = {
          command =
            let
              data = "${cfg.dataDir}/data";
            in
            ''
              #!/bin/sh
              mkdir -p "${data}/chroma" "${cfg.dataDir}/logs"
              exec ${cfg.package}/bin/odysseus-chroma run \
                --path "${data}/chroma" \
                --host 127.0.0.1 \
                --port ${toString cfg.chromaPort}
            '';

          serviceConfig = {
            KeepAlive = true;
            RunAtLoad = true;
            StandardOutPath = "${cfg.dataDir}/logs/chroma.out.log";
            StandardErrorPath = "${cfg.dataDir}/logs/chroma.err.log";
            EnvironmentVariables = {
              PYTHONUNBUFFERED = "1";
              LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
            };
          };
        };

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
            # CWD must be the data dir so the app's relative "data/..." paths
            # (e.g. the SQLite DB) resolve there instead of "/". Without this
            # the server fails to open its database and the daemon crash-loops.
            WorkingDirectory = cfg.dataDir;
            StandardOutPath = "${cfg.dataDir}/logs/launchd.out.log";
            StandardErrorPath = "${cfg.dataDir}/logs/launchd.err.log";
            EnvironmentVariables = {
              PYTHONUNBUFFERED = "1";
              ODYSSEUS_DATA_DIR = "${cfg.dataDir}/data";
              LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
              # launchd daemons get a bare PATH, so the app's shutil.which
              # probes (tmux, npx, git, …) would all read as missing. Put the
              # service tools up front, then the standard system paths.
              # llama-server is included only when llamaCpp is enabled.
              PATH = "${
                pkgs.lib.makeBinPath (mkServiceTools pkgs ++ lib.optional cfg.llamaCpp.enable cfg.llamaCpp.package)
              }:/usr/bin:/bin:/usr/sbin:/sbin";
              # Connect to the bundled ChromaDB server (odysseus-chroma daemon).
              CHROMADB_HOST = "127.0.0.1";
              CHROMADB_PORT = toString cfg.chromaPort;
            }
            // optionalAttrs cfg.searxng.enable {
              # Connect to the bundled SearXNG daemon (odysseus-searxng).
              SEARXNG_INSTANCE = "http://127.0.0.1:${toString cfg.searxng.port}";
            }
            // cfg.extraEnvironmentVariables;
          };
        };

        environment.systemPackages = mkServiceTools pkgs ++ runtimeLibs;
      };
    };
}
