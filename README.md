# SSH 在线运维平台（Web SSH）

浏览器里直接连接服务器的 SSH 系统：内置**用户管理**、**主机管理**、**常用 SSH 命令库**、**实时 Web 终端**（xterm.js + paramiko）、**快速执行**与**审计日志**，可一键 Docker 部署。

## 功能

| 模块 | 说明 |
| --- | --- |
| 用户管理 | 登录/登出、角色（管理员 / 普通用户）、增删改查、重置密码、启用禁用、修改个人密码、登录失败限流 |
| 主机管理 | 密码或 SSH 私钥认证、连接测试、共享主机（管理员）、主机指纹校验开关、凭据加密存储、**批量导入/导出（JSON）** |
| Web 终端 | xterm.js 实时交互终端，**支持多标签同时连接多台主机**，窗口自适应、Ctrl+C、粘贴、自动补全 |
| 会话保持 | 切换到其他菜单/刷新页面**不会断开 SSH**，回到终端页自动恢复（含断开期间的输出回放）；「重连」会复用原会话且不清空屏幕；只有点标签「×」或关闭浏览器才真正断开；空闲时每 30s 发送 SSH keepalive |
| 常用命令库 | 内置 80+ 条常用命令（系统信息/文件/磁盘/进程/网络/服务/权限/软件包/压缩/Docker/排查），可自定义、可搜索、可一键插入或执行 |
| 快速执行 | 选中命令直接执行并在下方面板展示输出、退出码与耗时 |
| 审计日志 | 记录连接、终端输入命令与快速执行结果，支持按用户/类型/关键字筛选与详情查看 |

## 目录结构

```
app/
  config.py          配置（环境变量）
  crypto.py          主机凭据加密（Fernet，密钥由 SECRET_KEY 派生）
  models.py          数据模型：User / Host / CommandSnippet / CommandLog
  ssh_service.py     paramiko 封装：交互式会话、一次性命令、连接测试、会话回收
  sockets.py         SocketIO 事件通道（终端 IO、快速执行、审计）
  auth.py  views.py  登录与业务页面
  templates/ static/ 页面与前端资源（xterm.js、socket.io 客户端已内置，无需外网）
Dockerfile  docker-compose.yml  requirements.txt  wsgi.py  run.py
```

## 快速开始（Docker）

```bash
cp .env.example .env        # 按需修改 SECRET_KEY / ADMIN_PASSWORD
docker compose up -d --build
# 浏览器访问 http://localhost:5000
```

默认管理员账号：`admin` / `admin123`（仅首次启动创建，登录后请立即修改密码）。

常用命令：

```bash
docker compose logs -f      # 查看日志
docker compose down         # 停止
docker compose up -d --build# 更新代码后重建
```

数据持久化在 `ssh-data` 卷中（SQLite 数据库 + known_hosts），容器重建不会丢失。

## 本地运行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=please-change-me ADMIN_USERNAME=admin ADMIN_PASSWORD=admin123
python run.py               # http://127.0.0.1:5000
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | `dev-secret-key-please-change` | 会话签名 + 主机凭据加密密钥，**必须修改且不要频繁变更**（变更后会无法解密已存凭据） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123` | 初始化管理员（仅在无用户时生效） |
| `DATABASE_URL` | `sqlite:///<DATA_DIR>/ssh_web.db` | 数据库连接串 |
| `SSH_CONNECT_TIMEOUT` | `10` | 建立 SSH 连接超时（秒） |
| `SSH_COMMAND_TIMEOUT` | `60` | 快速执行超时（秒） |
| `SSH_MAX_SESSION_SECONDS` | `0` | 单个终端会话最长存活时间，0 表示不限制 |
| `SSH_DETACH_TIMEOUT` | `600` | 页面离开后会话最长保留时间（秒），超时自动回收；0 表示一直保留 |
| `SSH_PENDING_CLOSE_SECONDS` | `30` | 浏览器发出「即将离开」后的宽限期；切页/刷新会在宽限期内重新接入，真正关闭浏览器则到期回收 |
| `MAX_SESSIONS_PER_SOCKET` | `10` | 单个浏览器连接允许同时打开的终端标签数 |
| `MAX_AUDIT_LOGS` | `5000` | 审计日志最大保留条数 |
| `DATA_DIR` | `/app/data`（容器内） | 数据库与 known_hosts 目录 |

## 使用流程

1. 管理员登录 →「主机管理」→ 新增主机（填写地址、端口、用户名与密码/私钥，可先点「测试」验证连通性）。
   - **批量迁移**：右上角「导出」生成 JSON（可勾选「包含密码/私钥」，默认不含）；「导入」选择 JSON 文件，可勾选「覆盖已存在的同名主机」。导入的主机归属当前用户，`shared` 仅管理员可设置。
2. 「Web 终端」→ 选择主机 →「+ 新建连接」，即可像本地终端一样操作；重复此步骤可同时连接多台主机，每个连接是一个独立标签：
   - 标签圆点表示状态（绿=已连接、橙=连接中、灰=未连接、红=失败）
   - 单击标签切换，双击标签重连，点「×」关闭该连接
   - 快捷键：`Ctrl+Alt+T` 新建连接，`Ctrl+Alt+W` 关闭当前标签
   - 右侧命令的「插入 / 执行」作用于当前激活的标签
   - **切换到其他菜单或刷新页面不会断开连接**，回到终端页会自动恢复原有会话并回放断开期间的输出；只有点击标签「×」或关闭浏览器才真正断开
3. 右侧命令库可按关键字搜索，点击「插入」写入终端，或点击「执行」直接运行并查看输出。
4. 「命令库」页面可维护自定义命令；「审计日志」可回溯所有人的操作记录。

## 安全建议

- 部署后立刻修改 `SECRET_KEY` 与管理员密码；不要使用默认口令。
- 建议通过反向代理（Nginx）启用 HTTPS，并限制来源 IP。
- 主机指纹变更导致无法连接时，可在主机编辑中关闭「校验主机指纹」（会跳过 known_hosts 校验）。
- 普通用户只能看到自己创建或被共享的主机；管理员可管理全部主机与用户。
