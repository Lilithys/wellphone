#!/bin/zsh
# Compatibility entry point: always use the validated focus-isolated session.
# Credentials come from PHONE_AGENT_API_KEY or a hidden Terminal prompt, never argv.
set -eu
script_dir="${0:A:h}"
exec python3 "$script_dir/run_autoglm_focus.py" "$@"
