# The Odysseus development shell
#
# flake.nix references this for devShells.default (passing its pinned pkgs);
# a bare `nix-shell nix/shell.nix` falls back to your <nixpkgs> channel.
# The Darwin test-skip overlay is applied in both paths so the interpreter the
# shell sees matches the one the flake package uses.
{
  pkgs ? import <nixpkgs> { overlays = [ (import ./overlay.nix) ]; },
  extraInputs ? [ ],
}:
let
  inherit (import ./lib.nix) mkRuntimeLibs;
  inherit (pkgs) lib;
  # Shared libs needed by pip-installed native wheels.
  runtimeLibs = mkRuntimeLibs pkgs;
  # Default app package (../. is the repo root from this file).
  # Uses callPackage to wire up dependencies automatically.
  odysseus = pkgs.callPackage ./odysseus.nix {
    src = ../.;
    extraPythonPackages = _ps: [ ];
  };
in
pkgs.mkShell {
  name = "odysseus-dev-env";

  # Dependencies that will be available in the environment.
  # These are completely isolated from the host operating system.
  inputsFrom = [ odysseus ];

  buildInputs =
    with pkgs;
    [
      # System tools required for building and running the application.
      # cmake is needed because Cookbook builds llama.cpp / llama-cpp-python
      # from source (`cmake -B build ...`) when no native llama-server is found;
      # in the isolated Nix shell it's only on PATH if we list it here.
      git
      cmake
      nodejs
      tmux
      openssh
      curl
      pkg-config
      odysseus
      # The full Python interpreter (python, uvicorn, pip, chroma) so the
      # documented manual workflow (`uvicorn app:app ...`) works directly.
      # PYTHONPATH="." below makes the working tree the source of truth, so
      # this is the editable dev environment (cf. PR #2567 review feedback).
      odysseus.passthru.pythonEnv
    ]
    ++ lib.optionals pkgs.stdenv.isLinux [
      gosu
    ]
    ++ runtimeLibs
    ++ extraInputs;

  # Environment variables automatically injected into the shell
  env = {
    APP_HOST = "0.0.0.0";
    APP_PORT = "7000";
    PYTHONPATH = ".";
    ODYSSEUS_DATA_DIR = "./data";
    CHROMA_DATA_DIR = "./data/chroma";
    # Built-in "Browser" MCP server (`npx @playwright/mcp`) needs browsers.
    # Point Playwright at the nixpkgs-provided, pre-patched browsers so it
    # doesn't try to download unpatched binaries that won't run on Nix.
    PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
  };

  shellHook = ''
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

    echo "Odysseus Nix shell for ${pkgs.stdenv.hostPlatform.system} is loaded."
    echo ""
    echo "Available commands:"
    echo "  odysseus-setup-wrapper  - Run first-time setup (create admin user)"
    echo "  odysseus-run-tmux       - Start Chroma + Odysseus in a tmux session"
    echo "  odysseus                - Start the app directly"
    echo "  odysseus-chroma run     - Start ChromaDB only"
    echo ""
    echo "Quick start:"
    echo "  1. Run 'odysseus-setup wrapper' if this is a fresh clone"
    echo "  2. Run 'odysseus-run-tmux' or start services manually"
  '';
}
