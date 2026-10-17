PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin

.PHONY: all test install uninstall user-install user-uninstall

all:

test:
	python3 test_pingha.py

user-install:
	$(MAKE) install PREFIX=$${HOME}/.local

user-uninstall:
	$(MAKE) uninstall PREFIX=$${HOME}/.local

install:
	install -d $(BINDIR)
	install -m 755 pingha $(BINDIR)/pingha

uninstall:
	rm -f $(BINDIR)/pingha
