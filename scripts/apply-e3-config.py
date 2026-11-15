#!/usr/bin/env python3
"""Stage the reviewed Hermes + SearXNG configuration for the E3/E5 release. Dry run unless --apply.

Run it with the Hermes runtime interpreter (it needs ruamel.yaml, which keeps comments and order) while the gateway is stopped or about
to be restarted; nothing here restarts anything. Every file it would change is copied to --backup-dir first, with SHA-256 digests, and
the run is idempotent. It never prints .env values.

Changes (all decided by the user, see docs/p0-config-and-search-assessment.md):
  Hermes config.yaml   plugins.enabled: remove 'rea' (the plugin folder stays), add 'odysseus-local-extract'
                       mcp_servers.alyn.sampling.enabled: false
                       memory.write_approval / skills.write_approval: true
                       checkpoints: enabled, 10 snapshots, 200 MB, 7 days
                       web.search_backend: searxng, web.extract_backend: odysseus-local-extract,
                       web.keyless_fallback / web.keyless_rescue: false (no silent fall back to remote services)
  Hermes .env          SEARXNG_URL=http://127.0.0.1:8888 (added only if absent)
  Hermes plugins/      odysseus-local-extract (copied from this checkout; an existing different copy is refused)
  SearXNG settings.yml engines: google cse weight 1.5, brave weight 0.6 (DuckDuckGo stays disabled: it CAPTCHAs)
"""
import argparse
import hashlib
import io
import os
import shutil
import sys
import time
from pathlib import Path

from ruamel.yaml import YAML

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOME = Path(os.environ.get('HERMES_HOME') or Path(os.environ.get('LOCALAPPDATA', '')) / 'Odysseus' / 'hermes' / 'state' / 'hermes-gateway')
DEFAULT_SEARX = Path.home() / 'Apps' / 'searxng-windows' / 'config' / 'settings.yml'
PLUGIN = 'odysseus-local-extract'
SEARX_URL = 'http://127.0.0.1:8888'

# ruamel's default 'rt' (round-trip) loader builds only plain mappings, lists and scalars; it does not construct arbitrary Python objects.
yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    with open(path, encoding='utf-8') as handle:
        return yaml.load(handle)


def dump(data):
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def ensure(mapping, key):
    if mapping.get(key) is None:
        mapping[key] = {}
    return mapping[key]


def plan_hermes(config):
    changes = []

    def setv(parent, key, value, label):
        if parent.get(key) != value:
            changes.append(f'{label}: {parent.get(key)!r} -> {value!r}')
            parent[key] = value

    plugins = ensure(config, 'plugins')
    enabled = plugins.get('enabled')
    enabled = enabled if enabled is not None else []
    if 'rea' in enabled:
        enabled.remove('rea')
        changes.append("plugins.enabled: remove 'rea' (folder kept; enable it only in a dedicated profile)")
    if PLUGIN not in enabled:
        enabled.append(PLUGIN)
        changes.append(f"plugins.enabled: add '{PLUGIN}'")
    plugins['enabled'] = enabled
    alyn = (config.get('mcp_servers') or {}).get('alyn')
    if alyn is not None:
        sampling = ensure(alyn, 'sampling')
        setv(sampling, 'enabled', False, 'mcp_servers.alyn.sampling.enabled')
    setv(ensure(config, 'memory'), 'write_approval', True, 'memory.write_approval')
    setv(ensure(config, 'skills'), 'write_approval', True, 'skills.write_approval')
    checkpoints = ensure(config, 'checkpoints')
    for key, value in (('enabled', True), ('max_snapshots', 10), ('max_total_size_mb', 200), ('retention_days', 7), ('auto_prune', True)):
        setv(checkpoints, key, value, f'checkpoints.{key}')
    web = ensure(config, 'web')
    for key, value in (('search_backend', 'searxng'), ('extract_backend', PLUGIN), ('keyless_fallback', False), ('keyless_rescue', False)):
        setv(web, key, value, f'web.{key}')
    return changes


def plan_searx(config):
    changes = []
    engines = config.get('engines')
    if engines is None:
        engines = []
        config['engines'] = engines
    for name, weight in (('google cse', 1.5), ('brave', 0.6)):
        entry = next((item for item in engines if isinstance(item, dict) and item.get('name') == name), None)
        if entry is None:
            engines.append({'name': name, 'weight': weight})
            changes.append(f'engine {name}: weight {weight}')
        elif entry.get('weight') != weight:
            changes.append(f'engine {name}: weight {entry.get("weight")!r} -> {weight}')
            entry['weight'] = weight
    return changes


def env_has(path, name):
    if not path.exists():
        return False
    return any(line.split('=', 1)[0].strip() == name for line in path.read_text(encoding='utf-8').splitlines() if '=' in line and not line.lstrip().startswith('#'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--home', type=Path, default=DEFAULT_HOME)
    parser.add_argument('--searx-settings', type=Path, default=DEFAULT_SEARX)
    parser.add_argument('--backup-dir', type=Path, default=Path('config-backups'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)

    home, searx = args.home, args.searx_settings
    config_path, env_path = home / 'config.yaml', home / '.env'
    plugin_src = APP_ROOT / 'integrations/hermes-mode/plugins' / PLUGIN
    plugin_dst = home / 'plugins' / PLUGIN
    for path in (config_path, searx, plugin_src):
        if not path.exists():
            sys.exit(f'missing: {path}')

    config = load(config_path)
    searx_config = load(searx)
    hermes_changes = plan_hermes(config)
    searx_changes = plan_searx(searx_config)
    env_change = [] if env_has(env_path, 'SEARXNG_URL') else [f'.env: add SEARXNG_URL={SEARX_URL}']
    plugin_change = []
    if not plugin_dst.exists():
        plugin_change = [f'plugins/{PLUGIN}: copy']
    else:
        differs = [p.name for p in plugin_src.glob('*') if p.is_file() and (not (plugin_dst / p.name).exists() or digest(p) != digest(plugin_dst / p.name))]
        if differs:
            sys.exit(f'plugins/{PLUGIN} already exists and differs ({", ".join(differs)}); not overwriting it')

    for title, items in (('Hermes config.yaml', hermes_changes), ('Hermes .env', env_change), ('Hermes plugin', plugin_change), ('SearXNG settings.yml', searx_changes)):
        print(f'[{title}]')
        print('\n'.join(f'  {item}' for item in items) if items else '  already as wanted')
    if not args.apply:
        print('\nDry run. Nothing was written. Re-run with --apply (gateway restart and SearXNG restart are yours).')
        return 0

    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = args.backup_dir / f'e3-config-{stamp}'
    backup.mkdir(parents=True)
    lines = []
    for label, path in (('config.yaml', config_path), ('env', env_path), ('searxng-settings.yml', searx)):
        if path.exists():
            shutil.copy2(path, backup / label)
            lines.append(f'{label}  {digest(path)}  {path}')
    (backup / 'MANIFEST.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\nBacked up to {backup}')

    if hermes_changes:
        config_path.write_text(dump(config), encoding='utf-8')
    if env_change:
        with open(env_path, 'a', encoding='utf-8') as handle:
            handle.write(f'\nSEARXNG_URL={SEARX_URL}\n')
    if plugin_change:
        shutil.copytree(plugin_src, plugin_dst, ignore=shutil.ignore_patterns('__pycache__'))
    if searx_changes:
        searx.write_text(dump(searx_config), encoding='utf-8')

    check = load(config_path)
    assert check['web']['search_backend'] == 'searxng' and check['web']['extract_backend'] == PLUGIN
    assert check['memory']['write_approval'] is True and check['skills']['write_approval'] is True
    assert 'rea' not in check['plugins']['enabled'] and PLUGIN in check['plugins']['enabled']
    load(searx)
    print('Applied and re-read cleanly. Restart the Hermes gateway and SearXNG to load it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
