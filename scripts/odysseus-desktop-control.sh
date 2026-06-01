#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="${ROOT_DIR}/venv/bin"
STATE_DIR="${HOME}/Library/Application Support/OdysseusDesktop"
PID_DIR="${STATE_DIR}/run"
LOG_DIR="${STATE_DIR}/logs"

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-7001}"
CHROMA_HOST="${CHROMA_HOST:-127.0.0.1}"
CHROMA_PORT="${CHROMA_PORT:-8100}"
CHROMA_PATH="${CHROMA_PATH:-${ROOT_DIR}/data/chroma}"

CHROMA_PID_FILE="${PID_DIR}/chroma.pid"
APP_PID_FILE="${PID_DIR}/odysseus.pid"

mkdir -p "${PID_DIR}" "${LOG_DIR}"

usage() {
  echo "Usage: $0 {start|stop|restart|status|open}"
}

is_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

read_pid() {
  local file="$1"
  if [[ -f "${file}" ]]; then
    cat "${file}"
  fi
}

wait_for_http() {
  local host="$1"
  local port="$2"
  local timeout="${3:-40}"
  local i=0
  while (( i < timeout )); do
    if curl -sS -o /dev/null --max-time 1 "http://${host}:${port}/login"; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

require_env() {
  if [[ ! -x "${VENV_BIN}/python" ]]; then
    echo "Missing virtual environment at ${ROOT_DIR}/venv"
    echo "Run setup first."
    exit 1
  fi
  if [[ ! -x "${VENV_BIN}/uvicorn" ]]; then
    echo "Missing uvicorn in venv. Install dependencies first."
    exit 1
  fi
  if [[ ! -x "${VENV_BIN}/chroma" ]]; then
    echo "Missing chroma CLI in venv."
    echo "Install it with: source venv/bin/activate && pip install chromadb"
    exit 1
  fi
}

start_chroma() {
  local pid
  pid="$(read_pid "${CHROMA_PID_FILE}")"
  if is_running "${pid}"; then
    echo "ChromaDB already running (pid ${pid})"
    return
  fi

  echo "Starting ChromaDB on ${CHROMA_HOST}:${CHROMA_PORT} ..."
  (
    cd "${ROOT_DIR}"
    nohup "${VENV_BIN}/chroma" run \
      --host "${CHROMA_HOST}" \
      --port "${CHROMA_PORT}" \
      --path "${CHROMA_PATH}" \
      >> "${LOG_DIR}/chroma.log" 2>&1 &
    echo $! > "${CHROMA_PID_FILE}"
  )
}

start_app() {
  local pid
  pid="$(read_pid "${APP_PID_FILE}")"
  if is_running "${pid}"; then
    echo "Odysseus already running (pid ${pid})"
    return
  fi

  echo "Starting Odysseus on ${APP_HOST}:${APP_PORT} ..."
  (
    cd "${ROOT_DIR}"
    CHROMADB_HOST="${CHROMA_HOST}" CHROMADB_PORT="${CHROMA_PORT}" \
      nohup "${VENV_BIN}/uvicorn" app:app \
      --host "${APP_HOST}" \
      --port "${APP_PORT}" \
      >> "${LOG_DIR}/odysseus.log" 2>&1 &
    echo $! > "${APP_PID_FILE}"
  )
}

do_start() {
  require_env
  start_chroma
  start_app

  if wait_for_http "${APP_HOST}" "${APP_PORT}" 60; then
    echo "Odysseus is up: http://${APP_HOST}:${APP_PORT}"
  else
    echo "Odysseus did not become ready in time."
    echo "Check logs:"
    echo "  ${LOG_DIR}/odysseus.log"
    echo "  ${LOG_DIR}/chroma.log"
    exit 1
  fi
}

do_stop() {
  local app_pid chroma_pid
  app_pid="$(read_pid "${APP_PID_FILE}")"
  chroma_pid="$(read_pid "${CHROMA_PID_FILE}")"

  if is_running "${app_pid}"; then
    echo "Stopping Odysseus (pid ${app_pid})"
    kill "${app_pid}" || true
  fi
  if is_running "${chroma_pid}"; then
    echo "Stopping ChromaDB (pid ${chroma_pid})"
    kill "${chroma_pid}" || true
  fi

  rm -f "${APP_PID_FILE}" "${CHROMA_PID_FILE}"
}

do_status() {
  local app_pid chroma_pid
  app_pid="$(read_pid "${APP_PID_FILE}")"
  chroma_pid="$(read_pid "${CHROMA_PID_FILE}")"

  if is_running "${app_pid}"; then
    echo "Odysseus: running (pid ${app_pid}) http://${APP_HOST}:${APP_PORT}"
  else
    echo "Odysseus: stopped"
  fi

  if is_running "${chroma_pid}"; then
    echo "ChromaDB: running (pid ${chroma_pid}) ${CHROMA_HOST}:${CHROMA_PORT}"
  else
    echo "ChromaDB: stopped"
  fi
}

do_open() {
  open "http://${APP_HOST}:${APP_PORT}"
}

cmd="${1:-}"
case "${cmd}" in
  start)
    do_start
    ;;
  stop)
    do_stop
    ;;
  restart)
    do_stop
    do_start
    ;;
  status)
    do_status
    ;;
  open)
    do_open
    ;;
  *)
    usage
    exit 2
    ;;
esac
