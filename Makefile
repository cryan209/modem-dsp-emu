PYTHON ?= .venv/bin/python

.PHONY: build test test-portable test-local test-all

build:
	$(MAKE) -C tools/adsp2181emu all

# The default gate is deliberately independent of JIT access, local captures,
# QEMU, remote services, root privileges, and physical hardware.
test: test-portable

test-portable: build
	$(MAKE) -C tools/adsp2181emu test
	$(PYTHON) -m pytest -m portable

# Broader native baseline, excluding only the explicitly marked long training.
test-local: build
	$(MAKE) -C tools/adsp2181emu test
	$(PYTHON) tools/check_test_environment.py
	$(PYTHON) -m pytest -m 'not slow'

# Includes native MIPS/firmware integration and optional local-artifact tests.
# Missing optional inputs may skip; inspect the summary rather than hiding it.
test-all: build
	$(MAKE) -C tools/adsp2181emu test
	$(PYTHON) tools/check_test_environment.py
	$(PYTHON) -m pytest
