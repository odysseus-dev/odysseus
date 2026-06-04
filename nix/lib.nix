# Odysseus package builders.
#
# Exposes:
#   mkRuntimeLibs     pkgs                 -> shared C/C++ libs pip wheels load at runtime
#   mkPythonEnv       pkgs extraPyPkgs     -> the app's Python environment
#   mkOdysseusPackage pkgs src extraPyPkgs -> the bundled app derivation
#   mkContainer       pkgs odysseusPackage -> a layered OCI image of it
let
  # Shared libs needed by pip-installed native wheels (onnxruntime / fastembed).
  mkRuntimeLibs =
    pkgs: with pkgs; [
      stdenv.cc.cc.lib # libstdc++.so.6, libgomp.so.1 (onnxruntime / fastembed)
      zlib
      openssl
      libffi
      bzip2
      xz
      sqlite
      ncurses
      readline
    ];

  # The app's Python environment. `extraPythonPackages` is a withPackages-style
  # function (ps: [ ... ]) merged into the base set, so consumers can add deps
  # the Cookbook would otherwise pip-install — which fails on the read-only Nix
  # store. Defaults to no extras.
  mkPythonEnv =
    pkgs: extraPythonPackages:
    (pkgs.python3.override {
      packageOverrides = pyself: pysuper: {
        niquests = pysuper.niquests.overridePythonAttrs (old: {
          doCheck = !pkgs.stdenv.isDarwin;
        });
        # caldav's test suite fails on Darwin (notably under nixos-unstable,
        # which consumers pull in via inputs.nixpkgs.follows). Skip its
        # checks there; they pass on Linux / the pinned nixpkgs.
        caldav = pysuper.caldav.overridePythonAttrs (old: {
          doCheck = !pkgs.stdenv.isDarwin;
          doInstallCheck = !pkgs.stdenv.isDarwin;
        });
      };
    }).withPackages
      (
        ps:
        (with ps; [
          fastapi
          uvicorn
          python-multipart
          python-dotenv
          httpx
          pydantic
          pydantic-settings
          sqlalchemy
          pypdf
          beautifulsoup4
          charset-normalizer
          numpy
          chromadb
          fastembed
          youtube-transcript-api
          markdown
          icalendar
          python-dateutil
          caldav
          cryptography
          bcrypt
          mcp
          pyotp
          qrcode
          pillow
          croniter
          python-magic
          pytest
          pytest-asyncio
        ])
        ++ extraPythonPackages ps
      );

  # The odysseus package. Bundles the Python env (+ any extras) and wraps the
  # uvicorn / setup / chroma entrypoints. `src` is the repo root (passed by the
  # flake so the path resolves correctly from nested module files).
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
in
{
  inherit
    mkRuntimeLibs
    mkPythonEnv
    mkOdysseusPackage
    mkContainer
    ;
}
