# Odysseus package builders.
#
# Exposes:
#   mkOdysseusPackage pkgs src extraPyPkgs -> the bundled app derivation
#   mkContainer       pkgs odysseusPackage -> a layered OCI image of it
let
  inherit (import ../overlays/python.nix) mkPythonEnv;
in
{
  # The odysseus package. Bundles the Python env (+ any extras) and wraps the
  # uvicorn / setup / chroma entrypoints. `src` is the repo root (passed by the
  # flake so the path resolves correctly from this nested module file).
  mkOdysseusPackage =
    pkgs: src: extraPythonPackages:
    let
      pythonEnv = mkPythonEnv pkgs extraPythonPackages;
    in
    pkgs.stdenv.mkDerivation {
      pname = "odysseus";
      version = "0.9.1";
      src = pkgs.lib.cleanSource src;

      nativeBuildInputs = [ pkgs.makeWrapper ];

      dontBuild = true;
      dontConfigure = true;

      installPhase = ''
        mkdir -p $out/share/odysseus
        cp -r . $out/share/odysseus/

        mkdir -p $out/bin
        makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/odysseus \
          --set PYTHONUNBUFFERED "1" \
          --set PYTHONPATH "$out/share/odysseus" \
          --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
          --add-flags "app:app"

        makeWrapper ${pythonEnv}/bin/python $out/bin/odysseus-setup \
          --set PYTHONPATH "$out/share/odysseus" \
          --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
          --add-flags "$out/share/odysseus/setup.py"

        # ChromaDB server CLI (from chromadb in pythonEnv) so the service
        # modules can run the vector DB the app connects to over HTTP.
        makeWrapper ${pythonEnv}/bin/chroma $out/bin/odysseus-chroma
      '';
    };

  mkContainer =
    pkgs: odysseusPackage:
    pkgs.dockerTools.buildLayeredImage {
      name = "odysseus";
      tag = "latest";
      contents = [ odysseusPackage ];
      config = {
        Entrypoint = [ "${odysseusPackage}/bin/odysseus" ];
        Env = [
          "ODYSSEUS_DATA_DIR=/var/lib/odysseus/data"
          "PYTHONUNBUFFERED=1"
        ];
        ExposedPorts = {
          "7000/tcp" = { };
        };
        WorkingDir = "/var/lib/odysseus";
      };
      extraCommands = ''
        mkdir -p var/lib/odysseus/data
      '';
    };
}
