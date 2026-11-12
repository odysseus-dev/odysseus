"""Wiki Steward: the single controlled write path to a private, Git-backed knowledge wiki.

Local code only (stdlib + git). No LLM runs here: searching, indexing, evidence checks, deduplication, conflict and
freshness detection are mechanical. A reasoning model is only needed for claims that carry no checkable evidence;
it can then only *supply evidence* that this module re-verifies, never promote a claim by assertion.
"""
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

STEWARD = 'wiki-steward'
KINDS = ('fact', 'decision', 'security', 'policy', 'architecture')
LIVE = ('verified', 'provisional', 'needs-review', 'stale')
CAPS = ('search', 'propose', 'verify', 'record_task', 'handoff', 'status', 'conflicts', 'inbox', 'ack')
LOCK_STALE_SECONDS = 60

SENSITIVE_TEXT = re.compile(r'\b(secur|permission|authori|authenti|credential|secret|password|token|approval|sandbox|grant|revoke|polic)\w*', re.I)
SECRET_PATTERNS = [re.compile(p) for p in (
    r'(?i)bearer\s+[a-z0-9._\-]{20,}', r'\bsk-[A-Za-z0-9_\-]{20,}', r'\bghp_[A-Za-z0-9]{20,}',
    r'(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{8,}', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    r'ticket=[A-Za-z0-9_\-]{30,}')]
BLOCKED_PATH = re.compile(r'(^|/)(\.env[^/]*|auth\.json|id_rsa[^/]*|\.credentials\.json|[^/]*\.(pem|key|pfx)|vault[^/]*|\.ssh/.*|\.aws/.*)$', re.I)
STOP = set('a an and are as at be by for from in is it of on or that the this to was with'.split())
NEGATIONS = {'not', 'no', 'never', 'fails', 'failing', 'broken', 'cannot', 'without', 'unfixed'}


class WikiError(Exception):
    pass


class Denied(WikiError):
    pass


class IntegrityError(WikiError):
    pass


def _git_exe():
    return os.environ.get('WIKI_GIT') or shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'


def git(repo, *args, check=True, read_only=False):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never', 'GIT_CONFIG_GLOBAL': os.devnull}
    if read_only:
        env['GIT_OPTIONAL_LOCKS'] = '0'
    r = subprocess.run([_git_exe(), '-c', 'core.autocrlf=false', '-c', 'core.quotepath=false', '-C', str(repo), *args],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=60)
    if check and r.returncode:
        raise WikiError('git %s failed: %s' % (args[0], r.stderr.strip()[:300]))
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


def _scan(*texts):
    for text in texts:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text or ''):
                raise WikiError('refused: the text looks like it contains a credential (nothing was stored)')


def _safe_rel(path):
    return bool(path) and not os.path.isabs(path) and ':' not in path and '..' not in path.split('/') and not BLOCKED_PATH.search(path)


def _dump(path, meta, body=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '---\n' + json.dumps(meta, indent=1, sort_keys=True, ensure_ascii=False) + '\n---\n' + body + '\n'
    tmp = path.with_name('%s.tmp%d' % (path.name, os.getpid()))
    tmp.write_text(text, encoding='utf-8', newline='\n')
    os.replace(tmp, path)


def _load(path):
    return json.loads(path.read_text(encoding='utf-8').split('---\n', 2)[1])


class Wiki:
    def __init__(self, root):
        self.root = Path(root).resolve()

    # -- setup ------------------------------------------------------------------------------------------------
    @classmethod
    def init(cls, root, projects=None, agents=None, reviewers=('user',)):
        root = Path(root)
        if (root / '.git').exists():
            raise WikiError('already initialised')
        root.mkdir(parents=True, exist_ok=True)
        wiki = cls(root)
        git(root, 'init', '-q', '-b', 'main')
        (root / '.gitignore').write_text('.steward/\n*.tmp*\n', encoding='utf-8', newline='\n')
        (root / 'README.md').write_text('# Odysseus Wiki\n\nWritten only through the Wiki Steward (`src/wiki_steward.py`). '
                                        'Do not edit by hand; use `wiki adopt` for an intentional manual change.\n', encoding='utf-8', newline='\n')
        cfg = {'version': 1, 'projects': projects or {}, 'agents': agents or {}, 'reviewers': list(reviewers)}
        (root / 'steward.json').write_text(json.dumps(cfg, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        (root / 'audit').mkdir()
        (root / 'audit' / 'audit.jsonl').write_text('', encoding='utf-8')
        wiki._audit('init', 'init', str(root), None)
        wiki._commit('init', 'init')
        return wiki

    @property
    def config(self):
        return json.loads((self.root / 'steward.json').read_text(encoding='utf-8'))

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

    # -- claims ----------------------------------------------------------------------------------------------------
    def _claims(self, project=None):
        base = self.root / 'claims'
        if not base.exists():
            return []
        dirs = [base / project] if project else [d for d in base.iterdir() if d.is_dir()]
        return [_load(p) for d in dirs if d.exists() for p in sorted(d.glob('*.md'))]

    def _save_claim(self, rec):
        _dump(self.root / 'claims' / rec['project'] / (rec['id'] + '.md'), rec, rec['claim'])

    def _get(self, claim_id):
        for p in (self.root / 'claims').glob('*/%s.md' % re.sub(r'[^a-z0-9\-]', '', claim_id)):
            return _load(p)
        raise WikiError('unknown claim %s' % claim_id)

    @staticmethod
    def _oid(repo, spec):
        r = git(repo, 'rev-parse', '--verify', '--quiet', spec, check=False, read_only=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def _check_evidence(self, repo, ev):
        kind = ev.get('type')
        if kind == 'commit':
            ref = str(ev.get('ref', ''))
            if not re.fullmatch(r'[0-9a-f]{7,40}', ref):
                return {'type': kind, 'mechanical': True, 'ok': False, 'why': 'bad commit reference'}
            full = self._oid(repo, ref + '^{commit}')
            if not full:
                return {'type': kind, 'mechanical': True, 'ok': False, 'ref': ref, 'why': 'unknown commit'}
            reachable = git(repo, 'merge-base', '--is-ancestor', full, 'HEAD', check=False, read_only=True).returncode == 0
            return {'type': kind, 'mechanical': True, 'ok': reachable, 'ref': full, 'why': '' if reachable else 'not reachable from HEAD'}
        if kind == 'file':
            path = str(ev.get('path', '')).replace('\\', '/')
            if not _safe_rel(path):
                return {'type': kind, 'mechanical': True, 'ok': False, 'path': path, 'why': 'path not allowed'}
            r = git(repo, 'show', 'HEAD:' + path, check=False, read_only=True)
            if r.returncode or len(r.stdout) > 1_000_000:
                return {'type': kind, 'mechanical': True, 'ok': False, 'path': path, 'why': 'not found at HEAD or too large'}
            needle = ev.get('contains')
            ok = needle is None or needle in r.stdout
            return {'type': kind, 'mechanical': True, 'ok': ok, 'path': path, 'oid': self._oid(repo, 'HEAD:' + path),
                    'content_bound': needle is not None, 'why': '' if ok else 'expected text not in file'}
        return {'type': kind or 'note', 'mechanical': False, 'ok': True}

    def _watch_for(self, repo, rec, checks):
        paths = list(rec.get('watch_paths') or [])
        for c in checks:
            if c.get('ok') and c['type'] == 'file':
                paths.append(c['path'])
            elif c.get('ok') and c['type'] == 'commit' and not rec.get('watch_paths'):
                names = git(repo, 'show', '--name-only', '--pretty=format:', c['ref'], check=False, read_only=True).stdout.splitlines()
                paths += [n for n in names if _safe_rel(n)][:20]
        out, seen = [], set()
        for p in paths:
            if p not in seen and _safe_rel(p):
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
                if o['id'] != rec['id'] and o['status'] in ('verified', 'provisional', 'needs-review', 'stale')
                and o.get('subject') == rec['subject'] and _norm(o['claim']) != _norm(rec['claim'])]

    def _verify(self, rec, extra=(), recheck=False):
        repo = self._repo(rec['project'])
        rec['evidence'] = list(rec['evidence']) + list(extra)
        checks = [self._check_evidence(repo, e) for e in rec['evidence']]
        mech = [c for c in checks if c['mechanical']]
        good = [c for c in mech if c['ok']]
        failed = [c for c in mech if not c['ok']]
        if recheck:
            good = [c for c in good if c.get('content_bound')]
        rec['watch'] = self._watch_for(repo, rec, checks) if good else rec.get('watch', [])
        reasons = []
        conflicts = self._conflicts_of(rec)
        if conflicts:
            reasons.append('conflict')
        if failed:
            reasons.append('evidence-failed')
        if not good:
            reasons.append('stale-needs-reverification' if recheck else 'no-mechanical-evidence')
        if self._sensitive(rec):
            reasons.append('sensitive')
        if conflicts:
            status = 'conflict'
        elif recheck and not good:
            status = 'stale'
        elif failed or not good:
            status = 'provisional'
        elif 'sensitive' in reasons:
            status = 'needs-review'
        else:
            status = 'verified'
        rec['status'], rec['conflicts_with'] = status, conflicts
        rec['verification'] = {'method': 'mechanical', 'at': now(), 'reasons': reasons, 'checks': checks}
        if status == 'verified':
            rec['verified_at'] = now()
            rec['verified_commit'] = self._oid(repo, 'HEAD')
        for oid in conflicts:
            other = self._get(oid)
            if rec['id'] not in other['conflicts_with']:
                other['conflicts_with'].append(rec['id'])
                self._save_claim(other)
        return rec

    def propose(self, agent, claim, evidence, project, subject=None, kind='fact', watch_paths=None):
        self._agent(agent, 'propose', project)
        self._repo(project)
        claim = (claim or '').strip()
        if not 10 <= len(claim) <= 2000 or kind not in KINDS or not isinstance(evidence, list) or len(evidence) > 10:
            raise WikiError('invalid proposal')
        _scan(claim, subject or '', json.dumps(evidence), json.dumps(watch_paths or []))
        with self._txn(agent, 'propose', project) as ctx:
            for other in self._claims(project):
                if other['status'] in LIVE and (_norm(other['claim']) == _norm(claim) or
                                                (_jaccard(other['claim'], claim) >= 0.85 and other.get('subject') == subject)):
                    other.setdefault('corroborations', []).append({'agent': agent, 'at': now(), 'evidence': evidence})
                    if agent not in other['agents']:
                        other['agents'].append(agent)
                    self._save_claim(other)
                    ctx['detail'] = 'duplicate of ' + other['id']
                    return {'id': other['id'], 'status': other['status'], 'duplicate': True}
            rec = {'id': 'c-' + uuid.uuid4().hex[:10], 'project': project, 'claim': claim, 'subject': subject, 'kind': kind,
                   'evidence': list(evidence), 'watch_paths': list(watch_paths or []), 'agents': [agent], 'created': now(),
                   'status': 'provisional', 'verified_at': None, 'verified_commit': None, 'watch': [],
                   'conflicts_with': [], 'corroborations': [], 'verification': None}
            self._verify(rec)
            self._save_claim(rec)
            ctx['detail'] = rec['id'] + ' ' + rec['status']
            return {'id': rec['id'], 'status': rec['status'], 'reasons': rec['verification']['reasons'], 'duplicate': False}

    def verify(self, agent, proposal_id, evidence=None):
        rec = self._get(proposal_id)
        self._agent(agent, 'verify', rec['project'])
        evidence = evidence or []
        _scan(json.dumps(evidence))
        with self._txn(agent, 'verify', rec['id']) as ctx:
            rec = self._get(proposal_id)
            if rec['status'] in ('rejected', 'superseded'):
                raise WikiError('claim is %s' % rec['status'])
            stale = self._freshness(rec)[0] == 'stale' or rec['status'] == 'stale'
            self._verify(rec, evidence, recheck=stale)
            self._save_claim(rec)
            ctx['detail'] = rec['id'] + ' ' + rec['status']
            return {'id': rec['id'], 'status': rec['status'], 'reasons': rec['verification']['reasons']}

    def decide(self, by, claim_id, decision, note=''):
        """accept / reject a claim that needs review or is in conflict. Reviewers decide anything; the wiki bot may
        accept a non-sensitive fact only if it has mechanical evidence and every claim it conflicts with is stale."""
        _scan(note)
        with self._txn(by, 'decide', claim_id) as ctx:
            rec = self._get(claim_id)
            if rec['status'] not in ('needs-review', 'conflict', 'provisional'):
                raise WikiError('nothing to decide for a %s claim' % rec['status'])
            others = [self._get(i) for i in rec['conflicts_with']]
            if by not in (self.config.get('reviewers') or []):
                self._agent(by, 'verify', rec['project'])
                if by != 'wiki-bot' or decision != 'accept':
                    raise Denied('%s may not decide this claim' % by)
                mech = [c for c in (rec['verification'] or {}).get('checks', []) if c['mechanical'] and c['ok']]
                if self._sensitive(rec) or not mech or any(self._freshness(o)[0] != 'stale' and o['status'] == 'verified' for o in others):
                    raise Denied('review required: sensitive, no mechanical evidence, or the conflicting claim is still fresh')
            if decision == 'accept':
                repo = self._repo(rec['project'])
                for o in others:
                    o['status'], o['superseded_by'] = 'superseded', rec['id']
                    self._save_claim(o)
                rec.update(status='verified', conflicts_with=[], verified_at=now(), verified_commit=self._oid(repo, 'HEAD'))
                rec['watch'] = self._watch_for(repo, rec, (rec['verification'] or {}).get('checks', []))
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
            return {'id': rec['id'], 'status': rec['status']}

    # -- freshness, search, status, conflicts ---------------------------------------------------------------------
    def _freshness(self, rec, cache=None):
        if not rec.get('watch'):
            return 'unwatched', []
        repo, cache, changed = self._repo(rec['project']), cache if cache is not None else {}, []
        for w in rec['watch']:
            key = (repo, w['path'])
            if key not in cache:
                cache[key] = self._oid(repo, 'HEAD:' + w['path'])
            if cache[key] != w['oid']:
                changed.append(w['path'])
        return ('stale' if changed else 'fresh'), changed

    def search(self, agent, query, project=None, k=5, include_provisional=False, fresh_only=False):
        self._agent(agent, 'search', project)
        spec = self.config['agents'][agent]
        pool = [r for r in self._claims(project) if '*' in spec['projects'] or r['project'] in spec['projects']]
        wanted = ('verified', 'stale') if not include_provisional else ('verified', 'stale', 'provisional', 'needs-review', 'conflict')
        pool = [r for r in pool if r['status'] in wanted]
        docs = [_tok('%s %s %s %s' % (r['claim'], (r.get('subject') or '') * 2, r['project'], r.get('subject') or '')) for r in pool]
        avg = (sum(map(len, docs)) / len(docs)) if docs else 1
        qt = _tok(query)
        cache, hits = {}, []
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
                         'status': r['status'], 'freshness': fresh, 'changed': changed, 'verified_commit': r['verified_commit'],
                         'verified_at': r['verified_at'], 'agents': r['agents'],
                         'note': None if r['status'] == 'verified' and fresh != 'stale' else
                         ('STALE: watched code changed, re-verify before relying on it' if fresh == 'stale' else 'NOT VERIFIED (%s)' % r['status'])})
        return sorted(hits, key=lambda h: -h['score'])[:k]

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
        live = [r for r in claims if r['status'] in ('verified', 'provisional', 'needs-review') and not r.get('subject')]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if a['project'] == b['project'] and _jaccard(a['claim'], b['claim']) >= 0.5 and \
                        (set(_tok(a['claim'])) & NEGATIONS) != (set(_tok(b['claim'])) & NEGATIONS):
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
        tasks = sorted((_load(p) for p in (self.root / 'tasks' / project).glob('*.md')), key=lambda t: t['created']) \
            if (self.root / 'tasks' / project).exists() else []
        ho = [h for h in self._handoffs() if h['project'] == project and h['status'] == 'open']
        return {'project': project, 'claims': counts, 'stale': stale, 'open_conflicts': counts.get('conflict', 0),
                'verified': [{'id': r['id'], 'claim': r['claim'], 'verified_commit': r['verified_commit']} for r in claims if r['status'] == 'verified' and r['id'] not in stale],
                'recent_tasks': [{'id': t['id'], 'agent': t['agent'], 'summary': t['summary'], 'status': t['status']} for t in tasks[-5:]],
                'open_handoffs': [h['id'] for h in ho]}

    # -- tasks and handoffs ------------------------------------------------------------------------------------------
    def _unknown_commits(self, repo, shas):
        known, unknown = [], []
        for s in shas:
            full = self._oid(repo, str(s) + '^{commit}') if re.fullmatch(r'[0-9a-f]{7,40}', str(s)) else None
            (known if full else unknown).append(full or str(s))
        return known, unknown

    def record_task(self, agent, project, summary, commits=(), files=(), tests=(), status='done', key=None):
        self._agent(agent, 'record_task', project)
        _scan(summary, json.dumps([list(files), list(tests)]))
        known, unknown = self._unknown_commits(self._repo(project), commits)
        key = re.sub(r'[^a-z0-9]', '', (key or hashlib.sha1(('%s|%s|%s' % (agent, summary, commits)).encode()).hexdigest()[:16]).lower())
        path = self.root / 'tasks' / project / (key + '.md')
        with self._txn(agent, 'record_task', project) as ctx:
            if path.exists():
                ctx['detail'] = 'duplicate ' + key
                return {'id': key, 'duplicate': True}
            _dump(path, {'id': key, 'project': project, 'agent': agent, 'summary': summary, 'status': status, 'created': now(),
                         'commits': known, 'unverified_commits': unknown, 'files': list(files)[:50], 'tests': list(tests)[:50]}, summary)
            ctx['detail'] = key
            return {'id': key, 'duplicate': False, 'unverified_commits': unknown}

    def sync(self, agent, project):
        """Record new commits of the project's source repository as routine task logs. Mechanical; safe to run often."""
        self._agent(agent, 'record_task', project)
        repo, cursor = self._repo(project), self.root / 'state' / 'cursors' / (project + '.json')
        last = _load(cursor).get('last') if cursor.exists() else None
        head = self._oid(repo, 'HEAD')
        if not head or head == last:
            return {'recorded': 0, 'head': head}
        rng = [last + '..HEAD'] if last and self._oid(repo, last) else ['-20']
        raw = git(repo, 'log', '--reverse', '--name-only', '--format=%x1e%H%x1f%an%x1f%s%x1f%(trailers:key=Agent,valueonly,separator=%x2c)', *rng, read_only=True).stdout
        entries = []
        for chunk in filter(None, raw.split('\x1e')):
            lines = chunk.split('\n')
            sha, author, subject, trailer = (lines[0].split('\x1f') + ['', '', ''])[:4]
            entries.append((sha, trailer.strip() or author, subject, [l for l in lines[1:] if l.strip()][:50]))
        with self._txn(agent, 'sync', project) as ctx:
            for sha, who, subject, files in entries:
                _scan(subject)
                path = self.root / 'tasks' / project / ('git' + sha[:12] + '.md')
                if not path.exists():
                    _dump(path, {'id': 'git' + sha[:12], 'project': project, 'agent': who, 'summary': subject, 'status': 'committed',
                                 'created': now(), 'commits': [sha], 'unverified_commits': [], 'files': files, 'tests': []}, subject)
            _dump(cursor, {'last': head})
            ctx['detail'] = '%d commits' % len(entries)
        return {'recorded': len(entries), 'head': head}

    def _handoffs(self):
        base = self.root / 'handoffs'
        return [_load(p) for p in sorted(base.glob('*/*.md'))] if base.exists() else []

    def handoff(self, agent, to, project, summary, next_steps=(), open_questions=(), refs=None):
        self._agent(agent, 'handoff', project)
        if to not in (self.config.get('agents') or {}):
            raise WikiError('unknown recipient %s' % to)
        _scan(summary, json.dumps([list(next_steps), list(open_questions), refs or {}]))
        snapshot = [{'id': r['id'], 'status': r['status'], 'freshness': self._freshness(r)[0]} for r in self._claims(project)
                    if r['id'] in (refs or {}).get('claims', []) or r['status'] in ('conflict', 'stale', 'needs-review')]
        hid = 'h-' + uuid.uuid4().hex[:10]
        with self._txn(agent, 'handoff', project) as ctx:
            _dump(self.root / 'handoffs' / to / (hid + '.md'),
                  {'id': hid, 'project': project, 'from': agent, 'to': to, 'summary': summary, 'next_steps': list(next_steps),
                   'open_questions': list(open_questions), 'refs': refs or {}, 'attention': snapshot, 'status': 'open', 'created': now()}, summary)
            ctx['detail'] = hid
        return {'id': hid, 'attention': snapshot}

    def inbox(self, agent):
        self._agent(agent, 'inbox')
        return [h for h in self._handoffs() if h['to'] == agent and h['status'] == 'open']

    def ack(self, agent, handoff_id):
        self._agent(agent, 'ack')
        with self._txn(agent, 'ack', handoff_id) as ctx:
            for p in (self.root / 'handoffs' / agent).glob(re.sub(r'[^a-z0-9\-]', '', handoff_id) + '.md'):
                h = _load(p)
                h.update(status='acknowledged', acknowledged_at=now())
                _dump(p, h, h['summary'])
                return {'id': handoff_id, 'status': 'acknowledged'}
            raise WikiError('no such handoff for %s' % agent)
