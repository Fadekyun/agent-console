# Client Installation

Use `client/install-client.py` on Windows, macOS, or Linux with the Tailscale hostname, LAN address, and that device's existing private-key path. The key is referenced but never read or transferred.

Linux/macOS:

```bash
python3 client/install-client.py install \
  --tailscale-host <tailscale-host> \
  --lan-ip <lan-ip> \
  --identity-file ~/.ssh/id_ed25519
```

Windows PowerShell:

```powershell
py client/install-client.py install `
  --tailscale-host <tailscale-host> `
  --lan-ip <lan-ip> `
  --identity-file $HOME/.ssh/id_ed25519
```

The older Zsh installer remains supported:

```bash
install-client.zsh install \
  --tailscale-host <tailscale-host> \
  --lan-ip <lan-ip> \
  --identity-file ~/.ssh/id_ed25519
```

The managed block is prepended so selector aliases take precedence, while unrelated and prior entries remain intact. Remove only the managed block with:

```bash
python3 client/install-client.py uninstall
```

Remote devices reach `<lan-ip>` through the tunnel host's advertised LAN subnet route.
