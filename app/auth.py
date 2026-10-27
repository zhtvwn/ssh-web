"""登录 / 登出 / 个人设置。"""

import datetime as dt
import time
from collections import defaultdict

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

_FAILED_ATTEMPTS = defaultdict(list)
_WINDOW_SECONDS = 300


def _too_many_attempts(username: str, limit: int) -> bool:
    now = time.time()
    attempts = [t for t in _FAILED_ATTEMPTS.get(username, []) if now - t < _WINDOW_SECONDS]
    _FAILED_ATTEMPTS[username] = attempts
    return len(attempts) >= limit


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.terminal"))

    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()

        from flask import current_app

        if _too_many_attempts(username, current_app.config.get("MAX_LOGIN_ATTEMPTS", 10)):
            error = "登录失败次数过多，请 5 分钟后重试"
        elif user and user.active and user.check_password(password):
            _FAILED_ATTEMPTS.pop(username, None)
            user.last_login_at = dt.datetime.utcnow()
            user.last_login_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
            db.session.commit()
            login_user(user, remember=bool(request.form.get("remember")))
            target = request.args.get("next") or url_for("main.terminal")
            return redirect(target)
        else:
            _FAILED_ATTEMPTS[username].append(time.time())
            error = "用户名或密码错误，或账号已被禁用"

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    message = error = ""
    if request.method == "POST":
        old_password = request.form.get("old_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not current_user.check_password(old_password):
            error = "原密码不正确"
        elif len(new_password) < 6:
            error = "新密码至少 6 位"
        elif new_password != confirm:
            error = "两次输入的新密码不一致"
        else:
            current_user.set_password(new_password)
            db.session.commit()
            message = "密码修改成功"
    return render_template("profile.html", message=message, error=error)
