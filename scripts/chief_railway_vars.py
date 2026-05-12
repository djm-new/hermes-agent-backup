#!/usr/bin/env python3
"""Print Railway variables needed to bootstrap the local Chief profile.

This prints secrets. Run it only in a private terminal, then paste the output
into Railway variables or use it with `railway variables set`.
"""
from __future__ import annotations

import base64
from pathlib import Path

PROFILE = Path.home() / ".hermes" / "profiles" / "chief"
FILES = {
    "HERMES_CONFIG_YAML_BOOTSTRAP_B64": PROFILE / "config.yaml",
    "HERMES_ENV_BOOTSTRAP_B64": PROFILE / ".env",
    "HERMES_AUTH_JSON_BOOTSTRAP_B64": PROFILE / "auth.json",
    "HERMES_SOUL_MD_BOOTSTRAP_B64": PROFILE / "SOUL.md",
}

print("# Railway variables for Chief. These values contain secrets.")
print("HERMES_HOME=/opt/data")
print("RAILWAY_CHIEF_BOOTSTRAP_OVERWRITE=0")
for key, path in FILES.items():
    if not path.exists():
        print(f"# skipped {key}: {path} not found")
        continue
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    print(f"{key}={encoded}")
