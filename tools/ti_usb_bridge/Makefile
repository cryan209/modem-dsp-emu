CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra
OUT ?= ../../artifacts/ti_usb_bridge/bridge

all:
	mkdir -p $(dir $(OUT))
	$(CC) $(CFLAGS) bridge.c -framework IOKit -framework CoreFoundation -o $(OUT)

.PHONY: all
