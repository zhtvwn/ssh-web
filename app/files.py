"""远端文件管理：基于 SFTP 的目录浏览、上传下载、新建/删除/重命名与文本预览。

所有接口都校验主机访问权限（本人创建 / 已共享 / 管理员），
路径由服务端 normalize，前端只负责展示与交互。
"""

import datetime as dt
import posixpath
import stat as stat_lib
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from . import db
from .audit import log_action
from .models import Host
from .ssh_service import build_client

files_bp = Blueprint("files", __name__)

MAX_PREVIEW_BYTES = 200 * 1024  # 文本预览最多读取的字节数
CHUNK = 65536


def _get_host(host_id):
    """取出当前用户有权访问的主机。"""
    host = db.session.get(Host, int(host_id or 0))
    if not host:
        return None
    if host.shared or current_user.is_admin or host.owner_id == current_user.id:
        return host
    return None


def _fail(message, host=None, command="", source="file"):
    if host:
        log_action(host, command, source=source, ok=False, output=str(message))
    return jsonify({"ok": False, "message": str(message)})


def _open_sftp(host):
    """建立一次 SFTP 通道，返回 (client, sftp)。"""
    client = build_client(host, timeout=current_app.config["SSH_CONNECT_TIMEOUT"])
    try:
        return client, client.open_sftp()
    except Exception:
        client.close()
        raise


def _close(client, sftp):
    for handle in (sftp, client):
        if not handle:
            continue
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass


def _normalize(sftp, path):
    """空路径表示远端登录用户的家目录。"""
    path = (path or "").strip()
    return sftp.normalize(path) if path else sftp.normalize(".")


def _mode_text(mode):
    """把 st_mode 转成 ls 风格的字符串，如 drwxr-xr-x。"""
    if stat_lib.S_ISLNK(mode):
        kind = "l"
    elif stat_lib.S_ISDIR(mode):
        kind = "d"
    else:
        kind = "-"
    text = kind
    for index, char in enumerate("rwxrwxrwx"):
        text += char if mode & (0o400 >> index) else "-"
    return text


def _time_text(mtime):
    if not mtime:
        return ""
    try:
        return dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def _entries(sftp, path):
    out = []
    for attr in sftp.listdir_attr(path):
        mode = attr.st_mode or 0
        out.append(
            {
                "name": attr.filename,
                "path": posixpath.join(path, attr.filename),
                "is_dir": stat_lib.S_ISDIR(mode),
                "is_link": stat_lib.S_ISLNK(mode),
                "size": attr.st_size or 0,
                "mode": _mode_text(mode),
                "mtime": _time_text(attr.st_mtime),
            }
        )
    out.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return out


@files_bp.route("/api/files/list")
@login_required
def list_dir():
    """列出远端目录内容。"""
    host = _get_host(request.args.get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        path = _normalize(sftp, request.args.get("path"))
        entries = _entries(sftp, path)
        parent = posixpath.dirname(path) if path not in ("/", "") else None
        return jsonify(
            {
                "ok": True,
                "path": path,
                "parent": parent if parent and parent != path else None,
                "home": sftp.normalize("."),
                "host": host.endpoint,
                "entries": entries,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读取目录失败：{exc}", host=host, command="(浏览目录)")
    finally:
        _close(client, sftp)


@files_bp.route("/api/files/download")
@login_required
def download():
    """下载远端文件（流式转发，不落盘）。"""
    host = _get_host(request.args.get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")

    path = (request.args.get("path") or "").strip()
    if not path:
        return _fail("缺少文件路径")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        attr = sftp.stat(path)
        if stat_lib.S_ISDIR(attr.st_mode or 0):
            return _fail("不能直接下载目录", host=host, command=f"(下载 {path})")
        size = attr.st_size or 0
    except Exception as exc:  # noqa: BLE001
        return _fail(f"下载失败：{exc}", host=host, command=f"(下载 {path})")
    finally:
        _close(client, sftp)

    name = posixpath.basename(path) or "download"

    def generate():
        stream_client = stream_sftp = None
        try:
            stream_client, stream_sftp = _open_sftp(host)
            with stream_sftp.open(path, "rb") as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk
        finally:
            _close(stream_client, stream_sftp)

    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Length"] = str(size)
    resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(name)
    log_action(host, f"(下载 {path})", source="file")
    return resp


@files_bp.route("/api/files/upload", methods=["POST"])
@login_required
def upload():
    """上传本地文件到远端当前目录（支持多文件，前端拖拽/选择均走这里）。"""
    host = _get_host(request.form.get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")

    storages = request.files.getlist("file")
    if not storages:
        return _fail("没有选择要上传的文件")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        path = _normalize(sftp, request.form.get("path"))
        uploaded, errors = [], []
        for storage in storages:
            name = posixpath.basename((storage.filename or "").replace("\\", "/"))
            if not name or name in (".", ".."):
                errors.append("跳过无有效文件名的条目")
                continue
            remote = posixpath.join(path, name)
            try:
                sftp.putfo(storage.stream, remote)
                uploaded.append(name)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}：{exc}")
        message = f"已上传 {len(uploaded)} 个文件到 {path}"
        if errors:
            message += "（失败 " + str(len(errors)) + " 个）"
        log_action(host, f"(上传 {len(uploaded)} 个文件到 {path})", source="file",
                   ok=not errors, output="\n".join(uploaded) + "\n" + "\n".join(errors))
        return jsonify({"ok": not errors, "path": path, "uploaded": uploaded,
                        "errors": errors, "message": message})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"上传失败：{exc}", host=host, command="(上传文件)")
    finally:
        _close(client, sftp)


@files_bp.route("/api/files/mkdir", methods=["POST"])
@login_required
def mkdir():
    host = _get_host((request.get_json(silent=True) or request.form).get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")
    payload = request.get_json(silent=True) or request.form
    name = (payload.get("name") or "").strip().replace("\\", "/")
    name = posixpath.basename(name)
    if not name:
        return _fail("目录名不能为空")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        path = _normalize(sftp, payload.get("path"))
        remote = posixpath.join(path, name)
        sftp.mkdir(remote)
        log_action(host, f"(新建目录 {remote})", source="file")
        return jsonify({"ok": True, "path": remote, "message": f"已创建 {remote}"})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"创建目录失败：{exc}", host=host, command="(新建目录)")
    finally:
        _close(client, sftp)


@files_bp.route("/api/files/remove", methods=["POST"])
@login_required
def remove():
    host = _get_host((request.get_json(silent=True) or request.form).get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")
    payload = request.get_json(silent=True) or request.form
    path = (payload.get("path") or "").strip()
    if not path:
        return _fail("缺少路径")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        target = sftp.normalize(path)
        attr = sftp.stat(target)
        if stat_lib.S_ISDIR(attr.st_mode or 0):
            sftp.rmdir(target)
        else:
            sftp.remove(target)
        log_action(host, f"(删除 {target})", source="file")
        return jsonify({"ok": True, "path": target, "message": f"已删除 {target}"})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"删除失败：{exc}", host=host, command=f"(删除 {path})")
    finally:
        _close(client, sftp)


@files_bp.route("/api/files/rename", methods=["POST"])
@login_required
def rename():
    host = _get_host((request.get_json(silent=True) or request.form).get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")
    payload = request.get_json(silent=True) or request.form
    path = (payload.get("path") or "").strip()
    name = posixpath.basename((payload.get("name") or "").strip().replace("\\", "/"))
    if not path or not name:
        return _fail("缺少路径或新名称")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        target = sftp.normalize(path)
        remote = posixpath.join(posixpath.dirname(target), name)
        sftp.rename(target, remote)
        log_action(host, f"(重命名 {target} -> {remote})", source="file")
        return jsonify({"ok": True, "path": remote, "message": f"已重命名为 {name}"})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"重命名失败：{exc}", host=host, command=f"(重命名 {path})")
    finally:
        _close(client, sftp)


@files_bp.route("/api/files/read")
@login_required
def read_file():
    """文本文件预览：只读取前 MAX_PREVIEW_BYTES 字节。"""
    host = _get_host(request.args.get("host_id"))
    if not host:
        return _fail("主机不存在或无访问权限")
    path = (request.args.get("path") or "").strip()
    if not path:
        return _fail("缺少文件路径")

    client = sftp = None
    try:
        client, sftp = _open_sftp(host)
        target = sftp.normalize(path)
        attr = sftp.stat(target)
        if stat_lib.S_ISDIR(attr.st_mode or 0):
            return _fail("目录不支持预览", host=host, command=f"(预览 {path})")
        with sftp.open(target, "rb") as handle:
            data = handle.read(MAX_PREVIEW_BYTES)
        size = attr.st_size or 0
        if b"\x00" in data:
            return jsonify({"ok": False, "message": "该文件是二进制文件，暂不支持预览（可直接下载）"})
        text = data.decode("utf-8", "replace")
        return jsonify(
            {
                "ok": True,
                "path": target,
                "name": posixpath.basename(target),
                "size": size,
                "truncated": size > len(data),
                "content": text,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读取文件失败：{exc}", host=host, command=f"(预览 {path})")
    finally:
        _close(client, sftp)
