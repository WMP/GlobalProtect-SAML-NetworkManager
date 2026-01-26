# Makefile for GNOME NetworkManager VPN plugins

CC = gcc
PLUGIN_CFLAGS = `pkg-config --cflags glib-2.0 libnm` -Wall -fPIC
PLUGIN_LIBS = `pkg-config --libs glib-2.0 libnm` -ldl
EDITOR_GTK3_CFLAGS = `pkg-config --cflags glib-2.0 libnm gtk+-3.0 libnma` -Wall -fPIC
EDITOR_GTK3_LIBS = `pkg-config --libs glib-2.0 libnm gtk+-3.0 libnma`
EDITOR_GTK4_CFLAGS = `pkg-config --cflags glib-2.0 libnm gtk4 libnma-gtk4` -Wall -fPIC
EDITOR_GTK4_LIBS = `pkg-config --libs glib-2.0 libnm gtk4 libnma-gtk4`

# Check if libnma-gtk4 is available
HAS_GTK4 := $(shell pkg-config --exists libnma-gtk4 && echo yes)

# Set default targets based on GTK4 availability
ifeq ($(HAS_GTK4),yes)
ALL_TARGETS = libnm-vpn-plugin-gpclient.so libnm-vpn-plugin-gpclient-editor.so libnm-gtk4-vpn-plugin-gpclient-editor.so
else
ALL_TARGETS = libnm-vpn-plugin-gpclient.so libnm-vpn-plugin-gpclient-editor.so
endif

all: $(ALL_TARGETS)

libnm-vpn-plugin-gpclient.so: nm-gpclient-plugin-simple.c nm-vpn-plugin-utils.c
	$(CC) $(PLUGIN_CFLAGS) -shared -Wl,-soname,libnm-vpn-plugin-gpclient.so \
		-o libnm-vpn-plugin-gpclient.so nm-gpclient-plugin-simple.c nm-vpn-plugin-utils.c $(PLUGIN_LIBS)

libnm-vpn-plugin-gpclient-editor.so: nm-gpclient-editor-plugin.c nm-gpclient-editor-gtk3.c
	$(CC) $(EDITOR_GTK3_CFLAGS) -shared -Wl,-soname,libnm-vpn-plugin-gpclient-editor.so \
		-o libnm-vpn-plugin-gpclient-editor.so \
		nm-gpclient-editor-plugin.c nm-gpclient-editor-gtk3.c $(EDITOR_GTK3_LIBS)

libnm-gtk4-vpn-plugin-gpclient-editor.so: nm-gpclient-editor-plugin.c nm-gpclient-editor.c
	$(CC) $(EDITOR_GTK4_CFLAGS) -shared -Wl,-soname,libnm-gtk4-vpn-plugin-gpclient-editor.so \
		-o libnm-gtk4-vpn-plugin-gpclient-editor.so \
		nm-gpclient-editor-plugin.c nm-gpclient-editor.c $(EDITOR_GTK4_LIBS)

install: all
	install -D -m 644 libnm-vpn-plugin-gpclient.so /usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-vpn-plugin-gpclient.so
	install -D -m 644 libnm-vpn-plugin-gpclient-editor.so /usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-vpn-plugin-gpclient-editor.so
	install -D -m 644 nm-gpclient-service.name /usr/lib/NetworkManager/VPN/nm-gpclient-service.name
	# Install GTK4 plugin only if it was built
	if [ -f libnm-gtk4-vpn-plugin-gpclient-editor.so ]; then \
		install -D -m 644 libnm-gtk4-vpn-plugin-gpclient-editor.so /usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-gtk4-vpn-plugin-gpclient-editor.so; \
	fi
	# Create symlink for properties
	ln -sf /usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-vpn-plugin-gpclient-editor.so /usr/lib/libnm-gpclient-properties

clean:
	rm -f libnm-vpn-plugin-gpclient.so libnm-vpn-plugin-gpclient-editor.so libnm-gtk4-vpn-plugin-gpclient-editor.so

.PHONY: all install clean
