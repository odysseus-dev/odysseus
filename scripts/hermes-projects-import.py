"""Bring legacy WebUI projects into the Hermes Webapp's project store. Dry run unless --apply. See src/hermes_projects_import.py for what it never does.

    python scripts/hermes-projects-import.py [--root COMPONENT_ROOT] [--webui-state DIR]                     dry run: reads projects.db read-only, needs no token
    python scripts/hermes-projects-import.py --apply --backup-dir DIR [--root ...] [--webui-state ...]       backs up projects.db first, then projects.create only
"""
import argparse
import os
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src import hermes_projects_import as importer
from src.runtime_paths import get_hermes_component_root
from src.hermes_webapp_component import WebappComponent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=None, help='Hermes component root (default: the Odysseus one)')
    parser.add_argument('--webui-state', default=None, help='legacy WebUI state directory (default: %%LOCALAPPDATA%%\\hermes\\webui)')
    parser.add_argument('--use-folder', action='append', default=[], metavar='NAME=PATH', help="current folder for a legacy project whose recorded folder is gone; its old folders are attached as extra folders")
    parser.add_argument('--apply', action='store_true', help='create the projects (default: only print the plan)')
    parser.add_argument('--backup-dir', default=None, help='where projects.db is copied before anything is created (required with --apply)')
    args = parser.parse_args(argv)
    if args.apply and not args.backup_dir:
        parser.error('--apply needs --backup-dir')
    root = Path(args.root) if args.root else get_hermes_component_root()
    local = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    webui = args.webui_state or os.environ.get('ODYSSEUS_WEBUI_STATE') or str(Path(local) / 'hermes' / 'webui')
    config = WebappComponent(root, application_root=APP_ROOT).config()
    overrides = dict(item.split('=', 1) for item in args.use_folder if '=' in item)
    legacy = importer.legacy_projects(webui, overrides)
    actions = importer.plan(legacy, importer.existing_from_db(config['home']))
    print(f"legacy registry: {webui}\nHermes project store: {Path(config['home']) / 'projects.db'}")
    for a in actions:
        folder = a['folder'] or '-'
        print(f"{a['action']:6} {a['name']:<16} {a['colour'] or '-':<8} {folder}  [{a['conversations']} conversation(s) in that folder]" + (f"\n         why: {a['reason']}" if a['reason'] else '') + (f"\n         attaches historical folder(s): {', '.join(a['attach_history'])}" if a.get('attach_history') else ''))
    todo = [a for a in actions if a['action'] == 'create']
    if not args.apply:
        print(f'Dry run: nothing was changed. {len(todo)} project(s) would be created; no conversation, working folder or write grant is touched.')
        return 0
    if not todo:
        print('Nothing to create.')
        return 0
    saved = importer.backup_db(config['home'], args.backup_dir)
    print(f'projects.db backed up to: {saved}' if saved else 'no projects.db yet; nothing to back up')
    rpc = importer.GatewayRpc(config['url'], importer.token_for(config['home']))
    results = importer.execute(actions, rpc)
    for name, outcome in results:
        print(f'{name}: {outcome}')
    return 0 if all(o.startswith('created') for _, o in results) else 1


if __name__ == '__main__':
    sys.exit(main())
