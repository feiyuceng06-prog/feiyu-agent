"""Verify frozen SDK imports/initialization without making API requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="feiyu-payload-test-") as temporary:
    root = Path(temporary)
    config = root / "config" / "feiyu"
    config.mkdir(parents=True)
    env = dict(os.environ, HOME=temporary, XDG_CONFIG_HOME=str(root / "config"),
               XDG_STATE_HOME=str(root / "state"), XDG_DATA_HOME=str(root / "data"))
    for kind in ("openai", "anthropic"):
        (config / "models.json").write_text(json.dumps({"smoke": {
            "kind": kind, "model": "smoke-model", "key": "test-only-not-a-key",
            "base_url": "http://127.0.0.1:1", "limit": 32000,
        }}))
        result = subprocess.run([sys.argv[1], "--serve", "--own-window", "-w", temporary, "-m", "smoke"],
                                input='{"op":"status"}\n', text=True, capture_output=True,
                                env=env, timeout=30)
        assert result.returncode == 0, f"{kind} backend failed: {result.stderr}"
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert any(event.get("kind") == "status" for event in events), f"{kind}: no status received"
        print(f"PASS: frozen {kind} backend initializes without system Python")
