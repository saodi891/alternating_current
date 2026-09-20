# 交流电 —— QQ 语音发送小工具

把文件夹里的 wav / mp3 转换为 QQ 语音，发给指定好友或群，在软件窗口里管理好友/群名单

## 它能做什么

- 把 wav / mp3 当**QQ语音条**发送给你的好友和群聊
- 直接把音频**拖进窗口**即可导入，无需手动放文件夹
- 在软件里**增删好友/群、改备注**，配置自动保存，可以在 config.json 里自由配置
- **窗口置顶**开关，方便边传边发，增加效率

## 为什么需要 NapCat

QQ 官方不允许直接写脚本操作聊天。通用做法是用开源的「QQ 协议端」帮你登录 QQ，
它负责收发消息，本工具只跟它对接：


```
QQ 服务器 <-> NapCat（帮你登录 QQ）<-- WebSocket --> 交流电
```

音频转码也由 NapCat 完成，wav/mp3 会自动转成 QQ 需要的格式

## 准备工作：装好并配置 NapCat

1. 到 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 下载并启动，登录你的 QQ
2. 打开它的网页控制台（默认 `http://127.0.0.1:6099/webui`）
3. 在「网络配置」里新建一个**正向 WebSocket 服务器**：主机 `127.0.0.1`，端口 `3001`，启用
4. 设置 Token，稍后填进本工具

## 用法 A：直接用 exe（最简单）

1. 运行 `交流电.exe`。首次运行会在同目录生成 `config.json` 和 `voices` 文件夹。`config.json`存放目标好友与群聊，`voices`存放常用语音
2. 在 NapCat 设置 Token完成后，打开 `config.json`，把 `access_token` 填成你的Token
3. 把 wav / mp3 拖进窗口，或放进 `voices` 文件夹后点「刷新」
4. 左边选好友/群，右边选音频，点「发送语音」

> exe 依赖 `config.json` 和 `voices` 在**它自己所在的目录**，移动 exe 时这两样会在新位置重新生成。

## 用法 B：用 Python 源码运行

需要 Python 3.10+（开发用的是 3.12）

```bash
pip install -r requirements.txt
python voice_gui.py
```

`requirements.txt` 里 `pyinstaller` 和 `Pillow` 只在打包成 exe 时用到，平时运行用不上，可以只装 `websockets` 和 `tkinterdnd2`。

## 自己打包成 exe

```bash
python build_exe.py
```

生成的 exe 在 `dist/交流电.exe`



## 白名单格式（config.json）

```json
{
  "ws_url": "ws://127.0.0.1:3001",
  "access_token": "",
  "targets": [
    { "id": "33333333", "note": "某个好友" },
    { "id": "#111111",  "note": "某个群" }
  ]
}
```

- 好友：`id` 直接写 QQ 号
- 群聊：`id` 是群号，**前面加 `#`**
- `note` 是备注，只给你自己看，方便认人

这些在软件里点「加好友 / 加群 / 改备注 / 删除」就能改，会自动写回 `config.json`

## 文件说明

| 文件 | 作用 |
|---|---|
| `voice_gui.py` | 图形界面主程序 |
| `send_voice.py` | 命令行版（无界面，菜单式操作，可选） |
| `build_exe.py` | 打包成 exe 的脚本 |
| `icon.ico` | 图标|
| `requirements.txt` | 依赖清单 |
| `voices/` | 存放要发送的音频 |

## 常见问题

- **连接失败**：NapCat 没启动，或正向 WebSocket 没启用、端口不是 3001
- **提示 token 验证失败**：`config.json` 的 `access_token` 和 NapCat 里设的不一致
- **列表里没音频**：`voices` 文件夹里没有 wav/mp3，或拖进来的不是这两种格式
- **发送没反应 / 超时**：确认 NapCat 已登录 QQ；看软件底部状态栏的提示
- **太久不操作后发送失败**：本工具每次发送才临时连一次、发完就断，正常不会有这问题；如果仍失败，检查 NapCat 是否还在线

## 说明

- `access_token` 请填你自己 NapCat 的 Token。分享本工具给别人前，记得清空 `config.json`（里面有你的好友/群和 Token）
- 本工具只负责把本地音频交给 NapCat 发送，不收集、不上传任何数据。
