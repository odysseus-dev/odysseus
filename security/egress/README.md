# Brokered sandbox egress

`odysseus-egress-broker` is a fixed-purpose parent for the trusted seccomp
launcher. It owns a unique mode-0700 runtime directory and Unix socket outside
Bubblewrap, injects that directory as a read-only mount, clears its environment,
and resolves every requested destination itself.

`odysseus-egress-bridge` runs after Bubblewrap has created a private network
namespace and loaded the inner seccomp filter. It exposes only
`127.0.0.1:3128` and forwards bytes to the mounted Unix socket. The payload can
talk to the broker but cannot acquire the container's raw network namespace.

The broker supports absolute-form HTTP requests to public TCP port 80 and
HTTPS `CONNECT` to public TCP port 443. Every DNS answer must be globally
routable; mixed public/private answers fail closed. It connects to the exact
validated sockaddr, strips proxy authorization and hop-by-hop request headers,
and bounds headers, HTTP bodies, tunnels, concurrent connections, idle time,
connection lifetime, and total broker lifetime. It intentionally does not
support arbitrary TCP, UDP, SSH, SOCKS, private destinations, or raw network
sharing.

The production install target rewrites each helper's shebang to the first available interpreter in the fixed set `/usr/local/bin/python3` and `/usr/bin/python3`, requires that canonical interpreter to be a root-owned regular executable with no set-id or group/other write bits, enables Python isolated mode, and fixes the install path and ownership to `/usr/local/libexec` and `root:root`. Its interpreter, path, and ownership contract cannot be replaced through Make variables; the separate developer `check` target remains configurable. Runtime launch therefore never resolves a Python executable or imports user-site startup code through inherited or model-writable state.
