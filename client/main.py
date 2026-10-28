"""SSH 在线运维平台 - 桌面客户端（薄壳）。

客户端只负责打开服务端地址：首次启动填写服务端地址（保存在本机 JSON 配置），
之后直接进入该地址。服务端可以是 NAS / 服务器 / 本机部署的 ssh-web。
"""

import json
import os
import sys
import urllib.error
import urllib.request

import webview

APP_TITLE = "SSH 在线运维平台"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ssh-web-client.json")
SETUP_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "setup.html")


def resource_path(name):
    """兼容 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("server"):
            return data
    except Exception:  # noqa: BLE001 - 配置缺失或损坏时按首次启动处理
        pass
    return None


def save_config(server):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump({"server": server}, fh, ensure_ascii=False, indent=2)


def normalize(url):
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def probe(server, timeout=5):
    """探测服务端是否可达（无需登录）。"""
    server = normalize(server)
    if not server:
        return {"ok": False, "message": "请填写服务端地址"}
    try:
        with urllib.request.urlopen(server + "/api/ping", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "message": f"已连接（服务端版本 {data.get('version', '未知')}）"}
    except urllib.error.HTTPError as exc:
        # 老版本服务端没有 /api/ping，退回登录页探测
        if exc.code in (401, 403, 404):
            try:
                urllib.request.urlopen(server + "/auth/login", timeout=timeout).read()
                return {"ok": True, "message": "已连接（旧版服务端，建议升级）"}
            except Exception as inner:  # noqa: BLE001
                return {"ok": False, "message": f"无法访问：{inner}"}
        return {"ok": False, "message": f"服务端返回 {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"无法访问：{exc}"}


class Api:
    """暴露给页面的接口（页面通过 window.pywebview.api 调用）。"""

    def __init__(self):
        self.window = None

    def current_server(self):
        cfg = load_config()
        return cfg["server"] if cfg else ""

    def test_server(self, url):
        return probe(url)

    def save_server(self, url):
        result = probe(url)
        if not result.get("ok"):
            return result
        server = normalize(url)
        save_config(server)
        if self.window:
            self.window.set_title(APP_TITLE + " - " + server)
            self.window.load_url(server)
        return {"ok": True, "message": "已保存"}

    def change_server(self):
        """从服务端页面回到地址设置页。"""
        if self.window:
            self.window.load_html(read_setup_html())
        return True


def read_setup_html():
    path = resource_path("setup.html")
    if not os.path.exists(path):
        path = SETUP_HTML
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    api = Api()
    cfg = load_config()

    if cfg:
        result = probe(cfg["server"], timeout=3)
        if result.get("ok"):
            window = webview.create_window(APP_TITLE, url=cfg["server"], js_api=api,
                                           width=1360, height=860, min_size=(900, 600))
        else:
            window = webview.create_window(
                APP_TITLE, html=read_setup_html(), js_api=api,
                width=560, height=460, min_size=(480, 420))
    else:
        window = webview.create_window(APP_TITLE, html=read_setup_html(), js_api=api,
                                       width=560, height=460, min_size=(480, 420))

    api.window = window
    webview.start(debug=False)


if __name__ == "__main__":
    main()
