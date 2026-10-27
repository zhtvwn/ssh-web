import datetime as dt

from flask_login import UserMixin
from sqlalchemy import Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship

from . import db
from .crypto import decrypt_text, encrypt_text
from .commands_data import BUILTIN_COMMANDS

ROLE_ADMIN = "admin"
ROLE_USER = "user"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(Integer, primary_key=True)
    username = db.Column(String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(String(255), nullable=False)
    role = db.Column(String(16), nullable=False, default=ROLE_USER)
    active = db.Column(Boolean, nullable=False, default=True)
    remark = db.Column(String(255), default="")
    created_at = db.Column(DateTime, default=dt.datetime.utcnow)
    last_login_at = db.Column(DateTime, nullable=True)
    last_login_ip = db.Column(String(64), default="")

    hosts = relationship("Host", backref="owner", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def set_password(self, password: str) -> None:
        import hashlib

        from werkzeug.security import generate_password_hash

        # scrypt 需要较新的 OpenSSL，缺失时回退到 PBKDF2
        method = "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2:sha256:260000"
        self.password_hash = generate_password_hash(password, method=method)

    def check_password(self, password: str) -> bool:
        from werkzeug.security import check_password_hash

        return check_password_hash(self.password_hash, password)


class Host(db.Model):
    __tablename__ = "hosts"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(128), nullable=False)
    hostname = db.Column(String(255), nullable=False)
    port = db.Column(Integer, nullable=False, default=22)
    username = db.Column(String(64), nullable=False, default="root")
    auth_type = db.Column(String(16), nullable=False, default="password")  # password | key
    _password = db.Column("password", Text, default="")
    _private_key = db.Column("private_key", Text, default="")
    _passphrase = db.Column("passphrase", Text, default="")
    shared = db.Column(Boolean, nullable=False, default=False)
    verify_host_key = db.Column(Boolean, nullable=False, default=True)
    remark = db.Column(String(255), default="")
    owner_id = db.Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = db.Column(DateTime, default=dt.datetime.utcnow)
    last_used_at = db.Column(DateTime, nullable=True)

    @property
    def password(self) -> str:
        return decrypt_text(self._password or "")

    @password.setter
    def password(self, value: str) -> None:
        self._password = encrypt_text(value or "")

    @property
    def private_key(self) -> str:
        return decrypt_text(self._private_key or "")

    @private_key.setter
    def private_key(self, value: str) -> None:
        self._private_key = encrypt_text(value or "")

    @property
    def passphrase(self) -> str:
        return decrypt_text(self._passphrase or "")

    @passphrase.setter
    def passphrase(self, value: str) -> None:
        self._passphrase = encrypt_text(value or "")

    @property
    def endpoint(self) -> str:
        return f"{self.username}@{self.hostname}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "hostname": self.hostname,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "shared": self.shared,
            "verify_host_key": self.verify_host_key,
            "remark": self.remark,
            "owner": self.owner.username if self.owner else "",
            "endpoint": self.endpoint,
        }


class CommandSnippet(db.Model):
    __tablename__ = "command_snippets"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(128), nullable=False)
    command = db.Column(Text, nullable=False)
    category = db.Column(String(64), nullable=False, default="自定义")
    description = db.Column(String(255), default="")
    dangerous = db.Column(Boolean, nullable=False, default=False)
    builtin = db.Column(Boolean, nullable=False, default=False)
    usage_count = db.Column(Integer, nullable=False, default=0)
    owner_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = db.Column(DateTime, default=dt.datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "category": self.category,
            "description": self.description,
            "dangerous": self.dangerous,
            "builtin": self.builtin,
            "usage_count": self.usage_count,
        }


class CommandLog(db.Model):
    __tablename__ = "command_logs"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    username = db.Column(String(64), default="")
    host_id = db.Column(Integer, nullable=True)
    host_label = db.Column(String(255), default="")
    command = db.Column(Text, default="")
    source = db.Column(String(16), default="terminal")  # connect | terminal | quick
    exit_code = db.Column(Integer, nullable=True)
    ok = db.Column(Boolean, default=True)
    output = db.Column(Text, default="")
    created_at = db.Column(DateTime, default=dt.datetime.utcnow, index=True)


def ensure_default_admin():
    """系统初始化：无用户时创建默认管理员。"""
    from flask import current_app

    if User.query.count() == 0:
        admin = User(
            username=current_app.config["ADMIN_USERNAME"],
            role=ROLE_ADMIN,
            remark="系统初始化管理员",
        )
        admin.set_password(current_app.config["ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
        current_app.logger.warning(
            "已创建默认管理员 %s，请登录后立即修改密码", current_app.config["ADMIN_USERNAME"]
        )


def ensure_schema_patches():
    """为旧版本数据库补齐新增字段（SQLite）。"""
    from sqlalchemy import inspect, text

    try:
        columns = {c["name"] for c in inspect(db.engine).get_columns("hosts")}
        if "verify_host_key" not in columns:
            db.session.execute(text("ALTER TABLE hosts ADD COLUMN verify_host_key BOOLEAN DEFAULT 1"))
            db.session.commit()
    except Exception:  # noqa: BLE001 - 新库或无权限时忽略
        db.session.rollback()


def seed_builtin_commands():
    """初始化内置命令库（按命令去重，可重复执行）。"""
    exists = {c.command for c in CommandSnippet.query.filter_by(builtin=True).all()}
    added = 0
    for name, command, category, description, dangerous in BUILTIN_COMMANDS:
        if command in exists:
            continue
        db.session.add(
            CommandSnippet(
                name=name,
                command=command,
                category=category,
                description=description,
                dangerous=dangerous,
                builtin=True,
            )
        )
        added += 1
    if added:
        db.session.commit()
