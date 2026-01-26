# Instrukcje dla Claude Code - testy GUI

Ten plik zawiera instrukcje dla agenta Claude Code uruchomionego na maszynie z GTK.

## Środowisko

- Ubuntu 24.04 z sesją X11 (nie Wayland)
- GNOME lub inne DE z NetworkManager
- Accessibility włączone

## Cel

Uruchamiaj testy GUI, naprawiaj błędy w kodzie testów i pluginie, iteruj aż testy przejdą.

## Workflow

### 1. Sprawdź środowisko

```bash
echo $XDG_SESSION_TYPE  # musi być x11
gsettings get org.gnome.desktop.interface toolkit-accessibility  # musi być true
```

### 2. Zainstaluj zależności (jeśli brak)

```bash
sudo apt install -y \
    build-essential pkg-config \
    libnm-dev libgtk-3-dev libgtk-4-dev libnma-dev libnma-gtk4-dev \
    at-spi2-core python3-pyatspi libatk-adaptor \
    accerciser gnome-screenshot network-manager-gnome \
    python3-dogtail python3-pytest
```

### 3. Pętla testowania

```bash
# Pełny cykl: build + install + restart NM + test
make test-ui

# Tylko testy (bez przebudowy)
make test-ui-only

# Po błędzie - sprawdź logi i screenshoty
ls artifacts/
cat artifacts/test_ui.log
```

### 4. Debugowanie AT-SPI

Jeśli testy nie znajdują elementów:

```bash
# Uruchom accerciser i aplikację równolegle
accerciser &
nm-connection-editor &

# W accerciser klikaj elementy i notuj:
# - roleName (np. "push button", "frame", "dialog")
# - name (np. "Add", "Network Connections")
# Użyj tych wartości w testach
```

### 5. Edycja testów

Pliki testów:
- `tests/test_nm_connection_editor.py` - ETAP 1, nm-connection-editor
- `tests/test_gnome_settings.py` - ETAP 2, GNOME Settings
- `tests/helpers/dogtail_utils.py` - funkcje pomocnicze

### 6. Po zmianach w pluginie

Jeśli zmieniasz pliki `.c` w `plugins/gnome/`:

```bash
make install-dev   # przebuduj i zainstaluj
make restart-nm    # restart NetworkManager
make test-ui-only  # uruchom testy
```

## Kryteria sukcesu

1. `make test-ui` przechodzi bez błędów
2. Test weryfikuje że plugin GlobalProtect pojawia się w liście VPN
3. Screenshoty w `artifacts/` pokazują poprawne działanie

## Częste problemy

| Problem | Rozwiązanie |
|---------|-------------|
| "Element not found" | Użyj accerciser do sprawdzenia nazw elementów |
| "X11 session required" | Wyloguj się i zaloguj do "GNOME on Xorg" |
| "Accessibility not enabled" | `gsettings set org.gnome.desktop.interface toolkit-accessibility true` |
| Build fails | Sprawdź czy wszystkie `-dev` pakiety zainstalowane |

## Raportowanie

Po zakończeniu pracy:
1. Wylistuj co zostało zmienione
2. Pokaż wynik `make test-ui`
3. Opisz czy testy przechodzą i co jeszcze wymaga pracy
