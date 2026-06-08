# Odysseus package builders.
#
# Exposes:
#   mkRuntimeLibs pkgs                 -> shared C/C++ libs pip wheels load at runtime
#   mkContainer   pkgs odysseusPackage -> a layered OCI image
#
# The main odysseus package is in nix/odysseus.nix (use with pkgs.callPackage).
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
    mkContainer
    ;
}
