# Main Makefile - delegates GNOME plugin builds to plugins/gnome/Makefile

# Source directories
GNOME_DIR = plugins/gnome
GPOC_DIR = external/GlobalProtect-openconnect
RUSTUP_HOME ?= $(HOME)/.rustup
CARGO_HOME ?= $(HOME)/.cargo
CARGO = $(CARGO_HOME)/bin/cargo
RUST_VERSION = 1.91

# Main targets
all: gnome-plugins gpclient gpauth

# Build GNOME plugins by calling their Makefile
.PHONY: gnome-plugins
gnome-plugins:
	$(MAKE) -C $(GNOME_DIR) all
	# Copy built plugins to root for debian packaging
	cp $(GNOME_DIR)/*.so . 2>/dev/null || true

# Translations (docs/TRANSLATIONS.md): po/LINGUAS lists the languages. Every
# language has po/<lang>.po (domain NetworkManager-gpclient, GNOME editors and
# auth dialog) and plugins/plasma/po/<lang>/plasmanetworkmanagement_gpclientui.po
# (Plasma editor).
LINGUAS := $(shell sed -e 's/\#.*//' po/LINGUAS 2>/dev/null)
GNOME_DOMAIN = NetworkManager-gpclient
PLASMA_DOMAIN = plasmanetworkmanagement_gpclientui
LOCALE_BUILD = build/locale
LOCALEDIR ?= /usr/share/locale

# Compile the .po files to $(LOCALE_BUILD)/<lang>/LC_MESSAGES/<domain>.mo; a
# wrong placeholder or syntax error fails the build
.PHONY: mo
mo:
	@set -e; for lang in $(LINGUAS); do \
		mkdir -p $(LOCALE_BUILD)/$$lang/LC_MESSAGES; \
		msgfmt --check-format -c -o $(LOCALE_BUILD)/$$lang/LC_MESSAGES/$(GNOME_DOMAIN).mo po/$$lang.po; \
		msgfmt --check-format -c -o $(LOCALE_BUILD)/$$lang/LC_MESSAGES/$(PLASMA_DOMAIN).mo plugins/plasma/po/$$lang/$(PLASMA_DOMAIN).po; \
	done

# Install the .mo files of one domain below $(DESTDIR)$(LOCALEDIR):
#   make install-mo MO_DOMAIN=NetworkManager-gpclient DESTDIR=<root>
# The packages split the two domains (core and Plasma package), so the domain is a parameter.
.PHONY: install-mo
install-mo: mo
	@test -n "$(MO_DOMAIN)" || { echo "install-mo: pass MO_DOMAIN=$(GNOME_DOMAIN) or MO_DOMAIN=$(PLASMA_DOMAIN)" >&2; exit 1; }
	@set -e; for lang in $(LINGUAS); do \
		install -D -m 644 $(LOCALE_BUILD)/$$lang/LC_MESSAGES/$(MO_DOMAIN).mo \
			$(DESTDIR)$(LOCALEDIR)/$$lang/LC_MESSAGES/$(MO_DOMAIN).mo; \
	done

# Refresh the .pot templates from the sources and merge them into the .po files
.PHONY: pot
pot:
	python3 scripts/update_pot.py

# Build gpclient and gpauth from GlobalProtect-openconnect
.PHONY: setup-rust
setup-rust:
	@if command -v cargo >/dev/null 2>&1; then \
		echo "Using existing Rust installation: $$(cargo --version)"; \
	elif [ -f $(CARGO) ]; then \
		echo "Using Rust from $(CARGO): $$($(CARGO) --version)"; \
	else \
		echo "Installing Rust $(RUST_VERSION) via rustup..."; \
		curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain $(RUST_VERSION); \
	fi

# A real checkout: sync the URLs and update the nested submodules to the pinned
# commits (a no-op when they are right), so a bump of the pin that moves a
# nested submodule does not leave the old sources. Sources copied into the
# build container have no usable git (.dockerignore drops the root .git): keep
# them when the nested sources are there.
.PHONY: init-submodules
init-submodules:
	@if [ -e $(GPOC_DIR)/.git ] && git -C $(GPOC_DIR) rev-parse --git-dir >/dev/null 2>&1; then \
		echo "Updating GlobalProtect-openconnect submodules..."; \
		cd $(GPOC_DIR) && git submodule sync --recursive && git submodule update --init --recursive; \
	elif [ ! -f $(GPOC_DIR)/crates/openconnect/deps/openconnect/.git ]; then \
		echo "Initializing GlobalProtect-openconnect submodules..."; \
		cd $(GPOC_DIR) && git submodule update --init --recursive; \
	fi

gpclient: setup-rust init-submodules
	cd $(GPOC_DIR) && export PATH="$$PATH:$(CARGO_HOME)/bin" && \
		if cargo auditable --version >/dev/null 2>&1; then cargo auditable build --locked --release --bin gpclient; \
		else echo "cargo-auditable not found: the binary has no dependency list"; cargo build --locked --release --bin gpclient; fi
	cp $(GPOC_DIR)/target/release/gpclient .

gpauth: setup-rust init-submodules
	cd $(GPOC_DIR) && export PATH="$$PATH:$(CARGO_HOME)/bin" && \
		if cargo auditable --version >/dev/null 2>&1; then cargo auditable build --locked --release --bin gpauth; \
		else echo "cargo-auditable not found: the binary has no dependency list"; cargo build --locked --release --bin gpauth; fi
	cp $(GPOC_DIR)/target/release/gpauth .

install: all
	# Install GNOME plugins
	$(MAKE) -C $(GNOME_DIR) install
	# Install the translations of the GNOME plugins and the auth dialog
	$(MAKE) install-mo MO_DOMAIN=$(GNOME_DOMAIN)
	# Install Python service
	install -D -m 755 service/nm-gpclient-service.py /usr/lib/NetworkManager/nm-gpclient-service
	# Install scripts
	install -D -m 755 scripts/browser-wrapper.sh /usr/libexec/gpclient/browser-wrapper
	install -D -m 755 scripts/edge-wrapper.sh /usr/libexec/gpclient/edge-wrapper
	# Install auth dialog (interactive credentials/RSA token prompts)
	install -D -m 755 auth-dialog/nm-gpclient-auth-dialog.py /usr/libexec/nm-gpclient-auth-dialog
	# Install gpclient and gpauth binaries
	install -D -m 755 gpclient /usr/bin/gpclient
	install -D -m 755 gpauth /usr/bin/gpauth
	# Install vpnc hook for routing configuration
	install -D -m 755 config/90-gpclient-routing /etc/vpnc/connect.d/90-gpclient-routing
	install -D -m 755 config/90-gpclient-routing /etc/vpnc/disconnect.d/90-gpclient-routing
	install -D -m 755 config/gpclient-nm-notify /etc/vpnc/post-connect.d/90-gpclient-notify
	install -D -m 755 config/gpclient-nm-notify /etc/vpnc/reconnect.d/90-gpclient-notify
	install -D -m 755 config/gpclient-nm-script /usr/lib/NetworkManager/gpclient-nm-script
	# Install D-Bus policy, D-Bus activation file and systemd unit
	install -D -m 644 config/nm-gpclient.conf /usr/share/dbus-1/system.d/nm-gpclient.conf
	install -D -m 644 config/org.freedesktop.NetworkManager.gpclient.service /usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service
	install -D -m 644 config/nm-gpclient.service /lib/systemd/system/nm-gpclient.service

clean:
	$(MAKE) -C $(GNOME_DIR) clean
	rm -f *.so gpclient gpauth
	rm -rf $(LOCALE_BUILD)

.PHONY: all install clean

# === DEVELOPMENT INSTALL (for testing without building .deb) ===
.PHONY: install-dev uninstall-dev restart-nm

# Install paths
# Multiarch triplet (x86_64-linux-gnu, aarch64-linux-gnu, ...); override on the command line if needed
# Evaluated once (a recursive `?=` would run the shell on every use); no error
# when it is empty, so `make clean` still works without dpkg/gcc
ifeq ($(origin MULTIARCH),undefined)
MULTIARCH := $(shell dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || gcc -print-multiarch 2>/dev/null)
endif
# So $(MAKE) -C plugins/gnome reuses this value instead of computing it again
export MULTIARCH
NM_VPN_DIR ?= /usr/lib/$(MULTIARCH)/NetworkManager
# An empty MULTIARCH turns NM_VPN_DIR into /usr/lib//NetworkManager and would
# install into the wrong place. Checked on the effective dir (not on MULTIARCH),
# so an explicit NM_VPN_DIR=<dir> still works without a triplet. Recipe-level, so
# `make clean` keeps working.
CHECK_NM_VPN_DIR = @case "$(NM_VPN_DIR)" in *//*) echo "NM_VPN_DIR=$(NM_VPN_DIR) has an empty multiarch part: pass MULTIARCH=<triplet> (e.g. x86_64-linux-gnu) or NM_VPN_DIR=<dir>" >&2; exit 1;; esac
NM_LIB_DIR = /usr/lib/NetworkManager
NM_LIBEXEC_DIR = /usr/libexec/gpclient
DBUS_SERVICES_DIR = /usr/share/dbus-1/system-services
DBUS_CONF_DIR = /usr/share/dbus-1/system.d
SYSTEMD_DIR = /lib/systemd/system
VPNC_DIR = /etc/vpnc/connect.d

# Build and install for development/testing (requires sudo)
install-dev: gnome-plugins mo
	$(CHECK_NM_VPN_DIR)
	@echo "=== Installing GlobalProtect plugin for development ==="
	@echo "This requires sudo privileges..."

	# Create directories
	sudo mkdir -p $(NM_VPN_DIR)
	sudo mkdir -p $(NM_LIB_DIR)
	sudo mkdir -p $(NM_LIBEXEC_DIR)
	sudo mkdir -p $(DBUS_SERVICES_DIR)
	sudo mkdir -p $(DBUS_CONF_DIR)
	sudo mkdir -p $(VPNC_DIR)

	# Install GNOME plugins (.so files)
	sudo install -m 644 $(GNOME_DIR)/libnm-vpn-plugin-gpclient.so $(NM_VPN_DIR)/
	sudo install -m 644 $(GNOME_DIR)/libnm-vpn-plugin-gpclient-editor.so $(NM_VPN_DIR)/
	@if [ -f $(GNOME_DIR)/libnm-gtk4-vpn-plugin-gpclient-editor.so ]; then \
		sudo install -m 644 $(GNOME_DIR)/libnm-gtk4-vpn-plugin-gpclient-editor.so $(NM_VPN_DIR)/; \
		echo "Installed GTK4 editor plugin"; \
	fi

	# Install VPN service name file
	sudo install -m 644 $(GNOME_DIR)/nm-gpclient-service.name $(NM_LIB_DIR)/VPN/

	# Create symlink for properties
	sudo ln -sf $(NM_VPN_DIR)/libnm-vpn-plugin-gpclient-editor.so /usr/lib/libnm-gpclient-properties

	# Install Python D-Bus service
	sudo install -m 755 service/nm-gpclient-service.py $(NM_LIB_DIR)/nm-gpclient-service

	# Install helper scripts
	sudo install -m 755 scripts/browser-wrapper.sh $(NM_LIBEXEC_DIR)/browser-wrapper
	sudo install -m 755 scripts/edge-wrapper.sh $(NM_LIBEXEC_DIR)/edge-wrapper

	# Install auth dialog (interactive credentials/RSA token prompts)
	sudo install -m 755 auth-dialog/nm-gpclient-auth-dialog.py /usr/libexec/nm-gpclient-auth-dialog

	# Install the translations of the GNOME plugins and the auth dialog
	sudo $(MAKE) install-mo MO_DOMAIN=$(GNOME_DOMAIN)

	# Install D-Bus configuration
	sudo install -m 644 config/org.freedesktop.NetworkManager.gpclient.service $(DBUS_SERVICES_DIR)/
	sudo install -m 644 config/nm-gpclient.conf $(DBUS_CONF_DIR)/

	# Install systemd service
	sudo install -m 644 config/nm-gpclient.service $(SYSTEMD_DIR)/

	# Install the vpnc hooks: routing, and the tunnel notification
	sudo install -m 755 config/90-gpclient-routing $(VPNC_DIR)/
	sudo install -D -m 755 config/90-gpclient-routing /etc/vpnc/disconnect.d/90-gpclient-routing
	sudo install -D -m 755 config/gpclient-nm-notify /etc/vpnc/post-connect.d/90-gpclient-notify
	sudo install -D -m 755 config/gpclient-nm-notify /etc/vpnc/reconnect.d/90-gpclient-notify
	sudo install -D -m 755 config/gpclient-nm-script $(NM_LIB_DIR)/gpclient-nm-script

	@echo "=== Installation complete ==="
	@echo "Run 'make restart-nm' to restart NetworkManager"

# Uninstall development files
uninstall-dev:
	$(CHECK_NM_VPN_DIR)
	@echo "=== Uninstalling GlobalProtect plugin ==="
	sudo rm -f $(NM_VPN_DIR)/libnm-vpn-plugin-gpclient.so
	sudo rm -f $(NM_VPN_DIR)/libnm-vpn-plugin-gpclient-editor.so
	sudo rm -f $(NM_VPN_DIR)/libnm-gtk4-vpn-plugin-gpclient-editor.so
	sudo rm -f $(NM_LIB_DIR)/VPN/nm-gpclient-service.name
	sudo rm -f $(NM_LIB_DIR)/nm-gpclient-service
	sudo rm -f $(NM_LIB_DIR)/gpclient-nm-script
	sudo rm -f /usr/lib/libnm-gpclient-properties
	sudo rm -rf $(NM_LIBEXEC_DIR)
	sudo rm -f /usr/libexec/nm-gpclient-auth-dialog
	sudo rm -f $(LOCALEDIR)/*/LC_MESSAGES/$(GNOME_DOMAIN).mo
	sudo rm -f $(DBUS_SERVICES_DIR)/org.freedesktop.NetworkManager.gpclient.service
	sudo rm -f $(DBUS_CONF_DIR)/nm-gpclient.conf
	sudo rm -f $(SYSTEMD_DIR)/nm-gpclient.service
	sudo rm -f $(VPNC_DIR)/90-gpclient-routing /etc/vpnc/disconnect.d/90-gpclient-routing
	sudo rm -f /etc/vpnc/post-connect.d/90-gpclient-notify /etc/vpnc/reconnect.d/90-gpclient-notify
	@echo "=== Uninstall complete ==="

# Restart NetworkManager to reload plugins
restart-nm:
	@echo "Restarting NetworkManager..."
	sudo systemctl daemon-reload
	sudo systemctl restart NetworkManager
	@sleep 2
	@echo "NetworkManager status:"
	@systemctl is-active NetworkManager

# === GUI TESTS (Dogtail/AT-SPI) ===
.PHONY: test-ui test-ui-editor test-ui-gnome screenshot logs clean-artifacts

ARTIFACTS_DIR = artifacts

$(ARTIFACTS_DIR):
	mkdir -p $(ARTIFACTS_DIR)

# Full test: build, install, restart NM, run GUI tests
test-ui: install-dev restart-nm $(ARTIFACTS_DIR)
	@echo "=== Running GUI tests ==="
	@echo "Checking environment..."
	@echo "XDG_SESSION_TYPE: $${XDG_SESSION_TYPE:-not set}"
	@echo "DISPLAY: $${DISPLAY:-not set}"
	python3 -m pytest tests/ -v --tb=short 2>&1 | tee $(ARTIFACTS_DIR)/test_ui.log || \
		($(MAKE) screenshot SCREEN_NAME=fail_screen && exit 1)

# Run unit tests (no GUI/X11 required)
.PHONY: test-unit
test-unit:
	python3 -m pytest tests/unit -v

# Run unit tests with the service's line coverage (fails below .coveragerc's
# fail_under, as the unit test workflow does)
.PHONY: test-coverage
test-coverage:
	python3 -m pytest tests/unit -q --cov

# Shell scripts of the repository: every file with a sh/bash shebang outside external/
# (tests/unit/test_shellcheck.py checks the list).
SHELL_SCRIPTS := \
	.github/scripts/build-apt-repo.sh \
	.github/scripts/build-arch-repo.sh \
	.github/scripts/build-rpm-repo.sh \
	.github/scripts/gui-smoke.sh \
	.github/scripts/neon-repo.sh \
	.github/scripts/upgrade-test.sh \
	.github/scripts/verify-release-assets.sh \
	arch/build.sh \
	arch/container-build.sh \
	arch/install-test.sh \
	arch/network-manager-gpclient.install \
	build-all.sh \
	config/90-gpclient-routing \
	config/gpclient-nm-notify \
	config/gpclient-nm-script \
	debian/network-manager-gpclient.postinst \
	debian/network-manager-gpclient.postrm \
	plugins/plasma/Messages.sh \
	plugins/plasma/build.sh \
	rpm/build.sh \
	rpm/container-build.sh \
	rpm/install-test.sh \
	scripts/browser-wrapper.sh \
	scripts/edge-wrapper.sh \
	scripts/hide-desktop-entry.sh \
	scripts/vendor-crates.sh \
	scripts/vpnc-script-debug \
	scripts/install-pr-build.sh \
	tests/e2e/files/loginctl \
	tests/e2e/files/make-certs.sh \
	tests/e2e/install-edge.sh \
	tests/e2e/install-plugin.sh \
	tests/e2e/lib.sh \
	tests/e2e/run.sh \
	tests/e2e/scenario-auth-cancel.sh \
	tests/e2e/scenario-gnome.sh \
	tests/e2e/scenario-kde.sh \
	tests/e2e/scenario-routing.sh \
	tests/e2e/scenario-saml-dialog.sh \
	tests/e2e/session/desktop.sh \
	tests/e2e/session/start.sh

# Lint the shell scripts with shellcheck (the dialect comes from the shebang);
# fails on any finding at warning level
.PHONY: shellcheck
shellcheck:
	shellcheck --severity=warning $(SHELL_SCRIPTS)

# Static analysis of the Python code: ruff and mypy, both configured in
# pyproject.toml (the lint job of tests.yml pins their versions)
.PHONY: lint
lint:
	ruff check .
	mypy

# Everything that runs without a desktop or network
.PHONY: check
check: lint shellcheck test-unit

# Run the upstream contract tests (see docs/UPSTREAM.md)
.PHONY: test-upstream
test-upstream:
	python3 -m pytest tests/upstream -q

# Run GUI tests without rebuilding (assumes plugin already installed)
test-ui-only: $(ARTIFACTS_DIR)
	@echo "=== Running GUI tests (no rebuild) ==="
	python3 -m pytest tests/ -v --tb=short 2>&1 | tee $(ARTIFACTS_DIR)/test_ui.log || \
		($(MAKE) screenshot SCREEN_NAME=fail_screen && exit 1)

# Run only nm-connection-editor tests (ETAP 1 - stable)
test-ui-editor: $(ARTIFACTS_DIR)
	python3 -m pytest tests/test_nm_connection_editor.py -v 2>&1 | tee $(ARTIFACTS_DIR)/test_editor.log

# Run only GNOME Settings tests (ETAP 2 - GTK4)
test-ui-gnome: $(ARTIFACTS_DIR)
	python3 -m pytest tests/test_gnome_settings.py -v 2>&1 | tee $(ARTIFACTS_DIR)/test_gnome.log

# Take screenshot (use SCREEN_NAME=name to customize filename)
screenshot: $(ARTIFACTS_DIR)
	gnome-screenshot -f $(ARTIFACTS_DIR)/$${SCREEN_NAME:-screen}.png 2>/dev/null || \
		import -window root $(ARTIFACTS_DIR)/$${SCREEN_NAME:-screen}.png 2>/dev/null || \
		scrot $(ARTIFACTS_DIR)/$${SCREEN_NAME:-screen}.png 2>/dev/null || \
		echo "No screenshot tool available (gnome-screenshot, import, or scrot)"

# Collect system logs for debugging
logs: $(ARTIFACTS_DIR)
	@echo "Collecting logs..."
	journalctl --user -n 300 --no-pager > $(ARTIFACTS_DIR)/journal_user.log 2>&1 || true
	journalctl -u NetworkManager -n 300 --no-pager > $(ARTIFACTS_DIR)/journal_nm.log 2>&1 || true
	nmcli general status > $(ARTIFACTS_DIR)/nmcli_status.txt 2>&1 || true
	nmcli connection show > $(ARTIFACTS_DIR)/nmcli_connections.txt 2>&1 || true
	@echo "Logs saved to $(ARTIFACTS_DIR)/"

# Clean test artifacts
clean-artifacts:
	rm -rf $(ARTIFACTS_DIR)/*
