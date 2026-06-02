{
  description = "Odysseus – local AI workspace with GPU support (NixOS module). bro you wanted this, here it is.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        pythonWithRembg = pkgs.python3.withPackages (ps: [
          ps.rembg
          ps.onnxruntime
          ps.pillow
        ]);
      in
      {
        devShell = pkgs.mkShell {
          buildInputs = [
            pkgs.podman-compose
            pkgs.podman-tui
            pkgs.realesrgan-ncnn-vulkan
            pkgs.playwright
            pkgs.playwright-driver.browsers
            pythonWithRembg
          ];
          shellHook = ''
            echo "🔧 Odysseus dev environment. type 'podman-compose up' if you're a rebel."
          '';
        };
      }
    )
    // {
      nixosModules.odysseus =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.odysseus;
        in
        {
          options.services.odysseus = {
            enable = lib.mkEnableOption "Odysseus AI workspace (it's pretty cool)";
            dataDir = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/odysseus";
              description = "where all your models and memories live. don't lose it.";
            };
            port = lib.mkOption {
              type = lib.types.port;
              default = 7000;
              description = "web ui port – change if something else uses it";
            };
            llamaServerEndpoint = lib.mkOption {
              type = lib.types.str;
              default = "http://10.89.0.1:11434";
              description = "where your llama.cpp server is. for podman bridge it's 10.89.0.1. adjust if needed.";
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [
              pkgs.podman-compose
              pkgs.realesrgan-ncnn-vulkan
              pkgs.playwright
              pkgs.playwright-driver.browsers
              (pkgs.python3.withPackages (ps: [
                ps.rembg
                ps.onnxruntime
                ps.pillow
              ]))
            ];

            systemd.tmpfiles.rules = [ "d ${cfg.dataDir} 0755 root root -" ];

            environment.etc."odysseus/docker-compose.yml" = {
              text = ''
                services:
                  odysseus:
                    image: localhost/odysseus_odysseus:latest
                    build: ${self}
                    user: "0:0"
                    entrypoint: [""]
                    command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
                    ports:
                      - "${toString cfg.port}:7000"
                    volumes:
                      - ${cfg.dataDir}/models:/app/models
                      - ${cfg.dataDir}/vectorstore:/app/vectorstore
                      - ${cfg.dataDir}/documents:/app/documents
                      - ${cfg.dataDir}/email:/app/email
                      - ${cfg.dataDir}/cache:/app/cache
                      - /run/current-system/sw/bin/:/host-bin/:ro
                    environment:
                      - PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/host-bin/
                      - OLLAMA_BASE_URL=${cfg.llamaServerEndpoint}
                      - SEARXNG_INSTANCE=http://searxng:8080
                    devices:
                      - nvidia.com/gpu=all
                    restart: unless-stopped

                  chromadb:
                    image: docker.io/chromadb/chroma:latest
                    command: run /config.yaml
                    ports:
                      - "127.0.0.1:8100:8000"
                    volumes:
                      - ${cfg.dataDir}/chroma:/chroma/chroma
                    restart: unless-stopped

                  searxng:
                    image: docker.io/searxng/searxng:latest
                    ports:
                      - "127.0.0.1:8080:8080"
                    dns:
                      - 8.8.8.8
                      - 1.1.1.1
                    restart: unless-stopped

                  ntfy:
                    image: docker.io/binwiederhier/ntfy:latest
                    command: serve
                    ports:
                      - "127.0.0.1:8091:80"
                    restart: unless-stopped
              '';
            };

            systemd.services.odysseus = {
              description = "Odysseus AI workspace";
              after = [
                "network-online.target"
                "podman.service"
              ];
              wants = [ "network-online.target" ];
              serviceConfig = {
                Type = "oneshot";
                RemainAfterExit = true;
                WorkingDirectory = "/etc/odysseus";
                Environment = "PATH=/run/current-system/sw/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin";
                ExecStartPre = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml build";
                ExecStart = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml up -d";
                ExecStop = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml down";
                ExecReload = "${pkgs.podman-compose}/bin/podman-compose -f /etc/odysseus/docker-compose.yml restart";
                Restart = "no";
                TimeoutStartSec = 300;
              };
            };
          };
        };
    };
}
