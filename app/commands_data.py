"""内置常用 SSH 命令库。

每项： (名称, 命令, 分类, 说明, 是否高危)
"""

BUILTIN_COMMANDS = [
    # ---------------- 系统信息 ----------------
    ("查看内核与系统版本", "uname -a && cat /etc/os-release", "系统信息", "一次性查看内核、发行版信息", False),
    ("查看系统运行时长", "uptime", "系统信息", "运行时间与负载", False),
    ("查看 CPU 信息", "lscpu", "系统信息", "CPU 架构与核心数", False),
    ("查看内存使用", "free -h", "系统信息", "以人类可读格式显示内存", False),
    ("查看主机名", "hostname && hostname -I", "系统信息", "主机名与 IP 地址", False),
    ("查看当前用户", "whoami && id", "系统信息", "当前登录用户及 UID/GID", False),
    ("查看系统时间", "date '+%F %T %Z'", "系统信息", "当前系统时间与时区", False),
    ("查看内核启动日志", "dmesg | tail -n 50", "系统信息", "排查硬件/驱动问题", False),
    ("查看环境变量", "env | sort", "系统信息", "当前 shell 环境变量", False),
    ("查看登录历史", "last -n 20", "系统信息", "最近登录记录", False),

    # ---------------- 文件目录 ----------------
    ("列出目录详情", "ls -lah", "文件目录", "含隐藏文件的详细列表", False),
    ("当前路径", "pwd", "文件目录", "显示当前工作目录", False),
    ("创建多级目录", "mkdir -p /data/app", "文件目录", "递归创建目录", False),
    ("复制目录", "cp -r /data/src /data/src.bak", "文件目录", "递归复制", False),
    ("移动/重命名", "mv /data/a.txt /data/b.txt", "文件目录", "移动或重命名", False),
    ("统计目录大小", "du -sh ./* | sort -hr | head -n 20", "文件目录", "找出占用最大的目录", False),
    ("查找文件", "find / -name '*.log' -mtime -1 2>/dev/null | head -n 50", "文件目录", "按名称查找文件", False),
    ("查找大文件", "find / -type f -size +500M 2>/dev/null | head -n 30", "文件目录", "找出大于 500M 的文件", False),
    ("创建软链接", "ln -sfn /data/app /opt/app", "文件目录", "创建/更新软链接", False),
    ("删除目录（高危）", "rm -rf /data/tmp/*", "文件目录", "递归强制删除，请确认路径", True),

    # ---------------- 文本处理 ----------------
    ("查看文件", "cat /var/log/messages", "文本处理", "输出文件内容", False),
    ("实时跟踪日志", "tail -f -n 200 /var/log/syslog", "文本处理", "实时跟踪日志尾部", False),
    ("查看文件头", "head -n 50 /etc/passwd", "文本处理", "查看前 N 行", False),
    ("全局搜索关键字", "grep -rn 'ERROR' /var/log --color=auto | head -n 50", "文本处理", "递归搜索关键字", False),
    ("统计行数", "wc -l access.log", "文本处理", "统计文件行数", False),
    ("批量替换（高危）", "sed -i 's/old/new/g' ./config.yml", "文本处理", "批量替换文本内容", True),
    ("按列提取", "awk '{print $1, $7}' access.log | sort | uniq -c | sort -nr | head", "文本处理", "统计访问最多的来源", False),
    ("对比文件差异", "diff -u a.conf b.conf", "文本处理", "对比两个文件", False),

    # ---------------- 磁盘存储 ----------------
    ("查看磁盘空间", "df -hT", "磁盘存储", "各挂载点空间与文件系统类型", False),
    ("查看块设备", "lsblk -f", "磁盘存储", "磁盘与分区信息", False),
    ("查看挂载点", "mount | column -t", "磁盘存储", "当前挂载情况", False),
    ("磁盘 IO 测试", "dd if=/dev/zero of=/tmp/test bs=1M count=1024 oflag=direct && rm -f /tmp/test", "磁盘存储", "简单写入性能测试", False),
    ("查看 inode 使用", "df -i", "磁盘存储", "排查 inode 耗尽", False),

    # ---------------- 进程管理 ----------------
    ("查看进程", "ps aux --sort=-%mem | head -n 30", "进程管理", "按内存占用排序", False),
    ("进程快照", "top -b -n 1 | head -n 30", "进程管理", "非交互式 top 快照", False),
    ("查找进程 PID", "pgrep -af nginx", "进程管理", "按名称查找进程", False),
    ("终止进程（高危）", "pkill -9 -f 'process_name'", "进程管理", "按名称强制终止进程", True),
    ("后台运行程序", "nohup ./start.sh > app.log 2>&1 &", "进程管理", "脱离终端后台运行", False),
    ("查看进程打开文件", "lsof -p 1 | head -n 30", "进程管理", "查看进程打开的文件", False),

    # ---------------- 网络排查 ----------------
    ("查看网卡与 IP", "ip addr", "网络排查", "网卡与 IP 地址", False),
    ("查看监听端口", "ss -tulnp", "网络排查", "查看监听的端口与进程", False),
    ("查看路由表", "ip route", "网络排查", "查看路由与默认网关", False),
    ("测试连通性", "ping -c 4 8.8.8.8", "网络排查", "ICMP 连通性测试", False),
    ("追踪路由", "traceroute -n 8.8.8.8", "网络排查", "查看链路跳转", False),
    ("DNS 解析", "dig +short www.baidu.com", "网络排查", "DNS 解析测试", False),
    ("HTTP 探测", "curl -I -m 10 https://example.com", "网络排查", "查看响应头", False),
    ("检查端口连通", "nc -zv 127.0.0.1 22", "网络排查", "TCP 端口连通性检测", False),
    ("查看防火墙规则", "iptables -L -n --line-numbers", "网络排查", "查看 iptables 规则", False),
    ("抓包", "tcpdump -i any -nn -c 100 port 80", "网络排查", "简单抓包分析", False),

    # ---------------- 服务与日志 ----------------
    ("查看服务状态", "systemctl status nginx --no-pager", "服务与日志", "查看 systemd 服务状态", False),
    ("重启服务", "systemctl restart nginx && systemctl status nginx --no-pager", "服务与日志", "重启并查看状态", False),
    ("开机自启", "systemctl enable --now docker", "服务与日志", "设置并立即启动", False),
    ("查看服务日志", "journalctl -u nginx -n 200 --no-pager", "服务与日志", "查看服务最近日志", False),
    ("实时跟踪服务日志", "journalctl -u nginx -f", "服务与日志", "实时跟踪 journal 日志", False),
    ("查看定时任务", "crontab -l", "服务与日志", "当前用户定时任务", False),
    ("重启系统（高危）", "shutdown -r now", "服务与日志", "立即重启服务器", True),

    # ---------------- 用户与权限 ----------------
    ("创建用户", "useradd -m -s /bin/bash deploy", "用户与权限", "创建用户并生成 home 目录", False),
    ("修改密码", "passwd deploy", "用户与权限", "交互式修改用户密码", False),
    ("赋予 sudo 权限（高危）", "usermod -aG sudo deploy", "用户与权限", "将用户加入 sudo 组", True),
    ("批量授权目录（高危）", "chown -R www:www /data/www && chmod -R 755 /data/www", "用户与权限", "修改属主与权限", True),
    ("配置 SSH 公钥", "mkdir -p ~/.ssh && echo 'ssh-rsa AAAA...' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys", "用户与权限", "添加免密登录公钥", False),
    ("查看 sudo 配置", "sudo -l", "用户与权限", "查看当前用户 sudo 权限", False),

    # ---------------- 软件包 ----------------
    ("更新软件源", "apt update -y", "软件包", "Debian/Ubuntu 更新索引", False),
    ("安装软件", "apt install -y curl wget vim", "软件包", "Debian/Ubuntu 安装软件", False),
    ("升级全部软件（高危）", "apt upgrade -y", "软件包", "升级系统软件包", True),
    ("YUM 安装软件", "yum install -y epel-release", "软件包", "CentOS/RHEL 安装软件", False),
    ("查看已安装软件", "dpkg -l | head -n 30", "软件包", "Debian 系已安装包列表", False),

    # ---------------- 压缩与传输 ----------------
    ("打包目录", "tar -czvf app_$(date +%F).tar.gz /data/app", "压缩传输", "gzip 打包", False),
    ("解压缩", "tar -xzvf app.tar.gz -C /data", "压缩传输", "解压到指定目录", False),
    ("ZIP 打包", "zip -r app.zip /data/app", "压缩传输", "zip 格式打包", False),
    ("远程拷贝文件", "scp -P 22 -r ./dist user@host:/data/", "压缩传输", "通过 scp 传文件", False),
    ("断点续传同步", "rsync -avzP ./dist/ user@host:/data/dist/", "压缩传输", "rsync 增量同步", False),

    # ---------------- Docker ----------------
    ("容器列表", "docker ps -a", "Docker", "查看全部容器", False),
    ("镜像列表", "docker images", "Docker", "查看本地镜像", False),
    ("查看容器日志", "docker logs -f --tail 200 container_name", "Docker", "实时查看容器日志", False),
    ("进入容器", "docker exec -it container_name /bin/sh", "Docker", "进入运行中的容器", False),
    ("容器资源占用", "docker stats --no-stream", "Docker", "查看 CPU/内存占用", False),
    ("Compose 启动", "docker compose up -d", "Docker", "后台启动 compose 服务", False),
    ("清理无用资源（高危）", "docker system prune -af", "Docker", "清理停止的容器与悬空镜像", True),

    # ---------------- 性能排查 ----------------
    ("综合负载", "vmstat 1 5", "性能排查", "CPU/内存/IO 综合采样", False),
    ("磁盘 IO 监控", "iostat -x 1 5", "性能排查", "磁盘 IO 详细指标", False),
    ("占用端口的进程", "lsof -i :80", "性能排查", "查看端口占用进程", False),
    ("网络连接统计", "ss -s", "性能排查", "连接数统计", False),
    ("历史负载", "sar -u 1 5", "性能排查", "需要安装 sysstat", False),
]
