# Ins批量下载 v1.0.3

给开源工具 **Instaloader 4.16** 套的图形界面：全中文、鼠标操作、批量按账号下载 Instagram 帖子 / Reels / 头像 / 快拍 / 精选。

## 功能

- **登录**（登录后能下私密账号、快拍、精选，也更不容易被限流）
  - **打开浏览器登录（推荐）**：点「打开浏览器登录」，会弹出一个软件专用的 Edge 窗口（资料在 `data/browser_profile/`），在里面正常登录一次，软件通过本机调试口自己把登录读回来保存；以后再点这个按钮，没过期就直接完成，不用再登
  - 账号密码登录：填用户名+密码点「登录并保存」，登录状态保存在程序旁边的 `data/sessions/`，下次打开自动恢复；开了两步验证的账号会弹窗让你输验证码
  - 读取 Edge 登录：先在 Edge 里登录 Instagram、**把 Edge 完全关掉**，再点「读取Edge登录」
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
2. 构建完在该次运行底部下载产物 `InsDownloader_Windows_X64_v1.0.3`
3. 里面是 `InsDownloader_Windows_X64_v1.0.3.zip`，解压后双击文件夹里的 `InsDownloader_v1.0.3.exe` 使用（onedir 模式，exe 和旁边的文件要在一起，不要单独把 exe 拷出来）

## 已知限制（v1）

- 关掉「跳过已下载」也不能强制重新下载：Instaloader 引擎看到文件已存在会自动跳过，暂无强制开关
- Reels 和帖子有重叠时，不分类模式下 Reels 会单独存到 `Reels/` 文件夹，可能和帖子里的同一条重复一份
- 被 `@` 的帖子（tagged）这一版没做
- 下载是逐个顺序下的（Instagram 对并发很敏感，并发更容易被封/限流）
- 报错 429 = 被限流，等一段时间再下；提示需要安全验证时，按日志里给的链接在浏览器里先过一次验证

## 技术说明

- 单文件 GUI：`app.py`（tkinter），依赖 `instaloader==4.16`、`browser-cookie3`、`websocket-client`（浏览器登录走 CDP 用）
- 浏览器登录连本机调试口时强制不走代理（`ProxyHandler({})`），且启动时把 `localhost,127.0.0.1` 加进 `NO_PROXY`，避免用户的代理软件拦截 localhost
- 下载逐条手动迭代 `Profile.get_posts()` / `get_reels()`，方便计数、停止和分类；没有用 `download_profile`
- Instaloader 4.16 没有 `download_highlight()` 方法，精选是遍历 `get_highlights()` 后逐条 `download_storyitem()` 下的；Reels 方法名是 `get_reels()`（不是 `get_reels_posts()`）
