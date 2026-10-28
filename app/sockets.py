"""SocketIO 事件：Web 终端的输入输出通道。

会话键为「用户:浏览器标签:终端标签」，与 socket 连接解耦：
切换页面只是 detach（后台继续运行），回到终端页重新 attach 即可继续；
只有点击标签上的「×」、关闭页面或超时回收才会真正断开。
"""

import datetime as dt

from flask import current_app, request
from flask_login import current_user

from . import db, socketio
from .audit import log_action
from .models import Host
from .ssh_service import make_key, run_command, session_manager


def _accessible(host):
    if not host:
        return False
    return host.shared or host.owner_id == current_user.id or current_user.is_admin


def _session_key(data):
    """根据事件数据构造会话键。"""
    return make_key(current_user.id, (data or {}).get("ws") or "default",
                    (data or {}).get("session_id") or "default")


@socketio.on("connect")
def on_connect():
    if not current_user.is_authenticated:
        return False
    current_app.logger.info("socket connected: %s (%s)", current_user.username, request.sid)
    return True


@socketio.on("ssh_start")
def on_ssh_start(data):
    """开启（或复用）一个终端会话。"""
    sid = request.sid
    data = data or {}
    session_id = (data.get("session_id") or "").strip() or "default"
    key = _session_key(data)

    host = db.session.get(Host, int(data.get("host_id") or 0))
    if not _accessible(host):
        socketio.emit("ssh_error", {"session_id": session_id, "message": "主机不存在或无访问权限"}, to=sid)
        return

    cols = int(data.get("cols") or 120)
    rows = int(data.get("rows") or 30)

    def on_output(text):
        socketio.emit("ssh_output", {"session_id": session_id, "data": text}, to=sid)

    def on_close(closed_key):
        socketio.emit("ssh_closed", {"session_id": session_id, "message": "连接已关闭"}, to=sid)

    existing = session_manager.get(key)
    if existing:  # 会话仍在（例如刷新页面），直接重新接入
        replay = existing.attach(sid, on_output, on_close)
        if replay:
            socketio.emit("ssh_output", {"session_id": session_id, "data": replay}, to=sid)
        socketio.emit("ssh_ready", {"session_id": session_id, "host_id": existing.host.id,
                                    "endpoint": existing.host.endpoint, "name": existing.host.name,
                                    "restored": True}, to=sid)
        return

    max_sessions = current_app.config.get("MAX_SESSIONS_PER_SOCKET", 10)
    if session_manager.count_for(current_user.id, data.get("ws") or "default") >= max_sessions:
        socketio.emit(
            "ssh_error",
            {"session_id": session_id, "message": f"同时连接的会话数已达上限（{max_sessions}），请先关闭部分标签"},
            to=sid,
        )
        return

    try:
        session_manager.start(
            key, host, cols, rows, sid, on_output, on_close,
            timeout=current_app.config["SSH_CONNECT_TIMEOUT"],
        )
    except Exception as exc:  # noqa: BLE001
        log_action(host, "(建立连接)", source="connect", ok=False, output=str(exc))
        socketio.emit("ssh_error", {"session_id": session_id, "message": str(exc)}, to=sid)
        return

    host.last_used_at = dt.datetime.utcnow()
    db.session.commit()
    log_action(host, "(建立连接)", source="connect")
    socketio.emit(
        "ssh_ready",
        {"session_id": session_id, "host_id": host.id, "endpoint": host.endpoint, "name": host.name},
        to=sid,
    )


@socketio.on("ssh_attach")
def on_ssh_attach(data):
    """回到终端页时重新接入已有会话；会话不存在则通知前端重建。"""
    sid = request.sid
    data = data or {}
    session_id = (data.get("session_id") or "").strip() or "default"
    key = _session_key(data)

    session = session_manager.get(key)
    if not session:
        socketio.emit("ssh_gone", {"session_id": session_id}, to=sid)
        return

    def on_output(text):
        socketio.emit("ssh_output", {"session_id": session_id, "data": text}, to=sid)

    def on_close(closed_key):
        socketio.emit("ssh_closed", {"session_id": session_id, "message": "连接已关闭"}, to=sid)

    replay = session.attach(sid, on_output, on_close)
    socketio.emit("ssh_ready", {"session_id": session_id, "host_id": session.host.id,
                                "endpoint": session.host.endpoint, "name": session.host.name,
                                "restored": True}, to=sid)
    if replay:
        socketio.emit("ssh_output", {"session_id": session_id, "data": replay}, to=sid)
    else:
        socketio.emit("ssh_output", {"session_id": session_id, "data": "\r\n（已恢复到之前的会话）\r\n"}, to=sid)


@socketio.on("ssh_input")
def on_ssh_input(data):
    session = session_manager.get(_session_key(data))
    if session:
        session.send(data.get("data", ""))


@socketio.on("ssh_resize")
def on_ssh_resize(data):
    session = session_manager.get(_session_key(data))
    if session:
        session.resize(int(data.get("cols") or 120), int(data.get("rows") or 30))


@socketio.on("ssh_stop")
def on_ssh_stop(data=None):
    data = data or {}
    # 关闭事件由会话的 on_close 回调统一发出，避免重复提示
    session_manager.stop(_session_key(data))


@socketio.on("ssh_stop_all")
def on_ssh_stop_all(data=None):
    """关闭当前浏览器标签下的全部会话（关闭页面 / 清空标签）。"""
    session_manager.stop_workspace(current_user.id, (data or {}).get("ws") or "default")


@socketio.on("ssh_pending_close")
def on_ssh_pending_close(data=None):
    """页面即将离开（切页/刷新/关闭）：进入宽限期，重新接入则继续使用。"""
    session_manager.detach_workspace(
        current_user.id, (data or {}).get("ws") or "default",
        grace=current_app.config.get("SSH_PENDING_CLOSE_SECONDS", 30),
    )


@socketio.on("disconnect")
def on_disconnect():
    """socket 断开只解绑输出，会话继续在后台运行。"""
    session_manager.detach_sid(request.sid)


@socketio.on("ssh_exec")
def on_ssh_exec(data):
    """快速执行：优先复用该标签的会话连接，否则临时建立连接。"""
    sid = request.sid
    data = data or {}
    session_id = (data.get("session_id") or "").strip() or "default"
    host = db.session.get(Host, int(data.get("host_id") or 0))
    if not _accessible(host):
        socketio.emit("exec_result", {"session_id": session_id, "ok": False, "command": "",
                                      "output": "无访问权限", "error": "无访问权限", "exit_code": -1}, to=sid)
        return

    command = (data.get("command") or "").strip()
    if not command:
        return
    timeout = current_app.config["SSH_COMMAND_TIMEOUT"]

    session = session_manager.get(_session_key(data))
    if session and session.host.id == host.id:
        result = session.exec_command(command, timeout=timeout)
        if result.get("exit_code", -1) == -1:  # 复用当前连接失败时回退到独立连接
            result = run_command(host, command, timeout=timeout)
    else:
        result = run_command(host, command, timeout=timeout)
    result.setdefault("duration", 0)

    log_action(host, command, source="quick", ok=result.get("ok"), exit_code=result.get("exit_code"),
         output=(result.get("error") or "") + "\n" + (result.get("output") or ""))

    socketio.emit(
        "exec_result",
        {
            "session_id": session_id,
            "ok": result.get("ok", False),
            "command": command,
            "output": result.get("output", ""),
            "error": result.get("error", ""),
            "exit_code": result.get("exit_code", -1),
            "duration": result.get("duration", 0),
            "host": host.endpoint,
        },
        to=sid,
    )


@socketio.on("ssh_command_log")
def on_ssh_command_log(data):
    """记录终端中敲下的命令（用于审计）。"""
    command = (data.get("command") or "").strip()
    if not command:
        return
    host_id = int(data.get("host_id") or 0)
    host = db.session.get(Host, host_id) if host_id else None
    log_action(host, command, source="terminal")
