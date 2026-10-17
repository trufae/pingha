PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin

.PHONY: all install uninstall user-install user-uninstall

all:

user-install:
	$(MAKE) install PREFIX=$${HOME}/.local

user-uninstall:
	$(MAKE) uninstall PREFIX=$${HOME}/.local

install:
	install -d $(BINDIR)
	install -m 755 pingha $(BINDIR)/pingha

uninstall:
	rm -f $(BINDIR)/pingha
