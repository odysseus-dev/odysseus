# NixOS service module for Odysseus.
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
  options.services.odysseus = import ./options.nix { inherit src config lib pkgs; } // {
    openFirewall = lib.mkEnableOption "opening the firewall for the Odysseus app port";
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
      path = mkServiceTools ++ lib.optional cfg.llamaCpp.enable cfg.llamaCpp.package ++ runtimeLibs;

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

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];
  };
}
