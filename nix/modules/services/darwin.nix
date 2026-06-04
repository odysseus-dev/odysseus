# nix-darwin service module for Odysseus.
{ src }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.odysseus;
  inherit (import ../../lib.nix) mkRuntimeLibs;
  runtimeLibs = mkRuntimeLibs pkgs;
  # Tools the app shells out to / probes with shutil.which at runtime
  # (Cookbook background jobs, the Browser MCP via npx, remote server probes).
  mkServiceTools = with pkgs; [
    bash
    nodejs
    tmux
    openssh
    curl
    git
  ];
  inherit (lib) mkIf mkOption types optionalAttrs;
in
{
  options.services.odysseus = import ./options.nix { inherit src config lib pkgs; };

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
            pkgs.lib.makeBinPath (mkServiceTools ++ lib.optional cfg.llamaCpp.enable cfg.llamaCpp.package)
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

    environment.systemPackages = mkServiceTools ++ runtimeLibs;
  };
}
