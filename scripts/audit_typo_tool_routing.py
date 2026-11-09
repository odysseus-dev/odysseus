#!/usr/bin/env python3
"""Build and score deterministic typo variants of real labeled tool prompts."""

from __future__ import annotations

import argparse, hashlib, json, re, sqlite3
from collections import Counter
from pathlib import Path

from src.turn_contract import requested_capabilities

DB = Path(str(Path(__file__).resolve().parents[1] / "data" / "app.db"))
ANCHOR = "a37dcb3b-6864-4266-a115-f9e87aafd0eb"
TRIGGERS = {
    "calendar": ("calendar", "event", "meeting", "appointment", "agenda"),
    "notes": ("note", "notes", "checklist", "groceries"),
    "tasks": ("task", "tasks", "todo", "reminder"),
    "skills": ("skill", "skills"),
    "memory": ("memory", "memories", "remember", "forget"),
    "documents": ("document", "documents", "doc", "editor"),
    "email": ("email", "emails", "inbox", "mail", "spam"),
    "search_browser": ("search", "web", "browse", "browser", "website", "youtube"),
    "shell_files": ("file", "files", "folder", "directory", "shell", "terminal", "workspace", "bash", "python"),
    "cookbook_admin": ("cookbook", "endpoint", "model", "server", "download", "settings"),
}
TOOL_FAMILY = {
    "manage_calendar": "calendar", "manage_notes": "notes", "manage_tasks": "tasks",
    "manage_skills": "skills", "manage_memory": "memory", "search_chats": "memory",
    "manage_documents": "documents", "create_document": "documents", "edit_document": "documents",
    "update_document": "documents", "suggest_document": "documents",
    "list_email_accounts": "email", "list_emails": "email", "search_emails": "email",
    "read_email": "email", "send_email": "email", "reply_to_email": "email", "draft_email": "email",
    "web_search": "search_browser", "web_fetch": "search_browser", "private_browser": "search_browser",
    "youtube_tool": "search_browser", "search_hf_models": "search_browser",
    "bash": "shell_files", "python": "shell_files", "read_file": "shell_files", "write_file": "shell_files",
    "list_models": "cookbook_admin", "list_served_models": "cookbook_admin", "serve_model": "cookbook_admin",
    "stop_served_model": "cookbook_admin", "list_cookbook_servers": "cookbook_admin", "manage_endpoints": "cookbook_admin",
}
NEIGHBOR = {"a":"s","e":"r","i":"o","o":"p","s":"d","t":"y","r":"t","l":"k","n":"m","m":"n","d":"f","c":"v","b":"n","w":"e","f":"g","g":"h","h":"j","p":"o","k":"l","v":"b","u":"i"}

def variants(word: str) -> list[tuple[str,str]]:
    i = max(1, min(len(word)-2, len(word)//2))
    out = [("delete", word[:i]+word[i+1:]), ("duplicate", word[:i]+word[i]+word[i:])]
    if i+1 < len(word): out.append(("transpose", word[:i]+word[i+1]+word[i]+word[i+2:]))
    repl = NEIGHBOR.get(word[i].lower(), "x")
    out.append(("neighbor", word[:i]+repl+word[i+1:]))
    if len(word) >= 6: out.append(("split", word[:i]+" "+word[i:]))
    return out

def expected_family(metadata: str | None) -> str | None:
    try: events = json.loads(metadata or "{}").get("tool_events") or []
    except json.JSONDecodeError: return None
    families = []
    for event in events:
        tool = str(event.get("tool") or "").rsplit("__",1)[-1]
        if TOOL_FAMILY.get(tool): families.append(TOOL_FAMILY[tool])
    return families[0] if families and len(set(families)) == 1 else None

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--db",type=Path,default=DB); ap.add_argument("--out",type=Path,required=True); ap.add_argument("--per-family",type=int,default=20); a=ap.parse_args()
    con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
    t0=con.execute("select created_at from sessions where id=?",(ANCHOR,)).fetchone()[0]
    sessions=con.execute("select id from sessions where owner='sft_alex_creator' and created_at>=? order by created_at,id",(t0,)).fetchall()
    seeds={f:[] for f in TRIGGERS}
    for s in sessions:
        ms=con.execute("select role,content,metadata from chat_messages where session_id=? order by timestamp,id",(s[0],)).fetchall(); history=[]
        for i,m in enumerate(ms):
            if m['role']!='user': history.append({'role':m['role'],'content':m['content']}); continue
            nxt=next((x for x in ms[i+1:] if x['role']=='assistant'),None); fam=expected_family(nxt['metadata'] if nxt else None)
            if fam and len(seeds[fam])<a.per_family and fam in requested_capabilities(m['content'],history):
                hit=next((w for w in TRIGGERS[fam] if re.search(r'\b'+re.escape(w)+r'\b',m['content'],re.I)),None)
                if hit: seeds[fam].append((s[0],m['content'],tuple(history),hit))
            history.append({'role':'user','content':m['content']})
    rows=[]
    for fam,items in seeds.items():
        for sid,prompt,history,word in items:
            for kind,bad in variants(word):
                changed=re.sub(r'\b'+re.escape(word)+r'\b',bad,prompt,count=1,flags=re.I)
                actual=sorted(requested_capabilities(changed,history)); digest=hashlib.sha256((sid+changed).encode()).hexdigest()
                rows.append({'split':'blind' if int(digest[:2],16)<64 else 'dev','family':fam,'source_session':sid,'mutation':kind,'prompt':changed,'actual':actual,'passed':fam in actual})
    summary = {}
    for split in ('dev', 'blind'):
        selected = [row for row in rows if row['split'] == split]
        passed = sum(row['passed'] for row in selected)
        exact = sum(row['actual'] == [row['family']] for row in selected)
        wrong = sum(bool(row['actual']) and row['family'] not in row['actual']
                    and row['actual'] != ['unknown'] for row in selected)
        abstained = sum(not row['actual'] or row['actual'] == ['unknown'] for row in selected)
        summary[split] = {
            'total': len(selected), 'passed': passed,
            'accuracy': round(passed / len(selected), 6) if selected else None,
            'exact': exact, 'exact_accuracy': round(exact / len(selected), 6) if selected else None,
            'wrong_family': wrong,
            'wrong_family_rate': round(wrong / len(selected), 6) if selected else None,
            'abstained': abstained,
        }
    report={'summary':summary,'family_failures':dict(Counter(r['family'] for r in rows if not r['passed'])),'rows':rows}
    a.out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n'); print(json.dumps({'summary':summary,'family_failures':report['family_failures']},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
