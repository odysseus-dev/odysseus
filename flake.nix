{
  description = "Odysseus AI Workspace Environment";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.05";
  };

  outputs = { self, nixpkgs }:
  let
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

          buildInputs = with pkgs; [
            (python312.withPackages (ps: with ps; [
              fastapi uvicorn python-multipart python-dotenv httpx
              pydantic pydantic-settings sqlalchemy pypdf
              beautifulsoup4 charset-normalizer numpy
              chromadb fastembed youtube-transcript-api markdown
              icalendar python-dateutil caldav cryptography
              bcrypt mcp pyotp qrcode pytest pytest-asyncio
            ]))
            
            git cmake nodejs tmux openssh gosu curl gcc pkg-config process-compose
          ];

          env = {
            APP_HOST = "0.0.0.0";
            APP_PORT = "7000";
            PYTHONPATH = ".";
            CHROMA_DATA_DIR = "./data/chroma";
          };

          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc pkgs.zlib ]}:$LD_LIBRARY_PATH"
            
            SESSION_NAME="odysseus"
            
            echo "Odysseus Nix shell for ${system} is loaded."
            
            # Control if setup.py has already been executed previously
            if [ ! -d "$CHROMA_DATA_DIR" ]; then
                echo "First configuration detected. Everything is being set-up!"
                
                # Execute setup
                python setup.py
                echo "-----------------------------------------------------"
                echo "Make sure you remember your admin username and temporary password!"
                read -p "Press enter to continue to the dashboard!" < /dev/tty
            else
                echo "Setup has already been executed... Starting application"
            fi
            
            # Tmux automation: start or attach session
            if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
              tmux attach -t "$SESSION_NAME"
            else
              tmux new -s "$SESSION_NAME" "process-compose up"
            fi

            # Quit shell when tmux is quit
            exit
          '';
        };
    });
  };
}
