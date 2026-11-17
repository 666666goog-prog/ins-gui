# -*- coding: utf-8 -*-
"""无窗口冒烟测试：只测纯逻辑（提用户名、设置存取），不开窗口、不联网。

运行：python test_logic.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app


def test_extract_username():
    cases = [
        ("nasa", "nasa"),
        ("@nasa", "nasa"),
        ("  nasa  ", "nasa"),
        ("https://www.instagram.com/nasa/", "nasa"),
        ("https://www.instagram.com/nasa", "nasa"),
        ("https://www.instagram.com/nasa/?hl=en", "nasa"),
        ("instagram.com/nasa/", "nasa"),
        ("https://www.instagram.com/p/C8abcDEF12/", None),   # 帖子链接认不出
        ("https://www.instagram.com/reel/C8abcDEF12/", None),  # Reel链接认不出
        ("https://www.instagram.com/stories/nasa/123/", None),
        ("", None),
        ("   ", None),
        (None, None),
        ("hello world", None),
        ("bad!name", None),
    ]
    for text, expected in cases:
        got = app.extract_username(text)
        assert got == expected, f"extract_username({text!r}) = {got!r}，期望 {expected!r}"
    print("PASS extract_username")


def test_parse_targets():
    text = "nasa\n@nasa\nhttps://www.instagram.com/spacex/\n\nspacex\nbad line here\n"
    got = app.parse_targets(text)
    assert got == ["nasa", "spacex"], f"parse_targets = {got!r}"
    assert app.parse_targets("") == []
    print("PASS parse_targets")


def test_parse_cookie_string():
    # 典型整行 Cookie
    raw = "sessionid=abc123%3Axyz; csrftoken=tok123; ds_user_id=42; mid=ZzZz"
    got = app.parse_cookie_string(raw)
    assert got["sessionid"] == "abc123%3Axyz", got
    assert got["csrftoken"] == "tok123"
    assert got["ds_user_id"] == "42"
    # 带 Cookie: 前缀
    got2 = app.parse_cookie_string("Cookie: sessionid=s1; csrftoken=t1")
    assert got2 == {"sessionid": "s1", "csrftoken": "t1"}, got2
    # 值里含 "=" 不能切坏
    got3 = app.parse_cookie_string("sessionid=ab==cd; other=x=y")
    assert got3["sessionid"] == "ab==cd" and got3["other"] == "x=y", got3
    # 空 / 乱写
    assert app.parse_cookie_string("") == {}
    assert app.parse_cookie_string("   ") == {}
    assert app.parse_cookie_string(None) == {}
    assert app.parse_cookie_string("没有等号的一句话") == {}
    print("PASS parse_cookie_string")


def test_derive_user_id_and_sessionid_shape():
    # ds_user_id 优先
    assert app.derive_user_id({"ds_user_id": "42", "sessionid": "x"}) == "42"
    # 没有 ds_user_id 时从 sessionid 第一段拆（URL 编码形态）
    assert app.derive_user_id({"sessionid": "59977553097%3Aabcdef%3A28%3Axx"}) == "59977553097"
    # 都没有/乱写返回空
    assert app.derive_user_id({}) == ""
    assert app.derive_user_id({"sessionid": "notanumber"}) == ""
    # sessionid 完整性：多段长串算完整，只剩一截不算
    assert app.sessionid_looks_complete({"sessionid": "59977553097%3Aabcdef0123456789%3A28%3Axx"}) is True
    assert app.sessionid_looks_complete({"sessionid": "59977553097%3A"}) is False
    assert app.sessionid_looks_complete({"sessionid": "59977553097"}) is False
    assert app.sessionid_looks_complete({}) is False
    print("PASS derive_user_id / sessionid shape")


def test_settings_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "data" / "settings.json"
        # 文件不存在时给默认值
        s = app.load_settings(path)
        assert s["opt_posts"] is True and s["speed"] == "正常"
        # 存再读
        s["login_username"] = "someone"
        s["opt_posts"] = False
        s["speed"] = "快速"
        app.save_settings(s, path)
        assert path.is_file()
        s2 = app.load_settings(path)
        assert s2["login_username"] == "someone"
        assert s2["opt_posts"] is False
        assert s2["speed"] == "快速"
        # 坏文件不崩
        path.write_text("{不是json", encoding="utf-8")
        s3 = app.load_settings(path)
        assert s3["opt_posts"] is True
    print("PASS settings save/load")


def test_no_proxy_includes_loopback():
    # 启动时必须把回环加进 NO_PROXY，不然连不上本机浏览器调试口
    import os

    for var in ("NO_PROXY", "no_proxy"):
        parts = [p.strip() for p in os.environ.get(var, "").split(",")]
        assert "localhost" in parts and "127.0.0.1" in parts, (var, os.environ.get(var))
    print("PASS no_proxy loopback")


def test_paths_are_portable():
    # 数据必须在程序旁边，不能是 AppData
    assert app.DATA_DIR.parent == app.BASE_DIR
    assert app.SESSIONS_DIR.parent == app.DATA_DIR
    assert "appdata" not in str(app.DATA_DIR).lower()
    print("PASS portable paths:", app.DATA_DIR)


def test_verify_login_timed():
    # 赛跑版校验：谁先查到用谁；全空为 bad；全慢必须 timeout 且有进度日志
    import time as _time

    class _Ctx:
        sleep = True

        def update_cookies(self, cookies):
            pass

    class _Loader:
        def __init__(self):
            self.context = _Ctx()

    orig = app._verify_checks
    try:
        def _slow30():
            _time.sleep(30)
            return "late"

        # 快的先赢，不等慢的
        app._verify_checks = lambda cookies: [("慢的", _slow30), ("快的", lambda: "someuser")]
        logs = []
        t0 = _time.monotonic()
        result = app.verify_login_timed(_Loader(), {}, logs.append, timeout=30)
        elapsed = _time.monotonic() - t0
        assert result == ("ok", "someuser"), result
        assert elapsed < 10, elapsed
        assert any("确认通过" in m for m in logs), logs

        # 全部查不到 -> bad
        app._verify_checks = lambda cookies: [("甲", lambda: None), ("乙", lambda: None)]
        assert app.verify_login_timed(_Loader(), {}, lambda m: None, timeout=10) == ("bad", None)

        # 全部慢 -> timeout，且中途有进度日志
        app._verify_checks = lambda cookies: [("慢甲", _slow30), ("慢乙", _slow30)]
        logs = []
        t0 = _time.monotonic()
        result = app.verify_login_timed(_Loader(), {}, logs.append, timeout=12)
        elapsed = _time.monotonic() - t0
        assert result == ("timeout", None), result
        assert elapsed < 20, elapsed
        assert any("正在向 Instagram 确认" in m for m in logs), logs
    finally:
        app._verify_checks = orig
    print("PASS verify_login_timed")


def test_network_fixes():
    # IPv4 过滤：有 v4 时只留 v4；只有 v6 时原样返回（不能过滤成空）
    import socket as _socket

    fake = [(_socket.AF_INET6, None), (_socket.AF_INET, None), (_socket.AF_INET, None)]
    got = app._filter_ipv4(fake)
    assert len(got) == 2 and all(r[0] == _socket.AF_INET for r in got), got
    only6 = [(_socket.AF_INET6, None)]
    assert app._filter_ipv4(only6) == only6

    # 系统代理应用：读到代理就写进环境变量（已有的不覆盖）
    import os as _os
    import urllib.request as _ur

    saved = {k: _os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")}
    orig_gp = _ur.getproxies
    try:
        for k in saved:
            _os.environ.pop(k, None)
        _ur.getproxies = lambda: {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        note = app._apply_system_proxy()
        assert "127.0.0.1:7890" in note, note
        assert _os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:7890"
        # 已有环境变量时不覆盖
        _os.environ["HTTPS_PROXY"] = "http://example:1"
        _ur.getproxies = lambda: {"https": "http://127.0.0.1:9999"}
        app._apply_system_proxy()
        assert _os.environ["HTTPS_PROXY"] == "http://example:1"
    finally:
        _ur.getproxies = orig_gp
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
    print("PASS network fixes")


if __name__ == "__main__":
    test_extract_username()
    test_parse_targets()
    test_parse_cookie_string()
    test_derive_user_id_and_sessionid_shape()
    test_no_proxy_includes_loopback()
    test_settings_roundtrip()
    test_paths_are_portable()
    test_verify_login_timed()
    test_network_fixes()
    print("全部通过")
