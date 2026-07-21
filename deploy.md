Ubuntu 22.04 完全可以，是目前最主流的选择，systemd 原生支持，uv
  也能直接安装。

  部署步骤

1. 安装 uv
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source $HOME/.local/bin/env
2. 上传代码

本地执行，把项目传到服务器

  scp -r /Users/admin/dev/qqbot user@服务器IP:/home/user/qqbot
  或者用 git clone，看你怎么管理代码。

3. 服务器上安装依赖
   cd ~/qqbot
   uv sync

   在 `.env` 中配置状态接口：

   ```dotenv
   STATUS_API_ENABLED=true
   STATUS_API_HOST=0.0.0.0
   STATUS_API_PORT=8080
   STATUS_API_ALLOWED_ORIGIN=https://你的商品导航域名
   CONTENT_CHECK_INTERVAL=600
   CONTENT_CHANGE_GROUP_OPENIDS=8BCFB82E1F69A44440B64F2766022549
   CONTENT_CHANGE_USER_OPENIDS=
   ```

   `GROUP_OPENIDS` 只用于补货/新品通知；留空不会发送这类主动通知。
   商品说明、封面或详情图片 URL 变化只发送到
   `CONTENT_CHANGE_GROUP_OPENIDS`。私信目标必须填写 QQ 开放平台分配的
   `user_openid`，普通 QQ 号或群号不能直接用于主动消息。

   `shop-navigator` 的构建环境需要配置：

   ```dotenv
   VITE_PRODUCT_STATUS_API_URL=https://你的接口域名/api/v1/catalog/status
   ```

   建议由 Nginx/Caddy 给 8080 端口配置 HTTPS 反向代理，不要让 HTTPS
   网站直接请求 HTTP 接口。部署后可用 `curl http://127.0.0.1:8080/healthz`
   和 `curl http://127.0.0.1:8080/api/v1/catalog/status` 验证。
4. 确认 python 路径
   uv run which python

输出类似：/home/user/qqbot/.venv/bin/python

5. 创建 systemd service
   sudo nano /etc/systemd/system/qqbot.service



[Unit]

Description=QQ Shop Bot

After=network.target

[Service]

Type=simple

User=root

WorkingDirectory=/root/qqbot

ExecStart=/root/qqbot/.venv/bin/python main.py

Restart=on-failure

RestartSec=10

StandardOutput=journal

StandardError=journal

[Install]

WantedBy=multi-user.target


6. 启动
   sudo systemctl daemon-reload
   sudo systemctl enable qqbot
   sudo systemctl start qqbot
   sudo systemctl status qqbot
7. 以后更新代码

上传新文件后

  sudo systemctl restart qqbot
  journalctl -u qqbot -f   # 看日志确认正常
