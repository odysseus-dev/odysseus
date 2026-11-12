"""Wiki Steward: the single controlled write path to a private, Git-backed knowledge wiki.

Local code only (stdlib + git). No LLM runs here: searching, indexing, evidence checks, deduplication, conflict and
freshness detection, reconciliation are mechanical. A reasoning model can only *supply new evidence* that this module
re-verifies; it can never promote a claim by assertion.

Evidence grades (a claim is VERIFIED automatically only at one of the first three):
  behavior-tested      an allow-listed test, defined by a reviewer in steward.json, was run by the steward at HEAD, passed,
                       and is bound to the claim's subject
  source-confirmed     scope=source: a file at HEAD contains the quoted text and the claim names that file (or a commit that
                       touched a named file and is named in the claim). Never for a general claim.
  externally-verified  scope=external: the quote is present on at least N distinct allow-listed https hosts
  attested             anything else: an agent said so. A reachable commit alone proves nothing about an arbitrary claim.
  stale                derived: the watched source changed (or the external check expired) after verification
"""
import contextlib
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

STEWARD = 'wiki-steward'
KINDS = ('fact', 'decision', 'security', 'policy', 'architecture')
SCOPES = ('general', 'source', 'external')
LIVE = ('verified', 'provisional', 'needs-review', 'stale', 'conflict')
CAPS = ('search', 'propose', 'verify', 'record_task', 'handoff', 'status', 'conflicts', 'inbox', 'ack')
LOCK_STALE_SECONDS = 60
MAX_PROVISIONAL_PER_AGENT = 100
OPEN_TASK_MINUTES = 240

SENSITIVE_TEXT = re.compile(r'\b(secur|permission|authori|authenti|credential|secret|password|token|approval|sandbox|grant|revoke|polic)\w*', re.I)
SECRET_PATTERNS = [re.compile(p) for p in (
    r'(?i)bearer\s+[a-z0-9._\-]{20,}', r'\bsk-[A-Za-z0-9_\-]{20,}', r'\bghp_[A-Za-z0-9]{20,}',
    r'(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{8,}', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    r'ticket=[A-Za-z0-9_\-]{30,}')]
BLOCKED_PATH = re.compile(r'(^|/)(\.env[^/]*|auth\.json|id_rsa[^/]*|\.credentials\.json|[^/]*\.(pem|key|pfx)|vault[^/]*|\.ssh/.*|\.aws/.*)$', re.I)
RESERVED_NAMES = re.compile(r'(^|/)(con|prn|aux|nul|com\d|lpt\d)(\.|/|$)', re.I)
STOP = set('a an and are as at be by for from in is it of on or that the this to was with'.split())
NEGATIONS = {'not', 'no', 'never', 'fails', 'failing', 'failed', 'broken', 'cannot', 'without', 'unfixed', 'isnt', 'doesnt', 'wont'}


class WikiError(Exception):
    pass


class Denied(WikiError):
    pass


class IntegrityError(WikiError):
    pass


def _git_exe():
    return os.environ.get('WIKI_GIT') or shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'


def git(repo, *args, check=True, read_only=False, binary=False):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never', 'GIT_CONFIG_GLOBAL': os.devnull}
    if read_only:
        env['GIT_OPTIONAL_LOCKS'] = '0'
    kw = {} if binary else {'text': True, 'encoding': 'utf-8', 'errors': 'replace'}
    r = subprocess.run([_git_exe(), '-c', 'core.autocrlf=false', '-c', 'core.quotepath=false', '-C', str(repo), *args],
                       capture_output=True, env=env, timeout=120, **kw)
    if check and r.returncode:
        raise WikiError('git %s failed: %s' % (args[0], (r.stderr.decode('utf-8', 'replace') if binary else r.stderr).strip()[:300]))
    return r


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def _tok(text):
    return [w for w in re.findall(r'[a-z0-9_]+', (text or '').lower()) if w not in STOP]


def _norm(text):
    return ' '.join(_tok(text))


def _jaccard(a, b):
    a, b = set(_tok(a)), set(_tok(b))
    return len(a & b) / len(a | b) if a | b else 0.0


def _negated(text):
    return bool(set(_tok((text or '').replace("n't", 'nt'))) & NEGATIONS)


def _scan(*texts):
    for text in texts:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text or ''):
                raise WikiError('refused: the text looks like it contains a credential (nothing was stored)')


def _clean_rel(path):
    path = str(path or '').replace('\\', '/').strip()
    while path.startswith('./'):
        path = path[2:]
    return path


def _safe_rel(path):
    return (bool(path) and not os.path.isabs(path) and ':' not in path and '..' not in path.split('/') and not path.startswith('/')
            and not BLOCKED_PATH.search(path) and not RESERVED_NAMES.search(path) and '\x00' not in path)


def _dump(path, meta, body=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '---\n' + json.dumps(meta, indent=1, sort_keys=True, ensure_ascii=False) + '\n---\n' + body + '\n'
    tmp = path.with_name('%s.tmp%d' % (path.name, os.getpid()))
    tmp.write_text(text, encoding='utf-8', newline='\n')
    os.replace(tmp, path)


def _load(path):
    return json.loads(path.read_text(encoding='utf-8').split('---\n', 2)[1])


def _ekey(ev):
    return json.dumps(ev, sort_keys=True, ensure_ascii=False)


def _default_fetch(url, host, timeout=10):
    """https only, allow-listed host, no redirects, public addresses only, size-capped."""
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise WikiError('host resolves to a non-public address')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'wiki-steward/0.2'}), timeout=timeout) as r:
        return r.read(1_000_000).decode('utf-8', 'replace')


class Wiki:
    def __init__(self, root, fetcher=None):
        self.root = Path(root).resolve()
        self.fetcher = fetcher or _default_fetch

    # -- setup ------------------------------------------------------------------------------------------------
    @classmethod
    def init(cls, root, projects=None, agents=None, reviewers=('user',), tests=None, external=None):
        root = Path(root)
        if (root / '.git').exists():
            raise WikiError('already initialised')
        root.mkdir(parents=True, exist_ok=True)
        wiki = cls(root)
        git(root, 'init', '-q', '-b', 'main')
        (root / '.gitignore').write_text('.steward/\n*.tmp*\n', encoding='utf-8', newline='\n')
        (root / 'README.md').write_text('# Odysseus Wiki\n\nWritten only through the Wiki Steward (`src/wiki_steward.py`). '
                                        'Do not edit by hand; a reviewer uses `wiki adopt` for an intentional manual change.\n',
                                        encoding='utf-8', newline='\n')
        cfg = {'version': 2, 'projects': projects or {}, 'agents': agents or {}, 'reviewers': list(reviewers),
               'tests': tests or {}, 'external': external or {'hosts': [], 'min_sources': 2, 'ttl_days': 30}}
        (root / 'steward.json').write_text(json.dumps(cfg, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        (root / 'audit').mkdir()
        (root / 'audit' / 'audit.jsonl').write_text('', encoding='utf-8')
        wiki._audit('init', 'init', str(root), None)
        wiki._commit('init', 'init')
        return wiki

    @property
    def config(self):
        try:
            return json.loads((self.root / 'steward.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise WikiError('wiki configuration is unreadable (%s); refusing every operation' % type(exc).__name__)

    def _agent(self, agent, cap, project=None):
        spec = (self.config.get('agents') or {}).get(agent)
        if not spec or cap not in spec.get('can', []):
            raise Denied('%s may not %s' % (agent, cap))
        if project is not None and '*' not in spec.get('projects', []) and project not in spec.get('projects', []):
            raise Denied('%s has no access to project %s' % (agent, project))

    def _repo(self, project):
        spec = (self.config.get('projects') or {}).get(project)
        if not spec:
            raise Denied('unknown project %s' % project)
        return spec['repo']

    def _reviewer(self, by):
        if by not in (self.config.get('reviewers') or []):
            raise Denied('%s is not a reviewer' % by)

    # -- transaction, audit, integrity -------------------------------------------------------------------------
    def _lock(self):
        lock = self.root / '.steward' / 'lock'
        lock.parent.mkdir(exist_ok=True)
        deadline = time.time() + 30
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ('%d %f' % (os.getpid(), time.time())).encode())
                os.close(fd)
                return
            except FileExistsError:
                with contextlib.suppress(OSError):
                    if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                        lock.unlink()
                        continue
                if time.time() > deadline:
                    raise WikiError('wiki is busy')
                time.sleep(0.03)

    def _unlock(self):
        with contextlib.suppress(OSError):
            (self.root / '.steward' / 'lock').unlink()

    def _dirty(self):
        return [l for l in git(self.root, 'status', '--porcelain', read_only=True).stdout.splitlines() if l.strip()]

    def _audit(self, actor, action, target, detail):
        path = self.root / 'audit' / 'audit.jsonl'
        lines = [l for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
        prev = json.loads(lines[-1])['hash'] if lines else '0' * 64
        body = {'ts': now(), 'actor': actor, 'action': action, 'target': target, 'detail': detail, 'prev': prev}
        body['hash'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(body, sort_keys=True) + '\n')

    def _commit(self, action, actor):
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'user.name=' + STEWARD, '-c', 'user.email=wiki-steward@localhost', '-c', 'commit.gpgsign=false',
            'commit', '-q', '-m', '%s\n\nWiki-Steward: %s\nWiki-Actor: %s' % (action, action.split()[0], actor))

    @contextlib.contextmanager
    def _txn(self, actor, action, target=''):
        self._lock()
        try:
            dirty = self._dirty()
            if dirty:
                raise IntegrityError('out-of-band changes in the wiki; writes are blocked until reviewed: %s' % '; '.join(dirty[:5]))
            ctx = {'detail': None}
            try:
                yield ctx
                self._audit(actor, action, target, ctx['detail'])
                self._commit('%s %s' % (action, target), actor)
            except BaseException:
                git(self.root, 'reset', '-q', '--hard', 'HEAD', check=False)
                git(self.root, 'clean', '-fdq', check=False)
                raise
        finally:
            self._unlock()

    def integrity(self):
        problems = ['uncommitted change: ' + l for l in self._dirty()]
        out = git(self.root, 'log', '--format=%H%x1f%an%x1f%(trailers:key=Wiki-Steward,valueonly)%x1e', read_only=True).stdout
        for rec in filter(None, (r.strip() for r in out.split('\x1e'))):
            sha, author, trailer = (rec.split('\x1f') + ['', ''])[:3]
            if author != STEWARD or not trailer.strip():
                problems.append('commit %s was not made by the steward' % sha[:10])
        prev = '0' * 64
        for n, line in enumerate((self.root / 'audit' / 'audit.jsonl').read_text(encoding='utf-8').splitlines(), 1):
            body = json.loads(line)
            digest = body.pop('hash')
            if body['prev'] != prev or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != digest:
                problems.append('audit chain broken at line %d' % n)
                break
            prev = digest
        return problems

    def adopt(self, by, note):
        """Reviewer-only: accept an intentional manual edit (for example steward.json) into the audited history."""
        self._reviewer(by)
        self._lock()
        try:
            self._audit(by, 'adopt', note, self._dirty())
            self._commit('adopt manual change', by)
        finally:
            self._unlock()

    def revert(self, by, commit):
        """Reviewer-only: undo one steward commit. Refused if a later commit touched the same files."""
        self._reviewer(by)
        sha = git(self.root, 'rev-parse', '--verify', commit + '^{commit}').stdout.strip()
        changes = [l.split('\t') for l in git(self.root, 'diff', '--name-status', sha + '^', sha).stdout.splitlines()]
        paths = [c[-1] for c in changes if not c[-1].startswith('audit/')]
        if not paths:
            raise WikiError('nothing to revert')
        if git(self.root, 'log', '--format=%H', '%s..HEAD' % sha, '--', *paths).stdout.strip():
            raise WikiError('a later change touched the same files; revert that one first')
        with self._txn(by, 'revert', sha[:10]):
            for c in changes:
                if c[-1].startswith('audit/'):
                    continue
                if c[0] == 'A':
                    git(self.root, 'rm', '-q', '-f', c[-1])
                else:
                    git(self.root, 'checkout', sha + '^', '--', c[-1])

    # -- storage ----------------------------------------------------------------------------------------------------
    def _claims(self, project=None):
        base = self.root / 'claims'
        if not base.exists():
            return []
        out = []
        for d in ([base / project] if project else [d for d in base.iterdir() if d.is_dir()]):
            for p in sorted(d.glob('*.md')) if d.exists() else []:
                with contextlib.suppress(OSError, ValueError, IndexError):
                    out.append(_load(p))
        return out

    def _save_claim(self, rec):
        _dump(self.root / 'claims' / rec['project'] / (rec['id'] + '.md'), rec, rec['claim'])

    def _get(self, claim_id):
        for p in (self.root / 'claims').glob('*/%s.md' % re.sub(r'[^a-z0-9\-]', '', str(claim_id))):
            return _load(p)
        raise WikiError('unknown claim %s' % claim_id)

    @staticmethod
    def _oid(repo, spec):
        if spec.endswith(':'):
            spec = 'HEAD^{tree}'
        r = git(repo, 'rev-parse', '--verify', '--quiet', spec, check=False, read_only=True)
        return r.stdout.strip() if r.returncode == 0 else None

    # -- evidence ---------------------------------------------------------------------------------------------------
    def _run_test(self, repo, test_id, tests):
        spec = tests.get(test_id)
        if not spec:
            return {'type': 'test', 'mechanical': True, 'ok': False, 'test': str(test_id)[:60], 'why': 'no such test is defined by a reviewer'}
        head = self._oid(repo, 'HEAD')
        if not head:
            return {'type': 'test', 'mechanical': True, 'ok': False, 'test': test_id, 'why': 'repository has no HEAD'}
        scratch = Path(tempfile.mkdtemp(prefix='wiki-test-'))
        try:
            raw = git(repo, 'archive', '--format=tar', 'HEAD', binary=True).stdout
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                for m in tar.getmembers():
                    target = (scratch / m.name).resolve()
                    if not str(target).startswith(str(scratch.resolve())) or m.issym() or m.islnk():
                        continue
                    tar.extract(m, scratch)
            env = {k: v for k, v in os.environ.items() if k.upper() in ('PATH', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'WINDIR')}
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='', CI='1')
            started = time.time()
            try:
                r = subprocess.run(list(spec['argv']), cwd=str(scratch / spec.get('cwd', '.')), capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', env=env, timeout=int(spec.get('timeout', 300)))
                code, tail = r.returncode, (r.stdout + r.stderr)[-1500:]
            except subprocess.TimeoutExpired:
                code, tail = -1, 'timed out'
            if any(p.search(tail) for p in SECRET_PATTERNS):
                tail = '[output withheld: it looked like it contained a credential]'
            return {'type': 'test', 'mechanical': True, 'ok': code == 0, 'test': test_id, 'commit': head, 'exit': code,
                    'seconds': round(time.time() - started, 1), 'tail': tail, 'subjects': list(spec.get('subjects', [])),
                    'watch': list(spec.get('watch', [])), 'why': '' if code == 0 else 'test failed (exit %s)' % code}
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _fetch_external(self, ev, cfg):
        url, quote = str(ev.get('url', '')), str(ev.get('quote', ''))
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme != 'https' or host not in [h.lower() for h in cfg.get('hosts', [])] or len(quote) < 12:
            return {'type': 'external', 'mechanical': True, 'ok': False, 'host': host, 'why': 'url not https, host not allow-listed, or quote too short'}
        try:
            text = self.fetcher(url, host)
        except Exception as exc:  # noqa: BLE001
            return {'type': 'external', 'mechanical': True, 'ok': False, 'host': host, 'why': 'fetch failed: %s' % str(exc)[:100]}
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text))
        ok = re.sub(r'\s+', ' ', quote).strip() in plain
        return {'type': 'external', 'mechanical': True, 'ok': ok, 'host': host, 'url': url, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'fetched': now(), 'why': '' if ok else 'quote not found at that address'}

    def _precheck(self, project, evidence):
        """The slow evidence (test runs, network) is gathered BEFORE the wiki lock is taken."""
        pre, repo, cfg = {}, self._repo(project), self.config
        for ev in evidence:
            if ev.get('type') == 'test':
                pre[_ekey(ev)] = self._run_test(repo, ev.get('id'), {k: v for k, v in cfg.get('tests', {}).items()
                                                                       if v.get('project') == project})
            elif ev.get('type') == 'external':
                pre[_ekey(ev)] = self._fetch_external(ev, cfg.get('external', {}))
        return pre

    def _check_evidence(self, repo, ev, pre):
        kind = ev.get('type')
        if kind in ('test', 'external'):
            return pre.get(_ekey(ev)) or {'type': kind, 'mechanical': True, 'ok': False, 'why': 'not run'}
        if kind == 'commit':
            ref = str(ev.get('ref', ''))
            if not re.fullmatch(r'[0-9a-f]{7,40}', ref):
                return {'type': kind, 'mechanical': True, 'ok': False, 'why': 'bad commit reference'}
            full = self._oid(repo, ref + '^{commit}')
            if not full:
                return {'type': kind, 'mechanical': True, 'ok': False, 'ref': ref, 'why': 'unknown commit'}
            reachable = git(repo, 'merge-base', '--is-ancestor', full, 'HEAD', check=False, read_only=True).returncode == 0
            out = {'type': kind, 'mechanical': True, 'ok': reachable, 'ref': full, 'why': '' if reachable else 'not reachable from HEAD'}
            touches = _clean_rel(ev.get('touches')) if ev.get('touches') else None
            if touches:
                names = git(repo, 'show', '--name-only', '--pretty=format:', full, check=False, read_only=True).stdout.splitlines()
                out.update(touches=touches, touches_ok=_safe_rel(touches) and touches in names)
            return out
        if kind == 'file':
            path = _clean_rel(ev.get('path'))
            if not _safe_rel(path):
                return {'type': kind, 'mechanical': True, 'ok': False, 'path': path, 'why': 'path not allowed'}
            r = git(repo, 'show', 'HEAD:' + path, check=False, read_only=True)
            if r.returncode or len(r.stdout) > 1_000_000:
                return {'type': kind, 'mechanical': True, 'ok': False, 'path': path, 'why': 'not found at HEAD or too large'}
            needle = ev.get('contains')
            ok = needle is None or (isinstance(needle, str) and len(needle) >= 4 and needle in r.stdout)
            return {'type': kind, 'mechanical': True, 'ok': ok, 'path': path, 'oid': self._oid(repo, 'HEAD:' + path),
                    'content_bound': bool(needle) and ok, 'why': '' if ok else 'expected text (4+ characters) not in file'}
        return {'type': kind or 'note', 'mechanical': False, 'ok': True}

    def _grade(self, rec, checks):
        ok = [c for c in checks if c['mechanical'] and c['ok']]
        subject = rec.get('subject')
        for c in ok:
            if c['type'] == 'test' and subject and subject in c.get('subjects', []):
                return 'behavior-tested', c
        claim = rec['claim'].lower()
        cfg = self.config.get('external', {})
        ext = [c for c in ok if c['type'] == 'external']
        if rec['scope'] == 'external' and len({c['host'] for c in ext}) >= int(cfg.get('min_sources', 2)):
            return 'externally-verified', ext[0]
        if rec['scope'] == 'source':
            for c in ok:
                if c['type'] == 'file' and c.get('content_bound') and os.path.basename(c['path']).lower() in claim:
                    return 'source-confirmed', c
                if c['type'] == 'commit' and c.get('touches_ok') and c['ref'][:7] in claim and os.path.basename(c['touches']).lower() in claim:
                    return 'source-confirmed', c
        return 'attested', None

    def _watch_for(self, repo, rec, grade, proof):
        paths = list(rec.get('watch_paths') or [])
        if grade == 'source-confirmed':
            paths.append(proof['path'] if proof['type'] == 'file' else proof['touches'])
        elif grade == 'behavior-tested':
            paths += proof.get('watch') or ['']
        out, seen = [], set()
        for p in paths:
            p = _clean_rel(p) if p else ''
            if p not in seen and (p == '' or _safe_rel(p)):
                seen.add(p)
                oid = self._oid(repo, 'HEAD:' + p)
                if oid:
                    out.append({'path': p, 'oid': oid})
        return out

    def _sensitive(self, rec):
        return (rec['kind'] != 'fact' or bool(SENSITIVE_TEXT.search('%s %s' % (rec['claim'], rec.get('subject') or '')))
                or any(SENSITIVE_TEXT.search(w['path']) for w in rec.get('watch', [])))

    def _conflicts_of(self, rec):
        if not rec.get('subject'):
            return []
        return [o['id'] for o in self._claims(rec['project'])
                if o['id'] != rec['id'] and o['status'] in LIVE and o.get('subject') == rec['subject'] and _norm(o['claim']) != _norm(rec['claim'])]

    def _verify(self, rec, pre, extra=(), recheck=False):
        repo = self._repo(rec['project'])
        rec['evidence'] = list(rec['evidence']) + [e for e in extra if e not in rec['evidence']]
        checks = [self._check_evidence(repo, e, pre) for e in rec['evidence']]
        failed = [c for c in checks if c['mechanical'] and not c['ok']]
        grade, proof = self._grade(rec, checks)
        reasons, conflicts = [], self._conflicts_of(rec)
        if conflicts:
            reasons.append('conflict')
        if failed:
            reasons.append('evidence-failed')
        if grade == 'attested':
            reasons.append('evidence-does-not-establish-claim' if any(c['mechanical'] and c['ok'] for c in checks) else 'no-mechanical-evidence')
            if recheck:
                reasons.append('stale-needs-reverification')
        rec['watch'] = self._watch_for(repo, rec, grade, proof) if proof else rec.get('watch', [])
        if self._sensitive(rec):
            reasons.append('sensitive')
        if conflicts:
            status = 'conflict'
        elif recheck and grade == 'attested':
            status = 'stale'
        elif grade == 'attested' or failed:
            status = 'provisional'
        elif 'sensitive' in reasons:
            status = 'needs-review'
        else:
            status = 'verified'
        rec['status'], rec['grade'], rec['conflicts_with'] = status, grade, conflicts
        rec['verification'] = {'method': 'mechanical', 'grade': grade, 'at': now(), 'reasons': reasons, 'checks': checks}
        if status in ('verified', 'needs-review') and grade != 'attested':
            rec['verified_at'] = now()
            rec['verified_commit'] = self._oid(repo, 'HEAD')
            ttl = float(self.config.get('external', {}).get('ttl_days', 30)) * 86400 if grade == 'externally-verified' else None
            rec['expires_at'] = time.time() + ttl if ttl else None
        for oid in conflicts:
            other = self._get(oid)
            if rec['id'] not in other['conflicts_with']:
                other['conflicts_with'].append(rec['id'])
                self._save_claim(other)
        return rec

    # -- claims API ------------------------------------------------------------------------------------------------
    def propose(self, agent, claim, evidence, project, subject=None, kind='fact', watch_paths=None, scope='general'):
        self._agent(agent, 'propose', project)
        self._repo(project)
        claim = (claim or '').strip()
        if not 10 <= len(claim) <= 2000 or kind not in KINDS or scope not in SCOPES or not isinstance(evidence, list) or len(evidence) > 10 \
                or not all(isinstance(e, dict) for e in evidence):
            raise WikiError('invalid proposal')
        _scan(claim, subject or '', json.dumps(evidence), json.dumps(watch_paths or []))
        pre = self._precheck(project, evidence)
        with self._txn(agent, 'propose', project) as ctx:
            pending = sum(1 for c in self._claims(project) if c['status'] == 'provisional' and agent in c['agents'][:1])
            for other in self._claims(project):
                if other['status'] in LIVE and other['status'] != 'conflict' and other.get('subject') == subject and \
                        _negated(other['claim']) == _negated(claim) and \
                        (_norm(other['claim']) == _norm(claim) or _jaccard(other['claim'], claim) >= 0.85):
                    other.setdefault('corroborations', []).append({'agent': agent, 'at': now(), 'evidence': evidence})
                    if agent not in other['agents']:
                        other['agents'].append(agent)
                    self._save_claim(other)
                    ctx['detail'] = 'duplicate of ' + other['id']
                    return {'id': other['id'], 'status': other['status'], 'grade': other['grade'], 'duplicate': True}
            if pending >= MAX_PROVISIONAL_PER_AGENT:
                raise WikiError('too many unverified claims from %s; verify or withdraw some first' % agent)
            rec = {'id': 'c-' + uuid.uuid4().hex[:10], 'project': project, 'claim': claim, 'subject': subject, 'kind': kind, 'scope': scope,
                   'evidence': list(evidence), 'watch_paths': list(watch_paths or []), 'agents': [agent], 'created': now(),
                   'status': 'provisional', 'grade': 'attested', 'verified_at': None, 'verified_commit': None, 'expires_at': None,
                   'watch': [], 'conflicts_with': [], 'corroborations': [], 'verification': None}
            self._verify(rec, pre)
            self._save_claim(rec)
            ctx['detail'] = '%s %s %s' % (rec['id'], rec['status'], rec['grade'])
            return {'id': rec['id'], 'status': rec['status'], 'grade': rec['grade'], 'reasons': rec['verification']['reasons'], 'duplicate': False}

    def verify(self, agent, proposal_id, evidence=None):
        rec = self._get(proposal_id)
        self._agent(agent, 'verify', rec['project'])
        evidence = evidence or []
        if not isinstance(evidence, list) or not all(isinstance(e, dict) for e in evidence):
            raise WikiError('invalid evidence')
        _scan(json.dumps(evidence))
        pre = self._precheck(rec['project'], list(rec['evidence']) + evidence)
        with self._txn(agent, 'verify', rec['id']) as ctx:
            rec = self._get(proposal_id)
            if rec['status'] in ('rejected', 'superseded'):
                raise WikiError('claim is %s' % rec['status'])
            stale = rec['status'] == 'stale' or self._freshness(rec)[0] == 'stale'
            self._verify(rec, pre, evidence, recheck=stale)
            self._save_claim(rec)
            ctx['detail'] = '%s %s %s' % (rec['id'], rec['status'], rec['grade'])
            return {'id': rec['id'], 'status': rec['status'], 'grade': rec['grade'], 'reasons': rec['verification']['reasons']}

    def decide(self, by, claim_id, decision, note=''):
        """accept / reject. Reviewers decide anything (an accepted claim keeps its true grade and is marked as reviewer-accepted).
        The wiki bot may accept only a non-sensitive claim whose grade is not attested and whose every rival is stale."""
        _scan(note)
        with self._txn(by, 'decide', claim_id) as ctx:
            rec = self._get(claim_id)
            reviewer = by in (self.config.get('reviewers') or [])
            if not reviewer:
                self._agent(by, 'verify', rec['project'])
                if by != 'wiki-bot' or decision != 'accept':
                    raise Denied('%s may not decide this claim' % by)
            if rec['status'] not in ('needs-review', 'conflict', 'provisional'):
                raise WikiError('nothing to decide for a %s claim' % rec['status'])
            others = [self._get(i) for i in rec['conflicts_with']]
            if not reviewer:
                if self._sensitive(rec) or rec.get('grade', 'attested') == 'attested' \
                        or any(o['status'] == 'verified' and self._freshness(o)[0] != 'stale' for o in others):
                    raise Denied('review required: sensitive, only attested, or the conflicting claim is still fresh')
            if decision == 'accept':
                repo = self._repo(rec['project'])
                for o in others:
                    o['status'], o['superseded_by'] = 'superseded', rec['id']
                    self._save_claim(o)
                rec.update(status='verified', conflicts_with=[], verified_at=now(), verified_commit=self._oid(repo, 'HEAD'))
                if reviewer:
                    rec['reviewer_accepted'] = by
            elif decision == 'reject':
                rec['status'] = 'rejected'
                for o in others:
                    o['conflicts_with'] = [i for i in o['conflicts_with'] if i != rec['id']]
                    self._save_claim(o)
                rec['conflicts_with'] = []
            else:
                raise WikiError('decision must be accept or reject')
            rec.setdefault('decisions', []).append({'by': by, 'decision': decision, 'note': note, 'at': now()})
            self._save_claim(rec)
            ctx['detail'] = '%s %s' % (decision, rec['id'])
            return {'id': rec['id'], 'status': rec['status'], 'grade': rec['grade']}

    # -- freshness, search, status, conflicts ---------------------------------------------------------------------
    def _freshness(self, rec, cache=None):
        if rec.get('expires_at') and time.time() > rec['expires_at']:
            return 'stale', ['external check expired']
        if not rec.get('watch'):
            return 'unwatched', []
        repo, cache, changed = self._repo(rec['project']), cache if cache is not None else {}, []
        for w in rec['watch']:
            key = (repo, w['path'])
            if key not in cache:
                cache[key] = self._oid(repo, 'HEAD:' + w['path'])
            if cache[key] != w['oid']:
                changed.append(w['path'] or '(whole repository)')
        return ('stale' if changed else 'fresh'), changed

    @staticmethod
    def _note(r, fresh):
        if fresh == 'stale':
            return 'STALE: the evidence changed, re-verify before relying on it'
        if r['status'] != 'verified':
            return 'NOT VERIFIED (%s)' % r['status']
        if r['grade'] == 'attested':
            return 'ACCEPTED BY REVIEWER %s; not independently verified' % r.get('reviewer_accepted', '')
        return None

    def search(self, agent, query, project=None, k=5, include_provisional=False, fresh_only=False):
        self._agent(agent, 'search', project)
        spec = self.config['agents'][agent]
        pool = [r for r in self._claims(project) if '*' in spec['projects'] or r['project'] in spec['projects']]
        wanted = ('verified', 'stale') if not include_provisional else LIVE
        pool = [r for r in pool if r['status'] in wanted]
        docs = [_tok('%s %s %s' % (r['claim'], (r.get('subject') or '') * 2, r['project'])) for r in pool]
        avg = (sum(map(len, docs)) / len(docs)) if docs else 1
        qt, cache, hits = _tok(query), {}, []
        for r, d in zip(pool, docs):
            score = 0.0
            for t in qt:
                tf = d.count(t)
                if tf:
                    df = sum(1 for x in docs if t in x)
                    score += math.log(1 + (len(docs) - df + 0.5) / (df + 0.5)) * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * len(d) / avg))
            if score <= 0:
                continue
            fresh, changed = self._freshness(r, cache)
            if r['status'] == 'stale':
                fresh = 'stale'
            if fresh_only and fresh == 'stale':
                continue
            hits.append({'id': r['id'], 'score': round(score, 3), 'claim': r['claim'], 'project': r['project'], 'subject': r.get('subject'),
                         'status': r['status'], 'grade': 'stale' if fresh == 'stale' else r['grade'], 'freshness': fresh, 'changed': changed,
                         'verified_commit': r['verified_commit'], 'verified_at': r['verified_at'], 'agents': r['agents'],
                         'note': self._note(r, fresh)})
        return sorted(hits, key=lambda h: -h['score'])[:max(1, min(int(k), 25))]

    def conflicts(self, agent, project=None):
        self._agent(agent, 'conflicts', project)
        out = {'contradictions': [], 'possible': [], 'outdated': [], 'needs_review': [], 'unverified': []}
        claims, cache = self._claims(project), {}
        for r in claims:
            fresh = self._freshness(r, cache)
            if r['status'] == 'conflict':
                out['contradictions'].append({'id': r['id'], 'subject': r.get('subject'), 'with': r['conflicts_with'], 'claim': r['claim']})
            elif r['status'] == 'needs-review':
                out['needs_review'].append({'id': r['id'], 'claim': r['claim'], 'reasons': r['verification']['reasons']})
            elif r['status'] == 'provisional':
                out['unverified'].append({'id': r['id'], 'claim': r['claim'], 'reasons': (r['verification'] or {}).get('reasons', [])})
            if r['status'] in ('verified', 'stale') and (fresh[0] == 'stale' or r['status'] == 'stale'):
                out['outdated'].append({'id': r['id'], 'claim': r['claim'], 'changed': fresh[1]})
        live = [r for r in claims if r['status'] in ('verified', 'provisional', 'needs-review')]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if a['project'] == b['project'] and _jaccard(a['claim'], b['claim']) >= 0.5 and _negated(a['claim']) != _negated(b['claim']):
                    out['possible'].append({'a': a['id'], 'b': b['id'], 'note': 'similar claims, one negated; candidate for semantic review'})
        return out

    def recheck(self, agent, claim_id):
        return self.verify(agent, claim_id)

    def status(self, agent, project):
        self._agent(agent, 'status', project)
        claims, counts = self._claims(project), {}
        for r in claims:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        cache = {}
        stale = [r['id'] for r in claims if r['status'] == 'verified' and self._freshness(r, cache)[0] == 'stale'] + \
                [r['id'] for r in claims if r['status'] == 'stale']
        tasks = self._tasks(project)
        ho = [h for h in self._handoffs() if h['project'] == project and h['status'] == 'open']
        return {'project': project, 'claims': counts, 'stale': stale, 'open_conflicts': counts.get('conflict', 0),
                'verified': [{'id': r['id'], 'claim': r['claim'], 'grade': r['grade'], 'verified_commit': r['verified_commit']}
                             for r in claims if r['status'] == 'verified' and r['id'] not in stale],
                'recent_tasks': [{'id': t['id'], 'agent': t['agent'], 'summary': t['summary'], 'status': t['status']} for t in tasks[-5:]],
                'open_handoffs': [h['id'] for h in ho]}

    # -- tasks, handoffs, reconciliation ---------------------------------------------------------------------------
    def _tasks(self, project):
        base = self.root / 'tasks' / project
        out = []
        for p in sorted(base.glob('*.md')) if base.exists() else []:
            with contextlib.suppress(OSError, ValueError, IndexError):
                out.append(_load(p))
        return sorted(out, key=lambda t: t.get('created_ts', 0))

    def _handoffs(self):
        base = self.root / 'handoffs'
        out = []
        for p in sorted(base.glob('*/*.md')) if base.exists() else []:
            with contextlib.suppress(OSError, ValueError, IndexError):
                out.append(_load(p))
        return out

    def _unknown_commits(self, repo, shas):
        known, unknown = [], []
        for s in shas:
            full = self._oid(repo, str(s) + '^{commit}') if re.fullmatch(r'[0-9a-f]{7,40}', str(s)) else None
            (known if full else unknown).append(full or str(s)[:80])
        return known, unknown

    def begin_task(self, agent, project, summary, expect_minutes=OPEN_TASK_MINUTES):
        """Mark the start of work, so an interrupted session can be recognised and recovered by reconcile()."""
        self._agent(agent, 'record_task', project)
        _scan(summary)
        tid = 't-' + uuid.uuid4().hex[:10]
        with self._txn(agent, 'begin_task', project) as ctx:
            _dump(self.root / 'tasks' / project / (tid + '.md'),
                  {'id': tid, 'project': project, 'agent': agent, 'summary': summary[:500], 'status': 'open', 'created': now(),
                   'created_ts': time.time(), 'expires_ts': time.time() + max(1, min(int(expect_minutes), 1440)) * 60,
                   'start_commit': self._oid(self._repo(project), 'HEAD'), 'commits': [], 'unverified_commits': [], 'files': [], 'tests': []}, summary)
            ctx['detail'] = tid
        return {'task_id': tid}

    def record_task(self, agent, project, summary, commits=(), files=(), tests=(), status='done', key=None, task_id=None):
        self._agent(agent, 'record_task', project)
        _scan(summary, json.dumps([list(files), list(tests)]))
        if status not in ('done', 'partial', 'blocked'):
            raise WikiError('status must be done, partial or blocked')
        known, unknown = self._unknown_commits(self._repo(project), list(commits)[:50])
        with self._txn(agent, 'record_task', project) as ctx:
            if task_id:
                path = self.root / 'tasks' / project / (re.sub(r'[^a-z0-9\-]', '', str(task_id)) + '.md')
                if not path.exists():
                    raise WikiError('no such task')
                t = _load(path)
                if t['agent'] != agent:
                    raise Denied('that task belongs to %s' % t['agent'])
                if t['status'] not in ('open', 'interrupted'):
                    ctx['detail'] = 'duplicate ' + t['id']
                    return {'id': t['id'], 'duplicate': True}
                t.update(summary=summary[:500], status=status, completed=now(), commits=known, unverified_commits=unknown,
                         files=list(files)[:50], tests=list(tests)[:50])
                _dump(path, t, summary)
                ctx['detail'] = t['id']
                return {'id': t['id'], 'duplicate': False, 'unverified_commits': unknown}
            key = 't-' + re.sub(r'[^a-z0-9]', '', (key or hashlib.sha1(('%s|%s|%s' % (agent, summary, list(commits))).encode()).hexdigest()[:16]).lower())[:40]
            path = self.root / 'tasks' / project / (key + '.md')
            if path.exists():
                ctx['detail'] = 'duplicate ' + key
                return {'id': key, 'duplicate': True}
            _dump(path, {'id': key, 'project': project, 'agent': agent, 'summary': summary[:500], 'status': status, 'created': now(),
                         'created_ts': time.time(), 'commits': known, 'unverified_commits': unknown, 'files': list(files)[:50],
                         'tests': list(tests)[:50]}, summary)
            ctx['detail'] = key
            return {'id': key, 'duplicate': False, 'unverified_commits': unknown}

    def _unrecorded(self, repo, project):
        cursor = self.root / 'state' / 'cursors' / (project + '.json')
        last = _load(cursor).get('last') if cursor.exists() else None
        head = self._oid(repo, 'HEAD')
        if not head or head == last:
            return last, head, []
        rng = [last + '..HEAD'] if last and self._oid(repo, last) else ['-20']
        raw = git(repo, 'log', '--reverse', '--name-only', '--format=%x1e%H%x1f%an%x1f%s%x1f%(trailers:key=Agent,valueonly,separator=%x2c)',
                  *rng, read_only=True).stdout
        entries = []
        for chunk in filter(None, raw.split('\x1e')):
            lines = chunk.split('\n')
            sha, author, subject, trailer = (lines[0].split('\x1f') + ['', '', ''])[:4]
            entries.append((sha, trailer.strip() or author, subject, [l for l in lines[1:] if l.strip()][:50]))
        return last, head, entries

    def sync(self, agent, project):
        """Record new commits of the project's source repository as routine task logs. Mechanical, idempotent."""
        self._agent(agent, 'record_task', project)
        last, head, entries = self._unrecorded(self._repo(project), project)
        if not head or head == last:
            return {'recorded': 0, 'head': head}
        for e in entries:
            _scan(e[2])
        with self._txn(agent, 'sync', project) as ctx:
            for sha, who, subject, files in entries:
                path = self.root / 'tasks' / project / ('git' + sha[:12] + '.md')
                if not path.exists():
                    _dump(path, {'id': 'git' + sha[:12], 'project': project, 'agent': who, 'summary': subject, 'status': 'committed',
                                 'created': now(), 'created_ts': time.time(), 'commits': [sha], 'unverified_commits': [], 'files': files, 'tests': []}, subject)
            _dump(self.root / 'state' / 'cursors' / (project + '.json'), {'last': head})
            ctx['detail'] = '%d commits' % len(entries)
        return {'recorded': len(entries), 'head': head}

    def handoff(self, agent, to, project, summary, next_steps=(), open_questions=(), refs=None):
        self._agent(agent, 'handoff', project)
        if to not in (self.config.get('agents') or {}):
            raise WikiError('unknown recipient %s' % to)
        _scan(summary, json.dumps([list(next_steps), list(open_questions), refs or {}]))
        return self._write_handoff(agent, to, project, summary, list(next_steps), list(open_questions), refs or {}, 'h-' + uuid.uuid4().hex[:10])

    def _write_handoff(self, sender, to, project, summary, steps, questions, refs, hid, auto=False):
        snapshot = [{'id': r['id'], 'status': r['status'], 'freshness': self._freshness(r)[0]} for r in self._claims(project)
                    if r['id'] in refs.get('claims', []) or r['status'] in ('conflict', 'stale', 'needs-review')]
        with self._txn(sender, 'handoff', project) as ctx:
            path = self.root / 'handoffs' / to / (hid + '.md')
            if path.exists():
                return {'id': hid, 'duplicate': True}
            _dump(path, {'id': hid, 'project': project, 'from': sender, 'to': to, 'summary': summary[:2000], 'next_steps': steps[:20],
                         'open_questions': questions[:20], 'refs': refs, 'attention': snapshot, 'status': 'open', 'auto': auto,
                         'created': now(), 'created_ts': time.time()}, summary)
            ctx['detail'] = hid
        return {'id': hid, 'attention': snapshot}

    def inbox(self, agent):
        self._agent(agent, 'inbox')
        return [h for h in self._handoffs() if h['to'] == agent and h['status'] == 'open']

    def ack(self, agent, handoff_id):
        self._agent(agent, 'ack')
        with self._txn(agent, 'ack', handoff_id) as ctx:
            for p in (self.root / 'handoffs' / agent).glob(re.sub(r'[^a-z0-9\-]', '', str(handoff_id)) + '.md'):
                h = _load(p)
                h.update(status='acknowledged', acknowledged_at=now())
                _dump(p, h, h['summary'])
                ctx['detail'] = handoff_id
                return {'id': handoff_id, 'status': 'acknowledged'}
            raise WikiError('no such handoff for %s' % agent)

    def reconcile(self, agent, project=None, repair=False, run_tests=False):
        """Find (and with repair=True fix) what an interrupted session left behind. Mechanical; no LLM.

        Reports: unrecorded commits, late or incomplete tasks and their missing handoffs, stale knowledge, unresolved
        conflicts, proposals waiting for verification, and what needs the reasoning bot or a human."""
        self._agent(agent, 'record_task' if repair else 'status')
        cfg, cache, report = self.config, {}, {}
        for proj in ([project] if project else sorted(cfg.get('projects', {}))):
            try:
                self._agent(agent, 'record_task' if repair else 'status', proj)
            except Denied:
                if project:
                    raise
                continue
            repo = self._repo(proj)
            last, head, entries = self._unrecorded(repo, proj)
            tasks, handoffs, claims = self._tasks(proj), self._handoffs(), self._claims(proj)
            late = [t for t in tasks if t['status'] == 'open' and t.get('expires_ts', 0) < time.time()]
            covered = {tid for h in handoffs if h['project'] == proj for tid in h.get('refs', {}).get('tasks', [])}
            incomplete = [t for t in tasks if t['status'] in ('interrupted', 'partial', 'blocked') and t['id'] not in covered]
            waiting = [c for c in claims if c['status'] == 'provisional' and any(e.get('type') in ('file', 'commit', 'external') for e in c['evidence'])]
            waiting_tests = [c for c in claims if c['status'] == 'provisional' and any(e.get('type') == 'test' for e in c['evidence'])]
            stale = [c for c in claims if c['status'] in ('verified', 'stale') and (c['status'] == 'stale' or self._freshness(c, cache)[0] == 'stale')]
            conflicts = [c for c in claims if c['status'] == 'conflict']
            actions = []
            if repair:
                if entries:
                    actions.append('synced %d commits' % self.sync(agent, proj)['recorded'])
                for t in late + incomplete:
                    if t['status'] == 'open':
                        with self._txn(agent, 'interrupt', t['id']) as ctx:
                            path = self.root / 'tasks' / proj / (t['id'] + '.md')
                            cur = _load(path)
                            if cur['status'] == 'open':
                                cur.update(status='interrupted', interrupted=now())
                                _dump(path, cur, cur['summary'])
                            ctx['detail'] = t['id']
                    since = t.get('start_commit')
                    rng = [since + '..HEAD'] if since and self._oid(repo, since) else ['-3']
                    log = git(repo, 'log', '--format=%h %s', '-10', *rng, check=False, read_only=True).stdout.strip().splitlines()
                    if t['agent'] in cfg.get('agents', {}):
                        r = self._write_handoff(STEWARD, t['agent'], proj, 'Session interrupted: "%s". Commits since it started (not necessarily yours): %s'
                                                % (t['summary'][:200], '; '.join(log) or 'none'),
                                                ['Check the working tree and the listed commits', 'Finish, then record_task with this task_id'],
                                                [], {'tasks': [t['id']]}, 'h-auto-' + t['id'], auto=True)
                        actions.append('handoff for %s%s' % (t['id'], ' (already existed)' if r.get('duplicate') else ''))
                for c in waiting:
                    before = c['status']
                    if self.verify(agent, c['id'])['status'] != before:
                        actions.append('re-verified %s' % c['id'])
                if run_tests:
                    for c in waiting_tests:
                        actions.append('test claim %s -> %s' % (c['id'], self.verify(agent, c['id'])['status']))
            report[proj] = {
                'unrecorded_commits': len(entries), 'late_open_tasks': [t['id'] for t in late],
                'incomplete_tasks_without_handoff': [t['id'] for t in incomplete],
                'stale_knowledge': [c['id'] for c in stale], 'unresolved_conflicts': [c['id'] for c in conflicts],
                'waiting_for_verification': [c['id'] for c in waiting], 'waiting_for_test_run': [c['id'] for c in waiting_tests],
                'needs_review': [c['id'] for c in claims if c['status'] == 'needs-review'],
                'needs_reasoning_bot': sorted({c['id'] for c in conflicts} | {c['id'] for c in stale}), 'repaired': actions}
        return report
