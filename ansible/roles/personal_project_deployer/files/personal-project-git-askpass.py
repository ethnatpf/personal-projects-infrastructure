#!/usr/bin/python3
"""Provide Git HTTPS credentials from a root-only file without exposing them in argv."""

import json
import sys
from pathlib import Path


auth_file = Path("/etc/personal-project-deployer/github-auth.json")
try:
    credentials = json.loads(auth_file.read_text())
    prompt = sys.argv[1].lower()
    if "username" in prompt:
        value = credentials["username"]
    elif "password" in prompt:
        value = credentials["token"]
    else:
        sys.exit(1)
except (OSError, KeyError, IndexError, json.JSONDecodeError):
    sys.exit(1)

if not isinstance(value, str) or not value:
    sys.exit(1)
sys.stdout.write(value + "\n")
