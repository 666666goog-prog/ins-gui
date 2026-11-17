# -*- coding: utf-8 -*-
"""
Ins批量下载 v1.0.2
基于开源 Instaloader 4.16 的图形界面，全中文、鼠标操作为主。

便携规则：所有数据都放在程序旁边的文件夹里，绝不写 AppData。
  data/            设置和登录保存
  data/sessions/   登录凭证（session 文件）
  下载/            默认下载位置
"""

import json
import os
import queue
import re
import sys
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径（便携：永远在 exe / 脚本旁边）
# ---------------------------------------------------------------------------

def get_base_dir() -> Path:
    """打包成 exe 时用 exe 所在文件夹；直接跑脚本时用脚本所在文件夹。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "下载"

APP_TITLE = "Ins批量下载 v1.0.2"
VERSION = "1.0.2"

# ---------------------------------------------------------------------------
# 纯逻辑（不打开窗口、不联网，test_logic.py 会测这部分）
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# 这些是 Instagram 网址里的固定栏目词，不是用户名
_RESERVED_SEGMENTS = {
    "p", "reel", "reels", "stories", "explore", "accounts",
    "direct", "tv", "igtv", "about", "developer", "legal",
}


def extract_username(text):
    """从一行文字里提取 Instagram 用户名。

    支持：nasa / @nasa / https://www.instagram.com/nasa/ 等写法。
    提不出来（比如帖子链接 instagram.com/p/xxxx）返回 None。
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if "instagram.com" in s.lower():
        # 取 instagram.com/ 后面的第一段
        part = s[s.lower().index("instagram.com") + len("instagram.com"):]
        part = part.strip("/")
        segment = re.split(r"[/?#\s]", part)[0] if part else ""
        if not segment or segment.lower() in _RESERVED_SEGMENTS:
            return None
        s = segment
    else:
        s = s.strip()
        if s.startswith("@"):
            s = s[1:]
        s = s.strip().strip("/")
    if _USERNAME_RE.match(s or ""):
        return s
    return None


def parse_targets(text):
    """把多行文本解析成去重后的用户名列表（一行一个）。"""
    result = []
    seen = set()
    for line in str(text or "").splitlines():
        name = extract_username(line)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    return result


def parse_cookie_string(text):
    """把整串 Cookie 文字解析成字典。

    支持浏览器里复制出来的整行 Cookie（可带 "Cookie:" 前缀），
    值里可以含 "="（只按第一个 "=" 切分）。解析不出来返回空字典。
    """
    s = str(text or "").strip()
    if not s:
        return {}
    # 去掉请求头复制时带的前缀
    if s.lower().startswith("cookie:"):
        s = s[len("cookie:"):].strip()
    s = s.replace("\r", " ").replace("\n", " ")
    cookies = {}
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies[name] = value
    return cookies


def derive_user_id(cookies):
    """从 Cookie 里推出自己的数字 ID：先看 ds_user_id，没有就从 sessionid 第一段拆。"""
    uid = str((cookies or {}).get("ds_user_id") or "").strip()
    if uid.isdigit():
        return uid
    sid = urllib.parse.unquote(str((cookies or {}).get("sessionid") or ""))
    first = sid.split(":")[0].strip() if sid else ""
    return first if first.isdigit() else ""


def sessionid_looks_complete(cookies):
    """sessionid 正常是一长串、解码后至少有两段冒号分隔；只剩一截就是没复制全。"""
    sid = urllib.parse.unquote(str((cookies or {}).get("sessionid") or ""))
    return sid.count(":") >= 2 and len(sid) >= 30


def verify_login(loader, cookies):
    """校验 Cookie 登录，返回用户名或 None。

    先用库自带的 test_login；它偶尔会误判（返回空但登录其实有效），
    再用 ds_user_id 查本人资料兜底，查到用户名就算登录成立。
    """
    loader.context.update_cookies(cookies)
    who = None
    try:
        who = loader.test_login()
    except Exception:
        who = None
    if who:
        return who
    uid = derive_user_id(cookies)
    if uid:
        try:
            from instaloader.structures import Profile

            prof = Profile.from_id(loader.context, int(uid))
            if prof is not None and prof.username:
                return prof.username
        except Exception:
            pass
    return None


DEFAULT_SETTINGS = {
    "login_username": "",
    "download_dir": "",
    "opt_posts": True,
    "opt_profilepic": True,
    "opt_reels": True,
    "opt_stories": False,
    "opt_highlights": False,
    "opt_classify": True,
    "opt_only_video": False,
    "opt_only_image": False,
    "opt_skip_existing": True,
    "speed": "正常",
}


def load_settings(path=SETTINGS_FILE):
    """读设置；文件不存在或坏了就用默认值。"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        p = Path(path)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in settings:
                    if key in data:
                        settings[key] = data[key]
    except Exception:
        pass
    return settings


def save_settings(settings, path=SETTINGS_FILE):
    """保存设置到 data/settings.json。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in dict(settings).items() if k in DEFAULT_SETTINGS})
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def session_path(username):
    return SESSIONS_DIR / f"session-{username}"


def explain_error(exc):
    """把引擎报的错误翻成大白话中文，并带上真实原因。"""
    import instaloader

    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if isinstance(exc, instaloader.TooManyRequestsException) or "429" in msg:
        return ("被 Instagram 限流了（429）：短时间请求太多。"
                "先停一会儿（几十分钟到几小时），登录后再下也会好一些；"
                "速度选了“快速”时更容易这样，可以改回“正常”。原始信息：" + msg)
    if isinstance(exc, instaloader.LoginRequiredException):
        return "这个内容需要先登录才能下载（比如快拍、私密账号）。原始信息：" + msg
    if isinstance(exc, instaloader.PrivateProfileNotFollowedException):
        return "对方是私密账号，你登录的账号还没有关注他/没有通过关注，所以下不了。"
    if isinstance(exc, instaloader.ProfileNotExistsException):
        return "这个账号不存在（可能改名、注销或被封了）：原始信息：" + msg
    if isinstance(exc, instaloader.BadCredentialsException):
        return "登录失败：用户名或密码不对，或者密码最近改过。原始信息：" + msg
    if isinstance(exc, instaloader.TwoFactorAuthRequiredException):
        return "登录需要两步验证码，请输入你收到的验证码。"
    if "checkpoint" in low:
        m = re.search(r"https?://\S+", msg)
        url = m.group(0) if m else "instagram.com"
        return ("Instagram 要你先做一次安全验证。请在浏览器里打开这个地址、"
                "按提示确认“是我本人”之后，再回来重新登录：" + url)
    if isinstance(exc, instaloader.ConnectionException):
        return "网络连接失败（连不上 Instagram），检查网络/代理后再试。原始信息：" + msg
    if isinstance(exc, instaloader.LoginException):
        return "登录没有成功。原始信息：" + msg
    return f"{exc.__class__.__name__}：{msg}"


# ---------------------------------------------------------------------------
# 下载引擎构造
# ---------------------------------------------------------------------------

def make_loader(speed="正常", only_video=False, only_image=False, download_dir=None):
    """按“只要媒体文件、不要附加文件”的快设置构造 Instaloader。

    - 不下评论、不下定位、不存 json 元数据、不生成说明 txt —— 少发很多请求，快很多
    - 速度“快速”时 sleep=False，去掉原版每次请求前的随机等待（更容易被限流）
    - dirname_pattern 固定到下载文件夹下面，保证所有文件（含头像）都落在下载位置里
    """
    import instaloader

    dirname_pattern = "{target}"
    if download_dir:
        dirname_pattern = os.path.join(str(download_dir), "{target}")
    return instaloader.Instaloader(
        sleep=(speed != "快速"),
        quiet=True,
        user_agent=None,
        dirname_pattern=dirname_pattern,
        download_pictures=not only_video,
        download_videos=not only_image,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern=None,
        storyitem_metadata_txt_pattern=None,
        max_connection_attempts=3,
    )


def load_saved_session(loader, username):
    """把保存过的登录凭证装进 loader。成功返回 True。"""
    if not username:
        return False
    path = session_path(username)
    if not path.is_file():
        return False
    try:
        loader.load_session_from_file(username, filename=str(path))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------

class TwoFactorRequest:
    """后台线程向主线程要两步验证码的信封。"""

    def __init__(self):
        self.event = threading.Event()
        self.code = None


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import scrolledtext

        self.tk = tk
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("860x700")
        self.root.minsize(760, 560)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

        self.settings = load_settings()
        self.msg_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.downloading = False
        self.logged_in_user = ""

        # ---------------- 变量 ----------------
        s = self.settings
        self.login_user_var = tk.StringVar(value=s.get("login_username", ""))
        self.login_pass_var = tk.StringVar(value="")
        self.show_pass_var = tk.BooleanVar(value=False)
        self.login_status_var = tk.StringVar(value="登录状态：未登录")
        self.targets_hint_var = tk.StringVar(value="")
        self.download_dir_var = tk.StringVar(
            value=s.get("download_dir") or str(DEFAULT_DOWNLOAD_DIR))
        self.speed_var = tk.StringVar(value=s.get("speed", "正常"))
        self.progress_var = tk.StringVar(value="进度：还没开始")
        self.opt_vars = {
            "opt_posts": tk.BooleanVar(value=bool(s["opt_posts"])),
            "opt_profilepic": tk.BooleanVar(value=bool(s["opt_profilepic"])),
            "opt_reels": tk.BooleanVar(value=bool(s["opt_reels"])),
            "opt_stories": tk.BooleanVar(value=bool(s["opt_stories"])),
            "opt_highlights": tk.BooleanVar(value=bool(s["opt_highlights"])),
            "opt_classify": tk.BooleanVar(value=bool(s["opt_classify"])),
            "opt_only_video": tk.BooleanVar(value=bool(s["opt_only_video"])),
            "opt_only_image": tk.BooleanVar(value=bool(s["opt_only_image"])),
            "opt_skip_existing": tk.BooleanVar(value=bool(s["opt_skip_existing"])),
        }

        # ---------------- 登录区 ----------------
        login_frame = tk.LabelFrame(root, text="登录（不登录只能下公开账号，而且更容易被限流）", padx=8, pady=6)
        login_frame.pack(fill="x", padx=10, pady=(10, 4))

        row1 = tk.Frame(login_frame)
        row1.pack(fill="x")
        tk.Label(row1, text="用户名：").pack(side="left")
        self.login_user_entry = tk.Entry(row1, textvariable=self.login_user_var, width=22)
        self.login_user_entry.pack(side="left")
        tk.Button(row1, text="粘贴", command=lambda: self._paste_into(self.login_user_var)).pack(side="left", padx=(4, 10))
        tk.Label(row1, text="密码：").pack(side="left")
        self.login_pass_entry = tk.Entry(row1, textvariable=self.login_pass_var, width=22, show="*")
        self.login_pass_entry.pack(side="left")
        tk.Button(row1, text="粘贴", command=lambda: self._paste_into(self.login_pass_var)).pack(side="left", padx=(4, 6))
        tk.Checkbutton(row1, text="显示密码", variable=self.show_pass_var,
                       command=self._toggle_show_pass).pack(side="left")

        row2 = tk.Frame(login_frame)
        row2.pack(fill="x", pady=(6, 0))
        tk.Button(row2, text="登录并保存", width=12, command=self.on_password_login).pack(side="left")
        tk.Button(row2, text="读取Edge登录", width=12, command=self.on_edge_login).pack(side="left", padx=(6, 0))
        tk.Button(row2, text="读取Chrome登录", width=13, command=self.on_chrome_login).pack(side="left", padx=(6, 0))
        tk.Button(row2, text="退出登录", width=9, command=self.on_logout).pack(side="left", padx=(6, 0))
        tk.Label(row2, textvariable=self.login_status_var).pack(side="left", padx=(12, 0))

        crow = tk.Frame(login_frame)
        crow.pack(fill="x", pady=(6, 0))
        tk.Label(crow, text="Cookie：").pack(side="left", anchor="n")
        self.cookie_text = scrolledtext.ScrolledText(crow, height=2, wrap="word")
        self.cookie_text.pack(side="left", fill="x", expand=True)
        cbtn = tk.Frame(crow)
        cbtn.pack(side="left", padx=(6, 0), anchor="n")
        tk.Button(cbtn, text="粘贴", width=10, command=self.on_paste_cookie).pack(pady=(0, 4))
        tk.Button(cbtn, text="Cookie登录并保存", command=self.on_cookie_login).pack()

        tk.Label(login_frame, anchor="w", fg="#666666",
                 text="浏览器登录：先在 Edge 或 Chrome 里登录好 Instagram，再把浏览器完全关掉，然后点对应的“读取…登录”"
                      "（Chrome 新版加密严，可能读不到，读不到就用 Cookie 粘贴）。\n"
                      "Cookie登录：在浏览器里登录好 Instagram，复制整串 Cookie 粘贴到上面，点“Cookie登录并保存”（必须包含 sessionid）。\n"
                      f"数据都存在程序旁边：{DATA_DIR}（登录保存）和下载文件夹里，不放 AppData。").pack(fill="x", pady=(4, 0))

        # ---------------- 下载区 ----------------
        dl_frame = tk.LabelFrame(root, text="下载账号", padx=8, pady=6)
        dl_frame.pack(fill="x", padx=10, pady=4)

        tk.Label(dl_frame, anchor="w",
                 text="目标账号（一行一个，可以直接粘贴主页链接，会自动认出用户名）：").pack(fill="x")
        trow = tk.Frame(dl_frame)
        trow.pack(fill="x")
        self.targets_text = scrolledtext.ScrolledText(trow, height=4, wrap="word")
        self.targets_text.pack(side="left", fill="x", expand=True)
        tbtn = tk.Frame(trow)
        tbtn.pack(side="left", padx=(6, 0), anchor="n")
        tk.Button(tbtn, text="粘贴", width=8, command=self.on_paste_targets).pack(pady=(0, 4))
        tk.Button(tbtn, text="清空", width=8, command=self.on_clear_targets).pack()

        orow = tk.Frame(dl_frame)
        orow.pack(fill="x", pady=(6, 0))
        checks = [
            ("opt_posts", "帖子"),
            ("opt_profilepic", "头像"),
            ("opt_reels", "Reels"),
            ("opt_stories", "快拍Stories（要登录）"),
            ("opt_highlights", "精选Highlights（要登录）"),
            ("opt_classify", "按类型分文件夹（图片/视频）"),
            ("opt_skip_existing", "跳过已下载"),
            ("opt_only_video", "只下视频"),
            ("opt_only_image", "只下图片"),
        ]
        for i, (key, label) in enumerate(checks):
            cb = tk.Checkbutton(orow, text=label, variable=self.opt_vars[key],
                                command=lambda k=key: self._on_option_changed(k))
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 12))

        srow = tk.Frame(dl_frame)
        srow.pack(fill="x", pady=(6, 0))
        tk.Label(srow, text="速度：").pack(side="left")
        tk.Radiobutton(srow, text="正常", value="正常", variable=self.speed_var,
                       command=self._save_settings_from_ui).pack(side="left")
        tk.Radiobutton(srow, text="快速", value="快速", variable=self.speed_var,
                       command=self._save_settings_from_ui).pack(side="left")
        tk.Label(srow, text="（快速会去掉等待、明显更快，但更容易被限流）", fg="#666666").pack(side="left")

        drow = tk.Frame(dl_frame)
        drow.pack(fill="x", pady=(6, 0))
        tk.Label(drow, text="下载位置：").pack(side="left")
        tk.Entry(drow, textvariable=self.download_dir_var).pack(side="left", fill="x", expand=True)
        tk.Button(drow, text="选择文件夹", command=self.on_choose_dir).pack(side="left", padx=(6, 0))
        tk.Button(drow, text="打开下载文件夹", command=self.on_open_dir).pack(side="left", padx=(6, 0))

        brow = tk.Frame(dl_frame)
        brow.pack(fill="x", pady=(8, 0))
        self.start_btn = tk.Button(brow, text="开始下载", width=16, height=1, command=self.on_start_download)
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(brow, text="停止", width=10, command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        tk.Label(brow, textvariable=self.progress_var).pack(side="left", padx=(12, 0))

        # ---------------- 日志区 ----------------
        log_frame = tk.LabelFrame(root, text="日志", padx=8, pady=6)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_queue)

        self.log("程序已打开。数据文件夹：" + str(DATA_DIR))
        self._startup_session_check()

    # ---------------- 通用小工具 ----------------
    def log(self, text):
        self.msg_queue.put(("log", str(text)))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.login_status_var.set(payload)
                elif kind == "progress":
                    self.progress_var.set(payload)
                elif kind == "2fa":
                    self._handle_2fa_request(payload)
                elif kind == "download_done":
                    self.downloading = False
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _handle_2fa_request(self, req: TwoFactorRequest):
        from tkinter import simpledialog

        code = simpledialog.askstring(
            "两步验证",
            "Instagram 要两步验证码。\n请输入你手机上收到的验证码：",
            parent=self.root)
        req.code = (code or "").strip() or None
        req.event.set()

    def _paste_into(self, var):
        try:
            text = self.root.clipboard_get().strip()
        except Exception:
            text = ""
        if text:
            var.set(text)

    def _toggle_show_pass(self):
        self.login_pass_entry.config(show="" if self.show_pass_var.get() else "*")

    def _save_settings_from_ui(self):
        data = {
            "login_username": self.settings.get("login_username", ""),
            "download_dir": self.download_dir_var.get().strip(),
            "speed": self.speed_var.get(),
        }
        for key, var in self.opt_vars.items():
            data[key] = bool(var.get())
        self.settings.update(data)
        try:
            save_settings(self.settings)
        except Exception as exc:
            self.log(f"保存设置失败：{exc}")

    def _on_option_changed(self, key):
        # 只下视频 / 只下图片 不能同时开
        if key == "opt_only_video" and self.opt_vars["opt_only_video"].get():
            self.opt_vars["opt_only_image"].set(False)
        if key == "opt_only_image" and self.opt_vars["opt_only_image"].get():
            self.opt_vars["opt_only_video"].set(False)
        self._save_settings_from_ui()

    def on_close(self):
        self._save_settings_from_ui()
        self.root.destroy()

    # ---------------- 登录 ----------------
    def _startup_session_check(self):
        username = (self.settings.get("login_username") or "").strip()
        if username and session_path(username).is_file():
            self.login_status_var.set(f"登录状态：已保存 {username} 的登录，正在确认…")
            threading.Thread(target=self._verify_saved_session, args=(username,), daemon=True).start()
        else:
            self.login_status_var.set("登录状态：未登录")

    def _verify_saved_session(self, username):
        try:
            loader = make_loader(self.speed_var.get())
            if not load_saved_session(loader, username):
                self.msg_queue.put(("status", "登录状态：未登录"))
                return
            who = loader.test_login()
            if not who:
                # test_login 偶尔误判，用保存的 ds_user_id 查本人资料兜底
                try:
                    uid = loader.context.user_id
                    if uid:
                        from instaloader.structures import Profile

                        prof = Profile.from_id(loader.context, int(uid))
                        if prof is not None and prof.username:
                            who = prof.username
                except Exception:
                    who = None
            if who:
                self.logged_in_user = who
                self.msg_queue.put(("status", f"登录状态：已登录 {who}"))
                self.log(f"已自动恢复登录：{who}")
            else:
                self.msg_queue.put(("status", f"登录状态：{username} 的登录已失效，请重新登录"))
                self.log("保存的登录已经失效了，请重新登录一次。")
        except Exception as exc:
            self.msg_queue.put(("status", f"登录状态：已保存 {username} 的登录（暂时连不上，没确认成）"))
            self.log("确认登录状态时出错（不影响你手动下载/登录）：" + explain_error(exc))

    def on_password_login(self):
        username = self.login_user_var.get().strip().lstrip("@")
        password = self.login_pass_var.get()
        if not username or not password:
            self.log("请先填用户名和密码（可以用旁边的“粘贴”按钮）。")
            return
        self.login_status_var.set("登录状态：正在登录…")
        threading.Thread(target=self._password_login_worker, args=(username, password), daemon=True).start()

    def _password_login_worker(self, username, password):
        import instaloader

        loader = make_loader(self.speed_var.get())
        try:
            try:
                loader.login(username, password)
            except instaloader.TwoFactorAuthRequiredException:
                self.log("这个账号开了两步验证，正在等你输入验证码…")
                req = TwoFactorRequest()
                self.msg_queue.put(("2fa", req))
                if not req.event.wait(timeout=180):
                    self.log("等验证码超时，登录取消。")
                    self.msg_queue.put(("status", "登录状态：未登录"))
                    return
                if not req.code:
                    self.log("没有输入验证码，登录取消。")
                    self.msg_queue.put(("status", "登录状态：未登录"))
                    return
                loader.two_factor_login(req.code)
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            loader.save_session_to_file(filename=str(session_path(username)))
            self.logged_in_user = username
            self.settings["login_username"] = username
            save_settings(self.settings)
            self.msg_queue.put(("status", f"登录状态：已登录 {username}"))
            self.log(f"登录成功，登录状态已保存，下次打开不用再登：{username}")
        except Exception as exc:
            self.msg_queue.put(("status", "登录状态：未登录"))
            self.log("登录失败：" + explain_error(exc))

    def on_edge_login(self):
        self.log("读取Edge登录前确认一下：先在 Edge 里登录好 Instagram，再把 Edge 完全关掉（窗口全关），不然读不到。开始读取…")
        self.login_status_var.set("登录状态：正在读取Edge登录…")
        threading.Thread(target=self._browser_login_worker, args=("edge", "Edge"), daemon=True).start()

    def on_chrome_login(self):
        self.log("读取Chrome登录前确认一下：先在 Chrome 里登录好 Instagram，再把 Chrome 完全关掉（窗口全关），不然读不到。开始读取…")
        self.login_status_var.set("登录状态：正在读取Chrome登录…")
        threading.Thread(target=self._browser_login_worker, args=("chrome", "Chrome"), daemon=True).start()

    def _browser_login_worker(self, kind, label):
        try:
            import browser_cookie3
        except Exception:
            self.msg_queue.put(("status", "登录状态：未登录"))
            self.log(f"缺少 browser_cookie3 组件，读不了 {label} 登录（打包版里是带好的，出现这个说明打包不完整）。")
            return
        try:
            cookies = {}
            jar = getattr(browser_cookie3, kind)(domain_name="instagram.com")
            for cookie in jar:
                if "instagram.com" in (cookie.domain or ""):
                    cookies[cookie.name] = cookie.value
        except Exception as exc:
            self.msg_queue.put(("status", "登录状态：未登录"))
            extra = "Chrome 新版加密比较严，读不到是常见情况，这种时候用上面 Cookie 粘贴那条路。" if kind == "chrome" else ""
            self.log(f"读取 {label} 登录失败（多半是 {label} 没完全关掉，或者 {label} 里还没登录 Instagram）：{exc}。{extra}")
            return
        if not cookies.get("sessionid"):
            self.msg_queue.put(("status", "登录状态：未登录"))
            self.log(f"没读到 Instagram 的登录（没有 sessionid）。请在 {label} 里先登录一次 Instagram，再关掉 {label} 重试。")
            return
        loader = make_loader(self.speed_var.get())
        try:
            who = verify_login(loader, cookies)
            if not who:
                self.msg_queue.put(("status", "登录状态：未登录"))
                self.log(f"{label} 里的登录已经失效了（Instagram 不认）。请在 {label} 里重新登录一次再来读。")
                return
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            loader.save_session_to_file(filename=str(session_path(who)))
            self.logged_in_user = who
            self.settings["login_username"] = who
            save_settings(self.settings)
            self.msg_queue.put(("status", f"登录状态：已登录 {who}"))
            self.log(f"{label} 登录读取成功，已保存，下次打开不用再读：{who}")
        except Exception as exc:
            self.msg_queue.put(("status", "登录状态：未登录"))
            self.log(f"{label} 登录确认失败：" + explain_error(exc))

    def on_paste_cookie(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text.strip():
            self.cookie_text.delete("1.0", "end")
            self.cookie_text.insert("1.0", text.strip())

    def on_cookie_login(self):
        raw = self.cookie_text.get("1.0", "end").strip()
        cookies = parse_cookie_string(raw)
        if not cookies:
            self.log("Cookie 是空的。先在浏览器里登录 Instagram，复制整串 Cookie，再点“粘贴”。")
            return
        if not cookies.get("sessionid"):
            self.log("这串 Cookie 里没有 sessionid，这一项是登录的关键，缺了登不上。"
                     "请确认复制的是 instagram.com 的完整 Cookie（整行都要）。")
            return
        if not cookies.get("csrftoken"):
            self.log("提醒：这串 Cookie 里没有 csrftoken，可能下载时会报错，先试着登录校验。")
        self.login_status_var.set("登录状态：正在用Cookie校验…")
        threading.Thread(target=self._cookie_login_worker, args=(cookies,), daemon=True).start()

    def _cookie_login_worker(self, cookies):
        found = "、".join(
            f"{name} {'有' if cookies.get(name) else '没有'}"
            for name in ("sessionid", "csrftoken", "ds_user_id"))
        self.log(f"解析到 {len(cookies)} 项 Cookie，其中 {found}。（只报有无，不显示内容）")
        if not sessionid_looks_complete(cookies):
            self.log("提醒：sessionid 看起来不完整——正常的是一长串、中间有好几段。"
                     "如果这次验不过，多半是复制时只复制到一截，请回到浏览器把整行 Cookie 完整复制。")
        loader = make_loader(self.speed_var.get())
        try:
            who = verify_login(loader, cookies)
            if not who:
                self.msg_queue.put(("status", "登录状态：未登录"))
                self.log("这个 Cookie 没通过：Instagram 没认这个登录。"
                         "先确认浏览器里打开 instagram.com 还是登录着的状态；"
                         "复制时要选发往 www.instagram.com 的那条请求、整行 Cookie 都要，不要只截一截。")
                return
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            loader.save_session_to_file(filename=str(session_path(who)))
            self.logged_in_user = who
            self.settings["login_username"] = who
            save_settings(self.settings)
            self.msg_queue.put(("status", f"登录状态：已登录 {who}"))
            self.log(f"Cookie 登录成功，登录状态已保存，下次打开不用再粘：{who}")
        except Exception as exc:
            self.msg_queue.put(("status", "登录状态：未登录"))
            self.log("Cookie 登录失败：" + explain_error(exc))

    def on_logout(self):
        username = (self.settings.get("login_username") or self.login_user_var.get()).strip()
        removed = False
        if username:
            path = session_path(username)
            if path.is_file():
                try:
                    path.unlink()
                    removed = True
                except Exception as exc:
                    self.log(f"删除登录保存失败：{exc}")
        self.settings["login_username"] = ""
        self.logged_in_user = ""
        save_settings(self.settings)
        self.login_status_var.set("登录状态：未登录")
        self.log("已退出登录" + ("，保存的登录已删除。" if removed else "。"))

    # ---------------- 下载 ----------------
    def on_paste_targets(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text.strip():
            current = self.targets_text.get("1.0", "end").strip()
            self.targets_text.delete("1.0", "end")
            self.targets_text.insert("1.0", (current + "\n" + text.strip()).strip() + "\n")

    def on_clear_targets(self):
        self.targets_text.delete("1.0", "end")

    def on_choose_dir(self):
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self.download_dir_var.get() or str(DEFAULT_DOWNLOAD_DIR))
        if chosen:
            self.download_dir_var.set(chosen)
            self._save_settings_from_ui()

    def on_open_dir(self):
        path = Path(self.download_dir_var.get().strip() or str(DEFAULT_DOWNLOAD_DIR))
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))  # Windows 打开文件夹
        except Exception as exc:
            self.log(f"打开下载文件夹失败：{exc}（路径：{path}）")

    def on_stop(self):
        self.stop_flag.set()
        self.log("正在停止：当前这一个下完就会停…")

    def on_start_download(self):
        if self.downloading:
            return
        targets = parse_targets(self.targets_text.get("1.0", "end"))
        if not targets:
            self.log("先填要下载的账号：一行一个用户名，或者粘贴主页链接。帖子链接认不出用户名。")
            return
        self._save_settings_from_ui()
        self.stop_flag.clear()
        self.downloading = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        opts = {key: bool(var.get()) for key, var in self.opt_vars.items()}
        args = (
            targets,
            self.download_dir_var.get().strip() or str(DEFAULT_DOWNLOAD_DIR),
            self.speed_var.get(),
            opts,
            (self.settings.get("login_username") or "").strip(),
        )
        threading.Thread(target=self._download_worker, args=args, daemon=True).start()

    def _download_worker(self, targets, download_dir, speed, opts, login_username):
        import instaloader
        from instaloader import Profile

        counts = {"downloaded": 0, "skipped": 0, "failed": 0}

        def report_progress():
            self.msg_queue.put((
                "progress",
                f"进度：已下载 {counts['downloaded']} · 已跳过 {counts['skipped']} · 失败 {counts['failed']}",
            ))

        def note_result(ok):
            if ok:
                counts["downloaded"] += 1
            else:
                counts["skipped"] += 1
            report_progress()

        loader = make_loader(
            speed=speed,
            only_video=opts["opt_only_video"],
            only_image=opts["opt_only_image"],
            download_dir=download_dir,
        )
        if login_username and load_saved_session(loader, login_username):
            self.log(f"本次下载使用已登录的账号：{login_username}")
        else:
            self.log("本次下载没有登录（只能下公开账号，快拍/精选下不了，也更容易被限流）。")
        if not opts["opt_skip_existing"]:
            self.log("提醒：你关了“跳过已下载”，但原版引擎看到文件已经存在时还是会自动跳过，这版暂时不能强制重下。")

        self.log(f"开始下载，共 {len(targets)} 个账号，保存到：{download_dir}")

        for username in targets:
            if self.stop_flag.is_set():
                self.log("已停止。")
                break
            self.log(f"===== 开始：@{username} =====")
            try:
                profile = Profile.from_username(loader.context, username)
            except Exception as exc:
                counts["failed"] += 1
                report_progress()
                self.log(f"@{username} 打不开：" + explain_error(exc))
                continue

            user = profile.username
            if profile.is_private and not profile.followed_by_viewer:
                self.log(f"提醒：@{user} 是私密账号，你登录的账号没关注他/没通过，可能下不到内容。")

            # 头像
            if opts["opt_profilepic"]:
                try:
                    loader.download_profilepic(profile)
                    self.log(f"@{user} 头像已下载（已存在会自动跳过）")
                except Exception as exc:
                    self.log(f"@{user} 头像下载失败：" + explain_error(exc))

            # 帖子
            if opts["opt_posts"]:
                try:
                    index = 0
                    for post in profile.get_posts():
                        if self.stop_flag.is_set():
                            break
                        index += 1
                        if opts["opt_only_video"] and not post.is_video:
                            continue
                        if opts["opt_only_image"] and post.is_video:
                            continue
                        if opts["opt_classify"]:
                            if post.typename == "GraphSidecar":
                                target = f"{user}/图文"
                            elif post.is_video:
                                target = f"{user}/视频"
                            else:
                                target = f"{user}/图片"
                        else:
                            target = user
                        self.log(f"正在下载 @{user} 的帖子 第{index}个…")
                        try:
                            note_result(loader.download_post(post, target))
                        except Exception as exc:
                            counts["failed"] += 1
                            report_progress()
                            self.log(f"@{user} 帖子第{index}个失败：" + explain_error(exc))
                    self.log(f"@{user} 帖子处理完（共看到 {index} 个）")
                except Exception as exc:
                    counts["failed"] += 1
                    report_progress()
                    self.log(f"@{user} 帖子列表拉取失败：" + explain_error(exc))

            # Reels（4.16 的方法是 get_reels）
            if opts["opt_reels"] and hasattr(profile, "get_reels"):
                try:
                    index = 0
                    for post in profile.get_reels():
                        if self.stop_flag.is_set():
                            break
                        index += 1
                        if opts["opt_only_image"]:
                            continue
                        target = f"{user}/视频" if opts["opt_classify"] else f"{user}/Reels"
                        self.log(f"正在下载 @{user} 的Reels 第{index}个…")
                        try:
                            note_result(loader.download_post(post, target))
                        except Exception as exc:
                            counts["failed"] += 1
                            report_progress()
                            self.log(f"@{user} Reels第{index}个失败：" + explain_error(exc))
                    if index:
                        self.log(f"@{user} Reels处理完（共看到 {index} 个）")
                except Exception as exc:
                    counts["failed"] += 1
                    report_progress()
                    self.log(f"@{user} Reels列表拉取失败：" + explain_error(exc))

            # 快拍 Stories
            if opts["opt_stories"]:
                try:
                    for story in loader.get_stories(userids=[profile.userid]):
                        for item in story.get_items():
                            if self.stop_flag.is_set():
                                break
                            if opts["opt_only_video"] and not item.is_video:
                                continue
                            if opts["opt_only_image"] and item.is_video:
                                continue
                            self.log(f"正在下载 @{user} 的快拍…")
                            try:
                                note_result(loader.download_storyitem(item, f"{user}/快拍"))
                            except Exception as exc:
                                counts["failed"] += 1
                                report_progress()
                                self.log(f"@{user} 快拍失败：" + explain_error(exc))
                except Exception as exc:
                    counts["failed"] += 1
                    report_progress()
                    self.log(f"@{user} 快拍拉取失败：" + explain_error(exc))

            # 精选 Highlights（4.16 没有 download_highlight，逐条下精选里的条目）
            if opts["opt_highlights"]:
                try:
                    for highlight in loader.get_highlights(profile):
                        if self.stop_flag.is_set():
                            break
                        self.log(f"正在下载 @{user} 的精选：{highlight.title}")
                        for item in highlight.get_items():
                            if self.stop_flag.is_set():
                                break
                            if opts["opt_only_video"] and not item.is_video:
                                continue
                            if opts["opt_only_image"] and item.is_video:
                                continue
                            try:
                                note_result(loader.download_storyitem(item, f"{user}/精选"))
                            except Exception as exc:
                                counts["failed"] += 1
                                report_progress()
                                self.log(f"@{user} 精选失败：" + explain_error(exc))
                except Exception as exc:
                    counts["failed"] += 1
                    report_progress()
                    self.log(f"@{user} 精选拉取失败：" + explain_error(exc))

            self.log(f"===== 完成：@{user} =====")

        self.log(
            f"全部结束：已下载 {counts['downloaded']} · 已跳过 {counts['skipped']} · 失败 {counts['failed']}"
            f"。文件在：{download_dir}")
        self.msg_queue.put(("download_done", None))


def main():
    import tkinter as tk

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
