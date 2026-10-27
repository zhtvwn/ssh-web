"""主要页面：Web 终端、主机管理、命令库、用户管理、审计日志。"""

import datetime as dt
import json

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from . import db
from .models import ROLE_ADMIN, ROLE_USER, CommandLog, CommandSnippet, Host, User
from .ssh_service import session_manager, test_connection

main_bp = Blueprint("main", __name__)


def visible_hosts_query():
    if current_user.is_admin:
        return Host.query
    return Host.query.filter(or_(Host.owner_id == current_user.id, Host.shared.is_(True)))


def accessible_host(host_id):
    host = db.session.get(Host, host_id)
    if not host:
        return None
    if host.shared or current_user.is_admin or host.owner_id == current_user.id:
        return host
    return None


def require_admin():
    if not current_user.is_admin:
        abort(403)


@main_bp.route("/")
@login_required
def index():
    return redirect(url_for("main.terminal"))


@main_bp.route("/terminal")
@login_required
def terminal():
    hosts = visible_hosts_query().order_by(Host.name.asc()).all()
    snippets = CommandSnippet.query.order_by(
        CommandSnippet.builtin.desc(), CommandSnippet.category.asc(), CommandSnippet.id.asc()
    ).all()
    host_id = request.args.get("host", type=int)
    if host_id and not accessible_host(host_id):
        host_id = None
    if not host_id and hosts:
        host_id = hosts[0].id  # 仅用于下拉框默认选中
    return render_template(
        "terminal.html",
        hosts=[h.to_dict() for h in hosts],
        snippets=[s.to_dict() for s in snippets],
        host_id=host_id,
        # 只有显式带 ?host= 的入口（如主机管理点「连接」）才自动建连，
        # 从菜单进入时不再自动连接，避免关闭所有连接后又被重新拉起
        auto_connect=bool(request.args.get("host")) and bool(host_id),
        active_sessions=session_manager.count(),
    )


# ------------------------------ 主机管理 ------------------------------


@main_bp.route("/hosts", methods=["GET", "POST"])
@login_required
def hosts():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        hostname = (request.form.get("hostname") or "").strip()
        if not name or not hostname:
            flash("名称与主机地址不能为空", "error")
            return redirect(url_for("main.hosts"))

        host = Host(
            name=name,
            hostname=hostname,
            port=int(request.form.get("port") or 22),
            username=(request.form.get("username") or "root").strip(),
            auth_type=request.form.get("auth_type") or "password",
            shared=bool(request.form.get("shared")) and current_user.is_admin,
            verify_host_key=bool(request.form.get("verify_host_key")),
            remark=(request.form.get("remark") or "").strip(),
            owner_id=current_user.id,
        )
        if host.auth_type == "key":
            host.private_key = request.form.get("private_key") or ""
            host.passphrase = request.form.get("passphrase") or ""
        else:
            host.password = request.form.get("password") or ""
        db.session.add(host)
        db.session.commit()
        flash(f"主机「{name}」已添加", "success")
        return redirect(url_for("main.hosts"))

    keyword = (request.args.get("q") or "").strip()
    query = visible_hosts_query()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(Host.name.like(like), Host.hostname.like(like)))
    hosts_list = query.order_by(Host.id.desc()).all()
    return render_template("hosts.html", hosts=hosts_list, keyword=keyword)


@main_bp.route("/hosts/<int:host_id>/update", methods=["POST"])
@login_required
def host_update(host_id):
    host = accessible_host(host_id)
    if not host or (not current_user.is_admin and host.owner_id != current_user.id):
        flash("只能管理自己创建的主机", "error")
        return redirect(url_for("main.hosts"))

    host.name = (request.form.get("name") or host.name).strip()
    host.hostname = (request.form.get("hostname") or host.hostname).strip()
    host.port = int(request.form.get("port") or host.port)
    host.username = (request.form.get("username") or host.username).strip()
    host.auth_type = request.form.get("auth_type") or host.auth_type
    host.verify_host_key = bool(request.form.get("verify_host_key"))
    host.remark = (request.form.get("remark") or "").strip()
    if current_user.is_admin:
        host.shared = bool(request.form.get("shared"))
    if host.auth_type == "key":
        key = request.form.get("private_key") or ""
        if key.strip():
            host.private_key = key
        host.passphrase = request.form.get("passphrase") or ""
    else:
        pwd = request.form.get("password") or ""
        if pwd:
            host.password = pwd
    db.session.commit()
    session_manager.stop_host(host.id)
    flash(f"主机「{host.name}」已更新", "success")
    return redirect(url_for("main.hosts"))


@main_bp.route("/hosts/<int:host_id>/delete", methods=["POST"])
@login_required
def host_delete(host_id):
    host = accessible_host(host_id)
    if not host or (not current_user.is_admin and host.owner_id != current_user.id):
        flash("只能管理自己创建的主机", "error")
        return redirect(url_for("main.hosts"))
    session_manager.stop_host(host.id)
    db.session.delete(host)
    db.session.commit()
    flash(f"主机「{host.name}」已删除", "success")
    return redirect(url_for("main.hosts"))


@main_bp.route("/hosts/<int:host_id>/test", methods=["POST"])
@login_required
def host_test(host_id):
    host = accessible_host(host_id)
    if not host:
        return jsonify({"ok": False, "message": "无访问权限"})
    ok, message = test_connection(host, timeout=current_app.config["SSH_CONNECT_TIMEOUT"])
    return jsonify({"ok": ok, "message": message})


@main_bp.route("/hosts/export")
@login_required
def host_export():
    """批量导出可见主机为 JSON；默认不含密码/私钥。"""
    include_secrets = request.args.get("secrets") == "1"
    items = []
    for host in visible_hosts_query().order_by(Host.name.asc()).all():
        item = {
            "name": host.name,
            "hostname": host.hostname,
            "port": host.port or 22,
            "username": host.username,
            "auth_type": host.auth_type,
            "shared": bool(host.shared),
            "verify_host_key": bool(host.verify_host_key),
            "remark": host.remark or "",
        }
        # 只有所有者或管理员能导出该主机的凭据
        if include_secrets and (current_user.is_admin or host.owner_id == current_user.id):
            item["password"] = host.password
            item["private_key"] = host.private_key
            item["passphrase"] = host.passphrase
        items.append(item)

    payload = {
        "type": "ssh-web-hosts",
        "version": 1,
        "exported_at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "hosts": items,
    }
    filename = "hosts-%s.json" % dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    resp = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@main_bp.route("/hosts/import", methods=["POST"])
@login_required
def host_import():
    """批量导入主机。同 name+hostname 视为已存在，可选择覆盖。"""
    payload = request.get_json(silent=True) or {}
    items = payload.get("hosts")
    if isinstance(payload, list):  # 兼容直接提交数组
        items = payload
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "message": "文件内容为空或格式不正确（需包含 hosts 数组）"})

    overwrite = bool(payload.get("overwrite"))
    created = updated = skipped = 0
    errors = []

    for index, item in enumerate(items[:500]):
        if not isinstance(item, dict):
            errors.append(f"第 {index + 1} 条格式不正确")
            continue
        hostname = str(item.get("hostname") or "").strip()
        if not hostname:
            errors.append(f"第 {index + 1} 条缺少主机地址")
            continue
        name = str(item.get("name") or "").strip() or hostname
        try:
            port = int(item.get("port") or 22)
        except (TypeError, ValueError):
            port = 22
        auth_type = item.get("auth_type") or "password"
        if auth_type not in ("password", "key"):
            auth_type = "password"

        existing = Host.query.filter_by(name=name, hostname=hostname).first()
        if existing and not (current_user.is_admin or existing.owner_id == current_user.id):
            skipped += 1
            errors.append(f"「{name}」已存在且无管理权限，已跳过")
            continue
        if existing and not overwrite:
            skipped += 1
            continue

        host = existing or Host(owner_id=current_user.id)
        if existing:
            updated += 1
        else:
            created += 1
        host.name = name
        host.hostname = hostname
        host.port = port
        host.username = (str(item.get("username") or "root").strip() or "root")
        host.auth_type = auth_type
        host.verify_host_key = bool(item.get("verify_host_key", True))
        host.remark = str(item.get("remark") or "").strip()
        if current_user.is_admin:
            host.shared = bool(item.get("shared"))

        password = str(item.get("password") or "")
        private_key = str(item.get("private_key") or "")
        if password:
            host.password = password
        if private_key:
            host.private_key = private_key
            host.passphrase = str(item.get("passphrase") or "")
        db.session.add(host)

    db.session.commit()
    message = f"导入完成：新增 {created} 台，更新 {updated} 台，跳过 {skipped} 台"
    if created and not any((h or {}).get("password") or (h or {}).get("private_key") for h in items
                           if isinstance(h, dict)):
        message += "（未包含凭据，请编辑主机补充密码/私钥）"
    return jsonify({"ok": True, "created": created, "updated": updated, "skipped": skipped,
                    "errors": errors[:10], "message": message})


# ------------------------------ 命令库 ------------------------------


@main_bp.route("/commands", methods=["GET", "POST"])
@login_required
def commands():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        command = (request.form.get("command") or "").strip()
        if not name or not command:
            flash("名称与命令内容不能为空", "error")
            return redirect(url_for("main.commands"))
        snippet = CommandSnippet(
            name=name,
            command=command,
            category=(request.form.get("category") or "自定义").strip(),
            description=(request.form.get("description") or "").strip(),
            dangerous=bool(request.form.get("dangerous")),
            builtin=False,
            owner_id=current_user.id,
        )
        db.session.add(snippet)
        db.session.commit()
        flash("命令已添加", "success")
        return redirect(url_for("main.commands"))

    keyword = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    query = CommandSnippet.query
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(CommandSnippet.name.like(like), CommandSnippet.command.like(like)))
    if category:
        query = query.filter_by(category=category)
    items = query.order_by(
        CommandSnippet.builtin.desc(), CommandSnippet.category.asc(), CommandSnippet.id.asc()
    ).all()
    categories = [c[0] for c in db.session.query(CommandSnippet.category).distinct().all()]
    return render_template(
        "commands.html", items=items, categories=categories, keyword=keyword, category=category
    )


@main_bp.route("/commands/<int:snippet_id>/delete", methods=["POST"])
@login_required
def command_delete(snippet_id):
    snippet = db.session.get(CommandSnippet, snippet_id)
    if not snippet or snippet.builtin:
        flash("内置命令不可删除", "error")
        return redirect(url_for("main.commands"))
    if not current_user.is_admin and snippet.owner_id != current_user.id:
        abort(403)
    db.session.delete(snippet)
    db.session.commit()
    flash("命令已删除", "success")
    return redirect(url_for("main.commands"))


@main_bp.route("/commands/<int:snippet_id>/use", methods=["POST"])
@login_required
def command_use(snippet_id):
    snippet = db.session.get(CommandSnippet, snippet_id)
    if snippet:
        snippet.usage_count = (snippet.usage_count or 0) + 1
        db.session.commit()
    return jsonify({"ok": True})


# ------------------------------ 用户管理 ------------------------------


@main_bp.route("/admin/users", methods=["GET", "POST"])
@login_required
def users():
    require_admin()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or len(password) < 6:
            flash("用户名不能为空，且密码至少 6 位", "error")
            return redirect(url_for("main.users"))
        if User.query.filter_by(username=username).first():
            flash("用户名已存在", "error")
            return redirect(url_for("main.users"))
        user = User(
            username=username,
            role=request.form.get("role") or ROLE_USER,
            active=True,
            remark=(request.form.get("remark") or "").strip(),
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"用户「{username}」已创建", "success")
        return redirect(url_for("main.users"))

    users_list = User.query.order_by(User.id.asc()).all()
    return render_template("users.html", users=users_list)


@main_bp.route("/admin/users/<int:user_id>/update", methods=["POST"])
@login_required
def user_update(user_id):
    require_admin()
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    action = request.form.get("action") or "profile"
    if action == "profile":
        username = (request.form.get("username") or "").strip()
        if not username:
            flash("用户名不能为空", "error")
            return redirect(url_for("main.users"))
        if username != user.username and User.query.filter_by(username=username).first():
            flash("用户名已存在", "error")
            return redirect(url_for("main.users"))
        user.username = username
        user.role = request.form.get("role") or user.role
        user.remark = (request.form.get("remark") or "").strip()
        user.active = bool(request.form.get("active"))
        flash(f"用户「{username}」已更新", "success")
    elif action == "reset_password":
        password = request.form.get("password") or ""
        if len(password) < 6:
            flash("密码至少 6 位", "error")
            return redirect(url_for("main.users"))
        user.set_password(password)
        flash(f"用户「{user.username}」密码已重置", "success")
    elif action == "toggle_active":
        user.active = not user.active
        flash(f"用户「{user.username}」已{'启用' if user.active else '禁用'}", "success")
    db.session.commit()
    return redirect(url_for("main.users"))


@main_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def user_delete(user_id):
    require_admin()
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if user.id == current_user.id:
        flash("不能删除当前登录的管理员", "error")
        return redirect(url_for("main.users"))
    if User.query.filter_by(role=ROLE_ADMIN).count() == 1 and user.is_admin:
        flash("系统至少需要保留一个管理员", "error")
        return redirect(url_for("main.users"))
    db.session.delete(user)
    db.session.commit()
    flash(f"用户「{user.username}」已删除", "success")
    return redirect(url_for("main.users"))


# ------------------------------ 审计日志 ------------------------------


@main_bp.route("/logs")
@login_required
def logs():
    page = request.args.get("page", 1, type=int)
    keyword = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "").strip()
    user_filter = request.args.get("user", type=int)

    query = CommandLog.query
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(CommandLog.command.like(like), CommandLog.host_label.like(like)))
    if source:
        query = query.filter_by(source=source)
    if user_filter:
        query = query.filter_by(user_id=user_filter)

    pagination = query.order_by(CommandLog.id.desc()).paginate(page=page, per_page=30, error_out=False)
    users_list = User.query.order_by(User.username.asc()).all() if current_user.is_admin else []
    return render_template(
        "logs.html",
        logs=pagination.items,
        pagination=pagination,
        keyword=keyword,
        source=source,
        user_filter=user_filter,
        users=users_list,
    )


@main_bp.route("/logs/<int:log_id>")
@login_required
def log_detail(log_id):
    entry = db.session.get(CommandLog, log_id)
    if not entry:
        abort(404)
    if not current_user.is_admin and entry.user_id != current_user.id:
        abort(403)
    return jsonify(
        {
            "id": entry.id,
            "user": entry.username,
            "host": entry.host_label,
            "command": entry.command,
            "source": entry.source,
            "exit_code": entry.exit_code,
            "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else "",
            "output": entry.output,
        }
    )


@main_bp.route("/api/hosts")
@login_required
def api_hosts():
    hosts = visible_hosts_query().order_by(Host.name.asc()).all()
    return jsonify([h.to_dict() for h in hosts])


@main_bp.route("/api/history")
@login_required
def api_history():
    """当前用户最近执行过的命令，用于终端输入提示补全。"""
    rows = (
        CommandLog.query.filter_by(user_id=current_user.id)
        .filter(CommandLog.source.in_(["terminal", "quick"]))
        .order_by(CommandLog.id.desc())
        .limit(300)
        .all()
    )
    seen, out = set(), []
    for row in rows:
        cmd = (row.command or "").strip()
        if not cmd or cmd.startswith("(") or cmd in seen:
            continue
        seen.add(cmd)
        out.append(cmd)
        if len(out) >= 100:
            break
    return jsonify(out)


@main_bp.route("/api/snippets")
@login_required
def api_snippets():
    items = CommandSnippet.query.order_by(CommandSnippet.category.asc(), CommandSnippet.id.asc()).all()
    return jsonify([i.to_dict() for i in items])
