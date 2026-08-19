# Odysseus process seccomp policies

The payload allowlist is derived deterministically from Moby's default seccomp profile at commit `35797366d7cdae8d1d84eac06fbb314ccaf3ccaf` (`vendor/github.com/moby/profiles/seccomp/default.json`). The upstream blob SHA-256 is recorded in `policy.json` and verified before generation.

`generate.py` removes capability-dependent and argument-dependent rules, then adds the reviewed payload constraints recorded in `policy.json`. It emits the C allowlist consumed by the trusted launcher and the outer OCI profile used only by the Odysseus Compose service. Run `python3 generate.py --check --verify-arches` to verify provenance, deterministic output, and syscall resolution for x86_64 and ARM64.

The outer bootstrap trace is tied to the upstream Bubblewrap v0.11.0 release commit and annotated-tag object, its release-archive digest, and Debian package `0.11.0-2+deb13u1`. The image build rejects a different package or reported Bubblewrap version until the exact namespace and mount trace is reviewed and the policy is deliberately regenerated.

The launcher dynamically loads the stable libseccomp ABI from `libseccomp.so.2`, compiles cBPF in trusted code, exports it to a sealed anonymous memfd, and injects that descriptor into the fixed `/usr/bin/bwrap` invocation. It is intentionally not a generic program launcher. Native Linux installations must build it with `make` and install it as root with `make install`; Sandbox mode refuses to run if the fixed root-owned installation is missing or writable by non-root users.
