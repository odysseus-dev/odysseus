PYTHON ?= python3
PYTHON_PATH := $(shell realpath "$$(command -v $(PYTHON))")
INSTALL_DIR := /usr/local/libexec
INSTALL_OWNER ?= root
INSTALL_GROUP ?= root
BROKER := odysseus_egress_broker.py
BRIDGE := odysseus_egress_bridge.py

.PHONY: check install

check:
	test -n "$(PYTHON_PATH)" -a -x "$(PYTHON_PATH)"
	$(PYTHON) -m py_compile $(BROKER) $(BRIDGE)

install: check
	install -d -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(INSTALL_DIR)
	install -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(BROKER) $(INSTALL_DIR)/odysseus-egress-broker
	install -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0755 $(BRIDGE) $(INSTALL_DIR)/odysseus-egress-bridge
	sed -i '1c\#!$(PYTHON_PATH) -I' $(INSTALL_DIR)/odysseus-egress-broker
	sed -i '1c\#!$(PYTHON_PATH) -I' $(INSTALL_DIR)/odysseus-egress-bridge
	chown $(INSTALL_OWNER):$(INSTALL_GROUP) $(INSTALL_DIR)/odysseus-egress-broker $(INSTALL_DIR)/odysseus-egress-bridge
	chmod 0755 $(INSTALL_DIR)/odysseus-egress-broker $(INSTALL_DIR)/odysseus-egress-bridge
