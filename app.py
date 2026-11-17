# -*- coding: utf-8 -*-
"""ins批量下载器 v2.0.0 — instagrapi 发动机（手机 App 私有接口）

登录：粘贴浏览器 Cookie（只用其中的 sessionid），保存后下次不用再粘。
下载：输入用户名，一次下完该账号全部帖子 + Reels，按 图片/视频/Reels/图文
分文件夹，已下载的自动跳过。引擎逻辑与桌面实测版 batch_core 一致。
"""
import json
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
import urllib.request

VERSION = "2.0.0"

# ---------------------------------------------------------------- engine

def extract_sessionid(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"sessionid=([^;\s]+)", text)
    if m:
        return m.group(1)
    return text


def make_client(sessionid: str, proxy: str = ""):
    """Logged-in Client validated ONLY via the private mobile API (account_info).
    private_transport='requests' avoids the curl_cffi stack entirely."""
    from instagrapi import Client

    cl = Client(private_transport="requests")
    try:
        cl.delay_range = [1, 3]
    except Exception:
        pass
    if proxy:
        try:
            cl.set_proxy(proxy)
        except Exception:
            pass
    user_id = re.match(r"\d+", sessionid).group()
    cl.settings["cookies"] = {"sessionid": sessionid}
    cl.init()
    cl.authorization_data = {
        "ds_user_id": user_id,
        "sessionid": sessionid,
        "should_use_header_over_cookies": True,
    }
    cl.private.cookies.set("ds_user_id", user_id)
    cl.private.headers.update(cl.base_headers)
    cl.private.headers.update({"Authorization": cl.authorization})
    info = cl.account_info()
    cl.username = info.username
    return cl


def classify(media) -> str:
    if media.product_type == "clips":
        return "Reels"
    if media.media_type == 1:
        return "图片"
    if media.media_type == 2:
        return "视频"
    if media.media_type == 8:
        return "图文"
    return "其他"


def fetch_all(cl, user_id: int, log, should_stop=None):
    """All feed medias + all clips, deduped, manual pagination (50/page)."""
    seen = {}
    cursor = ""
    pages = 0
    while True:
        if should_stop and should_stop():
            break
        batch, cursor = cl.user_medias_paginated_v1(user_id, amount=50, end_cursor=cursor)
        for md in batch:
            seen[str(md.pk)] = md
        pages += 1
        log(f"帖子第 {pages} 页：累计 {len(seen)} 个")
        if not cursor or not batch:
            break
        if pages >= 400:
            log("达到安全上限，停止翻页")
            break
    try:
        cursor = ""
        pages = 0
        while True:
            if should_stop and should_stop():
                break
            batch, cursor = cl.user_clips_paginated_v1(user_id, amount=50, end_cursor=cursor)
            new = 0
            for md in batch:
                if str(md.pk) not in seen:
                    new += 1
                seen[str(md.pk)] = md
            pages += 1
            log(f"Reels 第 {pages} 页：本页新增 {new}，累计 {len(seen)} 个")
            if not cursor or not batch:
                break
            if pages >= 400:
                break
    except Exception as e:
        log(f"Reels 列表没拉完（{type(e).__name__}），已拉到的照常下载")
    return list(seen.values())


def _fname(media, idx=None):
    date = media.taken_at.strftime("%Y-%m-%d") if media.taken_at else "0000-00-00"
    base = f"{date}_{media.code}"
    if idx is not None:
        base += f"_{idx}"
    return base


def download_one(cl, media, dest_dir: Path, log) -> str:
    kind = classify(media)
    folder = dest_dir / kind
    folder.mkdir(parents=True, exist_ok=True)
    jobs = []
    if media.media_type == 8:
        for i, res in enumerate(media.resources or [], start=1):
            if res.media_type == 2:
                jobs.append((res.video_url, _fname(media, i), True))
            else:
                jobs.append((res.thumbnail_url, _fname(media, i), False))
    elif media.media_type == 2:
        jobs.append((media.video_url, _fname(media), True))
    else:
        jobs.append((media.thumbnail_url, _fname(media), False))

    got_any = False
    skipped_all = True
    for url, fname, is_video in jobs:
        if not url:
            skipped_all = False
            continue
        ext = ".mp4" if is_video else ".jpg"
        target = folder / f"{fname}{ext}"
        if target.exists() and target.stat().st_size > 0:
            got_any = True
            continue
        skipped_all = False
        try:
            if is_video:
                cl.video_download_by_url(url, fname, folder)
            else:
                cl.photo_download_by_url(url, fname, folder)
            if target.exists() and target.stat().st_size > 0:
                got_any = True
            elif list(folder.glob(fname + ".*")):
                got_any = True
        except Exception as e:
            log(f"    失败 {fname}：{type(e).__name__} {str(e)[:120]}")
    if got_any and skipped_all:
        return "skip"
    return "ok" if got_any else "fail"


def run_batch(cl, username: str, base_dir: Path, log, should_stop=None, progress=None):
    user_id = cl.user_id_from_username(username)
    info = cl.user_info(user_id)
    log(f"目标 @{username}：帖子数 {info.media_count}，粉丝 {info.follower_count}")
    medias = fetch_all(cl, user_id, log, should_stop)
    log(f"共取到 {len(medias)} 个作品（帖子+Reels 合并去重后），开始下载")
    dest = Path(base_dir) / username
    dest.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "skip": 0, "fail": 0}
    for i, md in enumerate(medias, start=1):
        if should_stop and should_stop():
            log("已停止")
            break
        r = download_one(cl, md, dest, log)
        stats[r] += 1
        if progress:
            progress(i, len(medias))
        if i % 20 == 0:
            log(f"进度 {i}/{len(medias)}（新下 {stats['ok']}，跳过 {stats['skip']}，失败 {stats['fail']}）")
    log(f"完成：新下 {stats['ok']}，跳过 {stats['skip']}，失败 {stats['fail']}，共 {len(medias)}")
    return stats


# ---------------------------------------------------------------- settings

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


SETTINGS_FILE = app_dir() / "ins_batch_settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def detect_proxy() -> str:
    try:
        proxies = urllib.request.getproxies()
        return proxies.get("https") or proxies.get("http") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------- GUI

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"ins批量下载器 v{VERSION}")
        root.geometry("760x720")
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.settings = load_settings()

        pad = {"padx": 10, "pady": 4}

        tk.Label(root, text="第一步：粘贴 Cookie 登录（保存后下次不用再粘）", font=("", 11, "bold")).pack(anchor="w", **pad)
        self.cookie_text = tk.Text(root, height=4, font=("", 9))
        self.cookie_text.pack(fill="x", **pad)
        row = tk.Frame(root)
        row.pack(fill="x", **pad)
        tk.Button(row, text="从剪贴板粘贴", command=self.paste_cookie).pack(side="left")
        self.btn_login = tk.Button(row, text="保存并登录", command=self.on_login)
        self.btn_login.pack(side="left", padx=8)
        self.login_status = tk.Label(row, text="登录状态：未登录")
        self.login_status.pack(side="left")

        tk.Label(root, text="第二步：输入要下载的账号用户名", font=("", 11, "bold")).pack(anchor="w", **pad)
        row2 = tk.Frame(root)
        row2.pack(fill="x", **pad)
        self.username_entry = tk.Entry(row2, font=("", 12))
        self.username_entry.pack(side="left", fill="x", expand=True)
        tk.Button(row2, text="粘贴", command=self.paste_username).pack(side="left", padx=8)

        row3 = tk.Frame(root)
        row3.pack(fill="x", **pad)
        tk.Label(row3, text="下载到：").pack(side="left")
        self.dir_entry = tk.Entry(row3)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        tk.Button(row3, text="选择文件夹", command=self.choose_dir).pack(side="left", padx=8)

        row4 = tk.Frame(root)
        row4.pack(fill="x", **pad)
        tk.Label(row4, text="代理（可空）：").pack(side="left")
        self.proxy_entry = tk.Entry(row4)
        self.proxy_entry.pack(side="left", fill="x", expand=True)
        tk.Label(row4, text="空 = 自动读系统代理；也可填如 http://127.0.0.1:7890").pack(side="left")

        row5 = tk.Frame(root)
        row5.pack(fill="x", **pad)
        self.btn_start = tk.Button(row5, text="开始下载", font=("", 12, "bold"), command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(row5, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.progress_label = tk.Label(row5, text="")
        self.progress_label.pack(side="left")

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", **pad)

        tk.Label(root, text="日志", font=("", 11, "bold")).pack(anchor="w", **pad)
        self.log_text = scrolledtext.ScrolledText(root, height=20, font=("", 9))
        self.log_text.pack(fill="both", expand=True, **pad)

        if self.settings.get("cookie"):
            self.cookie_text.insert("1.0", self.settings["cookie"])
            self.login_status.config(text="登录状态：已保存 Cookie，点「保存并登录」验证")
        self.username_entry.insert(0, self.settings.get("last_username", ""))
        self.dir_entry.insert(0, self.settings.get("download_dir", str(app_dir() / "downloads")))
        self.proxy_entry.insert(0, self.settings.get("proxy", ""))

        self.root.after(100, self.drain)

    # ---------- helpers
    def log(self, msg):
        self.q.put(("log", str(msg)))

    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                elif kind == "progress":
                    cur, total = payload
                    if total:
                        self.progress["value"] = cur * 100.0 / total
                        self.progress_label.config(text=f"进度：{cur} / {total}")
                elif kind == "login_ok":
                    self.login_status.config(text=f"登录状态：已登录 @{payload}")
                elif kind == "login_fail":
                    self.login_status.config(text="登录状态：登录失败，看日志")
                elif kind == "busy":
                    self.busy = payload
                    state = "disabled" if payload else "normal"
                    self.btn_login.config(state=state)
                    self.btn_start.config(state=state)
                    self.btn_stop.config(state="normal" if payload else "disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def paste_cookie(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text:
            self.cookie_text.delete("1.0", "end")
            self.cookie_text.insert("1.0", text.strip())
            self.log("已从剪贴板粘贴 Cookie")
        else:
            self.log("剪贴板是空的")

    def paste_username(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text:
            self.username_entry.delete(0, "end")
            self.username_entry.insert(0, text.strip())

    def choose_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, d)

    def current_proxy(self) -> str:
        manual = self.proxy_entry.get().strip()
        if manual:
            return manual
        return detect_proxy()

    def persist(self):
        self.settings.update({
            "cookie": self.cookie_text.get("1.0", "end").strip(),
            "last_username": self.username_entry.get().strip().lstrip("@"),
            "download_dir": self.dir_entry.get().strip(),
            "proxy": self.proxy_entry.get().strip(),
        })
        save_settings(self.settings)

    # ---------- actions
    def on_login(self):
        if self.busy:
            return
        cookie = self.cookie_text.get("1.0", "end").strip()
        if not cookie:
            self.log("先粘贴 Cookie 再登录")
            return
        self.persist()
        proxy = self.current_proxy()
        self.q.put(("busy", True))

        def work():
            try:
                if proxy:
                    self.log(f"使用代理：{proxy}")
                cl = make_client(extract_sessionid(cookie), proxy)
                self.log(f"登录成功：@{cl.username}")
                self.q.put(("login_ok", cl.username))
            except Exception as e:
                self.log(f"登录失败：{type(e).__name__}：{str(e)[:300]}")
                self.q.put(("login_fail", ""))
            finally:
                self.q.put(("busy", False))

        threading.Thread(target=work, daemon=True).start()

    def on_start(self):
        if self.busy:
            return
        cookie = self.cookie_text.get("1.0", "end").strip()
        username = self.username_entry.get().strip().lstrip("@")
        base_dir = self.dir_entry.get().strip()
        if not cookie:
            self.log("先粘贴 Cookie 并登录")
            return
        if not username:
            self.log("先输入要下载的用户名")
            return
        if not base_dir:
            self.log("先选下载文件夹")
            return
        self.persist()
        proxy = self.current_proxy()
        self.stop_event.clear()
        self.q.put(("busy", True))
        self.progress["value"] = 0

        def work():
            try:
                if proxy:
                    self.log(f"使用代理：{proxy}")
                cl = make_client(extract_sessionid(cookie), proxy)
                self.log(f"已登录：@{cl.username}，开始下载 @{username} 的全部内容")
                run_batch(
                    cl, username, Path(base_dir), self.log,
                    should_stop=self.stop_event.is_set,
                    progress=lambda i, n: self.q.put(("progress", (i, n))),
                )
            except Exception as e:
                self.log(f"出错：{type(e).__name__}：{str(e)[:300]}")
            finally:
                self.q.put(("busy", False))

        threading.Thread(target=work, daemon=True).start()

    def on_stop(self):
        self.stop_event.set()
        self.log("正在停止…（当前文件下完就停）")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
