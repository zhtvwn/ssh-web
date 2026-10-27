import os
import threading
import time

from flask import Flask
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录后再访问该页面"
login_manager.session_protection = "basic"

socketio = SocketIO(
    async_mode="threading",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
    ping_interval=25,
    ping_timeout=60,
    max_http_buffer_size=1_000_000,
)


def _background_sweeper(app):
    """定期回收超时会话（含长期脱离页面的孤立会话）。"""
    from .ssh_service import session_manager

    def loop():
        while True:
            time.sleep(30)
            try:
                with app.app_context():
                    session_manager.sweep(
                        app.config.get("SSH_MAX_SESSION_SECONDS", 0),
                        app.config.get("SSH_DETACH_TIMEOUT", 0),
                    )
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=loop, daemon=True).start()


def create_app():
    from .config import DATA_DIR, Config

    os.makedirs(DATA_DIR, exist_ok=True)
    os.environ.setdefault("KNOWN_HOSTS_PATH", Config.KNOWN_HOSTS_PATH)

    app = Flask(__name__)
    app.config.from_object(Config)
    app.jinja_env.globals.update(app_name="SSH 在线运维平台")

    db.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):  # pragma: no cover - 延迟导入避免循环依赖
        from .models import User

        return db.session.get(User, int(user_id))

    from . import sockets  # noqa: F401  注册 socketio 事件
    from .auth import auth_bp
    from .views import main_bp
    from .models import ensure_default_admin, ensure_schema_patches, seed_builtin_commands

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        ensure_schema_patches()
        ensure_default_admin()
        seed_builtin_commands()

    _background_sweeper(app)
    return app
