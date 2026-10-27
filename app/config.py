import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT_DIR, "data"))


class Config:
    # 生产环境请通过环境变量注入，切勿使用默认值
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-please-change")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(DATA_DIR, "ssh_web.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 管理员初始账号（仅当系统内暂无用户时创建）
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

    # SSH 默认参数
    SSH_CONNECT_TIMEOUT = int(os.environ.get("SSH_CONNECT_TIMEOUT", "10"))
    SSH_COMMAND_TIMEOUT = int(os.environ.get("SSH_COMMAND_TIMEOUT", "60"))
    SSH_MAX_SESSION_SECONDS = int(os.environ.get("SSH_MAX_SESSION_SECONDS", "0"))  # 0 表示不限制

    # 单个浏览器标签允许同时打开的终端标签数量
    MAX_SESSIONS_PER_SOCKET = int(os.environ.get("MAX_SESSIONS_PER_SOCKET", "10"))

    # 页面离开（socket 断开）后会话保留时间，超时自动回收，0 表示一直保留
    SSH_DETACH_TIMEOUT = int(os.environ.get("SSH_DETACH_TIMEOUT", "600"))

    # 浏览器发出「即将离开」后的宽限期：真正关闭浏览器时快速回收，切页/刷新不受影响
    SSH_PENDING_CLOSE_SECONDS = int(os.environ.get("SSH_PENDING_CLOSE_SECONDS", "30"))

    # known_hosts 持久化位置
    KNOWN_HOSTS_PATH = os.path.join(DATA_DIR, "known_hosts")

    # 审计日志保留条数（超出后自动清理最旧记录，0 表示不清理）
    MAX_AUDIT_LOGS = int(os.environ.get("MAX_AUDIT_LOGS", "5000"))

    # 登录失败锁定策略
    MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "10"))
