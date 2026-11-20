"""Windows-only controlled Uvicorn entrypoint and external restart coordinator."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if os.name != 'nt':
        raise RuntimeError('This control infrastructure is Windows-only')
    from src.windows_app_restart import serve, request_control, restart, rebaseline
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'status', 'stop', 'restart', 'rebaseline'])
    parser.add_argument('--control-dir', required=True)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--port', type=int, default=7000)
    parser.add_argument('--gateway-port', type=int, default=9130)
    parser.add_argument('--chroma-port', type=int, default=8100)
    parser.add_argument('--state-dir', default=str(ROOT / 'data'))
    parser.add_argument('--gateway-db', default=str(Path(os.environ['LOCALAPPDATA']) / 'Odysseus/hermes/state/hermes-gateway/state.db'))
    parser.add_argument('--workers-dir', default=None,
                        help='worker receipt inventory (default: <state-dir>/worker_receipts, durable)')
    parser.add_argument('--launcher-pid', type=int)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--lab-application', default='')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        parser.error('timeout must be finite, positive and at most 3600 seconds')
    if args.action == 'run':
        if not args.launcher_pid:
            parser.error('run requires --launcher-pid')
        config = vars(args).copy()
        for key in ('action', 'timeout'):
            config.pop(key)
        config['root'] = str(ROOT)
        if config.get('workers_dir') is None:
            config['workers_dir'] = str(Path(config['state_dir']) / 'worker_receipts')
        for key in ('control_dir', 'state_dir', 'workers_dir', 'gateway_db', 'python'):
            config[key] = str(Path(config[key]).resolve())
        asyncio.run(serve(config))
        return 0
    try:
        if args.action == 'status':
            value = request_control(args.control_dir, 'status', args.timeout)
        elif args.action == 'rebaseline':
            # Explicit operator recovery after a health-verified shared-service
            # restart; never invoked automatically by stop/restart.
            value = rebaseline(args.control_dir, timeout=args.timeout)
        else:
            value = restart(args.control_dir, timeout=args.timeout, start=args.action == 'restart')
    except Exception as exc:
        value = {'ok': False, 'state': 'blocked', 'error': str(exc), 'forced': False}
    print(json.dumps(value, indent=2))
    return 0 if value.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
