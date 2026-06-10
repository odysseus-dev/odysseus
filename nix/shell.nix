# The Odysseus development shell
#
# flake.nix references this for devShells.default (passing its pinned pkgs);
# a bare `nix-shell nix/shell.nix` falls back to your <nixpkgs> channel.
{
  pkgs ? import <nixpkgs> { },
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
      # System tools required for building and running the application
      git
      cmake
      nodejs
      tmux
      openssh
      curl
      gcc
      pkg-config
      gnumake
      odysseus
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
    CHROMA_DATA_DIR = "./data/chroma";
    # Built-in "Browser" MCP server (`npx @playwright/mcp`) needs browsers.
    # Point Playwright at the nixpkgs-provided, pre-patched browsers so it
    # doesn't try to download unpatched binaries that won't run on Nix.
    PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
  };

  # A bash script that executes automatically when a user runs `nix develop`
  shellHook = ''
    # Fixes dynamic linking issues for Python libraries relying on C/C++ dependencies
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

    SESSION_NAME="odysseus"

    echo "Odysseus Nix shell for ${pkgs.system} is loaded."

    # 1. First-time Setup Check
    # If the database directory doesn't exist, we assume this is a fresh clone.
    if [ ! -d "$ODYSSEUS_DATA_DIR" ]; then
        echo "First configuration detected. Everything is being set-up!"

        # Execute the initial setup to generate the admin account
        ${lib.getExe' odysseus "odysseus-setup"}
        echo "-----------------------------------------------------"
        echo "Make sure you remember your admin username and temporary password!"
    else
        echo "Setup has already been executed..."
    fi

    echo ""
    echo "How to run Odysseus:"
    echo ""
    echo "  Keep it running after you close the terminal (detached tmux):"
    echo "    tmux new-session -d -s $SESSION_NAME 'chroma run --path ./data/chroma --host 0.0.0.0 --port 8100' \\; split-window -h 'odysseus' \\; attach"
    echo "    tmux attach -t $SESSION_NAME                     # reattach later"
    echo "    tmux kill-session -t $SESSION_NAME               # stop everything"
    echo ""
    echo "  Or run the pieces manually in separate shells:"
    echo "    chroma run --path ./data/chroma --host 0.0.0.0 --port 8100"
    echo "    odysseus"
  '';
}
