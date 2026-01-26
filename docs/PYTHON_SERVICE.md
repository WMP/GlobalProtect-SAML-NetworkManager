# Python VPN Service Implementation

## Overview

The `nm-gpclient-service` is a Python3-based VPN service that implements the NetworkManager VPN plugin interface via D-Bus.

## Architecture

### Components

1. **nm-gpclient-service.py** - Main VPN service
   - Implements D-Bus interface `org.freedesktop.NetworkManager.VPN.Plugin`
   - Communicates with NetworkManager via System Bus
   - Manages the `gpclient` process
   - Monitors tunnel interfaces (gpd0, tun0, tun1)

2. **D-Bus Interface**
   - Service name: `org.freedesktop.NetworkManager.gpclient`
   - Object path: `/org/freedesktop/NetworkManager/gpclient`
   - Interface: `org.freedesktop.NetworkManager.VPN.Plugin`

### D-Bus Methods

#### `Connect(a{sa{sv}})`
Starts VPN connection.

**Parameters from connection['data']:**
- `gateway` (required) - VPN gateway URL
- `browser` (optional) - browser path (default: `/usr/bin/firefox`)
- `dns` (optional) - DNS servers separated by semicolons (e.g., `8.8.8.8;8.8.4.4`)

**Process:**
1. Gets configuration from NetworkManager
2. Runs `gpclient connect --browser X --gateway Y Z`
3. Monitors gpclient stdout in separate thread
4. Checks for tunnel interface every 500ms
5. After detecting interface, emits `Ip4Config` signal

#### `Disconnect()`
Disconnects VPN.

**Process:**
1. Stops interface monitoring
2. Terminates gpclient process (SIGTERM, then SIGKILL if needed)
3. Runs `gpclient disconnect`
4. Clears internal state

#### `SetConfig(a{sv})`
Optional method for compatibility (not used).

#### `SetIp4Config(s)`
Optional method for compatibility (not used).

### D-Bus Signals

#### `StateChanged(u)`
Informs NetworkManager about service state change.

**States:**
- `1` - INIT
- `3` - STARTING
- `4` - STARTED (connected)
- `5` - STOPPING
- `6` - STOPPED

#### `Ip4Config(a{sv})`
Passes IP configuration to NetworkManager.

**Parameters:**
- `tundev` (string) - tunnel interface name (e.g., "gpd0")
- `dns` (array of uint32) - DNS servers as 32-bit integers

#### `Failure(u)`
Reports connection failure.

**Reasons:**
- `0` - LOGIN_FAILED
- `1` - CONNECT_FAILED
- `2` - BAD_IP_CONFIG

## Process Monitoring

### User Detection
When service runs as root (via NetworkManager), it detects real user:
```python
sudo_uid = os.environ.get('SUDO_UID')
sudo_user = os.environ.get('SUDO_USER')
```

### Environment for gpclient
```python
env['DISPLAY'] = ':0'  # For browser
env['SUDO_UID'] = str(real_uid)  # For gpclient
env['XAUTHORITY'] = '/home/user/.Xauthority'  # For X11
```

### Stdout Monitoring
Background thread reads gpclient stdout and detects messages:
- "ESP tunnel connected"
- "Connected to"
- "Tunnel is up"
- "VPN connected"

After detecting message, immediately checks for tunnel interface.

### Tunnel Interface Detection
Every 500ms checks for:
```
/sys/class/net/gpd0
/sys/class/net/tun0
/sys/class/net/tun1
```

After finding interface:
1. Builds IP configuration (interface name + DNS)
2. Emits `Ip4Config` signal
3. Emits `StateChanged(STARTED)` signal
4. Stops timer

## Installation

### Requirements
```
python3
python3-sdbus
openconnect
```

### Installation Paths
- Service: `/usr/lib/NetworkManager/nm-gpclient-service`
- Descriptor: `/usr/lib/NetworkManager/VPN/nm-gpclient-service.name`

### Permissions
Service must be executable (755) and will be run by NetworkManager.

## Manual Execution (Debugging)

### As root via D-Bus:
```bash
sudo /usr/lib/NetworkManager/nm-gpclient-service --debug
```

### Command-line options:
- `--persist` - don't close service after VPN disconnect
- `--debug` - enable detailed logging

### Testing connection via D-Bus:
```bash
# Check if service is registered
dbus-send --system --print-reply \
  --dest=org.freedesktop.DBus \
  /org/freedesktop/DBus \
  org.freedesktop.DBus.ListNames

# Connect via NetworkManager GUI or nmcli
nmcli connection up "GlobalProtect VPN"
```

## Logs

### Systemd journal:
```bash
sudo journalctl -u NetworkManager -f
```

Look for entries:
```
Starting gpclient VPN service
Connect() called
Gateway: portal.example.com
Started gpclient with PID 12345
gpclient output: ...
VPN connected - tunnel interface gpd0 detected!
```

### Log levels:
- `INFO` - normal operations
- `WARNING` - issues that don't block operation
- `ERROR` - critical errors
- `DEBUG` - detailed information (only with --debug)

## Troubleshooting

### Service doesn't start
```bash
# Check logs
sudo journalctl -u NetworkManager | grep gpclient

# Check permissions
ls -l /usr/lib/NetworkManager/nm-gpclient-service

# Check interpreter
head -1 /usr/lib/NetworkManager/nm-gpclient-service
# Should be: #!/usr/bin/env python3
```

### VPN doesn't connect
```bash
# Run service manually with debug
sudo /usr/lib/NetworkManager/nm-gpclient-service --debug

# In another terminal activate connection
nmcli connection up "GlobalProtect VPN"

# Check if gpclient is running
ps aux | grep gpclient

# Check interfaces
ip link show
```

### Browser issues
Service passes environment variables:
- `DISPLAY=:0`
- `SUDO_UID=1000`
- `XAUTHORITY=/home/user/.Xauthority`

If browser doesn't open:
```bash
# Check if user can run GUI
sudo -u $SUDO_USER DISPLAY=:0 firefox --version

# Use edge-wrapper for Microsoft Edge
# (in VPN config set browser=/usr/bin/edge-wrapper)
```

## References

- [NetworkManager D-Bus API](https://networkmanager.dev/docs/api/latest/spec.html)
- [python-sdbus documentation](https://python-sdbus.readthedocs.io/)
- [NetworkManager VPN examples](https://github.com/lcp/NetworkManager/blob/master/examples/python/vpn.py)
