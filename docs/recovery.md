# Recovery

If selectors or web access fail:

1. Use `ssh <host>` for the root recovery shell.
2. Confirm the existing tmux sessions with `sudo -iu <user> tmux list-sessions`.
3. Attach directly with `sudo -iu <user> tmux attach -t NAME` and `TERM=xterm-256color` when the client reports `TERM=dumb`.
4. Check `agentctl doctor` and the two user services.
5. Check tunnel host loopback port <tunnel-port> and Tailscale Serve status.

tmux survives SSH and browser disconnection, but not a host reboot. Reconciliation marks lost managed sessions as process-exited/interrupted; it does not restart unfinished coding tasks automatically.
