"""敏感信息（主机密码 / 私钥）加密存储。

加密密钥由 SECRET_KEY（或 ENCRYPTION_KEY）派生，因此修改 SECRET_KEY 后
历史保存的主机凭据将无法解密，需要重新录入。
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _key_material() -> str:
    try:
        from flask import current_app

        if current_app and current_app.config.get("SECRET_KEY"):
            return current_app.config["SECRET_KEY"]
    except Exception:  # pragma: no cover - 无应用上下文
        pass
    return os.environ.get("ENCRYPTION_KEY") or os.environ.get("SECRET_KEY") or "dev-secret-key-please-change"


def _fernet() -> Fernet:
    digest = hashlib.sha256(str(_key_material()).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_text(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
