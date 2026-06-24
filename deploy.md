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
