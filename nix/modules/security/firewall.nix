# Firewall for the Odysseus app port (NixOS only; macOS has no port firewall
# managed via nix-darwin). Imported by the NixOS service module.
#
# Off by default: the app binds 0.0.0.0 but isn't reachable from the network
# unless you opt in. ChromaDB and SearXNG stay loopback-only regardless.
{ config, lib, ... }:
let
  cfg = config.services.odysseus;
in
{
  options.services.odysseus.openFirewall = lib.mkEnableOption "opening the firewall for the Odysseus app port";

  config = lib.mkIf (cfg.enable && cfg.openFirewall) {
    networking.firewall.allowedTCPPorts = [ cfg.port ];
  };
}
