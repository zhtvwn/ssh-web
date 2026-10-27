"""SSH 能力封装：交互式会话、一次性命令执行、连接测试。"""

import io
import os
import select
import socket
import threading
import time
import datetime as dt

import paramiko

from .models import Host


class SSHConnectError(Exception):
    pass


def _load_private_key(key_text: str, passphrase: str = ""):
    last_error = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(key_text), password=passphrase or None)
        except Exception as exc:  # 逐个类型尝试
            last_error = exc
    raise SSHConnectError(f"私钥解析失败：{last_error}")


def build_client(host: Host, timeout: int = 10) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # 关闭指纹校验时不再读取 known_hosts，配合 AutoAddPolicy 自动接受新指纹
    known_hosts = os.environ.get("KNOWN_HOSTS_PATH", "") if getattr(host, "verify_host_key", True) else ""
    if known_hosts and os.path.exists(known_hosts):
        try:
            client.load_host_keys(known_hosts)
        except Exception:
            pass
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if host.auth_type == "key":
        key_text = host.private_key
        if not key_text.strip():
            raise SSHConnectError("该主机配置为密钥认证，但未保存私钥")
        pkey = _load_private_key(key_text, host.passphrase)

    password = host.password if host.auth_type == "password" else None
    try:
        client.connect(
            hostname=host.hostname,
            port=host.port or 22,
            username=host.username,
            password=password,
            pkey=pkey,
            passphrase=host.passphrase if host.auth_type == "key" else None,
            timeout=timeout,
            banner_timeout=timeout + 5,
            auth_timeout=timeout + 5,
            allow_agent=False,
            look_for_keys=False,
        )
    except socket.timeout:
        raise SSHConnectError(f"连接 {host.endpoint} 超时")
    except paramiko.AuthenticationException:
        raise SSHConnectError("认证失败：用户名或凭据不正确")
    except paramiko.SSHException as exc:
        hint = ""
        if "does not match" in str(exc):
            hint = "（主机指纹已变更，可在主机编辑中关闭「校验主机指纹」后重连）"
        raise SSHConnectError(f"SSH 协议错误：{exc}{hint}")
    except OSError as exc:
        raise SSHConnectError(f"无法连接 {host.endpoint}：{exc}")
    except Exception as exc:  # noqa: BLE001
        raise SSHConnectError(f"连接失败：{exc}")

    if known_hosts:
        try:
            os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
            client.save_host_keys(known_hosts)
        except Exception:
            pass
    return client


def test_connection(host: Host, timeout: int = 10) -> tuple:
    """测试连通性，返回 (ok, message)。"""
    client = None
    try:
        client = build_client(host, timeout=timeout)
        _, stdout, stderr = client.exec_command("echo ok", timeout=timeout)
        code = stdout.channel.recv_exit_status()
        err = stderr.read().decode("utf-8", "replace").strip()
        if code == 0 and not err:
            return True, f"连接成功：{host.endpoint}"
        return False, f"命令返回 {code}：{err or '未知错误'}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        if client:
            client.close()


def run_command(host: Host, command: str, timeout: int = 60) -> dict:
    """一次性执行命令（非交互）。"""
    client = None
    started = time.time()
    try:
        client = build_client(host, timeout=min(timeout, 15))
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {
            "ok": code == 0,
            "exit_code": code,
            "output": out,
            "error": err,
            "duration": round(time.time() - started, 2),
        }
    except socket.timeout:
        return {"ok": False, "exit_code": -1, "output": "", "error": f"命令执行超时（{timeout}s）",
                "duration": round(time.time() - started, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exit_code": -1, "output": "", "error": str(exc),
                "duration": round(time.time() - started, 2)}
    finally:
        if client:
            client.close()


REPLAY_MAX = 131072  # 断开页面期间保留的输出字节数，用于返回页面时回放


class SSHSession:
    """一个交互式 shell 会话，后台线程读取输出。

    会话与 socket 连接解耦：页面切走时只是 detach（后台继续运行并缓存输出），
    回到页面重新 attach 即可继续操作，只有点「×」或超时回收才真正断开。
    """

    def __init__(self, key: str, host: Host, cols: int, rows: int, on_output, on_close,
                 timeout: int = 10, sid: str = ""):
        self.key = key
        self.host = host
        self.on_output = on_output
        self.on_close = on_close
        self.sid = sid
        self.created_at = dt.datetime.utcnow()
        self.detached_at = None
        self.pending_close = None
        self._replay = bytearray()
        self._stop = threading.Event()
        self._closed = False
        self._lock = threading.Lock()

        self.client = build_client(host, timeout=timeout)
        try:
            # 每 30 秒发一次 SSH keepalive，避免空闲被服务端/防火墙踢掉
            self.client.get_transport().set_keepalive(30)
        except Exception:
            pass
        self.channel = self.client.invoke_shell(
            term=os.environ.get("SSH_TERM", "xterm-256color"),
            width=cols or 120,
            height=rows or 30,
        )
        self.channel.settimeout(0.1)
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([self.channel], [], [], 0.2)
                if r:
                    data = self.channel.recv(65536)
                    if not data:
                        break
                    self._record(data)
                    self.on_output(data.decode("utf-8", "replace"))
                elif self.channel.exit_status_ready() and not self.channel.recv_ready():
                    break
        except (OSError, ValueError) as exc:
            # 主动断开时句柄会被关闭，这类错误无需提示
            if not self._stop.is_set():
                self.on_output(f"\r\n[连接已断开] {exc}\r\n")
        except Exception as exc:  # noqa: BLE001
            self.on_output(f"\r\n[会话读取异常] {exc}\r\n")
        finally:
            self.close()

    def _record(self, data: bytes):
        """缓存最近输出，供页面返回时回放。"""
        self._replay.extend(data)
        if len(self._replay) > REPLAY_MAX:
            del self._replay[: len(self._replay) - REPLAY_MAX]

    def attach(self, sid: str, on_output, on_close) -> str:
        """重新绑定输出回调，返回断连期间缓存的输出。"""
        self.sid = sid
        self.on_output = on_output
        self.on_close = on_close
        self.detached_at = None
        self.pending_close = None
        replay = bytes(self._replay).decode("utf-8", "replace")
        self._replay = bytearray()
        return replay

    def detach(self, grace: int = None):
        """与当前 socket 解绑，会话继续在后台运行。

        grace：页面发出的「即将离开」宽限期（秒），超时未重新接入则回收；
        不传表示正常脱离（按 SSH_DETACH_TIMEOUT 回收）。
        """
        self.sid = ""
        self.on_output = lambda text: None
        self.on_close = None
        self.detached_at = dt.datetime.utcnow()
        self.pending_close = grace

    def send(self, data: str):
        if self._closed or not self.channel:
            return
        with self._lock:
            try:
                self.channel.send(data)
            except Exception as exc:  # noqa: BLE001
                self.on_output(f"\r\n[发送失败] {exc}\r\n")
                self.close()

    def resize(self, cols: int, rows: int):
        if self._closed or not self.channel:
            return
        try:
            self.channel.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def exec_command(self, command: str, timeout: int) -> dict:
        """复用当前连接执行命令（不影响交互 shell）。"""
        try:
            _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            return {"ok": code == 0, "exit_code": code, "output": out, "error": err}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "exit_code": -1, "output": "", "error": str(exc)}

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass
        if self.on_close:
            try:
                self.on_close(self.key)
            except Exception:
                pass


def make_key(user_id: int, workspace: str = "", session_id: str = "") -> str:
    """会话键：用户 + 浏览器标签（workspace）+ 终端标签 id。

    与 socket sid 无关，因此切换页面导致 socket 重连后仍能找回原会话。
    """
    return f"{user_id}:{workspace or 'default'}:{session_id or 'default'}"


class SessionManager:
    """按 用户:workspace:session_id 管理会话，支持脱离/重连与超时回收。"""

    def __init__(self):
        self.sessions = {}
        self._lock = threading.Lock()

    def start(self, key, host, cols, rows, sid, on_output, on_close, timeout=10) -> SSHSession:
        self.stop(key)
        session = SSHSession(key, host, cols, rows, on_output, on_close, timeout=timeout, sid=sid)
        with self._lock:
            self.sessions[key] = session
        return session

    def get(self, key):
        with self._lock:
            return self.sessions.get(key)

    def stop(self, key):
        with self._lock:
            session = self.sessions.pop(key, None)
        if session:
            session.close()

    def stop_host(self, host_id: int):
        with self._lock:
            targets = [k for k, s in self.sessions.items() if s.host.id == host_id]
        for key in targets:
            self.stop(key)

    def stop_workspace(self, user_id: int, workspace: str):
        """关闭某个浏览器标签下的全部会话（用户主动关闭页面/标签）。"""
        prefix = f"{user_id}:{workspace or 'default'}:"
        with self._lock:
            targets = [k for k in self.sessions if k.startswith(prefix)]
        for key in targets:
            self.stop(key)

    def detach_sid(self, sid: str):
        """socket 断开时不关闭会话，仅解绑输出回调。"""
        with self._lock:
            sessions = [s for s in self.sessions.values() if s.sid == sid]
        for session in sessions:
            session.detach()

    def detach_workspace(self, user_id: int, workspace: str, grace: int = None):
        """页面即将离开：解绑并按宽限期等待重新接入。"""
        prefix = f"{user_id}:{workspace or 'default'}:"
        with self._lock:
            sessions = [s for s in self.sessions.values() if s.key.startswith(prefix)]
        for session in sessions:
            session.detach(grace)

    def count_for(self, user_id: int, workspace: str) -> int:
        prefix = f"{user_id}:{workspace or 'default'}:"
        with self._lock:
            return len([k for k in self.sessions if k.startswith(prefix)])

    def sweep(self, max_seconds: int, detach_seconds: int = 0):
        """回收超时会话；detach_seconds 用于回收长时间没有页面接入的孤立会话。"""
        now = dt.datetime.utcnow()
        with self._lock:
            keys = list(self.sessions.keys())
        for key in keys:
            session = self.get(key)
            if not session:
                continue
            if max_seconds and (now - session.created_at).total_seconds() > max_seconds:
                session.send("echo '\\n[会话超时已被系统断开]' && exit\n")
                self.stop(key)
            elif detach_seconds and session.detached_at and not session.sid:
                limit = session.pending_close or detach_seconds
                if (now - session.detached_at).total_seconds() > limit:
                    self.stop(key)

    def count(self) -> int:
        with self._lock:
            return len(self.sessions)


session_manager = SessionManager()
