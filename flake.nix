{
  description = "Odysseus AI Workspace Environment";

  # Defines the source of our packages. Pinned to the 25.05 branch for stability.
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
  let
    # Ensures this development environment works across Linux and macOS (Intel & Apple Silicon)
    supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    forAllSystems = f: nixpkgs.lib.genAttrs supportedSystems (system: f system);
  in
  {
    devShells = forAllSystems (system: {
      default = 
        let 
          pkgs = import nixpkgs { inherit system; };
        in 
        pkgs.mkShell {
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
          ];

          # Environment variables automatically injected into the shell
          env = {
            APP_HOST = "0.0.0.0";
            APP_PORT = "7000";
            PYTHONPATH = ".";
            CHROMA_DATA_DIR = "./data/chroma";
          };

          # A bash script that executes automatically when a user runs `nix develop`
          shellHook = ''
            # Fixes dynamic linking issues for Python libraries relying on C/C++ dependencies
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc pkgs.zlib ]}:$LD_LIBRARY_PATH"
            
            SESSION_NAME="odysseus"
            
            echo "Odysseus Nix shell for ${system} is loaded."
            
            # 1. First-time Setup Check
            # If the database directory doesn't exist, we assume this is a fresh clone.
            if [ ! -d "$CHROMA_DATA_DIR" ]; then
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
    });
  };
}
