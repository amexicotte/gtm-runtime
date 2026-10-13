#!/usr/bin/env bash
set -euo pipefail

# The Ubuntu package is materially older than the client used to mint Harbor's
# existing-account token. Pin the official binary and verify it before install.
RCLONE_VERSION="1.75.0"
RCLONE_ARCHIVE="/tmp/rclone-v${RCLONE_VERSION}-linux-amd64.zip"
RCLONE_DIR="/tmp/rclone-v${RCLONE_VERSION}-linux-amd64"
RCLONE_SHA256="aa2804e08f48250e71009c727124b6341cd1000000016a9a09d14663cabafbaa"

curl --fail --silent --show-error --location \
  "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-amd64.zip" \
  --output "$RCLONE_ARCHIVE"
printf '%s  %s\n' "$RCLONE_SHA256" "$RCLONE_ARCHIVE" | sha256sum --check --status
unzip -q -o "$RCLONE_ARCHIVE" -d /tmp
sudo install -m 0755 "$RCLONE_DIR/rclone" /usr/local/bin/rclone
rclone version
