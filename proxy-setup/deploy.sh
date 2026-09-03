#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/secrets.env"

INSTALL_DIR="${INSTALL_DIR:-/opt/alpu-proxy}"
NGINX_SNIPPET="${NGINX_SNIPPET:-/etc/nginx/snippets/alpu-proxy-sub.conf}"
HY2_BIN_SRC="${HY2_BIN_SRC:-}"

render() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "$dst")"
  sed \
    -e "s|__DOMAIN__|${DOMAIN}|g" \
    -e "s|__HY2_PORT__|${HY2_PORT}|g" \
    -e "s|__HY2_PASSWORD__|${HY2_PASSWORD}|g" \
    -e "s|__OBFS_PASSWORD__|${OBFS_PASSWORD}|g" \
    -e "s|__SUB_TOKEN__|${SUB_TOKEN}|g" \
    -e "s|__CERT_PATH__|${CERT_PATH:-}|g" \
    -e "s|__KEY_PATH__|${KEY_PATH:-}|g" \
    "$src" > "$dst"
}

write_share_txt() {
  local dst="$1"
  cat > "$dst" <<EOF
hysteria2://${HY2_PASSWORD}@${DOMAIN}:${HY2_PORT}/?sni=${DOMAIN}&obfs=salamander&obfs-password=${OBFS_PASSWORD}#回国-Hysteria2
EOF
}

generate_local() {
  CERT_PATH="${CERT_PATH:-/etc/letsencrypt/live/${DOMAIN}/fullchain.pem}"
  KEY_PATH="${KEY_PATH:-/etc/letsencrypt/live/${DOMAIN}/privkey.pem}"
  mkdir -p "$ROOT/generated/sub"
  render "$ROOT/templates/clash.yaml" "$ROOT/generated/sub/clash.yaml"
  write_share_txt "$ROOT/generated/sub/share.txt"
  render "$ROOT/templates/config.yaml" "$ROOT/generated/config.yaml"
  render "$ROOT/templates/nginx-sub.conf" "$ROOT/generated/nginx-sub.conf"
  echo "已生成: $ROOT/generated/"
  echo "Hiddify / Clash Verge / Clash Meta 订阅:"
  echo "  https://${DOMAIN}/s/${SUB_TOKEN}/clash.yaml"
}

find_nginx_certs() {
  local conf
  conf="$(grep -Rsl "server_name.*${DOMAIN}" /etc/nginx 2>/dev/null | head -n 1 || true)"
  if [[ -n "$conf" ]]; then
    CERT_PATH="$(awk '/ssl_certificate / && !/ssl_certificate_key/ {gsub(/;/,""); print $2; exit}' "$conf" || true)"
    KEY_PATH="$(awk '/ssl_certificate_key/ {gsub(/;/,""); print $2; exit}' "$conf" || true)"
  fi
  if [[ -z "${CERT_PATH:-}" || -z "${KEY_PATH:-}" ]]; then
    CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
  fi
  if [[ ! -f "$CERT_PATH" || ! -f "$KEY_PATH" ]]; then
    echo "找不到 ${DOMAIN} 的 TLS 证书。"
    echo "已尝试: $CERT_PATH / $KEY_PATH"
    exit 1
  fi
}

install_hysteria() {
  if [[ -x /usr/local/bin/hysteria ]]; then
    echo "已安装 hysteria: $(/usr/local/bin/hysteria version 2>/dev/null | head -n 1 || echo ok)"
    return
  fi
  if [[ -n "$HY2_BIN_SRC" && -f "$HY2_BIN_SRC" ]]; then
    install -m 0755 "$HY2_BIN_SRC" /usr/local/bin/hysteria
    /usr/local/bin/hysteria version
    return
  fi
  local arch name tmp ver url
  arch="$(uname -m)"
  case "$arch" in
    x86_64) name=hysteria-linux-amd64 ;;
    aarch64|arm64) name=hysteria-linux-arm64 ;;
    *) echo "不支持的架构: $arch"; exit 1 ;;
  esac
  tmp="$(mktemp -d)"
  ver="$(curl -fsSL https://api.github.com/repos/apernet/hysteria/releases/latest | sed -n 's/.*"tag_name": "app\/v\([^"]*\)".*/\1/p' | head -n 1)"
  if [[ -z "$ver" ]]; then
    echo "无法从 GitHub 读取 Hysteria2 版本。请把二进制放到 HY2_BIN_SRC 再跑。"
    exit 1
  fi
  url="https://github.com/apernet/hysteria/releases/download/app%2Fv${ver}/${name}"
  echo "下载 $url"
  curl -fL "$url" -o "$tmp/hysteria"
  install -m 0755 "$tmp/hysteria" /usr/local/bin/hysteria
  rm -rf "$tmp"
  /usr/local/bin/hysteria version
}

remove_singbox() {
  systemctl disable --now alpu-proxy.service 2>/dev/null || true
  if systemctl is-active --quiet sing-box 2>/dev/null; then
    systemctl disable --now sing-box 2>/dev/null || true
  fi
  pkill -f '/usr/local/bin/sing-box' 2>/dev/null || true
  rm -f /usr/local/bin/sing-box
  if [[ -f /opt/alpu-proxy/config.json ]]; then
    rm -f /opt/alpu-proxy/config.json
  fi
}

check_udp_443() {
  if [[ "$HY2_PORT" != "443" ]]; then
    return
  fi
  if ss -ulnp | grep -q ':443 '; then
    echo "警告: UDP 443 已被占用（多半是网站 HTTP/3）。"
    echo "关掉 QUIC，或把 secrets.env 的 HY2_PORT 改成 8443。"
    ss -ulnp | grep ':443 ' || true
    exit 1
  fi
}

patch_nginx() {
  render "$ROOT/templates/nginx-sub.conf" "$NGINX_SNIPPET"
  local site
  site="$(grep -Rsl "server_name.*${DOMAIN}" /etc/nginx/sites-enabled /etc/nginx/conf.d /etc/nginx/sites-available 2>/dev/null | head -n 1 || true)"
  if [[ -z "$site" ]]; then
    echo "没找到 ${DOMAIN} 的 nginx 站点配置，请手动把下面这行放进 443 server 块："
    echo "    include ${NGINX_SNIPPET};"
    return
  fi
  if grep -q "alpu-proxy-sub.conf" "$site"; then
    echo "nginx 已包含订阅路径: $site"
    return
  fi
  cp -a "$site" "${site}.bak.alpu-proxy"
  python3 - "$site" "$NGINX_SNIPPET" "$DOMAIN" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
snippet = sys.argv[2]
domain = sys.argv[3]
text = path.read_text()
needle = None
for line in text.splitlines():
    if "server_name" in line and domain in line:
        needle = line
        break
if not needle:
    raise SystemExit(f"未在 {path} 里找到 server_name {domain}")
insert = needle + f"\n    include {snippet};"
if needle + f"\n    include {snippet};" in text:
    raise SystemExit(0)
path.write_text(text.replace(needle, insert, 1))
PY
  if nginx -t; then
    systemctl reload nginx
    echo "已写入订阅路径并 reload nginx: $site"
  else
    mv "${site}.bak.alpu-proxy" "$site"
    echo "nginx -t 失败，已回滚。请手动 include ${NGINX_SNIPPET}"
    exit 1
  fi
}

install_all() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "完整安装请用 root: sudo $0"
    exit 1
  fi
  remove_singbox
  find_nginx_certs
  check_udp_443
  install_hysteria

  mkdir -p "$INSTALL_DIR/sub"
  render "$ROOT/templates/config.yaml" "$INSTALL_DIR/config.yaml"
  render "$ROOT/templates/clash.yaml" "$INSTALL_DIR/sub/clash.yaml"
  write_share_txt "$INSTALL_DIR/sub/share.txt"
  cp "$ROOT/secrets.env" "$INSTALL_DIR/secrets.env"
  chmod 600 "$INSTALL_DIR/secrets.env" "$INSTALL_DIR/config.yaml"

  install -m 0644 "$ROOT/templates/alpu-proxy.service" /etc/systemd/system/alpu-proxy.service
  install -m 0644 "$ROOT/templates/sysctl.conf" /etc/sysctl.d/99-alpu-proxy.conf
  sysctl -p /etc/sysctl.d/99-alpu-proxy.conf >/dev/null || true

  if [[ -d /etc/letsencrypt/renewal-hooks/deploy ]]; then
    install -m 0755 "$ROOT/templates/certbot-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/restart-alpu-proxy.sh
  fi

  patch_nginx

  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow "${HY2_PORT}/udp" comment "alpu hysteria2" || true
  fi

  systemctl daemon-reload
  systemctl enable --now alpu-proxy.service
  systemctl --no-pager --full status alpu-proxy.service || true

  echo
  echo "======== 部署完成 ========"
  echo "Hiddify / Clash Verge / Clash Meta 订阅:"
  echo "  https://${DOMAIN}/s/${SUB_TOKEN}/clash.yaml"
  echo
  echo "阿里云安全组放行 UDP ${HY2_PORT}。TCP 443 保持原样给网站。"
}

usage() {
  echo "用法:"
  echo "  $0 generate   只在本机渲染配置，不安装"
  echo "  $0            在服务器上安装（需要 root）"
}

case "${1:-install}" in
  generate) generate_local ;;
  install) install_all ;;
  -h|--help) usage ;;
  *) usage; exit 1 ;;
esac
