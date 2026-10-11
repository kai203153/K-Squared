#!/bin/sh
# Start a clean Align demo: fresh activity, DE-104 context + demo code scanned, server on :8765.
# Usage: scripts/demo.sh        (Ctrl-C to stop; run again to reset between rehearsals)
set -e
cd "$(dirname "$0")/.."
python3 -m align init --example de-104
python3 -m align reset
echo "Dashboard: http://127.0.0.1:8765   Demo workspace: $(pwd)/examples/de-104/demo_repo"
exec python3 -m align serve
