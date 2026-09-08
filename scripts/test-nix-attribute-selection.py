"""Check arbitrary attribute lookup with real Nix outside the Nix build sandbox."""

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "checks", Path(__file__).with_name("run-flake-checks.py")
)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)

system = subprocess.check_output(
    ["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"],
    text=True,
).strip()
names = [
    "ordinary",
    "with space",
    "with.dot",
    "🔥",
    'quote"name',
    "back\\slash",
    '${throw "must not evaluate"}',
    "line\nbreak",
    "tab\tname",
]
original = Path.cwd()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "names.json").write_text(json.dumps(names))
    (root / "flake.nix").write_text(
        """{
          outputs = { self }: {
            checks.SYSTEM = builtins.listToAttrs (map (name: {
              inherit name;
              value = builtins.derivation {
                name = "attribute-lookup-fixture";
                system = "SYSTEM";
                builder = "/not-executed";
              };
            }) (builtins.fromJSON (builtins.readFile ./names.json)));
          };
        }""".replace("SYSTEM", system)
    )
    try:
        os.chdir(root)
        paths = [checks.check_derivation(system, name) for name in names]
        assert len(set(paths)) == 1
        # Verify the build selector too, without running a fixture's builder.
        subprocess.run(["nix", "build", "--dry-run", paths[0] + "^*"], check=True)
    finally:
        os.chdir(original)
print(f"Real Nix resolved all {len(names)} attribute names and the build selector.")
