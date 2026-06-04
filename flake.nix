{
  description = "Odysseus AI Workspace Environment";

  # Defines the source of our packages. Pinned to the 26.05 branch for stability.
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Lets shell.nix / default.nix reuse the flake's devShell for non-flake
    # `nix-shell` users. Source only — not imported as a flake.
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  # Nix code is split out of this file (cf. the native-nix effort in issue #605):
  #   nix/lib.nix                       — mkRuntimeLibs / mkPythonEnv /
  #                                       mkOdysseusPackage / mkContainer
  #   nix/modules/services/odysseus.nix — the NixOS & nix-darwin service modules
  #                                       (Chroma, SearXNG, firewall, app)
  #   nix/modules/checks/integration.nix — the integration tests
  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      nix-darwin,
      ...
    }:
    let
      inherit (import ./nix/lib.nix)
        mkOdysseusPackage
        mkContainer
        mkRuntimeLibs
        mkPythonEnv
        ;
      odysseusModules = import ./nix/modules/services/odysseus.nix { src = ./.; };
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Shared libs needed by pip-installed native wheels.
        runtimeLibs = mkRuntimeLibs pkgs;
        # Default app Python environment (no extra packages).
        pythonEnv = mkPythonEnv pkgs (ps: [ ]);
        # Default app package (no extra packages); ./. is the flake root.
        odysseus = mkOdysseusPackage pkgs ./. (ps: [ ]);
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

            echo "Odysseus Nix shell for ${system} is loaded."

            # 1. First-time Setup Check
            # If the database directory doesn't exist, we assume this is a fresh clone.
            if [ ! -d "$ODYSSEUS_DATA_DIR" ]; then
                echo "First configuration detected. Everything is being set-up!"

                # Execute the initial setup to generate the admin account
                ${odysseus}/bin/odysseus-setup
                echo "-----------------------------------------------------"
                echo "Make sure you remember your admin username and temporary password!"
            else
                echo "Setup has already been executed..."
            fi

            echo ""
            echo "How to run Odysseus:"
            echo ""
            echo "  Recommended - start the whole stack (ChromaDB + app) together:"
            echo "    process-compose up          # foreground, Ctrl-C to stop"
            echo "    process-compose up -D       # detached; 'process-compose down' to stop"
            echo "                                # 'process-compose attach' to view logs/TUI"
            echo ""
            echo "  Keep it running after you close the terminal (detached tmux):"
            echo "    tmux new -s $SESSION_NAME 'process-compose up'   # start in background"
            echo "    tmux attach -t $SESSION_NAME                     # reattach later"
            echo "    tmux kill-session -t $SESSION_NAME               # stop everything"
            echo ""
            echo "  Or run the pieces manually in separate shells:"
            echo "    chroma run --path ./data/chroma --host 0.0.0.0 --port 8100"
            echo "    odysseus"
          '';
        };

        packages = {
          default = odysseus;
          container = mkContainer pkgs odysseus;
        };
      }
    )
    // {
      nixosModules.default = odysseusModules.nixosModule;
      darwinModules.default = odysseusModules.darwinModule;
      checks = import ./nix/modules/checks/integration.nix {
        inherit
          self
          nixpkgs
          nix-darwin
          mkRuntimeLibs
          ;
      };
    };
}
