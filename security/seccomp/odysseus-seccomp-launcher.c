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
#define FILTER_FD 3

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
    static const uint64_t socket_families[] = {AF_UNIX, AF_INET, AF_INET6};
    static const uint64_t socketpair_families[] = {AF_UNIX};
    static const uint64_t personality_values[] = {
        0,
        8,
        131072,
        131080,
        UINT32_MAX,
    };
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
    scmp_filter_ctx filter = api->init(SCMP_ACT_ERRNO(EPERM));
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
        || add_rule(api, filter, SCMP_ACT_ERRNO(ENOSYS), "clone3", 0, NULL) < 0
        /* The action must differ from the default EPERM for libseccomp to
         * retain a masked deny rule alongside the compatibility allow rule.
         * Add the deny first: affected libseccomp releases can otherwise
         * weaken an overlapping 64-bit comparison while merging the tree. */
        || add_rule(
            api,
            filter,
            SCMP_ACT_ERRNO(EACCES),
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
            personality_values,
            sizeof(personality_values) / sizeof(personality_values[0])
        ) < 0
        || add_exact_argument_rules(
            api,
            filter,
            "socket",
            0,
            socket_families,
            sizeof(socket_families) / sizeof(socket_families[0])
        ) < 0
        || add_exact_argument_rules(
            api,
            filter,
            "socketpair",
            0,
            socketpair_families,
            sizeof(socketpair_families) / sizeof(socketpair_families[0])
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

static bool forbidden_seccomp_option(const char *argument)
{
    return strcmp(argument, "--seccomp") == 0
        || strncmp(argument, "--seccomp=", 10) == 0
        || strcmp(argument, "--add-seccomp-fd") == 0
        || strncmp(argument, "--add-seccomp-fd=", 17) == 0
        /* An args file could smuggle either seccomp option past this scan. */
        || strcmp(argument, "--args") == 0
        || strncmp(argument, "--args=", 7) == 0;
}

static int find_command_separator(int argc, char **argv)
{
    for (int index = 2; index < argc; index++) {
        if (strcmp(argv[index], "--") == 0) {
            return index;
        }
        if (forbidden_seccomp_option(argv[index])) {
            return -2;
        }
    }
    return -1;
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
    int separator = find_command_separator(argc, argv);
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
    char **bwrap_argv = calloc((size_t)argc + 2U, sizeof(*bwrap_argv));
    if (bwrap_argv == NULL) {
        fail_message("inner seccomp filter creation failed");
        return EXIT_FILTER;
    }
    int output = 0;
    for (int index = 1; index < separator; index++) {
        bwrap_argv[output++] = argv[index];
    }
    bwrap_argv[output++] = "--seccomp";
    bwrap_argv[output++] = descriptor;
    for (int index = separator; index < argc; index++) {
        bwrap_argv[output++] = argv[index];
    }
    bwrap_argv[output] = NULL;

    if (injected_failure("exec")) {
        errno = ENOENT;
    } else {
        execv(TRUSTED_BWRAP, bwrap_argv);
    }
    free(bwrap_argv);
    fail_message("trusted Bubblewrap execution failed");
    return EXIT_EXEC;
}
