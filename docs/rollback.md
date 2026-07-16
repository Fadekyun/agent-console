# Rollback

Rollback does not require the web interface.

1. Disable the Agent Console web and tunnel user services.
2. Restore the saved tunnel host Serve configuration; do not use `tailscale serve reset` if other services share the tunnel.
3. Remove only the managed client SSH block.
4. Remove Agent Console wrapper symlinks if required.
5. Restore backed-up systemd/configuration files.

Do not kill existing tmux sessions, remove authorized keys, delete plan artifacts, remove worktrees, or alter Hermes during rollback. State and transcripts remain under `~/.local/share/agent-console` unless the user separately authorizes deletion.
