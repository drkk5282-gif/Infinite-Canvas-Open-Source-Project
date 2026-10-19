Canvas Server — Python 轻量部署包（MyApi）
==========================================

本包为「纯画布 + MyApi」源码部署版：
- 使用服务器已有 Python 运行（无 PyInstaller / 无内置 exe）
- 内存占用更低，体积更小
- 仅 MyApi / 公共模型 HTTP 渠道

准备
----
1. 安装 Python 3.10+（安装时勾选 Add to PATH）
2. 解压本包到服务器目录

Windows 启动（推荐）
--------------------
1. （可选）编辑 port.txt，默认 10027
2. 双击「启动画布服务器.bat」
3. 另开窗口双击「启动任务服务.bat」
4. 浏览器打开 http://127.0.0.1:端口

首次启动会自动：
- 创建本地 .venv
- 按 requirements.txt 安装依赖
之后只有 requirements.txt 变更才会再装一次。

手动安装依赖（可选）
--------------------
若不想用 bat 自动安装：

   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt

Linux 启动
----------
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   export DOUBAO_HOST=0.0.0.0
   export DOUBAO_PORT=10027
   python run.py
   # 另开终端：
   export CANVAS_ROLE=worker
   python run.py

自定义端口
----------
- 修改 port.txt，或设置环境变量 DOUBAO_PORT

配置说明
--------
- build_profile.json 已固定为纯 MyApi（无需改）
- 业务数据在 data/（画布、用户、上传）
- MyApi / 公共模型在管理后台（8090）配置

HTTPS（可选）
------------
包内含 Caddy 脚本；证书放到 caddy/ssl/ 后运行「启动HTTPS.bat」

超分（可选）
------------
AI 超分需要 GPU + Vulkan；无显卡可忽略 tools/engines

安全
----
- API Key 保存在 data/，勿公开上传 data 目录
