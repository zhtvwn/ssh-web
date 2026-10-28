"""审计日志写入：终端输入、快速执行、连接、文件操作共用。"""

import datetime as dt

from flask import current_app
from flask_login import current_user

from . import db
from .models import CommandLog

MAX_LOG_OUTPUT = 4000


def log_action(host, command, source="terminal", ok=True, output="", exit_code=None):
    """写入一条审计记录，并在超过 MAX_AUDIT_LOGS 时清理最旧的记录。"""
    entry = CommandLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        username=current_user.username if current_user.is_authenticated else "anonymous",
        host_id=host.id if host else None,
        host_label=host.endpoint if host else "",
        command=(command or "")[:2000],
        source=source,
        ok=bool(ok),
        output=(output or "")[:MAX_LOG_OUTPUT],
        exit_code=exit_code,
        created_at=dt.datetime.utcnow(),
    )
    db.session.add(entry)
    max_logs = current_app.config.get("MAX_AUDIT_LOGS", 0)
    if max_logs:
        total = CommandLog.query.count()
        if total > max_logs:
            old_ids = (
                db.session.query(CommandLog.id)
                .order_by(CommandLog.id.asc())
                .limit(total - max_logs)
                .all()
            )
            db.session.query(CommandLog).filter(
                CommandLog.id.in_([i[0] for i in old_ids])
            ).delete(synchronize_session=False)
    db.session.commit()
