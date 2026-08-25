PYTHON ?= python3
PYTHON_PATH = $(shell realpath "$$(command -v $(PYTHON))")
override SHELL := /bin/sh
override .SHELLFLAGS := -eu -c
override TRUSTED_PYTHON := $(firstword $(realpath /usr/local/bin/python3 /usr/bin/python3))
override INSTALL_DIR := /usr/local/libexec
override INSTALL_OWNER := root
override INSTALL_GROUP := root
override INSTALL_TOOL := /usr/bin/install
override SED_TOOL := /usr/bin/sed
override CHOWN_TOOL := /usr/bin/chown
override CHMOD_TOOL := /usr/bin/chmod
override BROKER := odysseus_egress_broker.py
override BRIDGE := odysseus_egress_bridge.py

.PHONY: check trusted-check install

check:
	test -n "$(PYTHON_PATH)" -a -x "$(PYTHON_PATH)"
	$(PYTHON) -m py_compile $(BROKER) $(BRIDGE)

trusted-check:
	test -n "$(TRUSTED_PYTHON)" -a -x "$(TRUSTED_PYTHON)"
	test -z "$$($(realpath /usr/bin/find) "$(TRUSTED_PYTHON)" -maxdepth 0 \( ! -type f -o ! -uid 0 -o -perm /022 -o -perm /6000 \) -print -quit)"
	"$(TRUSTED_PYTHON)" -I -m py_compile $(BROKER) $(BRIDGE)

install: trusted-check
	$(INSTALL_TOOL) -d -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(INSTALL_DIR)
	$(INSTALL_TOOL) -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(BROKER) $(INSTALL_DIR)/odysseus-egress-broker
	$(INSTALL_TOOL) -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(BRIDGE) $(INSTALL_DIR)/odysseus-egress-bridge
	$(SED_TOOL) -i '1c\#!$(TRUSTED_PYTHON) -I' $(INSTALL_DIR)/odysseus-egress-broker
	$(SED_TOOL) -i '1c\#!$(TRUSTED_PYTHON) -I' $(INSTALL_DIR)/odysseus-egress-bridge
	$(CHOWN_TOOL) $(INSTALL_OWNER):$(INSTALL_GROUP) $(INSTALL_DIR)/odysseus-egress-broker $(INSTALL_DIR)/odysseus-egress-bridge
	$(CHMOD_TOOL) 0755 $(INSTALL_DIR)/odysseus-egress-broker $(INSTALL_DIR)/odysseus-egress-bridge
