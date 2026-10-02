#!/bin/bash
set -euo pipefail
# shellcheck disable=SC1091
source /opt/alpu-proxy/socks.env
exec /usr/local/bin/gost -L "socks5://${SOCKS_USER}:${SOCKS_PASS}@:${SOCKS_PORT}"
