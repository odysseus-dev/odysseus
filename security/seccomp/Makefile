CC ?= cc
CPPFLAGS := -D_FORTIFY_SOURCE=3
CFLAGS := -std=c11 -O2 -fPIE -fstack-protector-strong -Wall -Wextra -Wpedantic -Werror -Wformat=2 -Werror=format-security
LDFLAGS := -pie -Wl,-z,relro,-z,now,-z,noexecstack
LDLIBS := -ldl

BUILD_DIR := build
LAUNCHER := $(BUILD_DIR)/odysseus-seccomp-launcher
TEST_LAUNCHER := $(BUILD_DIR)/odysseus-seccomp-launcher-test
INSTALL_DIR := /usr/local/libexec

.PHONY: all check-generated clean install test-launcher

all: check-generated $(LAUNCHER)

check-generated:
	python3 generate.py --check --verify-arches

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(LAUNCHER): odysseus-seccomp-launcher.c generated_inner_policy.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@ $(LDFLAGS) $(LDLIBS)

test-launcher: check-generated $(TEST_LAUNCHER)

$(TEST_LAUNCHER): odysseus-seccomp-launcher.c generated_inner_policy.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) -DODYSSEUS_LAUNCHER_TESTING $(CFLAGS) $< -o $@ $(LDFLAGS) $(LDLIBS)

install: all
	install -d -o root -g root -m 0755 $(INSTALL_DIR)
	install -o root -g root -m 0755 $(LAUNCHER) $(INSTALL_DIR)/odysseus-seccomp-launcher

clean:
	rm -rf $(BUILD_DIR)
