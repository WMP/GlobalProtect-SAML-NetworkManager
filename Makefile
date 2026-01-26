# Main Makefile - delegates GNOME plugin builds to plugins/gnome/Makefile

# Source directories
GNOME_DIR = plugins/gnome
GPOC_DIR = external/GlobalProtect-openconnect
RUSTUP_HOME ?= $(HOME)/.rustup
CARGO_HOME ?= $(HOME)/.cargo
CARGO = $(CARGO_HOME)/bin/cargo
RUST_VERSION = 1.85

# Main targets
all: gnome-plugins gpclient gpauth

# Build GNOME plugins by calling their Makefile
.PHONY: gnome-plugins
gnome-plugins:
	$(MAKE) -C $(GNOME_DIR) all
	# Copy built plugins to root for debian packaging
	cp $(GNOME_DIR)/*.so . 2>/dev/null || true

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

.PHONY: init-submodules
init-submodules:
	@if [ ! -f $(GPOC_DIR)/crates/openconnect/deps/openconnect/.git ]; then \
		echo "Initializing GlobalProtect-openconnect submodules..."; \
		cd $(GPOC_DIR) && git submodule update --init --recursive; \
	fi

gpclient: setup-rust init-submodules
	cd $(GPOC_DIR) && (command -v cargo >/dev/null 2>&1 && cargo build --release --bin gpclient --no-default-features || $(CARGO) build --release --bin gpclient --no-default-features)
	cp $(GPOC_DIR)/target/release/gpclient .

gpauth: setup-rust init-submodules
	cd $(GPOC_DIR) && (command -v cargo >/dev/null 2>&1 && cargo build --release --bin gpauth --no-default-features || $(CARGO) build --release --bin gpauth --no-default-features)
	cp $(GPOC_DIR)/target/release/gpauth .

install: all
	# Install GNOME plugins
	$(MAKE) -C $(GNOME_DIR) install
	# Install Python service
	install -D -m 755 service/nm-gpclient-service.py /usr/lib/NetworkManager/nm-gpclient-service
	# Install scripts
	install -D -m 755 scripts/edge-wrapper.sh /usr/libexec/gpclient/edge-wrapper
	# Install gpclient and gpauth binaries
	install -D -m 755 gpclient /usr/bin/gpclient
	install -D -m 755 gpauth /usr/bin/gpauth
	# Install vpnc hook for routing configuration
	install -D -m 755 config/90-gpclient-routing /etc/vpnc/connect.d/90-gpclient-routing

clean:
	$(MAKE) -C $(GNOME_DIR) clean
	rm -f *.so gpclient gpauth

.PHONY: all install clean

# === DEVELOPMENT INSTALL (for testing without building .deb) ===
.PHONY: install-dev uninstall-dev restart-nm

# Install paths
NM_VPN_DIR = /usr/lib/x86_64-linux-gnu/NetworkManager
NM_LIB_DIR = /usr/lib/NetworkManager
NM_LIBEXEC_DIR = /usr/libexec/gpclient
DBUS_SERVICES_DIR = /usr/share/dbus-1/system-services
DBUS_CONF_DIR = /etc/dbus-1/system.d
SYSTEMD_DIR = /lib/systemd/system
VPNC_DIR = /etc/vpnc/connect.d

# Build and install for development/testing (requires sudo)
install-dev: gnome-plugins
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
	sudo install -m 755 scripts/edge-wrapper.sh $(NM_LIBEXEC_DIR)/edge-wrapper

	# Install D-Bus configuration
	sudo install -m 644 config/org.freedesktop.NetworkManager.gpclient.service $(DBUS_SERVICES_DIR)/
	sudo install -m 644 config/nm-gpclient.conf $(DBUS_CONF_DIR)/

	# Install systemd service
	sudo install -m 644 config/nm-gpclient.service $(SYSTEMD_DIR)/

	# Install vpnc routing hook
	sudo install -m 755 config/90-gpclient-routing $(VPNC_DIR)/

	@echo "=== Installation complete ==="
	@echo "Run 'make restart-nm' to restart NetworkManager"

# Uninstall development files
uninstall-dev:
	@echo "=== Uninstalling GlobalProtect plugin ==="
	sudo rm -f $(NM_VPN_DIR)/libnm-vpn-plugin-gpclient.so
	sudo rm -f $(NM_VPN_DIR)/libnm-vpn-plugin-gpclient-editor.so
	sudo rm -f $(NM_VPN_DIR)/libnm-gtk4-vpn-plugin-gpclient-editor.so
	sudo rm -f $(NM_LIB_DIR)/VPN/nm-gpclient-service.name
	sudo rm -f $(NM_LIB_DIR)/nm-gpclient-service
	sudo rm -f /usr/lib/libnm-gpclient-properties
	sudo rm -rf $(NM_LIBEXEC_DIR)
	sudo rm -f $(DBUS_SERVICES_DIR)/org.freedesktop.NetworkManager.gpclient.service
	sudo rm -f $(DBUS_CONF_DIR)/nm-gpclient.conf
	sudo rm -f $(SYSTEMD_DIR)/nm-gpclient.service
	sudo rm -f $(VPNC_DIR)/90-gpclient-routing
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
