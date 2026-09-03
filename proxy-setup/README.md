# 回国代理

人在国外时，把国内网站的流量转到这台阿里云再访问。服务端是官方 Hysteria2，和 qqbot、nginx 网站共用同一台机，但不抢 TCP 443。

完整步骤、客户端导入和排错见 **[回国代理教程.md](./回国代理教程.md)**。

```
proxy-setup/
├── 回国代理教程.md      # 教程
├── deploy.sh             # 服务器安装脚本
├── secrets.env.example   # 密钥模板（复制为 secrets.env）
└── templates/            # Hysteria2 / Clash / nginx / systemd 模板
```

密钥在 `secrets.env`，已 gitignore，不要上传。
