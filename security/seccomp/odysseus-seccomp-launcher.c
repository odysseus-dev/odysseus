#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/memfd.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#include "generated_inner_policy.h"

/* Minimal libseccomp ABI copied from seccomp.h.in at
 * de2bf463afa565e1573f58096167e31eaf6e08b6. */
typedef void *scmp_filter_ctx;

enum scmp_compare {
    SCMP_CMP_NE = 1,
    SCMP_CMP_EQ = 4,
    SCMP_CMP_MASKED_EQ = 7,
};

struct scmp_arg_cmp {
    unsigned int arg;
    enum scmp_compare op;
    uint64_t datum_a;
    uint64_t datum_b;
};

#define SCMP_ACT_ERRNO(value) (0x00050000U | ((uint32_t)(value) & 0x0000ffffU))
#define SCMP_ACT_ALLOW 0x7fff0000U

#define TRUSTED_BWRAP "/usr/bin/bwrap"
#define BROKER_RUNTIME_DESTINATION "/run/odysseus-egress"
#define NATIVE_VENV_DESTINATION "/run/odysseus-python-venv"
#define FILTER_FD 3
#define MAX_SETENV_VALUE 4096U

enum launcher_exit {
    EXIT_INVALID_BWRAP = 64,
    EXIT_INVALID_ARGUMENTS = 65,
    EXIT_LIBSECCOMP = 66,
    EXIT_FILTER = 67,
    EXIT_MEMFD = 68,
    EXIT_EXPORT = 69,
    EXIT_SEAL = 70,
    EXIT_EXEC = 71,
};

struct seccomp_api {
    void *handle;
    scmp_filter_ctx (*init)(uint32_t);
    void (*release)(scmp_filter_ctx);
    int (*rule_add_exact_array)(
        scmp_filter_ctx,
        uint32_t,
        int,
        unsigned int,
        const struct scmp_arg_cmp *
    );
    int (*export_bpf)(scmp_filter_ctx, int);
    int (*resolve_name)(const char *);
};

static void fail_message(const char *message)
{
    (void)fprintf(stderr, "odysseus-seccomp-launcher: %s\n", message);
}

#ifdef ODYSSEUS_LAUNCHER_TESTING
static bool injected_failure(const char *stage)
{
    const char *requested = getenv("ODYSSEUS_TEST_FAIL");
    return requested != NULL && strcmp(requested, stage) == 0;
}
#else
static bool injected_failure(const char *stage)
{
    (void)stage;
    return false;
}
#endif

static bool load_symbol(void *handle, const char *name, void *destination, size_t size)
{
    void *symbol = dlsym(handle, name);
    if (symbol == NULL || size != sizeof(symbol)) {
        return false;
    }
    memcpy(destination, &symbol, sizeof(symbol));
    return true;
}

static bool load_seccomp(struct seccomp_api *api)
{
    if (injected_failure("libseccomp")) {
        return false;
    }
    api->handle = dlopen("libseccomp.so.2", RTLD_NOW | RTLD_LOCAL);
    if (api->handle == NULL) {
        return false;
    }
    return load_symbol(api->handle, "seccomp_init", &api->init, sizeof(api->init))
        && load_symbol(
            api->handle,
            "seccomp_release",
            &api->release,
            sizeof(api->release)
        )
        && load_symbol(
            api->handle,
            "seccomp_rule_add_exact_array",
            &api->rule_add_exact_array,
            sizeof(api->rule_add_exact_array)
        )
        && load_symbol(
            api->handle,
            "seccomp_export_bpf",
            &api->export_bpf,
            sizeof(api->export_bpf)
        )
        && load_symbol(
            api->handle,
            "seccomp_syscall_resolve_name",
            &api->resolve_name,
            sizeof(api->resolve_name)
        );
}

static int add_rule(
    const struct seccomp_api *api,
    scmp_filter_ctx filter,
    uint32_t action,
    const char *name,
    unsigned int argument_count,
    const struct scmp_arg_cmp *arguments
)
{
    int syscall_number = api->resolve_name(name);
    if (syscall_number < 0) {
        return -1;
    }
    return api->rule_add_exact_array(
        filter,
        action,
        syscall_number,
        argument_count,
        arguments
    );
}

static int add_allowlist(
    const struct seccomp_api *api,
    scmp_filter_ctx filter,
    const char *const *names,
    size_t count
)
{
    for (size_t index = 0; index < count; index++) {
        int syscall_number = api->resolve_name(names[index]);
        /* Moby's portable lists contain pseudo syscall numbers for calls that
         * do not exist on the running architecture. The default-deny action
         * already covers them; only add native, nonnegative syscall numbers. */
        if (syscall_number < 0) {
            continue;
        }
        if (api->rule_add_exact_array(
                filter,
                SCMP_ACT_ALLOW,
                syscall_number,
                0,
                NULL
            ) < 0) {
            return -1;
        }
    }
    return 0;
}

static int add_exact_argument_rules(
    const struct seccomp_api *api,
    scmp_filter_ctx filter,
    const char *name,
    unsigned int argument,
    const uint64_t *values,
    size_t count
)
{
    for (size_t index = 0; index < count; index++) {
        const struct scmp_arg_cmp comparison = {
            .arg = argument,
            .op = SCMP_CMP_EQ,
            .datum_a = values[index],
            .datum_b = 0,
        };
        if (add_rule(api, filter, SCMP_ACT_ALLOW, name, 1, &comparison) < 0) {
            return -1;
        }
    }
    return 0;
}

static scmp_filter_ctx build_filter(const struct seccomp_api *api)
{
#if defined(__x86_64__)
    const char *const *allowlist = ODYSSEUS_ALLOWED_X86_64;
    const size_t allowlist_count = ODYSSEUS_ALLOWED_X86_64_COUNT;
#elif defined(__aarch64__)
    const char *const *allowlist = ODYSSEUS_ALLOWED_AARCH64;
    const size_t allowlist_count = ODYSSEUS_ALLOWED_AARCH64_COUNT;
#else
#error "odysseus-seccomp-launcher supports only x86_64 and aarch64"
#endif

    if (injected_failure("filter")) {
        return NULL;
    }
    scmp_filter_ctx filter = api->init(SCMP_ACT_ERRNO(ODYSSEUS_DEFAULT_ERRNO));
    if (filter == NULL) {
        return NULL;
    }

    const struct scmp_arg_cmp clone_comparison = {
        .arg = 0,
        .op = SCMP_CMP_MASKED_EQ,
        .datum_a = ODYSSEUS_CLONE_NAMESPACE_MASK,
        .datum_b = 0,
    };
    const struct scmp_arg_cmp ioctl_comparison = {
        .arg = 1,
        .op = SCMP_CMP_NE,
        .datum_a = TIOCSTI,
        .datum_b = 0,
    };
    const struct scmp_arg_cmp tiocsti_comparison = {
        /* Linux truncates the ioctl command to unsigned int after the seccomp
         * check. Match the low 32 bits so high bits cannot bypass the deny. */
        .arg = 1,
        .op = SCMP_CMP_MASKED_EQ,
        .datum_a = UINT32_MAX,
        .datum_b = TIOCSTI,
    };

    if (add_allowlist(api, filter, allowlist, allowlist_count) < 0
        || add_rule(api, filter, SCMP_ACT_ALLOW, "clone", 1, &clone_comparison) < 0
        || add_rule(
            api,
            filter,
            SCMP_ACT_ERRNO(ODYSSEUS_CLONE3_ERRNO),
            "clone3",
            0,
            NULL
        ) < 0
        /* The action must differ from the default EPERM for libseccomp to
         * retain a masked deny rule alongside the compatibility allow rule.
         * Add the deny first: affected libseccomp releases can otherwise
         * weaken an overlapping 64-bit comparison while merging the tree. */
        || add_rule(
            api,
            filter,
            SCMP_ACT_ERRNO(ODYSSEUS_TIOCSTI_ERRNO),
            "ioctl",
            1,
            &tiocsti_comparison
        ) < 0
        || add_rule(api, filter, SCMP_ACT_ALLOW, "ioctl", 1, &ioctl_comparison) < 0
        || add_exact_argument_rules(
            api,
            filter,
            "personality",
            0,
            ODYSSEUS_PERSONALITY_VALUES,
            ODYSSEUS_PERSONALITY_VALUES_COUNT
        ) < 0
        || add_exact_argument_rules(
            api,
            filter,
            "socket",
            0,
            ODYSSEUS_SOCKET_FAMILIES,
            ODYSSEUS_SOCKET_FAMILIES_COUNT
        ) < 0
        || add_exact_argument_rules(
            api,
            filter,
            "socketpair",
            0,
            ODYSSEUS_SOCKETPAIR_FAMILIES,
            ODYSSEUS_SOCKETPAIR_FAMILIES_COUNT
        ) < 0) {
        api->release(filter);
        return NULL;
    }
    return filter;
}

static bool valid_bwrap(const char *path)
{
    struct stat metadata;
    char resolved[PATH_MAX];
    if (path == NULL || strcmp(path, TRUSTED_BWRAP) != 0) {
        return false;
    }
    if (realpath(path, resolved) == NULL || strcmp(resolved, TRUSTED_BWRAP) != 0) {
        return false;
    }
    return stat(path, &metadata) == 0
        && S_ISREG(metadata.st_mode)
        && metadata.st_uid == 0
        && (metadata.st_mode & (S_ISUID | S_ISGID | S_IWGRP | S_IWOTH)) == 0
        && access(path, X_OK) == 0;
}

struct bwrap_contract {
    bool unshare_user;
    bool unshare_ipc;
    bool unshare_pid;
    bool unshare_net;
    bool unshare_uts;
    bool unshare_cgroup;
    bool die_with_parent;
    bool new_session;
    bool clearenv;
    bool cap_drop;
    bool proc_mount;
    bool dev_mount;
    bool chdir;
    bool usr_runtime;
    bool full_access;
    bool symlink_bin;
    bool symlink_lib;
    bool symlink_lib64;
    bool broker_runtime;
    bool native_venv;
    unsigned int writable_binds;
    uint32_t environment_names;
    const char *broker_runtime_source;
    const char *native_venv_source;
    const char *writable_root;
};

static bool set_once(bool *field)
{
    if (*field) {
        return false;
    }
    *field = true;
    return true;
}

static bool safe_absolute_path(const char *path)
{
    if (path == NULL || path[0] != '/') {
        return false;
    }
    const char *component = path;
    while (*component != '\0') {
        while (*component == '/') {
            component++;
        }
        const char *end = component;
        while (*end != '\0' && *end != '/') {
            end++;
        }
        size_t length = (size_t)(end - component);
        if ((length == 1U && component[0] == '.')
            || (length == 2U && component[0] == '.' && component[1] == '.')) {
            return false;
        }
        component = end;
    }
    return true;
}

static bool sensitive_mount_source(const char *path)
{
    static const char *const names[] = {
        "/.aws",
        "/.azure",
        "/.codex",
        "/.config/gh",
        "/.docker",
        "/.gnupg",
        "/.kube",
        "/.ssh",
    };
    size_t length = strlen(path);
    if (length >= 5U && strcmp(path + length - 5U, "/.env") == 0) {
        return true;
    }
    if (strstr(path, "/.env.") != NULL) {
        return true;
    }
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        const char *match = strstr(path, names[index]);
        size_t name_length = strlen(names[index]);
        if (match != NULL
            && (match[name_length] == '\0' || match[name_length] == '/')) {
            return true;
        }
    }
    return false;
}

static bool same_resolved_path(const char *first, const char *second)
{
    char first_resolved[PATH_MAX];
    char second_resolved[PATH_MAX];
    return realpath(first, first_resolved) != NULL
        && realpath(second, second_resolved) != NULL
        && strcmp(first_resolved, second_resolved) == 0;
}

static bool canonical_existing_path(const char *path)
{
    char resolved[PATH_MAX];
    return realpath(path, resolved) != NULL && strcmp(path, resolved) == 0;
}

static bool path_at_or_below(const char *path, const char *root)
{
    size_t length = strlen(root);
    return strcmp(path, root) == 0
        || (strncmp(path, root, length) == 0 && path[length] == '/');
}

static bool trusted_native_venv(const char *source)
{
    struct stat metadata;
    char config[PATH_MAX];
    char interpreter[PATH_MAX];
    char resolved[PATH_MAX];
    const char *name = strrchr(source, '/');
    if (!canonical_existing_path(source)
        || name == NULL
        || (strcmp(name + 1, "venv") != 0 && strcmp(name + 1, ".venv") != 0)
        || stat(source, &metadata) != 0
        || !S_ISDIR(metadata.st_mode)
        || (metadata.st_uid != 0 && metadata.st_uid != getuid())
        || (metadata.st_mode & (S_ISUID | S_ISGID | S_IWGRP | S_IWOTH)) != 0) {
        return false;
    }
    int config_length = snprintf(config, sizeof(config), "%s/pyvenv.cfg", source);
    if (config_length <= 0 || (size_t)config_length >= sizeof(config)
        || realpath(config, resolved) == NULL
        || strcmp(config, resolved) != 0
        || !path_at_or_below(resolved, source)
        || stat(config, &metadata) != 0
        || !S_ISREG(metadata.st_mode)
        || (metadata.st_uid != 0 && metadata.st_uid != getuid())
        || (metadata.st_mode & (S_ISUID | S_ISGID | S_IWGRP | S_IWOTH)) != 0) {
        return false;
    }
    int interpreter_length = snprintf(
        interpreter,
        sizeof(interpreter),
        "%s/bin/python",
        source
    );
    if (interpreter_length <= 0
        || (size_t)interpreter_length >= sizeof(interpreter)
        || realpath(interpreter, resolved) == NULL
        || !path_at_or_below(resolved, "/usr")
        || stat(resolved, &metadata) != 0
        || !S_ISREG(metadata.st_mode)
        || metadata.st_uid != 0
        || (metadata.st_mode & (S_ISUID | S_ISGID | S_IWGRP | S_IWOTH)) != 0
        || access(resolved, X_OK) != 0) {
        return false;
    }
    return true;
}

static bool trusted_broker_runtime(const char *source)
{
    static const char prefix[] = "/tmp/odysseus-egress-";
    struct stat metadata;
    if (strncmp(source, prefix, sizeof(prefix) - 1U) != 0) {
        return false;
    }
    const char *suffix = source + sizeof(prefix) - 1U;
    if (*suffix == '\0'
        || strchr(suffix, '/') != NULL
        || !canonical_existing_path(source)
        || stat(source, &metadata) != 0
        || !S_ISDIR(metadata.st_mode)
        || metadata.st_uid != getuid()
        || (metadata.st_mode & 07777) != 0700) {
        return false;
    }
    return true;
}

static bool allowed_environment_name(const char *name, uint32_t *mask)
{
    static const char *const names[] = {
        "COLUMNS",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "LINES",
        "PATH",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "http_proxy",
        "https_proxy",
    };
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (strcmp(name, names[index]) == 0) {
            uint32_t bit = UINT32_C(1) << index;
            if ((*mask & bit) != 0U) {
                return false;
            }
            *mask |= bit;
            return true;
        }
    }
    return false;
}

static bool validate_ro_bind(
    struct bwrap_contract *contract,
    const char *source,
    const char *destination
)
{
    struct stat metadata;
    if (!safe_absolute_path(source) || !safe_absolute_path(destination)
        || sensitive_mount_source(source)
        || same_resolved_path(source, TRUSTED_BWRAP)) {
        return false;
    }
    if (strcmp(source, "/usr") == 0 && strcmp(destination, "/usr") == 0) {
        contract->usr_runtime = canonical_existing_path(source);
        return contract->usr_runtime;
    }
#ifdef ODYSSEUS_LAUNCHER_TESTING
    /* The secretless review runner forbids mounting a fresh procfs. Retaining
     * its read-only proc mount keeps runtime filter probes executable without
     * weakening the production launcher's mandatory --proc /proc contract. */
    if (strcmp(source, "/proc") == 0 && strcmp(destination, "/proc") == 0) {
        contract->proc_mount = true;
        return true;
    }
#endif
    if (strcmp(source, "/dev/null") == 0) {
        return true;
    }
    if (strcmp(destination, BROKER_RUNTIME_DESTINATION) == 0) {
        if (!set_once(&contract->broker_runtime)
            || !trusted_broker_runtime(source)) {
            return false;
        }
        contract->broker_runtime_source = source;
        return true;
    }
    if (strcmp(destination, NATIVE_VENV_DESTINATION) == 0) {
        if (!set_once(&contract->native_venv)
            || !trusted_native_venv(source)) {
            return false;
        }
        contract->native_venv_source = source;
        return true;
    }
    if (strcmp(source, destination) == 0) {
        return canonical_existing_path(source)
            && (strcmp(source, "/etc/ssl/certs/ca-certificates.crt") == 0
                || strstr(source, "/.git/") != NULL
                || (strlen(source) >= 5U
                    && strcmp(source + strlen(source) - 5U, "/.git") == 0));
    }
#ifdef ODYSSEUS_LAUNCHER_TESTING
    return strcmp(destination, "/run/odysseus/command.sh") == 0
        && stat(source, &metadata) == 0
        && S_ISREG(metadata.st_mode)
        && metadata.st_nlink == 1;
#else
    char resolved[PATH_MAX];
    const char *filename = strrchr(source, '/');
    const char *jobs = strstr(source, "/bg_jobs/");
    return strcmp(destination, "/run/odysseus/command.sh") == 0
        && filename != NULL
        && strlen(filename + 1) == 19U
        && strcmp(filename + 13, ".cmd.sh") == 0
        && jobs != NULL
        && realpath(source, resolved) != NULL
        && strcmp(source, resolved) == 0
        && stat(source, &metadata) == 0
        && S_ISREG(metadata.st_mode)
        && metadata.st_uid == getuid()
        && metadata.st_nlink == 1
        && (metadata.st_mode & (S_IRWXG | S_IRWXO)) == 0;
#endif
}

static bool validate_bind(
    struct bwrap_contract *contract,
    const char *source,
    const char *destination
)
{
    if (!safe_absolute_path(source) || strcmp(source, destination) != 0
        || !canonical_existing_path(source)
        || sensitive_mount_source(source)
        || same_resolved_path(source, TRUSTED_BWRAP)) {
        return false;
    }
    contract->writable_binds++;
    if (strcmp(source, "/") == 0) {
        contract->full_access = true;
        contract->writable_root = source;
        return true;
    }
    static const char *const system_roots[] = {
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/proc",
        "/root",
        "/run",
        "/sys",
        "/usr",
    };
    for (size_t index = 0;
         index < sizeof(system_roots) / sizeof(system_roots[0]);
         index++) {
        if (path_at_or_below(source, system_roots[index])) {
            return false;
        }
    }
    bool allowed = strcmp(source, "/home") != 0
        && strcmp(source, "/opt") != 0
        && strcmp(source, "/srv") != 0
        && strcmp(source, "/tmp") != 0
        && strcmp(source, "/var") != 0;
    if (allowed) {
        contract->writable_root = source;
    }
    return allowed;
}

static bool validate_symlink(
    struct bwrap_contract *contract,
    const char *source,
    const char *destination
)
{
    if (strcmp(source, "usr/bin") == 0 && strcmp(destination, "/bin") == 0) {
        return set_once(&contract->symlink_bin);
    }
    if (strcmp(source, "usr/lib") == 0 && strcmp(destination, "/lib") == 0) {
        return set_once(&contract->symlink_lib);
    }
    if (strcmp(source, "usr/lib64") == 0
        && strcmp(destination, "/lib64") == 0) {
        return set_once(&contract->symlink_lib64);
    }
    return false;
}

static int validate_bwrap_arguments(int argc, char **argv)
{
    struct bwrap_contract contract = {0};
    int index = 2;
    while (index < argc) {
        const char *option = argv[index];
        if (strcmp(option, "--") == 0) {
            break;
        }
#define FIXED_FLAG(name, field) \
        if (strcmp(option, name) == 0) { \
            if (!set_once(&contract.field)) { \
                return -1; \
            } \
            index++; \
            continue; \
        }
        FIXED_FLAG("--unshare-user", unshare_user)
        FIXED_FLAG("--unshare-ipc", unshare_ipc)
        FIXED_FLAG("--unshare-pid", unshare_pid)
        FIXED_FLAG("--unshare-net", unshare_net)
        FIXED_FLAG("--unshare-uts", unshare_uts)
        FIXED_FLAG("--unshare-cgroup", unshare_cgroup)
        FIXED_FLAG("--die-with-parent", die_with_parent)
        FIXED_FLAG("--new-session", new_session)
        FIXED_FLAG("--clearenv", clearenv)
#undef FIXED_FLAG
        if (strcmp(option, "--cap-drop") == 0) {
            if (index + 1 >= argc || strcmp(argv[index + 1], "ALL") != 0
                || !set_once(&contract.cap_drop)) {
                return -1;
            }
            index += 2;
            continue;
        }
        if (strcmp(option, "--setenv") == 0) {
            if (index + 2 >= argc
                || !allowed_environment_name(
                    argv[index + 1],
                    &contract.environment_names
                )
                || strlen(argv[index + 2]) > MAX_SETENV_VALUE) {
                return -1;
            }
            index += 3;
            continue;
        }
        if (strcmp(option, "--ro-bind") == 0) {
            if (index + 2 >= argc
                || !validate_ro_bind(
                    &contract,
                    argv[index + 1],
                    argv[index + 2]
                )) {
                return -1;
            }
            index += 3;
            continue;
        }
        if (strcmp(option, "--bind") == 0) {
            if (index + 2 >= argc
                || !validate_bind(
                    &contract,
                    argv[index + 1],
                    argv[index + 2]
                )) {
                return -1;
            }
            index += 3;
            continue;
        }
        if (strcmp(option, "--symlink") == 0) {
            if (index + 2 >= argc
                || !validate_symlink(
                    &contract,
                    argv[index + 1],
                    argv[index + 2]
                )) {
                return -1;
            }
            index += 3;
            continue;
        }
        if (strcmp(option, "--dev") == 0) {
            if (index + 1 >= argc || strcmp(argv[index + 1], "/dev") != 0
                || !set_once(&contract.dev_mount)) {
                return -1;
            }
            index += 2;
            continue;
        }
        if (strcmp(option, "--proc") == 0) {
            if (index + 1 >= argc || strcmp(argv[index + 1], "/proc") != 0
                || !set_once(&contract.proc_mount)) {
                return -1;
            }
            index += 2;
            continue;
        }
        if (strcmp(option, "--tmpfs") == 0 || strcmp(option, "--dir") == 0) {
            if (index + 1 >= argc || !safe_absolute_path(argv[index + 1])) {
                return -1;
            }
            index += 2;
            continue;
        }
        if (strcmp(option, "--chdir") == 0) {
            if (index + 1 >= argc || !safe_absolute_path(argv[index + 1])
                || !set_once(&contract.chdir)) {
                return -1;
            }
            index += 2;
            continue;
        }
        return -1;
    }
    bool fixed_isolation = contract.unshare_user
        && contract.unshare_ipc
        && contract.unshare_pid
        && contract.unshare_net
        && contract.unshare_uts
        && contract.unshare_cgroup
        && contract.die_with_parent
        && contract.new_session
        && contract.cap_drop
        && contract.proc_mount
        && contract.chdir;
    bool full_access_mounts = contract.writable_binds == 1U
        && !contract.usr_runtime
        && !contract.dev_mount
        && !contract.symlink_bin
        && !contract.symlink_lib
        && !contract.symlink_lib64;
    bool sandbox_mounts = contract.writable_binds == 1U
        && contract.usr_runtime
        && contract.dev_mount
        && contract.symlink_bin
        && contract.symlink_lib;
    bool mount_profile = contract.full_access
        ? full_access_mounts
        : sandbox_mounts;
    bool separated_runtime_mounts = contract.writable_root != NULL
        && (contract.native_venv_source == NULL
            || (!path_at_or_below(
                    contract.native_venv_source,
                    contract.writable_root
                )
                && !path_at_or_below(
                    contract.writable_root,
                    contract.native_venv_source
                )))
        && (contract.broker_runtime_source == NULL
            || (!path_at_or_below(
                    contract.broker_runtime_source,
                    contract.writable_root
                )
                && !path_at_or_below(
                    contract.writable_root,
                    contract.broker_runtime_source
                )));
    return fixed_isolation && mount_profile && separated_runtime_mounts
        && index + 1 < argc ? index : -1;
}

static bool seal_filter_fd(int filter_fd)
{
    const int required = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    if (injected_failure("seal") || lseek(filter_fd, 0, SEEK_SET) < 0) {
        return false;
    }
    if (fcntl(filter_fd, F_ADD_SEALS, required) < 0) {
        return false;
    }
    int actual = fcntl(filter_fd, F_GET_SEALS);
    return actual >= 0 && (actual & required) == required;
}

#ifdef ODYSSEUS_LAUNCHER_TESTING
static bool inspect_filter_fd(int filter_fd)
{
    static const char memfd_prefix[] = "/memfd:odysseus-inner-seccomp";
    struct stat metadata;
    char link_path[64];
    char target[128];
    int written = snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", filter_fd);
    if (written < 0 || (size_t)written >= sizeof(link_path)) {
        return false;
    }
    ssize_t length = readlink(link_path, target, sizeof(target) - 1);
    if (length < 0 || (size_t)length >= sizeof(target)) {
        return false;
    }
    target[length] = '\0';
    return fstat(filter_fd, &metadata) == 0
        && S_ISREG(metadata.st_mode)
        && strncmp(target, memfd_prefix, strlen(memfd_prefix)) == 0;
}
#endif

static bool retain_only_filter_fd(int filter_fd)
{
    if (filter_fd != FILTER_FD) {
        if (dup3(filter_fd, FILTER_FD, 0) < 0) {
            return false;
        }
        (void)close(filter_fd);
    } else if (fcntl(FILTER_FD, F_SETFD, 0) < 0) {
        return false;
    }

#ifdef SYS_close_range
    if (syscall(SYS_close_range, 4U, UINT_MAX, 0U) == 0) {
        return true;
    }
    if (errno != ENOSYS && errno != EINVAL) {
        return false;
    }
#endif

    struct rlimit limit;
    if (getrlimit(RLIMIT_NOFILE, &limit) < 0) {
        return false;
    }
    rlim_t maximum = limit.rlim_cur == RLIM_INFINITY ? 1048576U : limit.rlim_cur;
    for (rlim_t descriptor = 4; descriptor < maximum; descriptor++) {
        (void)close((int)descriptor);
    }
    return true;
}

int main(int argc, char **argv)
{
    if (argc < 4 || !valid_bwrap(argv[1])) {
        fail_message("invalid trusted Bubblewrap path");
        return EXIT_INVALID_BWRAP;
    }
    int separator = validate_bwrap_arguments(argc, argv);
    if (separator < 2 || separator + 1 >= argc) {
        fail_message("invalid Bubblewrap arguments");
        return EXIT_INVALID_ARGUMENTS;
    }

    struct seccomp_api api = {0};
    if (!load_seccomp(&api)) {
        if (api.handle != NULL) {
            (void)dlclose(api.handle);
        }
        fail_message("libseccomp is unavailable or incompatible");
        return EXIT_LIBSECCOMP;
    }
    scmp_filter_ctx filter = build_filter(&api);
    if (filter == NULL) {
        (void)dlclose(api.handle);
        fail_message("inner seccomp filter creation failed");
        return EXIT_FILTER;
    }

    int filter_fd = injected_failure("memfd")
        ? -1
        : memfd_create(
            "odysseus-inner-seccomp",
            MFD_CLOEXEC | MFD_ALLOW_SEALING
        );
    if (filter_fd < 0) {
        api.release(filter);
        (void)dlclose(api.handle);
        fail_message("anonymous filter storage creation failed");
        return EXIT_MEMFD;
    }
    if (injected_failure("export") || api.export_bpf(filter, filter_fd) < 0) {
        (void)close(filter_fd);
        api.release(filter);
        (void)dlclose(api.handle);
        fail_message("inner seccomp filter export failed");
        return EXIT_EXPORT;
    }
    if (!seal_filter_fd(filter_fd)) {
        (void)close(filter_fd);
        api.release(filter);
        (void)dlclose(api.handle);
        fail_message("inner seccomp filter sealing failed");
        return EXIT_SEAL;
    }

#ifdef ODYSSEUS_LAUNCHER_TESTING
    if (injected_failure("inspect")) {
        bool valid = inspect_filter_fd(filter_fd);
        (void)close(filter_fd);
        api.release(filter);
        (void)dlclose(api.handle);
        return valid ? 0 : EXIT_SEAL;
    }
#endif

    api.release(filter);
    (void)dlclose(api.handle);
    if (!retain_only_filter_fd(filter_fd)) {
        fail_message("inner seccomp filter descriptor setup failed");
        return EXIT_SEAL;
    }

    char descriptor[16];
    int descriptor_length = snprintf(descriptor, sizeof(descriptor), "%d", FILTER_FD);
    if (descriptor_length <= 0 || (size_t)descriptor_length >= sizeof(descriptor)) {
        fail_message("inner seccomp filter descriptor setup failed");
        return EXIT_SEAL;
    }
    char **bwrap_argv = calloc((size_t)argc + 7U, sizeof(*bwrap_argv));
    if (bwrap_argv == NULL) {
        fail_message("inner seccomp filter creation failed");
        return EXIT_FILTER;
    }
    int output = 0;
    bwrap_argv[output++] = argv[1];
    bwrap_argv[output++] = "--clearenv";
    for (int index = 2; index < separator; index++) {
        if (strcmp(argv[index], "--clearenv") == 0) {
            continue;
        }
        bwrap_argv[output++] = argv[index];
    }
    bwrap_argv[output++] = "--seccomp";
    bwrap_argv[output++] = descriptor;
    /* The outer OCI filter necessarily permits Bubblewrap's namespace and
     * mount bootstrap. Hide the trusted executable before the payload starts;
     * a copied implementation is still stopped by the loaded inner filter. */
    bwrap_argv[output++] = "--ro-bind";
    bwrap_argv[output++] = "/dev/null";
    bwrap_argv[output++] = TRUSTED_BWRAP;
    for (int index = separator; index < argc; index++) {
        bwrap_argv[output++] = argv[index];
    }
    bwrap_argv[output] = NULL;

    if (injected_failure("exec")) {
        errno = ENOENT;
    } else {
        char *const clean_environment[] = {NULL};
        execve(TRUSTED_BWRAP, bwrap_argv, clean_environment);
    }
    free(bwrap_argv);
    fail_message("trusted Bubblewrap execution failed");
    return EXIT_EXEC;
}
