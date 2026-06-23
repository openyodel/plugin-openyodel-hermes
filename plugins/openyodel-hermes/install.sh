#!/usr/bin/env bash
# Install the Open Yodel plugin into Hermes
# Usage: ./install.sh [--symlink]

set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_PLUGINS="$HOME/.hermes/plugins"
TARGET="$HERMES_PLUGINS/openyodel-hermes"

echo "== Open Yodel Hermes Plugin Installer =="
echo ""

if [ "$1" = "--symlink" ]; then
    echo "→ Creating symlink..."
    mkdir -p "$HERMES_PLUGINS"
    rm -f "$TARGET"
    ln -s "$PLUGIN_DIR" "$TARGET"
    echo "  $TARGET → $PLUGIN_DIR"
else
    echo "→ Copying plugin..."
    mkdir -p "$HERMES_PLUGINS"
    rm -rf "$TARGET"
    cp -r "$PLUGIN_DIR" "$TARGET"
    echo "  Copied to $TARGET"
fi

echo ""
echo "→ Plugin installed."
echo ""
echo "Next steps:"
echo "  1. Set required env vars:"
echo "     hermes config set YODEL_PORT 8080"
echo '     hermes config set YODEL_API_KEY "your-secret-key"'
echo ""
echo "  2. Enable in config.yaml:"
echo "     gateway:"
echo "       platforms:"
echo "         openyodel:"
echo "           enabled: true"
echo ""
echo "  3. Restart Hermes"
echo ""
echo "  4. Test:"
echo "     python3 $PLUGIN_DIR/test_client.py --health"
echo '     python3 $PLUGIN_DIR/test_client.py --key "your-secret-key"'
