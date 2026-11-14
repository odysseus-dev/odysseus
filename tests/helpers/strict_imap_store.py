"""Loopback IMAP subset emulating GreenMail's parenthesized STORE requirement.

The fixture exercises imaplib's actual wire serialization. It is not a full
IMAP server: only the commands needed by flag/move/delete regressions exist.
RFC 3501/9051 also allow bare flags; rejecting them models a server
compatibility constraint, not a general RFC conformance requirement.
"""
import socketserver
import threading


class StrictImapStore(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.present = True
        self.flags = set()
        self.commands = []
        self.copies = []
        super().__init__(('127.0.0.1', 0), _Handler)

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'* OK strict UID STORE fixture\r\n')
        while line := self.rfile.readline():
            tag, command, *rest = line.rstrip(b'\r\n').split(b' ', 2)
            command = command.upper()
            args = rest[0] if rest else b''
            # Never retain authentication arguments in command evidence.
            if command != b'LOGIN':
                self.server.commands.append(command + b' ' + args)
            status = b'OK completed'
            if command == b'CAPABILITY':
                self.wfile.write(b'* CAPABILITY IMAP4rev1 UIDPLUS\r\n')
            elif command in (b'LOGIN', b'NOOP', b'CLOSE'):
                pass
            elif command == b'SELECT':
                self.wfile.write(b'* FLAGS (\\Seen \\Flagged \\Deleted)\r\n')
                self.wfile.write(b'* '+(b'1' if self.server.present else b'0')+b' EXISTS\r\n')
            elif command == b'LIST':
                for flag, folder in [(b'\\HasNoChildren', b'INBOX'), (b'\\Trash', b'Trash'), (b'\\Junk', b'Junk')]:
                    self.wfile.write(b'* LIST ('+flag+b') "/" "'+folder+b'"\r\n')
            elif command == b'UID':
                operation, *parts = args.split(b' ', 1)
                payload = parts[0] if parts else b''
                if operation == b'FETCH' and self.server.present:
                    flags = b' '.join(sorted(self.server.flags))
                    self.wfile.write(b'* 1 FETCH (UID 89773 FLAGS ('+flags+b'))\r\n')
                elif operation == b'SEARCH':
                    self.wfile.write(b'* SEARCH'+(b' 89773' if self.server.present else b'')+b'\r\n')
                elif operation == b'MOVE':
                    status = b'NO use COPY fallback'
                elif operation == b'COPY':
                    self.server.copies.append(payload)
                elif operation == b'STORE':
                    uid, mode, flags = payload.split(b' ', 2)
                    if not (flags.startswith(b'(') and flags.endswith(b')')):
                        status = b'BAD Parse Error: expected flag list'
                    elif uid == b'89773' and self.server.present:
                        values = set(flags[1:-1].split())
                        if mode.startswith(b'+'):
                            self.server.flags.update(values)
                        else:
                            self.server.flags.difference_update(values)
                        current = b' '.join(sorted(self.server.flags))
                        self.wfile.write(b'* 1 FETCH (UID 89773 FLAGS ('+current+b'))\r\n')
                elif operation != b'FETCH':
                    status = b'BAD unsupported fixture command'
            elif command == b'EXPUNGE':
                if b'\\Deleted' in self.server.flags:
                    self.server.present = False
                    self.wfile.write(b'* 1 EXPUNGE\r\n')
            elif command == b'LOGOUT':
                self.wfile.write(b'* BYE fixture closing\r\n'+tag+b' OK logout\r\n')
                return
            else:
                status = b'BAD unsupported fixture command'
            self.wfile.write(tag+b' '+status+b'\r\n')
