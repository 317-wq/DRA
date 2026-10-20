"""桌面入口：本地起服务 + 控制窗口；PyInstaller 打包成便携版。

便携版约定：配置与数据都放在 exe 同级的 `data/`，解压到任意目录即可用。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dra import __version__

LOG_NAME = "desktop.log"


def _base_dir() -> Path:
    """打包后是可执行文件所在目录；开发时是当前工作目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _portable_env() -> Path:
    """把配置与数据固定到 exe 同级的 data/，避免写到用户目录。"""
    base = _base_dir()
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DRA_CONFIG_FILE", str(data / "config.toml"))
    os.environ.setdefault("DRA_DATA_DIR", str(data))
    return data


def _log(message: str, data: Path) -> None:
    try:
        with (data / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _fatal(message: str) -> None:
    """无控制台时用系统消息框告知失败，否则直接打印。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "DRA 启动失败", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def _setup_dialog(settings) -> bool:
    """首次运行配置向导；保存后返回 True。"""
    import tkinter as tk
    from tkinter import messagebox, ttk

    from dra.config import write_toml_config

    root = tk.Tk()
    root.title("DRA · 首次配置")
    root.geometry("520x300")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="填写两个 API Key 就可以开始（密钥只保存在本机）").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    entries = {}
    rows = [
        ("base_url", "模型端点", settings.base_url),
        ("model", "模型名", settings.model),
        ("api_key", "模型 API Key", ""),
        ("tavily_api_key", "Tavily API Key", ""),
    ]
    for index, (key, label, value) in enumerate(rows, start=1):
        ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, width=44, show="*" if key.endswith("api_key") else "")
        entry.insert(0, value)
        entry.grid(row=index, column=1, sticky="we", pady=4)
        entries[key] = entry

    saved = {"ok": False}

    def save() -> None:
        values = {
            "base_url": entries["base_url"].get().strip(),
            "model": entries["model"].get().strip(),
            "api_key": entries["api_key"].get().strip(),
            "tavily_api_key": entries["tavily_api_key"].get().strip(),
        }
        if not values["api_key"] or not values["tavily_api_key"]:
            messagebox.showerror("还差一点", "两个 API Key 都要填。")
            return
        write_toml_config(values)
        saved["ok"] = True
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
    ttk.Button(buttons, text="稍后配置", command=root.destroy).pack(side="left", padx=6)
    ttk.Button(buttons, text="保存并启动", command=save).pack(side="left")
    frame.columnconfigure(1, weight=1)
    root.mainloop()
    return saved["ok"]


def _control_window(url: str, data: Path, server) -> None:
    """常驻控制窗口：打开界面、查看数据目录、退出。"""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"DRA · v{__version__}")
    root.geometry("520x260")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Deep Research Agent 正在运行", font=("Segoe UI", 13, "bold")).pack(
        anchor="w"
    )
    ttk.Label(frame, text="界面地址").pack(anchor="w", pady=(14, 2))
    link = ttk.Label(frame, text=url, foreground="#2563eb", cursor="hand2")
    link.pack(anchor="w")
    link.bind("<Button-1>", lambda _event: webbrowser.open(url))
    ttk.Label(frame, text=f"数据目录：{data}").pack(anchor="w", pady=(10, 0))

    def open_ui() -> None:
        webbrowser.open(url)

    def quit_app() -> None:
        server.should_exit = True
        root.after(200, root.destroy)

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="e", side="bottom", fill="x")
    ttk.Button(buttons, text="打开界面", command=open_ui).pack(side="right")
    ttk.Button(buttons, text="退出", command=quit_app).pack(side="right", padx=8)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.after(800, open_ui)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    """启动本地服务与桌面窗口。"""
    import argparse

    parser = argparse.ArgumentParser(prog="dra-desktop", add_help=True)
    parser.add_argument("--port", type=int, default=0, help="固定端口；默认自动选择")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--headless", action="store_true", help="不开窗口，供脚本/自检使用")
    args = parser.parse_args(argv)

    data = _portable_env()
    _log("desktop starting", data)

    try:
        import uvicorn

        from dra.config import Settings
        from dra.jobs import RunManager
        from dra.web.server import create_app, find_free_port

        settings = Settings()
        if not args.headless and (not settings.api_key or not settings.tavily_api_key):
            if not _setup_dialog(settings):
                return 0
            settings = Settings()

        manager = RunManager(settings)
        app = create_app(settings, manager=manager)
        port = args.port or find_free_port("127.0.0.1", 0)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            log_config=None,  # 冻结环境里 dictConfig 取不到 uvicorn.logging
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name="dra-uvicorn")
        thread.start()

        deadline = time.time() + 20
        while time.time() < deadline and not server.started:
            time.sleep(0.05)
        if not server.started:
            _fatal("本地服务启动超时，请把 data/desktop.log 发给开发者。")
            return 1

        url = f"http://127.0.0.1:{port}"
        _log(f"listening {url}", data)
        if not args.no_browser and args.headless:
            webbrowser.open(url)
        if args.headless:
            thread.join()
            return 0
        _control_window(url, data, server)
        server.should_exit = True
        thread.join(timeout=3)
        return 0
    except Exception as exc:  # 桌面程序要把异常变成可读信息
        _log(f"fatal: {type(exc).__name__}: {exc}", data)
        _fatal(f"启动失败：{type(exc).__name__}: {exc}\n\n日志：{data / LOG_NAME}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
