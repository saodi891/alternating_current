# -*- coding: utf-8 -*-
"""
交流电 —— QQ 语音发送小工具（图形界面版 · PySide6）
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

from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPainter,
    QColor,
    QPen,
    QBrush,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QInputDialog,
    QMessageBox,
    QStyle,
    QCheckBox,
    QStyledItemDelegate,
)

import websockets

# ============ 路径与配置 ============

def base_dir():
    """程序所在目录。打包成 exe 后用 exe 的目录，否则用脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """找打包进 exe 的只读资源（图标、字体、图标素材）。

    PyInstaller 单文件模式运行时会把资源解压到临时目录 sys._MEIPASS，
    没打包时就用脚本所在目录。
    """
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, name)


BASE_DIR = base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
VOICE_DIR = os.path.join(BASE_DIR, "voices")
ICON_PATH = resource_path("icon.ico")
FONT_DIR = resource_path(os.path.join("assets", "fonts"))
ICON_DIR = resource_path(os.path.join("assets", "icons"))
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
            cfg.setdefault("ws_url", DEFAULT_CONFIG["ws_url"])
            cfg.setdefault("access_token", DEFAULT_CONFIG["access_token"])
            cfg.setdefault("targets", [])
            return cfg
        except (OSError, ValueError):
            pass
    save_config(DEFAULT_CONFIG)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    """把配置写回 config.json。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        QMessageBox.critical(None, "交流电", f"保存配置失败：{e}")


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
        return name
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        name = f"{stem}({i}){ext}"
        dst = os.path.join(VOICE_DIR, name)
        i += 1
    try:
        shutil.copy2(src_path, dst)
    except OSError as e:
        QMessageBox.critical(None, "交流电", f"导入失败 {src_path}：{e}")
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

    每次发送都新开连接、发完就关，界面空闲期间不占连接，
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


# ============ 外观：配色 / 字体 / 图标 / 纯色文字 ============

BG_TOP = "#f4f5f7"       # 银白背景 顶
BG_BOTTOM = "#dcdee3"    # 银白背景 底
CARD = "#fbfbfc"         # 卡片底
LINE = "#cdcdda"         # 边框 / 分隔线
TEXT_COLOR = "#3a3f45"   # 纯色文字（深灰）
ACCENT = "#0e9c93"       # 强调色（置顶按钮边/图标）
ACCENT_HI = "#67fff4"    # 强调色亮版（置顶选中底）

FONT_FAMILY = None       # 得意黑加载成功后填入族名，失败则保持 None 用系统默认


def load_font():
    """扫描 assets/fonts 里的 ttf/otf 加载得意黑，成功返回族名，失败返回 None。"""
    if not os.path.isdir(FONT_DIR):
        return None
    for name in sorted(os.listdir(FONT_DIR)):
        if name.lower().endswith((".ttf", ".otf")):
            fid = QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, name))
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                return fams[0]
    return None


def ui_font(pt, bold=True):
    f = QFont(FONT_FAMILY) if FONT_FAMILY else QFont()
    f.setPointSize(pt)
    f.setBold(bold)
    return f


def icon_pixmap(name, size):
    """assets/icons/<name>.png → 缩放好的 QPixmap；不存在返回 None。"""
    if not name:
        return None
    path = os.path.join(ICON_DIR, name + ".png")
    if not os.path.isfile(path):
        return None
    pix = QPixmap(path)
    if pix.isNull():
        return None
    return pix.scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def draw_text(painter, x, rect, text, font):
    """在 rect 竖向居中，从 x 起画纯色、无描边的文字。"""
    if not text:
        return
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(font)
    fm = QFontMetricsF(font)
    y = rect.y() + (rect.height() - fm.height()) / 2 + fm.ascent()
    painter.setPen(QColor(TEXT_COLOR))
    painter.drawText(int(round(x)), int(round(y)), text)


class GradientLabel(QLabel):
    """文字用纯色绘制的 Label。"""

    def __init__(self, text="", pt=13, bold=True, parent=None):
        super().__init__(text, parent)
        self.setFont(ui_font(pt, bold))
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, _):
        text = self.text()
        if not text:
            return
        p = QPainter(self)
        r = QRectF(self.contentsRect())
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(text)
        al = self.alignment()
        if al & Qt.AlignHCenter:
            x = r.x() + (r.width() - tw) / 2
        elif al & Qt.AlignRight:
            x = r.right() - tw
        else:
            x = r.x()
        draw_text(p, x, r, text, self.font())


class GradientButton(QPushButton):
    """圆角银白底 + #cdcdda 边 + 纯色文字，可带前置图标。"""

    def __init__(self, text="", icon_name=None, pt=12, parent=None):
        super().__init__(parent)
        self._label = text
        self._pix = icon_pixmap(icon_name, 18)
        self.setFont(ui_font(pt, True))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        fm = QFontMetricsF(self.font())
        w = fm.horizontalAdvance(text) + 26
        if self._pix:
            w += self._pix.width() + 6
        self.setMinimumWidth(int(w))

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        if self.isDown():
            bg = QColor("#e6e8ec")
        elif self.underMouse():
            bg = QColor("#ffffff")
        else:
            bg = QColor(CARD)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(LINE), 1.2))
        p.drawRoundedRect(r, 9, 9)
        fm = QFontMetricsF(self.font())
        tw = fm.horizontalAdvance(self._label)
        iw = self._pix.width() if self._pix else 0
        gap = 6 if self._pix else 0
        x = r.x() + (r.width() - (iw + gap + tw)) / 2
        if self._pix:
            iy = r.y() + (r.height() - self._pix.height()) / 2
            p.drawPixmap(int(x), int(iy), self._pix)
        draw_text(p, x + iw + gap, r, self._label, self.font())


class GradientDelegate(QStyledItemDelegate):
    """列表每行文字用纯色绘制。"""

    def __init__(self, pt=12, parent=None):
        super().__init__(parent)
        self._font = ui_font(pt, False)

    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#e3f7f5"))
        r = QRectF(option.rect).adjusted(10, 0, -6, 0)
        draw_text(painter, r.x(), r, index.data() or "", self._font)
        painter.restore()

    def sizeHint(self, option, index):
        fm = QFontMetricsF(self._font)
        return QSize(
            int(fm.horizontalAdvance(index.data() or "") + 24),
            int(fm.height() + 12),
        )


QSS = f"""
#root {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {BG_TOP}, stop:1 {BG_BOTTOM}); }}
QFrame#card {{ background:{CARD}; border:1px solid {LINE}; border-radius:14px; }}
QFrame#dropzone {{ background:#ffffff; border:2px dashed {LINE}; border-radius:12px; }}
QListWidget {{ background:#ffffff; border:1px solid {LINE}; border-radius:10px; outline:0; }}
QListWidget::item:selected {{ background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{LINE}; border-radius:5px; min-height:24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
"""


# ============ 界面 ============

class VoiceWindow(QWidget):
    send_result = Signal(bool, str, str)  # ok, label, msg

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.setObjectName("root")
        self.setWindowTitle("交流电")
        self.resize(760, 500)
        self.setMinimumSize(680, 440)
        self.setAcceptDrops(True)
        self.setStyleSheet(QSS)
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.send_result.connect(self._send_done)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self._icon_label("pin", 16))
        top.addWidget(GradientLabel("窗口置顶", pt=11))
        self.pin_cb = QCheckBox()
        self.pin_cb.setToolTip("窗口置顶")
        self.pin_cb.setCursor(Qt.PointingHandCursor)
        self.pin_cb.toggled.connect(self.toggle_topmost)
        top.addWidget(self.pin_cb)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_left(), 1)
        body.addWidget(self._build_right(), 1)
        outer.addLayout(body, 1)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addWidget(self._icon_label("status", 14))
        self.status_label = GradientLabel("就绪", pt=10, bold=False)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        outer.addLayout(status_row)

        self.refresh_targets()
        self.refresh_audio()

    # ---- 通用小部件 ----

    def _icon_label(self, name, size):
        lab = QLabel()
        pix = icon_pixmap(name, size)
        if pix:
            lab.setPixmap(pix)
        else:
            lab.setFixedWidth(0)  # 缺图标就不占位
        return lab

    def _title_row(self, icon_name, text):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(self._icon_label(icon_name, 18))
        h.addWidget(GradientLabel(text, pt=13))
        h.addStretch(1)
        return w

    def _card(self):
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        return f, v

    def _build_left(self):
        card, v = self._card()
        v.addWidget(self._title_row("contacts", "发送对象（好友 / 群）"))
        self.target_list = QListWidget()
        self.target_list.setItemDelegate(GradientDelegate(12, self.target_list))
        v.addWidget(self.target_list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        for text, icon, slot in (
            ("加好友", "add_friend", self.add_friend),
            ("加群", "add_group", self.add_group),
            ("改备注", "edit_note", self.edit_note),
            ("删除", "delete_target", self.remove_target),
        ):
            b = GradientButton(text, icon)
            b.clicked.connect(lambda _=False, s=slot: s())
            row.addWidget(b)
        v.addLayout(row)
        return card

    def _build_right(self):
        card, v = self._card()
        v.addWidget(self._title_row("audio", "语音文件（把 wav/mp3 拖进来）"))
        drop = QFrame()
        drop.setObjectName("dropzone")
        drop.setMinimumHeight(56)
        dh = QHBoxLayout(drop)
        dh.setContentsMargins(10, 6, 10, 6)
        dh.setSpacing(6)
        dh.addStretch(1)
        dh.addWidget(self._icon_label("drop", 20))
        dh.addWidget(GradientLabel("把音频文件拖到这里", pt=12))
        dh.addStretch(1)
        v.addWidget(drop)
        self.audio_list = QListWidget()
        self.audio_list.setItemDelegate(GradientDelegate(12, self.audio_list))
        v.addWidget(self.audio_list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        b_refresh = GradientButton("刷新", "refresh")
        b_refresh.clicked.connect(lambda: self.refresh_audio())
        b_del = GradientButton("删除文件", "delete_file")
        b_del.clicked.connect(lambda: self.delete_audio())
        row.addWidget(b_refresh)
        row.addWidget(b_del)
        row.addStretch(1)
        b_send = GradientButton("发送语音", "send")
        b_send.clicked.connect(lambda: self.send_selected())
        row.addWidget(b_send)
        v.addLayout(row)
        return card

    # ---- 目标列表操作 ----

    def target_label(self, item):
        if item["id"].startswith("#"):
            return f"[群] {item['id'][1:]}（{item['note']}）"
        return f"{item['id']}（{item['note']}）"

    def refresh_targets(self):
        self.target_list.clear()
        for item in self.cfg["targets"]:
            self.target_list.addItem(self.target_label(item))

    def selected_target_index(self):
        row = self.target_list.currentRow()
        return row if row >= 0 else None

    def add_friend(self):
        qq, ok = QInputDialog.getText(self, "加好友", "输入好友 QQ 号：")
        if not ok or not qq:
            return
        qq = qq.strip()
        if not qq.isdigit():
            QMessageBox.warning(self, "交流电", "QQ 号只能是数字。")
            return
        note, ok = QInputDialog.getText(self, "加好友", "备注（可留空）：")
        self._add_target(qq, note.strip() if ok and note else "")

    def add_group(self):
        gid, ok = QInputDialog.getText(self, "加群", "输入群号：")
        if not ok or not gid:
            return
        gid = gid.strip().lstrip("#")
        if not gid.isdigit():
            QMessageBox.warning(self, "交流电", "群号只能是数字。")
            return
        note, ok = QInputDialog.getText(self, "加群", "备注（可留空）：")
        self._add_target("#" + gid, note.strip() if ok and note else "")

    def _add_target(self, tid, note):
        if any(t["id"] == tid for t in self.cfg["targets"]):
            QMessageBox.information(self, "交流电", "这个号已经在列表里了。")
            return
        self.cfg["targets"].append({"id": tid, "note": note})
        save_config(self.cfg)
        self.refresh_targets()
        self.set_status(f"已添加 {tid}")

    def edit_note(self):
        idx = self.selected_target_index()
        if idx is None:
            QMessageBox.information(self, "交流电", "先在左边选一个对象。")
            return
        item = self.cfg["targets"][idx]
        note, ok = QInputDialog.getText(
            self, "改备注", "新备注：", text=item["note"]
        )
        if not ok:
            return
        item["note"] = note.strip()
        save_config(self.cfg)
        self.refresh_targets()
        self.target_list.setCurrentRow(idx)
        self.set_status("备注已更新")

    def remove_target(self):
        idx = self.selected_target_index()
        if idx is None:
            QMessageBox.information(self, "交流电", "先在左边选一个对象。")
            return
        item = self.cfg["targets"][idx]
        if QMessageBox.question(
            self, "交流电", f"确定删除 {self.target_label(item)}？"
        ) != QMessageBox.Yes:
            return
        self.cfg["targets"].pop(idx)
        save_config(self.cfg)
        self.refresh_targets()
        self.set_status("已删除")
    # ---- 音频操作 ----

    def refresh_audio(self):
        self.audio_list.clear()
        for name in list_audio_files():
            self.audio_list.addItem(name)

    def delete_audio(self):
        row = self.audio_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "交流电", "先在右边选一个音频。")
            return
        name = self.audio_list.item(row).text()
        if QMessageBox.question(
            self, "交流电", f"从 voices 删除 {name}？"
        ) != QMessageBox.Yes:
            return
        try:
            os.remove(os.path.join(VOICE_DIR, name))
        except OSError as e:
            QMessageBox.critical(self, "交流电", f"删除失败：{e}")
            return
        self.refresh_audio()
        self.set_status(f"已删除 {name}")

    # ---- 拖拽导入 ----

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        added = 0
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and import_audio(p):
                added += 1
        self.refresh_audio()
        self.set_status(
            f"已导入 {added} 个音频到 voices" if added else "没有可导入的 wav/mp3"
        )

    # ---- 发送 ----

    def send_selected(self):
        tidx = self.selected_target_index()
        if tidx is None:
            QMessageBox.information(self, "交流电", "先在左边选一个发送对象。")
            return
        row = self.audio_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "交流电", "先在右边选一个音频。")
            return
        item = self.cfg["targets"][tidx]
        target = item["id"]
        label = self.target_label(item)
        fpath = os.path.join(VOICE_DIR, self.audio_list.item(row).text())
        self.set_status(f"正在发送到 {label} ...")
        threading.Thread(
            target=self._send_worker, args=(target, label, fpath), daemon=True
        ).start()

    def _send_worker(self, target, label, fpath):
        ok, msg = asyncio.run(
            _send(self.cfg["ws_url"], self.cfg["access_token"], target, fpath)
        )
        self.send_result.emit(ok, label, msg)

    def _send_done(self, ok, label, msg):
        if ok:
            self.set_status(f"[已发送] -> {label}")
        else:
            self.set_status(f"[失败] {msg}")
            QMessageBox.critical(self, "交流电", msg)

    # ---- 其它 ----

    def toggle_topmost(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        self.show()
        self.set_status("窗口已置顶" if on else "取消置顶")

    def set_status(self, text):
        self.status_label.setText(text)


def main():
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "alternating_current.voice"
            )
        except Exception:
            pass
    global FONT_FAMILY
    app = QApplication(sys.argv)
    FONT_FAMILY = load_font()
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = VoiceWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
