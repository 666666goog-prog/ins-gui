# Ins批量下载 v1.0.6

给开源工具 **Instaloader 4.16** 套的图形界面：全中文、鼠标操作、批量按账号下载 Instagram 帖子 / Reels / 头像 / 快拍 / 精选。

## 功能

- **登录**（登录后能下私密账号、快拍、精选，也更不容易被限流）—— 只保留两种（精简版，浏览器窗口/读取浏览器登录已删）
  - **Cookie 登录（推荐）**：在浏览器里登录好 Instagram，复制整串 Cookie 粘贴进来，点「Cookie登录并保存」；必须包含 `sessionid` 且要整行完整。校验走三道（test_login → 按 ds_user_id 查资料 → 手机接口），2026-11-17 已用真实 Cookie 在服务端实测验过、能查到用户名
  - 账号密码登录：填用户名+密码点「账号密码登录并保存」，密码不保存，只保存登录状态到程序旁边的 `data/sessions/`，下次打开自动恢复；开了两步验证的账号会弹窗让你输验证码
  - 不登录也能用，只能下公开账号
- **批量账号**：目标框一行一个用户名，也可以直接粘贴主页链接（自动认出用户名）
- **分类**：开「按类型分文件夹」后，文件按 `下载位置/作者/图片|视频|图文` 分好；快拍在 `快拍/`、精选在 `精选/`
- **速度**：默认只下媒体文件本身，不下原版附带的一堆说明/数据文件，已经比原版快；另有「正常 / 快速」两档，快速会去掉原版每次请求前的随机等待（更快，但更容易被限流 429）
- **跳过已下载**：已存在的文件自动跳过，重复跑只补新的
- **便携**：设置和登录都在程序旁边的 `data/` 里，下载默认在程序旁边的 `下载/` 里，**不写 AppData**，整个文件夹拷走就能用

## 本地运行（需要 Python 3.12）

```
pip install -r requirements.txt
python app.py
```

逻辑自测（不开窗口）：

```
python test_logic.py
```

## 打 Windows 包

仓库带了 GitHub Actions 手动构建（`.github/workflows/build-windows.yml`）：

1. 推到 GitHub 后，进仓库 **Actions** 页，选 **Build Windows**，点 **Run workflow**
2. 构建完在该次运行底部下载产物 `InsDownloader_Windows_X64_v1.0.6`
3. 里面是 `InsDownloader_Windows_X64_v1.0.6.zip`，解压后双击文件夹里的 `InsDownloader_v1.0.6.exe` 使用（onedir 模式，exe 和旁边的文件要在一起，不要单独把 exe 拷出来）

## 已知限制（v1）

- 关掉「跳过已下载」也不能强制重新下载：Instaloader 引擎看到文件已存在会自动跳过，暂无强制开关
- Reels 和帖子有重叠时，不分类模式下 Reels 会单独存到 `Reels/` 文件夹，可能和帖子里的同一条重复一份
- 被 `@` 的帖子（tagged）这一版没做
- 下载是逐个顺序下的（Instagram 对并发很敏感，并发更容易被封/限流）
- 报错 429 = 被限流，等一段时间再下；提示需要安全验证时，按日志里给的链接在浏览器里先过一次验证

## 技术说明

- 单文件 GUI：`app.py`（tkinter），依赖只有 `instaloader==4.16`
- 登录校验（`verify_login_timed`）：三道检查并行赛跑、每道单请求超时 15 秒/最多重试 2 次、全程上限 90 秒、每 10 秒报进度、超时明说（v1.0.4 串行校验在用户机器上 45 秒走不完的教训）
- 网络修正（v1.0.6）：启动时把 Windows 系统代理读进环境变量让 requests 全程走代理（浏览器认系统代理、Python 不一定认，认不到就直连被墙地址干等）；全局强制 IPv4（Python 挨个试地址，IPv6 不通会等满超时，浏览器有快速切换）；Cookie 校验前有一次“网络自检”（量打开 ins 首页的耗时并打进日志），日志能直接区分网络路径问题还是登录问题；启动日志会打印读到的系统代理
- 下载逐条手动迭代 `Profile.get_posts()` / `get_reels()`，方便计数、停止和分类；没有用 `download_profile`
- Instaloader 4.16 没有 `download_highlight()` 方法，精选是遍历 `get_highlights()` 后逐条 `download_storyitem()` 下的；Reels 方法名是 `get_reels()`（不是 `get_reels_posts()`）
