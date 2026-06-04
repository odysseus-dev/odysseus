# Python overlay helpers for Odysseus.
#
# Exposes:
#   mkRuntimeLibs pkgs              -> shared C/C++ libs pip wheels load at runtime
#   mkPythonEnv   pkgs extraPyPkgs  -> the app's Python environment
{
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
}
