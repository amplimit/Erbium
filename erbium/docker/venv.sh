VENV="/workspace/venv"
if [ -n "$PS1" ] && [ -f "$VENV/bin/activate" ]; then
  . "$VENV/bin/activate"
fi
export PATH="$HOME/.local/bin:$PATH"
if [ -d /opt/erbium/host-bin ]; then
  export PATH="$PATH:/opt/erbium/host-bin"
fi
