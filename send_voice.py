# -*- coding: utf-8 -*-
"""
QQ 语音发送小工具
------------------------------------------------
把 voices 文件夹里的 wav / mp3 当作 QQ 语音（变声/语音条）发给好友。

用法：直接运行，按菜单操作——
  1. 从白名单里选一个要发的 QQ 号
  2. 从 voices 文件夹里选一个音频文件
  3. 发送，然后可以接着发下一条，或退出

原理：NapCat 已经登录了你的 QQ，本工具通过 WebSocket 让它把音频当语音发出去。
NapCat 内置了转码，wav/mp3 会自动转成 QQ 需要的语音格式，不用自己转 silk。
"""


import asyncio
import json
import os
import sys

import websockets  # pip install websockets

# ============ 配置区（改这里）============

# NapCat 里开启的正向 WebSocket 地址，和主机器人一致，一般不用改
WS_URL = "ws://127.0.0.1:3001"

# NapCat WebSocket 的鉴权 Token（填你在 NapCat 里设的那个；没设就留空 ""）
ACCESS_TOKEN = ""

# 可以发语音的白名单：好友 QQ 号，或群号（群号前面加 # 号，写成字符串）
# 每次运行会把这些列出来让你选一个。
#   好友：直接写 QQ 号
#   群聊：群号前加 #，写成字符串
VOICE_QQ = {
    3333333: "实例qq",
    "#1111111": "示例群",
}

# 存放语音音频的文件夹（和本脚本同目录下的 voices 文件夹）
VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

# ============ 以下是逻辑，一般不用改 ============

# 支持的音频后缀
AUDIO_EXTS = (".wav", ".mp3")


def list_audio_files():
    """列出 voices 文件夹里的音频文件名（不含路径），没有则返回空列表。"""
    if not os.path.isdir(VOICE_DIR):
        print(f"[警告] 找不到语音文件夹：{VOICE_DIR}")
        return []
    try:
        files = [
            name
            for name in sorted(os.listdir(VOICE_DIR))
            if name.lower().endswith(AUDIO_EXTS)
            and os.path.isfile(os.path.join(VOICE_DIR, name))
        ]
    except OSError as e:
        print(f"[警告] 无法读取语音文件夹 {VOICE_DIR}：{e}")
        return []
    return files


def choose_from(title, items, render):
    """通用的编号选择菜单。items 为空返回 None；输入 q 也返回 None（表示退出）。"""
    print(f"\n{title}")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {render(item)}")
    while True:
        raw = input("输入编号（q 退出）：").strip()
        if raw.lower() == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("编号不对，重新输入。")


def build_action(target, file_uri, echo):
    """按 target 是好友还是群，拼出对应的发送动作。

    target 为字符串且以 # 开头 -> 群聊，走 send_group_msg；
    否则当作好友 QQ 号，走 send_private_msg。
    """
    voice_seg = [{"type": "record", "data": {"file": file_uri}}]
    if isinstance(target, str) and target.startswith("#"):
        group_id = int(target[1:])
        return {
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": voice_seg},
            "echo": echo,
        }
    return {
        "action": "send_private_msg",
        "params": {"user_id": int(target), "message": voice_seg},
        "echo": echo,
    }


async def send_voice(target, label, file_path):
    """临时连一次 NapCat，把本地音频当语音发给好友或群，等回执确认后关闭连接。

    每次发送都新开连接、发完就关——这样菜单等待期间不占着连接，
    也就不会因为空闲太久被 NapCat 的 keepalive ping 超时掐断。
    """
    file_uri = "file:///" + file_path.replace("\\", "/")
    echo = f"voice-{target}-{os.path.basename(file_path)}"
    action = build_action(target, file_uri, echo)

    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"} if ACCESS_TOKEN else None
    try:
        async with websockets.connect(
            WS_URL, additional_headers=headers, ping_interval=20, ping_timeout=60
        ) as ws:
            await ws.send(json.dumps(action))

            # 读回执：NapCat 会推普通事件，也会回带同一个 echo 的结果，只等我们那条
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    print("[超时] 30 秒没等到回执，可能没发出去，检查 NapCat 日志。")
                    return
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("echo") != echo:
                    continue  # 不是我们这条的回执，忽略
                if data.get("status") == "ok" or data.get("retcode") == 0:
                    print(f"[已发送] 语音 -> {label}（{target}）")
                else:
                    msg = data.get("message") or data.get("wording") or str(data)
                    print(f"[发送失败] {msg}")
                return
    except (OSError, websockets.WebSocketException) as e:
        print(f"[连接失败] {e}")
        print("检查：NapCat 是否已登录、正向 WebSocket（端口 3001）是否开启、Token 是否一致。")


async def run():
    if not VOICE_QQ:
        print("[退出] VOICE_QQ 白名单是空的，先在脚本里填几个 QQ 号或群号")
        return

    targets = list(VOICE_QQ.items())  # [(好友QQ号 或 "#群号", 备注), ...]
    while True:
        # 1. 选好友或群（群号菜单里标个「群」字，一眼能区分）
        picked = await asyncio.to_thread(
            choose_from,
            "选择要发语音的对象：",
            targets,
            lambda kv: (
                f"[群] {str(kv[0])[1:]}（{kv[1]}）"
                if isinstance(kv[0], str) and kv[0].startswith("#")
                else f"{kv[0]}（{kv[1]}）"
            ),
        )
        if picked is None:
            print("退出。")
            return
        target, label = picked

        # 2. 选音频文件
        files = list_audio_files()
        if not files:
            print(f"[提示] {VOICE_DIR} 里没有 wav/mp3，先放几个音频进去再使用")
            return
        fname = await asyncio.to_thread(
            choose_from, "选择要发的音频：", files, lambda name: name
        )
        if fname is None:
            # 退出文件选择就回到对象选择
            continue

        # 3. 发送（每次临时连一次，发完就关）
        await send_voice(target, label, os.path.join(VOICE_DIR, fname))


def main():
    # Windows 终端默认 GBK，音频名或备注有生僻字时打印会报错，这里统一走 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n已退出。")
    except (OSError, websockets.WebSocketException) as e:
        print(f"[NapCat连接失败] {e}")
        print("检查：NapCat 是否已登录、正向 WebSocket（端口 3001）是否开启、Token 是否一致。")


if __name__ == "__main__":
    main()
