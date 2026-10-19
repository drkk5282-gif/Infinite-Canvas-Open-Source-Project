Canvas Server — MyApi 部署版使用说明
=====================================

本包为「纯画布 + MyApi」服务器部署版：
- 支持 MyApi 自定义 HTTP API 渠道（OpenAI 兼容接口）
- 画布接单 + 任务服务执行 HTTP 请求

Windows 快速启动
----------------
1. 解压整个 CanvasServer 文件夹
2. （可选）编辑 port.txt 修改端口，默认 10027
3. 双击「启动画布服务器.bat」（画布 10027 + 管理 8090）
4. 另开窗口双击「启动任务服务.bat」（任务服务 8089）
5. 浏览器打开 http://127.0.0.1:端口

说明：启动脚本调用 CanvasServer.exe，不会强制关闭已占用端口。无需单独安装 Python。

自定义端口
----------
- 修改程序目录下的 port.txt（仅填数字，如 9000）
- 或在启动前设置环境变量 DOUBAO_PORT=9000
- Linux 源码部署同样支持上述两种方式

Linux 服务器部署（推荐）
------------------------
1. 安装 Python 3.10+ 与依赖：
   pip install -r requirements.txt
2. 复制 build_profile.json 到项目根目录（或设置环境变量）：
   export CANVAS_DISABLE_BROWSER_CHANNELS=1
3. 启动：
   export DOUBAO_HOST=0.0.0.0
   export DOUBAO_PORT=8088
   python run.py
4. 防火墙放行 8088；公网访问建议用 HTTPS（见下方 Caddy）

HTTPS（域名 + 证书，不带端口）
-----------------------------
本包已自带 Caddy 脚本，推荐在打包机上放好 caddy.exe 再发布：

1. 在本机下载 caddy_windows_amd64.zip 并解压出 caddy.exe
2. 放到项目 scripts/caddy/vendor/caddy.exe
3. 重新执行 build_release_server.ps1 打包
4. 服务器解压后：
   - 证书放到 caddy/ssl/fullchain.pem 与 privkey.pem
   - 编辑 caddy/Caddyfile（改成你的域名）
   - 腾讯云 DNS A 记录指向服务器 IP，安全组放行 443
   - 先启动「启动画布服务器.bat」，再双击「启动HTTPS.bat」
5. 浏览器访问 https://你的域名（不写 :8088）

若 zip 里没带 caddy.exe：把 caddy.exe 拷到 scripts/caddy/vendor/ 后运行
scripts/install_caddy_offline.bat

service_config.json 建议设置：
  "server_mode": true
  "public_base_url": "https://你的域名"
  "upscale_worker_public_url": "https://worker.example.com"
  （仅在需要外网 Worker 时填写自己的地址；默认留空，使用本机 8089 任务服务）

GPU 超分 Worker
---------------
- 外网 Worker 连 upscale_worker_public_url（默认 10029，映射本机 8089 任务服务）
- 仍可用画布端口 10027 的 /api/upscale/worker/*（自动转发到 8089）
- 本地开发不设 upscale_worker_public_url 时，链接为 http://127.0.0.1:8089

MyApi 配置提示
--------------
- 在设置里添加图片/视频/文本模型，填入第三方 API 地址与密钥
- 节点上切换渠道为 MyApi，选择对应模型
- 视频、图片通过 HTTP API 生成

数据目录
--------
- 所有数据保存在 data/（画布、MyApi 配置、上传文件）
- 换机/备份请整夹拷贝 data/

增量更新（已有安装）
--------------------
1. 关闭正在运行的「启动画布服务器.bat」窗口
2. 把 CanvasServer-update-*.zip 拖到「应用更新.bat」上
   （或运行 scripts\apply_update.ps1）
3. 更新包内列出的**所有文件**都会覆盖；脚本会校验 SHA256 并删除 removed 旧文件
4. 若 UPDATE.json 里包含 CanvasServer.exe，必须替换（后端有改动时会出现）
5. 纯前端改动时可能不含 exe，但仍需**重启服务器**
6. 浏览器打开后按 Ctrl+F5 强刷
7. data/ 目录不会被更新包修改

若功能仍未生效：用完整包 CanvasServer-portable.zip 解压到新目录对比 version.json 的 build 号。

视频超分（重要）
----------------
AI 超分依赖 Real-ESRGAN-ncnn-vulkan，需要 Vulkan：
1. 服务器需有 NVIDIA / AMD / Intel 显卡，并安装对应驱动（推荐）
2. 若提示找不到 vulkan-1.dll：
   - 双击包内「安装Vulkan运行库.bat」，或
   - winget install --id KhronosGroup.VulkanRT -e
   - 或打开 https://vulkan.lunarg.com/sdk/home 安装 Vulkan Runtime
   - 也可把本机 System32\\vulkan-1.dll 复制到
     tools\\engines\\realesrgan-ncnn-vulkan\\ 目录下
3. 装好后重启本程序再试
4. 纯 CPU、无显卡的云主机无法使用 AI 超分，请改用「常规高清」

安全说明
--------
- API Key 仅保存在本机 data/，请勿将 data 目录提交到公开仓库
