{
  description = "Odysseus AI Workspace Environment";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.05";
  };

  outputs = { self, nixpkgs }:
  let
    # the targetSystem should only be changed if you are on a different OS
    # Like MacOS or Windows (for Windows WSL2 must be enabled and the default target system is correct)
    targetSystem = "x86_64-linux";
# MacOS (silicon/M-series)
  # targetSystem = aarch64-darwin;
# MacOS (intel)
  # targetSystem = x86_64-darwin;
# Linux (ARM64)
  # targetSystem = aarch64-linux;


    pkgs = import nixpkgs { system = targetSystem; };
  in
  {

    devShells.${targetSystem}.default = pkgs.mkShell {
      name = "odysseus-dev-env";

      buildInputs = [
        (pkgs.python312.withPackages (ps: with ps; [
        fastapi uvicorn python-multipart python-dotenv httpx
        pydantic pydantic-settings sqlalchemy pypdf
        beautifulsoup4 charset-normalizer numpy
        chromadb fastembed youtube-transcript-api markdown
        icalendar python-dateutil caldav cryptography
        bcrypt mcp pyotp qrcode pytest pytest-asyncio
        ]))
        pkgs.git 
        pkgs.cmake 
        pkgs.nodejs 
        pkgs.tmux
        pkgs.openssh
        pkgs.gosu
        pkgs.curl
        pkgs.gcc
        pkgs.pkg-config
      ];
      

      env = {
        APP_PORT = "7000";
      };

      shellHook = ''
      export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc pkgs.zlib ]}:$LD_LIBRARY_PATH"
      echo "Odysseus nix shell is loaded."
    '';
    };
  };
}
