# -*- coding: utf-8 -*-
"""
交流电 —— QQ 语音发送小工具（图形界面版）
------------------------------------------------
把 voices 文件夹里的 wav / mp3 当作 QQ 语音发给好友或群。

窗口分两栏：
  · 左边：好友 / 群 列表，可增删、改备注（保存在 config.json，重启还在）
  · 右边：音频列表，把 wav/mp3 拖进窗口即自动收入 voices 文件夹，选中后点发送

原理：NapCat 已登录你的 QQ，本工具通过 WebSocket 让它把音频当语音发出去。
NapCat 内置转码，wav/mp3 会自动转成 QQ 需要的格式，不用自己转 silk。
"""


import asyncio
import json
import os
import shutil
import sys
import threading
from tkinter import (
    BooleanVar,
    Button,
    Checkbutton,
    END,
    Frame,
    Label,
    Listbox,
    SINGLE,
    StringVar,
    Tk,
    messagebox,
    simpledialog,
)

from tkinterdnd2 import DND_FILES, TkinterDnD

import websockets


# ============ 路径与配置 ============

def base_dir():
    """程序所在目录。打包成 exe 后用 exe 的目录，否则用脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """找打包进 exe 的只读资源（比如图标）。

    PyInstaller 单文件模式运行时会把资源解压到临时目录 sys._MEIPASS，
    没打包时就用脚本所在目录。
    """
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, name)


BASE_DIR = base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
VOICE_DIR = os.path.join(BASE_DIR, "voices")
ICON_PATH = resource_path("icon.ico")
AUDIO_EXTS = (".wav", ".mp3")

# 首次运行、config.json 不存在时的默认配置
DEFAULT_CONFIG = {
    "ws_url": "ws://127.0.0.1:3001",
    "access_token": "",
    # 每一项：{"id": "好友QQ号" 或 "#群号", "note": "备注"}
    "targets": [
        {"id": "33333333", "note": "示例qq"},
        {"id": "#111111", "note": "示例群"},
    ],
}


def load_config():
    """读 config.json；不存在或损坏就用默认配置并写回一份。"""
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 补齐可能缺失的字段
            cfg.setdefault("ws_url", DEFAULT_CONFIG["ws_url"])
            cfg.setdefault("access_token", DEFAULT_CONFIG["access_token"])
            cfg.setdefault("targets", [])
            return cfg
        except (OSError, ValueError):
            pass
    save_config(DEFAULT_CONFIG)
    return json.loads(json.dumps(DEFAULT_CONFIG))  # 返回一份副本


def save_config(cfg):
    """把配置写回 config.json。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        messagebox.showerror("交流电", f"保存配置失败：{e}")


# ============ 音频文件 ============

def list_audio_files():
    """列出 voices 文件夹里的音频文件名（不含路径）。"""
    if not os.path.isdir(VOICE_DIR):
        os.makedirs(VOICE_DIR, exist_ok=True)
        return []
    try:
        return [
            name
            for name in sorted(os.listdir(VOICE_DIR))
            if name.lower().endswith(AUDIO_EXTS)
            and os.path.isfile(os.path.join(VOICE_DIR, name))
        ]
    except OSError:
        return []


def import_audio(src_path):
    """把拖进来的音频复制进 voices 文件夹；重名自动加序号。返回最终文件名或 None。"""
    if not src_path.lower().endswith(AUDIO_EXTS):
        return None
    os.makedirs(VOICE_DIR, exist_ok=True)
    name = os.path.basename(src_path)
    dst = os.path.join(VOICE_DIR, name)
    if os.path.abspath(src_path) == os.path.abspath(dst):
        return name  # 本来就在文件夹里
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        name = f"{stem}({i}){ext}"
        dst = os.path.join(VOICE_DIR, name)
        i += 1
    try:
        shutil.copy2(src_path, dst)
    except OSError as e:
        messagebox.showerror("交流电", f"导入失败 {src_path}：{e}")
        return None
    return name


# ============ 发送（WebSocket） ============

def build_action(target, file_uri, echo):
    """按 target 是好友还是群，拼出对应的发送动作。"""
    voice_seg = [{"type": "record", "data": {"file": file_uri}}]
    if target.startswith("#"):
        return {
            "action": "send_group_msg",
            "params": {"group_id": int(target[1:]), "message": voice_seg},
            "echo": echo,
        }
    return {
        "action": "send_private_msg",
        "params": {"user_id": int(target), "message": voice_seg},
        "echo": echo,
    }


async def _send(ws_url, token, target, file_path):
    """临时连一次 NapCat 发送语音，等回执。返回 (成功?, 说明文字)。

    每次发送都新开连接、发完就关，菜单/界面空闲期间不占连接，
    不会被 NapCat 的 keepalive ping 超时掐断。
    """
    file_uri = "file:///" + file_path.replace("\\", "/")
    echo = f"voice-{target}-{os.path.basename(file_path)}"
    action = build_action(target, file_uri, echo)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        async with websockets.connect(
            ws_url, additional_headers=headers, ping_interval=20, ping_timeout=60
        ) as ws:
            await ws.send(json.dumps(action))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    return False, "30 秒没等到回执，检查 NapCat 日志"
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("echo") != echo:
                    continue
                if data.get("status") == "ok" or data.get("retcode") == 0:
                    return True, "发送成功"
                return False, str(
                    data.get("message") or data.get("wording") or data
                )
    except (OSError, websockets.WebSocketException) as e:
        return False, f"连接失败：{e}（检查 NapCat 是否登录、端口 3001、Token）"


# ============ 界面 ============

class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.status = StringVar(value="就绪")

        root.title("交流电")
        root.geometry("720x460")
        root.minsize(640, 400)

        # 窗口图标（标题栏左上角 + 任务栏）。default=True 让弹窗也用同一个图标
        if os.path.isfile(ICON_PATH):
            try:
                root.iconbitmap(default=ICON_PATH)
            except Exception:
                pass  # 个别环境不支持 .ico，忽略，不影响使用

        # ---- 顶栏：右上角「窗口置顶」开关 ----
        topbar = Frame(root, padx=10, pady=4)
        topbar.pack(fill="x")
        self.topmost = BooleanVar(value=False)
        Checkbutton(
            topbar,
            text="窗口置顶",
            variable=self.topmost,
            command=self.toggle_topmost,
        ).pack(side="right")

        body = Frame(root, padx=10)
        body.pack(fill="both", expand=True, pady=(0, 10))

        # ---- 左栏：目标列表 ----
        left = Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        Label(left, text="发送对象（好友 / 群）", anchor="w").pack(fill="x")
        self.target_box = Listbox(left, selectmode=SINGLE, exportselection=False)
        self.target_box.pack(fill="both", expand=True, pady=4)

        tbtns = Frame(left)
        tbtns.pack(fill="x")
        Button(tbtns, text="加好友", command=self.add_friend).pack(side="left")
        Button(tbtns, text="加群", command=self.add_group).pack(side="left", padx=4)
        Button(tbtns, text="改备注", command=self.edit_note).pack(side="left")
        Button(tbtns, text="删除", command=self.remove_target).pack(side="left", padx=4)

        # ---- 右栏：音频 ----
        right = Frame(body)
        right.pack(side="left", fill="both", expand=True)
        Label(right, text="语音文件（把 wav/mp3 拖进来）", anchor="w").pack(fill="x")

        self.drop = Label(
            right,
            text="⬇ 把音频文件拖到这里 ⬇",
            relief="ridge",
            bd=2,
            height=2,
            fg="#555",
        )
        self.drop.pack(fill="x", pady=4)
        self.drop.drop_target_register(DND_FILES)
        self.drop.dnd_bind("<<Drop>>", self.on_drop)

        self.audio_box = Listbox(right, selectmode=SINGLE, exportselection=False)
        self.audio_box.pack(fill="both", expand=True, pady=4)

        abtns = Frame(right)
        abtns.pack(fill="x")
        Button(abtns, text="刷新", command=self.refresh_audio).pack(side="left")
        Button(abtns, text="删除文件", command=self.delete_audio).pack(side="left", padx=4)
        Button(
            abtns, text="发送语音", command=self.send_selected
        ).pack(side="right")

        # ---- 底部状态栏 ----
        Label(
            root, textvariable=self.status, anchor="w", relief="sunken", bd=1
        ).pack(fill="x", side="bottom")

        # 整个窗口也接受拖拽
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self.on_drop)

        self.refresh_targets()
        self.refresh_audio()

    # ---- 目标列表操作 ----

    def target_label(self, item):
        if item["id"].startswith("#"):
            return f"[群] {item['id'][1:]}（{item['note']}）"
        return f"{item['id']}（{item['note']}）"

    def refresh_targets(self):
        self.target_box.delete(0, END)
        for item in self.cfg["targets"]:
            self.target_box.insert(END, self.target_label(item))

    def selected_target_index(self):
        sel = self.target_box.curselection()
        return sel[0] if sel else None

    def add_friend(self):
        qq = simpledialog.askstring("加好友", "输入好友 QQ 号：", parent=self.root)
        if not qq:
            return
        qq = qq.strip()
        if not qq.isdigit():
            messagebox.showwarning("交流电", "QQ 号只能是数字。")
            return
        note = simpledialog.askstring("加好友", "备注（可留空）：", parent=self.root) or ""
        self._add_target(qq, note.strip())

    def add_group(self):
        gid = simpledialog.askstring("加群", "输入群号：", parent=self.root)
        if not gid:
            return
        gid = gid.strip().lstrip("#")
        if not gid.isdigit():
            messagebox.showwarning("交流电", "群号只能是数字。")
            return
        note = simpledialog.askstring("加群", "备注（可留空）：", parent=self.root) or ""
        self._add_target("#" + gid, note.strip())

    def _add_target(self, tid, note):
        if any(t["id"] == tid for t in self.cfg["targets"]):
            messagebox.showinfo("交流电", "这个号已经在列表里了。")
            return
        self.cfg["targets"].append({"id": tid, "note": note})
        save_config(self.cfg)
        self.refresh_targets()
        self.set_status(f"已添加 {tid}")

    def edit_note(self):
        idx = self.selected_target_index()
        if idx is None:
            messagebox.showinfo("交流电", "先在左边选一个对象。")
            return
        item = self.cfg["targets"][idx]
        note = simpledialog.askstring(
            "改备注", "新备注：", initialvalue=item["note"], parent=self.root
        )
        if note is None:
            return
        item["note"] = note.strip()
        save_config(self.cfg)
        self.refresh_targets()
        self.target_box.selection_set(idx)
        self.set_status("备注已更新")

    def remove_target(self):
        idx = self.selected_target_index()
        if idx is None:
            messagebox.showinfo("交流电", "先在左边选一个对象。")
            return
        item = self.cfg["targets"][idx]
        if not messagebox.askyesno("交流电", f"确定删除 {self.target_label(item)}？"):
            return
        self.cfg["targets"].pop(idx)
        save_config(self.cfg)
        self.refresh_targets()
        self.set_status("已删除")

    # ---- 音频操作 ----

    def refresh_audio(self):
        self.audio_box.delete(0, END)
        for name in list_audio_files():
            self.audio_box.insert(END, name)

    def on_drop(self, event):
        # event.data 是拖入文件的路径，多个文件用空格分隔、带花括号包裹含空格的路径
        paths = self.root.tk.splitlist(event.data)
        added = 0
        for p in paths:
            if import_audio(p):
                added += 1
        self.refresh_audio()
        if added:
            self.set_status(f"已导入 {added} 个音频到 voices")
        else:
            self.set_status("没有可导入的 wav/mp3")

    def delete_audio(self):
        sel = self.audio_box.curselection()
        if not sel:
            messagebox.showinfo("交流电", "先在右边选一个音频。")
            return
        name = self.audio_box.get(sel[0])
        if not messagebox.askyesno("交流电", f"从 voices 删除 {name}？"):
            return
        try:
            os.remove(os.path.join(VOICE_DIR, name))
        except OSError as e:
            messagebox.showerror("交流电", f"删除失败：{e}")
            return
        self.refresh_audio()
        self.set_status(f"已删除 {name}")

    # ---- 发送 ----

    def send_selected(self):
        tidx = self.selected_target_index()
        if tidx is None:
            messagebox.showinfo("交流电", "先在左边选一个发送对象。")
            return
        asel = self.audio_box.curselection()
        if not asel:
            messagebox.showinfo("交流电", "先在右边选一个音频。")
            return

        item = self.cfg["targets"][tidx]
        target = item["id"]
        label = self.target_label(item)
        fpath = os.path.join(VOICE_DIR, self.audio_box.get(asel[0]))

        self.set_status(f"正在发送到 {label} ...")
        # 网络在后台线程跑，别卡住界面
        threading.Thread(
            target=self._send_worker, args=(target, label, fpath), daemon=True
        ).start()

    def _send_worker(self, target, label, fpath):
        ok, msg = asyncio.run(
            _send(self.cfg["ws_url"], self.cfg["access_token"], target, fpath)
        )
        # 回到主线程更新界面
        self.root.after(0, lambda: self._send_done(ok, label, msg))

    def _send_done(self, ok, label, msg):
        if ok:
            self.set_status(f"[已发送] -> {label}")
        else:
            self.set_status(f"[失败] {msg}")
            messagebox.showerror("交流电", msg)

    def toggle_topmost(self):
        # -topmost 让窗口一直浮在其它窗口上面
        self.root.attributes("-topmost", self.topmost.get())
        self.set_status("窗口已置顶" if self.topmost.get() else "取消置顶")

    def set_status(self, text):
        self.status.set(text)


def main():
    root = TkinterDnD.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
