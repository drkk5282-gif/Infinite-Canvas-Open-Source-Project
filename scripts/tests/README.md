# 保存与加载回归验证

2026-09-15 桌面素材拖入画布和工作台：

```powershell
python scripts/tests/desktop_drop_browser.py
node --test scripts/tests/*.test.cjs
python scripts/tests/canvas_entry_agent_browser.py --output artifacts/desktop-drop-qa/canvas-interactions.json
```

画布的导入监听提前到首次显示前，防止已显示画板但监听尚未绑定时吞掉文件；撤销历史同时提前初始化。工作台根据当前拖放的类型和文件项识别桌面文件，不再被上次素材拖动遗留的全局状态挡住。拖动期间 FileList 为空时仍允许复制，松手同步提取文件，兼容仅提供 items 的浏览器。内部素材拖动与桌面文件分流，取消/失焦清除拖放光标和遮罩；移除临时的“拖放修复已加载”标记。另修复视频解码失败分支访问未定义 preview，避免阻断加载收尾。

19 项浏览器专项、99 项 Node 测试以及现有 600 节点画布交互回归通过。专项覆盖浏览器原生拖放事件、首次显示即拖入、图片/视频/音频批量导入、图片与视频解码、空 MIME/items-only、拖动状态残留、公共资产/本集素材、引用条、内部拖动、一次上传、取消及刷新保存。修改前源码运行同一专项为 13/19，六项失败已在修改后通过。原始结果在 `artifacts/desktop-drop-qa/`。

测试在独立 Chromium 中使用临时生成的文件和模拟 API，不访问真实账号或素材。原生拖放由 CDP 注入，未自动操作 Windows 桌面；Chromium 的网络拦截不提供磁盘文件正文，模拟上传响应使用对应原始测试文件验证预览。不能把这些测试视为真实上传服务或用户当前 Chrome 窗口的实测。

修改前前端备份：`backups/before-desktop-drop-20260915-162704/frontend/`。只改前端，相关脚本查询版本为 `b238-desktop-drop-20260915`，本机 10027 服务已确认提供新脚本；强制刷新画布和工作台页面加载，无需重启服务。未改发布目录。

2026-09-15 智能体效率与取消请求：

```powershell
python scripts/tests/agent_reliability_test.py
python scripts/tests/agent_efficiency_test.py
node --test scripts/tests/*.test.cjs
python scripts/tests/canvas_entry_agent_browser.py --output artifacts/agent-efficiency-qa/browser.json
python scripts/verify_agent_efficiency.py
```

后端 40 项、全量 Node 98 项通过，浏览器画布回归无脚本错误。覆盖真实 ASGI 请求经过 HTTP 中间件后的断开取消、总时限内的修复、网络超时不重复提交、参数明确被拒后的兼容回退、修复仍关闭长思考、Ask 选项清除操作、截断回答拒绝执行、完整原文及不同文档版本保留、看图与纯文字操作分流、前端停止后禁止执行刚返回的计划、生成批次重复目标去重，以及已删除模型不静默切换。

新增严格匹配的本地规划：明确单一目标的改名、明确所有/选中范围的横竖排列、画布节点数量查询。不匹配、同名歧义、复合请求、粘贴文档继续走模型；Ask 不执行本地修改。所有本地计划仍经过后端校验和前端原有确认/自动模式、节点快照检查。

合并重复系统规则；只读请求不携带操作协议。相同长文在历史中保留一份原文与重复提交标记，节点正文与已有用户材料完全相同时引用全文所在位置；不同版本、不同提示词/生成正文均保留。聚焦明确目标时只发送目标和相关节点详情，仍保留全量索引及未读节点列表。网络请求去除重复 body 字段；已选模型直接调用后端，不再每次先查询两份目录。仅文字/结构操作跳过视觉；真正看图、改图与不确定意图仍读取图片。视觉、请求、兼容回退和一次必要修复共用 90 秒总时限，网络错误不作为参数兼容理由重试。停止会取消本机上游等待和后续提交；已经提交的媒体任务继续由原任务队列管理，关闭连接无法保证供应商端停止计算。

实测使用默认 Agnes 3.0 Flash 和虚构剧本，只规划，不执行返回动作。原始记录在 `artifacts/agent-efficiency-qa/`：文档提取输入从 19,893 字符降至 7,132（约 64%），但这次真实请求从 10.9 秒变为 21.3 秒，不能据此声称模型整体加速。改名在原版经模型约 6.3 秒；中间版仅压缩上下文时仍等了 56.2 秒；最终本地规划约 2 毫秒、模型调用数为 0（`final-local.json`）。这些是单次样本和规划耗时，不含前端操作/图片生成，不代表所有设备和上游网络。`request-comparison.json` 使用模拟传输，只比较内容体积，不用于推断网络耗时。手动复测真实上游可加 `--live`，会产生模型调用；`--case rename-selected --only-after` 只验证本地规划。

原有后端基线 21 项中有 4 项失败，现已处理。其中视觉失败测试的旧样本没有图片地址，无法进入视觉调用，现补齐真实目标图样本；未让无图片任务为通过测试而额外调用视觉。另三项是含糊只读回答绕过校验、只读选项夹带操作，已修复。

前端资源版本为 `b235-agent-efficiency-20260915`。本轮修改前源码备份：`backups/before-agent-efficiency-20260915-143935/`。画布/管理服务已重启加载后端，8089 任务服务保持原进程；刷新画布页面加载新脚本。未更新 dist 或发布压缩包。重启执行了项目原有启动维护，日志记录清理了 36 个超过保留期的回收站文件。启动维护逻辑不是本轮改动。

2026-09-15 密集素材区节点拖动、取消和画布平移：

```powershell
node --test scripts/tests/*.test.cjs
python scripts/tests/canvas_entry_agent_browser.py --paint-profile --paint-cpu-rate 1 --output artifacts/canvas-drag-qa/dense-normal-final.json
python scripts/tests/canvas_entry_agent_browser.py --paint-profile --paint-cpu-rate 1 --baseline --frontend-source backups/canvas-drag-smoothness-20260915-102755/frontend --output artifacts/canvas-drag-qa/dense-normal-before.json
python scripts/tests/canvas_entry_agent_browser.py --motion-profile --output artifacts/canvas-drag-qa/final-motion.json
python scripts/tests/canvas_entry_agent_browser.py --interaction-load --output artifacts/canvas-drag-qa/final-load.json
python scripts/tests/canvas_entry_agent_browser.py --overview --output artifacts/canvas-drag-qa/final-overview.json
python scripts/tests/canvas_entry_agent_browser.py --output artifacts/canvas-drag-qa/final-interactions.json
```

主要修复：对齐辅助线复用固定 SVG 线条池，范围限于视口；移除根元素上的全局 `:has()` 样式依赖，避免辅助线变化导致所有节点端口重新计算样式。吸附改为有序索引查询，拖动连线放入独立的视口大小 SVG。平移期间复用节点几何与空间索引，仅调整跨越可见边界的节点；网格改为独立变换层。停止拖动只更新涉及的选中节点，按模型位置更新连线边界；移除空编辑器的同步预测量及重复连线重建。取消/失焦/弹窗中断共享收尾和保存路径，上一轮面板回调不会在下一轮拖动中强制测量。

密集区测试固定视口，600 张合成 1024×576 图片全部加载、重叠摆放，599 条线，缩放 0.3，1440×900 无头 Chromium，CPU 为正常速度。与本轮原始备份比较，同机单次拖动帧间隔中位数约 33.4 ms → 16.7 ms，P95 约 50.1 ms → 33.3 ms；每帧样式计算元素数中位数 3607 → 4。最终测量松手恢复的最长帧间隔约 398 ms → 183 ms；连续 6 次取消的收尾 JS 分别约 4.5–6.7 ms，最后位置、撤销/恢复和媒体暂停计数均通过检查。松手恢复仍有合成和绘制成本，不能将收尾 JS 函数耗时当作用户看到的全部停顿。

分区场景的边缘拖动、区域平移 P95 约 16.8 ms。回归包括辅助线池复用、网格位置、连续取消/失焦/弹窗中断、连线和节点身份/顺序、媒体暂停计数、最后位置与撤销恢复；也覆盖任务完成期间拖动、框选、快捷键、提示词焦点、屏外选中、缩放和刷新保存。89 项 Node 测试通过。测试使用模拟接口，不调用真实模型或账号。性能数字来自单机合成压力场景，不代表所有设备或真实素材都达到 60 FPS；进一步的 4 倍 CPU 压力诊断仍能观察到密集画面的浏览器合成开销。

原始备份：`backups/canvas-drag-smoothness-20260915-102755/`，恢复说明在其中的 `RESTORE.md`。本地 10027 服务已逐字节确认提供新 CSS、canvas.js 和 agent/panel.js，页面查询版本为 `b234-canvas-motion-20260915`，刷新加载。诊断文件中的 `dense-before-isolation` 是中间版本，正式修改前对比以 `dense-normal-before.json` 为准。

2026-09-14 画布交互期间加载调度：

```powershell
node --test scripts/tests/*.test.cjs
python scripts/tests/canvas_entry_agent_browser.py --interaction-load --output artifacts/canvas-interaction-qa/after.json
python scripts/tests/canvas_entry_agent_browser.py --interaction-load --baseline --canvas-source backups/before-interaction-scheduling-20260914/frontend/js/canvas.js --output artifacts/canvas-interaction-qa/before.json
python scripts/tests/canvas_entry_agent_browser.py --overview --output artifacts/canvas-interaction-qa/overview-compatibility.json
```

拖动、平移和缩放期间暂停新媒体队列、节点补建及任务结果画面更新，任务数据照常接收；停顿后恢复，任务补画按时间预算执行。延迟节点优先补齐可见区域，单次补建预算由 8 ms 收紧至 4 ms，超过 40 个节点启用按需初始化。拖动缓存端点及对齐边界，避免每帧查询节点和重建全部连线；批量选择保留屏外占位，少量选择仍可立即操作。失焦及中断释放交互暂停状态，保留独立的灯箱队列暂停计数。

测试使用 600 个模拟图片节点、599 条连线和合成的 1024×576 图片，无真实账号或付费调用。持续拖动和平移缩放时注入刷新、媒体加载和任务完成：修改前拖动触发 21 次全量连线刷新，平移缩放补建 201 张卡片；修改后这些次数及新媒体启动次数均为 0，停下后可见内容、任务图片与连线正常补齐。全选 600 节点仍保留 422 个延迟节点。86 项 Node 测试通过。

同机单次压力场景帧间隔 P95 约 250 ms 降至 42 ms，拖动函数 P95 约 6.2 ms 降至 0.7 ms。此为带测试注入的无头浏览器测量，不能视为真实设备帧率或保证无卡顿；已开始的媒体传输/解码不会被中断，显式全量强制刷新也仍可能耗时。原始记录保存在 `artifacts/canvas-interaction-qa/`。本轮只改前端，canvas.js 查询版本为 `b201-interaction-20260914`，本机 10027 服务已确认提供新脚本，刷新页面生效。修改前备份在 `backups/before-interaction-scheduling-20260914/`，没有重启服务或更新发布包。

2026-09-14 画布进入与智能体适配：

```powershell
node --test scripts/tests/*.test.cjs
python scripts/tests/agent_reliability_test.py
python scripts/tests/canvas_entry_agent_browser.py --output artifacts/canvas-agent-qa/after.json
python scripts/tests/canvas_entry_agent_browser.py --overview --output artifacts/canvas-agent-qa/overview.json
python scripts/tests/canvas_entry_agent_browser.py --baseline --output artifacts/canvas-agent-qa/before.json
```

85 项 Node 测试、21 项后端智能体测试通过。新的浏览器入口直接读取当前 `/canvas?id=...` 页面，所有接口与任务均为模拟数据，复用原大画布交互检查。旧大画布脚本按单页首页启动，当前首页/画布已拆页，原启动等待不再适用；本次没有将旧脚本误报为通过。

600 节点、599 连线、1440×900、模拟任务同步延迟 1.8 秒的同机场景：修改前首次渲染约 522 ms、创建 600 张卡片，画布显示约 2979 ms；修改后约 68 ms、49 张卡片，显示约 797 ms，其余 551 个节点保留轻量几何壳。0.3 倍缩放首渲染约 100 ms，显示约 856 ms，可见节点随后分批完成初始化。数字是单次模拟验证，不能等同于真实图片、网络和不同设备上的帧率。原始数据与截图在 `artifacts/canvas-agent-qa/`。

画布超过 100 节点时，优先初始化首屏及选中节点，其它节点保留位置和身份，进入预加载范围后按时间预算初始化；一次新增卡片也有数量上限。任务恢复移至画布显示后，关闭的智能体面板不预加载长聊天记录。浏览器验证覆盖拖动、框选、缩放、快捷键、改名、复制、撤销/恢复、连线、编辑焦点、屏外选中与更新、刷新保存恢复。

智能体新增精确局部文字替换及基于当前原图修改同一节点。正文和可见提示词编辑器保持同步；局部替换保留换行、字面标记和素材引用；重复匹配或原文变化会停止。改图使用普通任务队列，原图进入参考图及重试快照，修改前历史保留，结果返回后更新目标节点。新测试还覆盖图片修改未提交、原图已变化、计划过期、切换画板、生成失败不假报成功，以及新建节点后使用 `$last_created` 连线。

上下文包含全量节点索引、选中/@/可见范围、位置和连线；大画布优先发送相关节点的完整正文，明确列出尚未提供的详情。主模型开启多模态时实际发送图片块；配置外挂视觉模型时先获取视觉观察。视觉读取设有 25 秒上限，失败会明确告诉规划模型没有读到像素。长文先由模型分析再动态追问；`decision:clarify` 支持无固定选项的自由回答，且不能夹带执行动作。后端使用模拟模型传输验证，未调用真实付费模型；生成效果与判断质量仍取决于模型能力及参考图支持。

修改前应用文件在 `backups/before-canvas-agent-adaptation-20260914/`。当前脚本查询版本为 `b200-canvas-agent-20260914`。前端刷新加载，后端 `backend/agent/` 修改需重启画布服务；本次未重启正在运行的服务，未更新发布包、`dist` 或 `最新版`。

2026-09-13 修复原有失败与离线刷新恢复：

```powershell
node --test scripts/tests/*.test.cjs
python scripts/tests/autosave_browser.py
python scripts/tests/canvas_large_board_browser.py --output artifacts/save-recovery-qa/large-canvas.json
python scripts/tests/canvas_smoothness_browser.py --screenshots artifacts/save-recovery-qa
```

全量 Node 测试 63/63 通过，原来的 6 项失败已解决。3 项 task-retry 测试补齐浏览器 window 环境；媒体调度测试接入当前加载器的超时释放流程，并保留“浏览器仍在传输时不抢占并发槽位”的验证。新增 7 项回归覆盖保存期间继续按文档编辑、完整改动与单文档改动合并、连续刷新、离线加载后补传、联网核对文档归属及存储代际，以及恢复任务时保护手动暂停和历史选择。

`document-save.js` 将恢复出的本地改动保留为待上传状态，先把这个状态写入 IndexedDB，再移除应急日志；服务器确认旧快照时不会清除请求期间的新编辑。离线启动后重连先校验服务器文档列表及存储代际，避免复活远端已删除的文档。350 毫秒防抖、2 秒检查点、Worker 编解码及原有自动重试保留，普通输入仍只标记改动。

画布根据后端任务的准确画板/节点关联恢复丢失的 taskId；手动停止、取消排队会持久化停止标记，新生成清除停止状态并记住上一任务，复制节点清除这些关联。恢复不会接入旧终态任务、覆盖另一任务或已选择的历史结果。任务轮询间隔、快捷键和大画布渲染优化保留。

三套浏览器检查均通过，无页面脚本错误。保存检查覆盖离线立即刷新、读写均失败时再次刷新、离线继续编辑后联网补传、视口恢复、旧服务器 PUT 回退、大于 64 KiB 的 UTF-8 保存、提示词恢复、任务绑定恢复、手动停止后刷新和旧轮询，以及远端更新和存储重置。2000 节点、约 7.39 MB 的模拟工作区，500 次保存标记调用耗时约 1.8 ms，同步缓存写入为 0。600 节点/599 连线浏览器回归通过，拖动、框选、快捷键、撤销/恢复、焦点、远端刷新和刷新恢复均通过。

首页/视频测试补充启动完成等待，避免启动布局尚未就绪时采集封面请求数量；首屏 120 张封面请求 19 张。性能数字来自本机模拟场景，原始日志在 `artifacts/save-recovery-qa/`。当前三个修改脚本的静态查询版本为 `b170-save-recovery-20260913`，源码服务刷新页面生效。修改前备份在 `backups/before-save-recovery-20260913/`。

以下为此前各轮记录，其中提到的 6 项失败和离线保存失败已在本次修复。

2026-09-13 大画布渲染优化：

```powershell
node --test scripts/tests/canvas-smoothness.test.cjs scripts/tests/background-refresh.test.cjs scripts/tests/media-loading-performance.test.cjs scripts/tests/lightbox-loading.test.cjs scripts/tests/network-loading.test.cjs
python scripts/tests/canvas_large_board_browser.py --output artifacts/large-canvas-qa/after.json
python scripts/tests/canvas_smoothness_browser.py --screenshots artifacts/large-canvas-qa
```

本轮应用代码仅修改 `frontend/js/canvas.js` 和 `frontend/index.html`。刷新间隔、保存流程、任务轮询、快捷键绑定不变。标题先集中重置、集中测量，再集中写入；屏外标题延后到进入可见预加载范围时批量测量。可见范围检查先读所有边界再更新样式。节点、连线和引用关系使用单次处理内的索引，连线按 ID 复用并更新路径，避免无变化刷新时拆除重建；索引不跨刷新保留，避免远端更新、撤销、换板后读到旧对象。

新增大画布浏览器测试使用 600 个模拟图片生成节点和 599 条连线，等待启动完成并确认画布可见后测量，每项取 5 次的中位数。本机修改前/后结果分别是：无变化刷新 313.7/6.8 ms，可见范围检查 68.9/1.6 ms，连线刷新 440.3/2.3 ms，缩放标题布局 152/2.6 ms。结果是函数耗时，不能等同于真实素材、不同设备上的整帧耗时或帧率。基线通过 `--canvas-source backups/before-large-canvas-20260913/frontend/js/canvas.js --benchmark-only` 读取备份，不覆盖运行中的源码；原始结果保存在 `artifacts/large-canvas-qa/before.json` 和 `after.json`。

新增浏览器测试覆盖节点/连线 DOM 保留、路径移动、连线顺序与删除/恢复、端口联动、0.3/1/2/4 倍缩放对齐、屏外节点选择与重新进入、方向键平移、Ctrl+加号缩放、F2 改名、Ctrl+D 副本、撤销/恢复、删除、输入文字/光标保留、框选、拖动、远端状态/名称/位置更新以及保存后刷新恢复。该测试和原视频交互测试通过，23 项相关 Node 测试通过。

全量 Node 测试原有 6 项失败（修改前 50/56 通过），详见下方 2026-09-09 记录。本次还运行了原 `autosave_browser.py`：修正其过早进入工作台的启动等待后，在“离线新增片段后立即刷新，服务端应恢复 3 个片段”的原有断言失败；用备份 canvas.js 对照同样失败。本轮未修改 document-save.js 或工作台保存逻辑，不能将这一原有离线恢复问题视为已解决。`autosave_browser.py` 增加 `--canvas-source` 供基线对比。

静态查询版本为 `b170-largecanvas-20260913`，已确认本机 10027 服务直接提供当前源码，刷新页面即可加载。本轮没有更新发布目录、最新版或压缩包。修改前文件保存在 `backups/before-large-canvas-20260913/`。

2026-09-09 画布、首页、素材及视频流畅性优化：

```powershell
node --test scripts/tests/canvas-smoothness.test.cjs scripts/tests/media-loading-performance.test.cjs scripts/tests/lightbox-loading.test.cjs
python scripts/tests/canvas_smoothness_browser.py --screenshots artifacts/smoothness-qa
python scripts/tests/asset_loading_browser.py
python scripts/tests/video_lightbox_browser.py
```

新增检查使用模拟 API、临时生成的视频和模拟图片，直接读取当前 frontend，不依赖运行中的服务。新增浏览器测试覆盖首页可见范围加载、无变化刷新保留 DOM 和焦点、改名更新、视频封面类型、节点拖动合帧与松手坐标、播放/手动暂停、键盘进度、平移中缓冲事件、隐藏页面暂停，以及工作台重复播放去重。1440×900 本机测试中，120 张封面首屏请求 19 张；20 次不变刷新无子节点变更；一帧内 120 个拖动事件产生 1 次节点/连线更新，松手后的额外更新正确保存最终位置。这些是模拟场景验证，不代表所有设备和网络的帧率。

本轮新增 5 项 Node 测试与三套相关浏览器测试通过。完整 Node 测试修改前为 51 项（45 通过、6 失败），修改后为 56 项（50 通过、同样 6 失败）。原有失败包括 pagehide 应急日志、3 项 task-retry 测试缺失 window 环境、1 项 scheduler 测试缺失 mediaLoadHoldDepth 环境，以及 active backend task rebind 断言；未更改这些测试或相关生成/保存代码。

已更新修改文件的静态资源查询版本 `b165-smooth-20260909`。使用项目根目录源码运行的服务刷新页面即可获取改动，无需重启后端。本轮未更新 dist、最新版或历史副本。

修改前备份：`backups/before-smoothness-20260909-114809/`，共 250 个源码/配置文件，逐文件 SHA-256 校验。回滚方法见该目录的 `RESTORE.md`。本轮应用代码修改仅涉及 frontend 下的 index.html、css/style.css、js/canvas.js、js/workbench.js、js/workbench-extras.js。

2026-09-07 视频 Range 修复：旧 Starlette 0.37.2 的 FileResponse 忽略浏览器 Range 请求。
实测运行中的旧服务请求 `bytes=0-1023`，返回 200 和完整 7,829,792 字节 MP4。
`backend/media/range_response.py` 为上传文件提供 206 分段读取、416、If-Range、HEAD 和完整下载兼容。
需重启 10027 Python 服务，刷新前端无法加载后端修改。测试：

```powershell
$env:PYTHONPATH='.'
python scripts/tests/media_range_test.py
python scripts/tests/text_compression_test.py
```

Range 测试包含临时文件协议测试，以及使用本机已有六个问题视频的只读 HTTP 检查；后者启动临时 loopback 服务，不替换生产进程，样本不存在时跳过。六个 1 KB 请求合计应只返回 6,144 字节。多范围请求按不支持处理为完整 200 响应。

2026-09-07 素材加载优化验证：

```powershell
node --test scripts/tests/media-loading-performance.test.cjs
python scripts/tests/asset_loading_browser.py
```

新浏览器测试直接拦截请求并读取当前 frontend 文件，无需启动服务；API、240 个素材和短视频均为临时模拟数据，不访问真实账号或素材。覆盖无变化刷新保留 DOM、多选、拖拽数据、双击预览、改名、分类、生成状态、删除，以及反复加载下的视频解码和播放。视频专项测试另覆盖慢请求去重、过期任务跳过、地址切换、超时重试和事件监听器清理。修改后的 JS 使用新的静态资源查询版本，当前源码服务刷新页面后生效；发布目录和压缩包不在本次修改范围内。

双击放大专项：`node --test scripts/tests/lightbox-loading.test.cjs` 和 `python scripts/tests/video_lightbox_browser.py`。后者现在直接读取当前前端静态文件，不依赖运行中的服务器；使用独立浏览器、模拟 API，并只读一份本机已有 MP4 作为媒体样本，模拟首次下载失败。覆盖同一播放器预热、快速重开复用、播放/进度调整、截图目标、前后切换和工作台预览。单元测试覆盖下载进展延长等待、无进展重试、最长等待上限、主动切换不误判失败、签名 URL 不变，以及关闭后 30 秒释放缓冲。预热与重开仅保留一个播放器，不预下载整张画布。

本次修改前全量 Node 测试基线为 35 项、32 通过、3 失败：pagehide journal、hung media scheduler（测试缺少 mediaLoadHoldDepth 上下文）、active backend task rebind。修改后这三项仍失败，未将其视为本次新增回归，也未改动这些测试来掩盖失败。

在项目根目录执行：

```powershell
node --test scripts/tests/document-save.test.cjs
python scripts/tests/document_updates_test.py
python scripts/tests/autosave_browser.py
```

页面检查需要画布服务运行在 `127.0.0.1:10027`。检查使用临时数据目录，不操作真实账号、素材或生成任务。

`document-save.js` 在操作时只标记改动，350 毫秒停顿后合并处理，连续操作最多等待约 2 秒开始保存。JSON 编解码使用独立 Worker；IndexedDB 按画板或工作台保存发生变化的内容。关闭、刷新、退出登录时用应急日志保存尚未写入异步缓存的改动。服务器不可用时自动重试。浏览器被强制终止或操作系统突然断电无法保证最近一瞬间的改动已经写入磁盘。

新增 `PATCH /api/canvas` 和 `PATCH /api/workbench` 只更新请求中包含的文档，返回小型确认响应，仍使用原有文件、权限判断与备份逻辑。删除仍走原有 DELETE 接口。旧服务器没有 PATCH 时，前端自动回退到原来的 PUT 接口；增量保存需重启画布服务并刷新页面后启用，任务服务无需因此重启。

加载会合并同时发出的请求，在短时间重新进入视图时复用内存数据。有未保存的编辑时，不用旧的加载结果覆盖它；重开页面时仅恢复标记为待同步的文档。服务器在用户数据目录内保存代际标识；目录删除并重建后标识变化，IndexedDB、应急日志和旧 localStorage 缓存会自动失效，避免把删除前的数据写回服务器。旧缓存仅在服务器仍有同一代数据时参与兼容恢复。

测试覆盖快速连续编辑、失败重试、立即刷新、删除恢复、远端更新、跨账号保护、旧接口回退以及大数据下的主线程保存调用。性能数据来自模拟 API 的本机测试，不代表网络、磁盘和所有设备上的完整耗时。

修改前源码在 `多余项目/备份文件/保存性能优化前-20260904/`。上一轮未启用的保存方案与旧测试保存在 `多余项目/临时排查与研究/旧保存方案-20260904/`。
