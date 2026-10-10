# Run from a checked-out repository in PowerShell on Windows 11.
# Source/state/workspaces live inside WSL's Linux filesystem, never /mnt/c.
param([string]$Distribution = 'Ubuntu-24.04')
$ErrorActionPreference = 'Stop'
if (!(Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'Install WSL2 with: wsl --install -d Ubuntu-24.04; reboot, then rerun.'
}
$available = (wsl.exe --list --quiet) -replace "`0", ''
if (!($available -contains $Distribution)) {
    throw "Install and initialize $Distribution with: wsl --install -d $Distribution; then rerun."
}
# WSL handles execution and Linux service setup. Never interpolate local paths or secrets.
wsl.exe -d $Distribution -- bash -lc 'set -eu; command -v python3; command -v git; command -v tmux; command -v npm; test -d "$HOME/agent-console" || { echo "Clone your approved Agent Console revision into ~/agent-console inside WSL first." >&2; exit 1; }; cd "$HOME/agent-console"; ./scripts/install.sh'
if ($LASTEXITCODE -ne 0) { throw "WSL installation failed (exit $LASTEXITCODE)." }
