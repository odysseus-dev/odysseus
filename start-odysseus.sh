#!/usr/bin/env bash
#
# start-odysseus.sh
# -----------------
# Arranca Odysseus sin Docker en una sesión tmux con dos paneles:
#   - Panel izquierdo:  servidor ChromaDB nativo (puerto 8100)
#   - Panel derecho:    servidor de la app (uvicorn, puerto 7000)
#
# Diseñado para WSL/Linux con un venv en ./venv. Seguro de relanzar:
# detecta si la sesión o los puertos ya están en uso y no duplica procesos.
#
# Uso:
#   ./start-odysseus.sh            # arranca y se conecta a la sesión tmux
#   ./start-odysseus.sh --no-attach  # arranca en segundo plano sin conectar
#   ./start-odysseus.sh --stop       # detiene la sesión y sus procesos
#   ./start-odysseus.sh --status     # muestra el estado de puertos y sesión

set -euo pipefail

# --- Configuración (ajustable por variable de entorno) ---------------------
PROJECT_DIR="${ODYSSEUS_DIR:-$HOME/projects/odysseus}"
VENV_DIR="${PROJECT_DIR}/venv"
SESSION="${ODYSSEUS_TMUX_SESSION:-odysseus}"

CHROMA_HOST="${CHROMADB_HOST:-127.0.0.1}"
CHROMA_PORT="${CHROMADB_PORT:-8100}"
CHROMA_PATH="${CHROMADB_PATH:-${PROJECT_DIR}/data/chroma}"

APP_HOST="${ODYSSEUS_HOST:-127.0.0.1}"
APP_PORT="${ODYSSEUS_PORT:-7000}"

# --- Utilidades ------------------------------------------------------------
log()  { printf '\033[1;36m[odysseus]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }

# Devuelve 0 si el puerto está ocupado (escuchando).
port_in_use() {
  local port="$1"
  # Intenta varios métodos según lo disponible en el sistema.
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" 2>/dev/null | grep -q LISTEN
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    # Fallback con bash: intenta abrir una conexión.
    (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1 && { exec 3>&- 3<&-; return 0; } || return 1
  fi
}

# --- Subcomandos -----------------------------------------------------------
cmd_status() {
  log "Estado de Odysseus"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    ok "Sesión tmux '$SESSION' activa."
  else
    echo "  Sesión tmux '$SESSION': no existe."
  fi
  port_in_use "$CHROMA_PORT" && ok "ChromaDB escuchando en $CHROMA_HOST:$CHROMA_PORT." \
                             || echo "  ChromaDB ($CHROMA_PORT): no escucha."
  port_in_use "$APP_PORT"    && ok "App escuchando en http://$APP_HOST:$APP_PORT" \
                             || echo "  App ($APP_PORT): no escucha."
}

cmd_stop() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "Deteniendo sesión tmux '$SESSION' (esto cierra ChromaDB y uvicorn)..."
    tmux kill-session -t "$SESSION"
    ok "Sesión detenida."
  else
    log "No hay sesión '$SESSION' que detener."
  fi
}

cmd_start() {
  local attach="$1"

  # 1) Validaciones de entorno -------------------------------------------
  [ -d "$PROJECT_DIR" ] || { err "No existe el directorio del proyecto: $PROJECT_DIR"; exit 1; }
  [ -x "$VENV_DIR/bin/python" ] || { err "No encuentro el venv en $VENV_DIR. Créalo antes de arrancar."; exit 1; }
  command -v tmux >/dev/null 2>&1 || { err "tmux no está instalado (sudo apt install tmux)."; exit 1; }

  # 2) Si la sesión ya existe, no duplicar -------------------------------
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "La sesión '$SESSION' ya está activa. Conéctate con: tmux attach -t $SESSION"
    cmd_status
    [ "$attach" = "yes" ] && exec tmux attach -t "$SESSION"
    exit 0
  fi

  mkdir -p "$CHROMA_PATH"

  # 3) Construir los comandos de cada panel ------------------------------
  # ChromaDB: solo arranca si el puerto está libre; si ya escucha, informa.
  local chroma_cmd
  chroma_cmd="cd '$PROJECT_DIR'; \
    if '$0' __port_in_use $CHROMA_PORT; then \
      echo '[chromadb] El puerto $CHROMA_PORT ya está en uso; reutilizando instancia existente.'; \
    else \
      echo '[chromadb] Arrancando ChromaDB nativo en $CHROMA_HOST:$CHROMA_PORT...'; \
      exec '$VENV_DIR/bin/chroma' run --host '$CHROMA_HOST' --port '$CHROMA_PORT' --path '$CHROMA_PATH'; \
    fi; \
    exec bash"

  # App: espera a que el puerto de Chroma responda antes de arrancar uvicorn.
  local app_cmd
  app_cmd="cd '$PROJECT_DIR'; \
    echo '[app] Esperando a ChromaDB en $CHROMA_HOST:$CHROMA_PORT...'; \
    for i in \$(seq 1 30); do \
      if '$0' __port_in_use $CHROMA_PORT; then echo '[app] ChromaDB disponible.'; break; fi; \
      sleep 1; \
    done; \
    echo '[app] Arrancando uvicorn en http://$APP_HOST:$APP_PORT ...'; \
    exec '$VENV_DIR/bin/python' -m uvicorn app:app --host '$APP_HOST' --port '$APP_PORT'; \
    exec bash"

  # 4) Crear la sesión tmux con dos paneles ------------------------------
  log "Creando sesión tmux '$SESSION'..."
  tmux new-session -d -s "$SESSION" -n odysseus "$chroma_cmd"
  tmux split-window -h -t "$SESSION":0 "$app_cmd"
  tmux select-layout -t "$SESSION":0 even-horizontal
  tmux select-pane -t "$SESSION":0.1

  ok "Sesión lista."
  log "ChromaDB: panel izquierdo  |  App: panel derecho  →  http://$APP_HOST:$APP_PORT"
  log "Para salir de tmux sin cerrar los procesos: Ctrl+b, luego d (detach)."
  log "Para detener todo:  $0 --stop"

  [ "$attach" = "yes" ] && exec tmux attach -t "$SESSION"
}

# --- Punto de entrada ------------------------------------------------------
case "${1:-}" in
  __port_in_use)  port_in_use "$2" && exit 0 || exit 1 ;;  # helper interno
  --stop)         cmd_stop ;;
  --status)       cmd_status ;;
  --no-attach)    cmd_start "no" ;;
  ""|--attach)    cmd_start "yes" ;;
  -h|--help)
    grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//' | sed -n '1,20p'
    ;;
  *)
    err "Opción no reconocida: $1"
    echo "Usa: $0 [--no-attach | --stop | --status | --help]"
    exit 1
    ;;
esac
