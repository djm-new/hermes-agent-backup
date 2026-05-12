#!/usr/bin/env bash
# Railway start wrapper for an always-on Chief/Hermes gateway.
#
# The base Docker entrypoint creates HERMES_HOME and default files before this
# script runs. This wrapper optionally bootstraps Chief's profile files from
# Railway environment variables, then runs the gateway in the foreground so
# Railway can supervise/restart it.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/opt/data}"
mkdir -p "$HERMES_HOME" "$HERMES_HOME/logs" "$HERMES_HOME/sessions" "$HERMES_HOME/cron"

write_b64_file() {
  local var_name="$1"
  local dest="$2"
  local mode="${3:-600}"
  local value="${!var_name:-}"

  if [ -z "$value" ]; then
    return 0
  fi

  if [ -f "$dest" ] && [ "${RAILWAY_CHIEF_BOOTSTRAP_OVERWRITE:-0}" != "1" ]; then
    echo "Keeping existing $dest (set RAILWAY_CHIEF_BOOTSTRAP_OVERWRITE=1 to replace it)."
    return 0
  fi

  echo "Bootstrapping $dest from $var_name"
  printf '%s' "$value" | base64 -d > "$dest"
  chmod "$mode" "$dest" 2>/dev/null || true
}

write_b64_file HERMES_CONFIG_YAML_BOOTSTRAP_B64 "$HERMES_HOME/config.yaml" 600
write_b64_file HERMES_ENV_BOOTSTRAP_B64 "$HERMES_HOME/.env" 600
write_b64_file HERMES_AUTH_JSON_BOOTSTRAP_B64 "$HERMES_HOME/auth.json" 600
write_b64_file HERMES_SOUL_MD_BOOTSTRAP_B64 "$HERMES_HOME/SOUL.md" 600

# Railway provides PORT for web services. The Telegram/Slack gateway does not
# need an inbound HTTP port when using polling/socket modes, so no listener is
# started here.
if [ -f /opt/hermes/.venv/bin/activate ]; then
  # Railway runs this start command directly, so activate the image venv here
  # instead of relying on docker/entrypoint.sh to do it.
  # shellcheck disable=SC1091
  source /opt/hermes/.venv/bin/activate
fi

export PATH="/opt/hermes/.venv/bin:/opt/data/.local/bin:$PATH"

echo "Starting Chief gateway on Railway with HERMES_HOME=$HERMES_HOME"
exec /opt/hermes/.venv/bin/hermes gateway run
