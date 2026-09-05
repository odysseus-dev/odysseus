#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/personality.h>
#include <sys/ptrace.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

static int expect_errno(long result, int expected)
{
    if (result == -1 && errno == expected) {
        return 0;
    }
    (void)fprintf(
        stderr,
        "unexpected syscall result=%ld errno=%d expected=%d\n",
        result,
        errno,
        expected
    );
    return 1;
}

static int expect_socket_denied(int family)
{
    errno = 0;
    int descriptor = socket(family, SOCK_RAW, 0);
    if (descriptor >= 0) {
        (void)close(descriptor);
        return 1;
    }
    return expect_errno(descriptor, EPERM);
}

static int expect_socket_allowed(int family)
{
    int descriptor = socket(family, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (descriptor < 0) {
        return 1;
    }
    return close(descriptor) == 0 ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        return 64;
    }
    const char *probe = argv[1];
    errno = 0;

#ifdef SYS_bpf
    if (strcmp(probe, "bpf") == 0) {
        return expect_errno(syscall(SYS_bpf, 0, NULL, 0), EPERM);
    }
#endif
#ifdef SYS_perf_event_open
    if (strcmp(probe, "perf_event_open") == 0) {
        return expect_errno(syscall(SYS_perf_event_open, NULL, 0, -1, -1, 0), EPERM);
    }
#endif
#ifdef SYS_clone
    if (strcmp(probe, "clone_namespace") == 0) {
        return expect_errno(
            syscall(SYS_clone, (unsigned long)CLONE_NEWNS | SIGCHLD, NULL, NULL, NULL, 0),
            EPERM
        );
    }
    if (strcmp(probe, "clone_process") == 0) {
        pid_t child = (pid_t)syscall(SYS_clone, SIGCHLD, NULL, NULL, NULL, 0);
        if (child < 0) {
            return 1;
        }
        if (child == 0) {
            _exit(0);
        }
        int status = 0;
        return waitpid(child, &status, 0) == child && WIFEXITED(status)
            && WEXITSTATUS(status) == 0
            ? 0
            : 1;
    }
#endif
#ifdef SYS_clone3
    if (strcmp(probe, "clone3") == 0) {
        return expect_errno(syscall(SYS_clone3, NULL, 0), ENOSYS);
    }
#endif
#ifdef SYS_unshare
    if (strcmp(probe, "unshare") == 0) {
        return expect_errno(syscall(SYS_unshare, CLONE_NEWNS), EPERM);
    }
#endif
#ifdef SYS_setns
    if (strcmp(probe, "setns") == 0) {
        return expect_errno(syscall(SYS_setns, -1, CLONE_NEWNS), EPERM);
    }
#endif
#ifdef SYS_mount
    if (strcmp(probe, "mount") == 0) {
        return expect_errno(syscall(SYS_mount, NULL, "/", NULL, 0, NULL), EPERM);
    }
#endif
#ifdef SYS_umount2
    if (strcmp(probe, "umount2") == 0) {
        return expect_errno(syscall(SYS_umount2, "/", 0), EPERM);
    }
#endif
#ifdef SYS_pivot_root
    if (strcmp(probe, "pivot_root") == 0) {
        return expect_errno(syscall(SYS_pivot_root, "/", "/"), EPERM);
    }
#endif
#ifdef SYS_ptrace
    if (strcmp(probe, "ptrace") == 0) {
        return expect_errno(syscall(SYS_ptrace, PTRACE_PEEKDATA, getpid(), NULL, NULL), EPERM);
    }
#endif
#ifdef SYS_process_vm_readv
    if (strcmp(probe, "process_vm_readv") == 0) {
        return expect_errno(
            syscall(SYS_process_vm_readv, getpid(), NULL, 0, NULL, 0, 0),
            EPERM
        );
    }
#endif
#ifdef SYS_process_vm_writev
    if (strcmp(probe, "process_vm_writev") == 0) {
        return expect_errno(
            syscall(SYS_process_vm_writev, getpid(), NULL, 0, NULL, 0, 0),
            EPERM
        );
    }
#endif
#ifdef SYS_keyctl
    if (strcmp(probe, "keyctl") == 0) {
        return expect_errno(syscall(SYS_keyctl, 0, 0, 0, 0, 0), EPERM);
    }
#endif
#ifdef SYS_open_by_handle_at
    if (strcmp(probe, "open_by_handle_at") == 0) {
        return expect_errno(syscall(SYS_open_by_handle_at, -1, NULL, 0), EPERM);
    }
#endif
    if (strcmp(probe, "af_packet") == 0) {
        return expect_socket_denied(AF_PACKET);
    }
    if (strcmp(probe, "socket_unix") == 0) {
        return expect_socket_allowed(AF_UNIX);
    }
    if (strcmp(probe, "socket_inet") == 0) {
        return expect_socket_allowed(AF_INET);
    }
    if (strcmp(probe, "socket_inet6") == 0) {
        return expect_socket_allowed(AF_INET6);
    }
    if (strcmp(probe, "socketpair_unix") == 0) {
        int descriptors[2] = {-1, -1};
        if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, descriptors) < 0) {
            return 1;
        }
        return close(descriptors[0]) == 0 && close(descriptors[1]) == 0 ? 0 : 1;
    }
    if (strcmp(probe, "socketpair_inet_denied") == 0) {
        int descriptors[2] = {-1, -1};
        return expect_errno(
            socketpair(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0, descriptors),
            EPERM
        );
    }
#ifdef AF_ALG
    if (strcmp(probe, "af_alg") == 0) {
        return expect_socket_denied(AF_ALG);
    }
#endif
#ifdef AF_VSOCK
    if (strcmp(probe, "af_vsock") == 0) {
        return expect_socket_denied(AF_VSOCK);
    }
#endif
#ifdef TIOCSTI
    if (strcmp(probe, "tiocsti") == 0) {
        return expect_errno(ioctl(STDIN_FILENO, TIOCSTI, "x"), EACCES);
    }
    if (strcmp(probe, "tiocsti_high_bits") == 0) {
        const unsigned long request = (1UL << 32U) | (unsigned long)TIOCSTI;
        return expect_errno(
            syscall(SYS_ioctl, STDIN_FILENO, request, "x"),
            EACCES
        );
    }
#endif
#ifdef SYS_userfaultfd
    if (strcmp(probe, "userfaultfd") == 0) {
        return expect_errno(syscall(SYS_userfaultfd, 0), EPERM);
    }
#endif
#ifdef SYS_io_uring_setup
    if (strcmp(probe, "io_uring_setup") == 0) {
        return expect_errno(syscall(SYS_io_uring_setup, 1, NULL), EPERM);
    }
#endif
#ifdef SYS_personality
    if (strcmp(probe, "personality_query") == 0) {
        return syscall(SYS_personality, UINT32_MAX) >= 0 ? 0 : 1;
    }
    if (strcmp(probe, "personality_denied") == 0) {
        return expect_errno(syscall(SYS_personality, 1UL), EPERM);
    }
#endif
    if (strcmp(probe, "direct_bwrap") == 0) {
        char *const arguments[] = {
            "/usr/bin/bwrap",
            "--ro-bind",
            "/",
            "/",
            "--",
            "/bin/true",
            NULL,
        };
        execv(arguments[0], arguments);
        return expect_errno(-1, EACCES);
    }
    return 77;
}
