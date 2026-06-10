# Shared options for NixOS and nix-darwin Odysseus service modules.
{ src, config, lib, pkgs }:
let
  cfg = config.services.odysseus;
  inherit (lib) mkEnableOption mkOption types;
in
{
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
    default = pkgs.callPackage ../../odysseus.nix {
      inherit src;
      extraPythonPackages = cfg.extraPythonPackages;
    };
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
}
