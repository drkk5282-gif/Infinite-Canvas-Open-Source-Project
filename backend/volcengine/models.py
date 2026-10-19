"""Official Volcengine Ark model catalog for the volcengine channel.

Do not list entries marked 即将下线.
"""

from __future__ import annotations

from typing import Any

# Canvas model id → upstream Ark / openspeech model name.
# Prefix volc_ so normalizeChannel / isVolcengineModel can detect.
#
# upload_note: 画布牵线后，本地图/视/音优先走方舟 Files API（file_id）；外网直链仍用 URL。
# 语音 openspeech 例外：仍用 Base64。用户不需要自己填 Base64。

VOLCENGINE_IMAGE_MODELS: list[dict[str, Any]] = [
    {
        "id": "volc_seedream_5_0_pro",
        "label": "Doubao-Seedream-5.0-pro",
        "model": "doubao-seedream-5-0-pro-260628",
        "desc": "精准图像编辑，图层拆分 / 交互编辑",
        "kind": "image",
        "volcengine": True,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"],
        "resolutions": ["1K", "1.5K", "2K"],
        "max_images": 10,
        "max_videos": 0,
        "max_audios": 0,
        "upload_note": "参考图 jpeg/png/webp/bmp/tiff/gif/heic/heif；单张≤30MB；宽高>14px；总像素≤6000×6000；最多10张。本地图优先 Files API。",
    },
    {
        "id": "volc_seedream_5_0_lite",
        "label": "Doubao-Seedream-5.0-lite",
        "model": "doubao-seedream-5-0-lite-260128",
        "desc": "更智能可控创作，联网搜索，组图",
        "kind": "image",
        "volcengine": True,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"],
        "resolutions": ["2K", "3K", "4K"],
        "max_images": 14,
        "max_videos": 0,
        "max_audios": 0,
        "upload_note": "参考图 jpeg/png/webp/bmp/tiff/gif；单张≤10MB；宽高比 [1/16,16]；最多14张。本地图优先 Files API。",
    },
    {
        "id": "volc_seedream_4_5",
        "label": "Doubao-Seedream-4.5",
        "model": "doubao-seedream-4-5-251128",
        "desc": "多图稳定融合，4K 超高清",
        "kind": "image",
        "volcengine": True,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"],
        "resolutions": ["2K", "4K"],
        "max_images": 14,
        "max_videos": 0,
        "max_audios": 0,
        "upload_note": "参考图 jpeg/png/webp/bmp/tiff/gif；单张≤10MB；最多14张。本地图优先 Files API。",
    },
]

VOLCENGINE_VIDEO_MODELS: list[dict[str, Any]] = [
    {
        "id": "volc_seedance_2_5",
        "label": "Doubao-Seedance-2.5",
        "model": "doubao-seedance-2-5-260628",
        "desc": "30 秒超长叙事，全模态参考扩容",
        "kind": "video",
        "volcengine": True,
        "duration_min": 4,
        "duration_max": 30,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "resolutions": ["480p", "720p", "1080p"],
        "max_images": 30,
        "max_videos": 10,
        "max_audios": 10,
        "generate_audio": True,
        "upload_note": "可参考图×30、视频×10、音频×10。本地素材优先走方舟 Files API。图 jpeg/png/webp；视频 mp4/mov；音频 mp3/wav。",
    },
    {
        "id": "volc_seedance_2_0",
        "label": "Doubao-Seedance-2.0",
        "model": "doubao-seedance-2-0-260128",
        "desc": "超强参考一致性，支持 4K",
        "kind": "video",
        "volcengine": True,
        "duration_min": 4,
        "duration_max": 15,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "resolutions": ["480p", "720p", "1080p", "4k"],
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "generate_audio": True,
        "upload_note": "可参考图×9、视频×3、音频×3（音频需搭配图/视频）。本地素材优先 Files API。图 jpeg/png/webp；视频 mp4/mov；音频 mp3/wav。",
    },
    {
        "id": "volc_seedance_2_0_mini",
        "label": "Doubao-Seedance-2.0-mini",
        "model": "doubao-seedance-2-0-mini-260615",
        "desc": "音视图文参考，高性价比",
        "kind": "video",
        "volcengine": True,
        "duration_min": 4,
        "duration_max": 15,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "resolutions": ["480p", "720p"],
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "generate_audio": True,
        "upload_note": "可参考图/视频/音频。本地素材优先 Files API。图 jpeg/png/webp；视频 mp4/mov；音频 mp3/wav。",
    },
    {
        "id": "volc_seedance_2_0_fast",
        "label": "Doubao-Seedance-2.0-fast",
        "model": "doubao-seedance-2-0-fast-260128",
        "desc": "更快，继承 2.0 核心优势",
        "kind": "video",
        "volcengine": True,
        "duration_min": 4,
        "duration_max": 15,
        "ratios": ["自动", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "resolutions": ["480p", "720p"],
        "max_images": 9,
        "max_videos": 3,
        "max_audios": 3,
        "generate_audio": True,
        "upload_note": "可参考图/视频/音频。本地素材优先 Files API。图 jpeg/png/webp；视频 mp4/mov；音频 mp3/wav。",
    },
]

# 文本：推荐 + 未下线的往期；不含 Seed-1.6 / 1.8 / glm-4.7 等「即将下线」
VOLCENGINE_TEXT_MODELS: list[dict[str, Any]] = [
    {
        "id": "volc_seed_evolving",
        "label": "Doubao-Seed-Evolving",
        "model": "doubao-seed-evolving",
        "desc": "Seed 最新 Coding & Agent，周级迭代",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 1,
        "max_audios": 3,
        "upload_note": "Chat Completions；本地图/视/音优先 Files API（file_id）。智能体需单独打开「用于智能体」。",
        "params_note": "必填 model、messages。常用 temperature、max_tokens。深度思考可用 thinking.type=disabled 关闭。",
    },
    {
        "id": "volc_seed_2_1_pro",
        "label": "Doubao-Seed-2.1-pro",
        "model": "doubao-seed-2-1-pro-260628",
        "desc": "面向生产级任务：编程、智能体与多模态",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 1,
        "max_audios": 3,
        "upload_note": "Chat Completions；本地图/视/音优先 Files API。适合智能体。",
        "params_note": "必填 model、messages。示例 ID：doubao-seed-2-1-pro-260628。",
    },
    {
        "id": "volc_seed_2_1_turbo",
        "label": "Doubao-Seed-2.1-turbo",
        "model": "doubao-seed-2-1-turbo-260628",
        "desc": "效果与成本均衡",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 1,
        "max_audios": 3,
        "upload_note": "Chat Completions；本地图/视/音优先 Files API。适合智能体。",
        "params_note": "必填 model、messages。示例 ID：doubao-seed-2-1-turbo-260628。",
    },
    {
        "id": "volc_seed_character",
        "label": "Doubao-Seed-Character",
        "model": "doubao-seed-character-260628",
        "desc": "面向新一代虚拟陪伴场景",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 1,
        "max_audios": 3,
        "upload_note": "可带参考图/视/音（Files API）。角色扮演增强。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_seed_2_0_lite",
        "label": "Doubao-Seed-2.0-lite",
        "model": "doubao-seed-2-0-lite-260428",
        "desc": "往期旗舰 lite，多模态与工具调用",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 1,
        "max_audios": 3,
        "upload_note": "可带参考图/视/音；音频理解官方示例模型。本地优先 Files API。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_ds_v4_1_flash",
        "label": "DeepSeek-V4.1-Flash",
        "model": "deepseek-v4-1-flash-260910",
        "desc": "混合推理，支持图片理解",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 0,
        "max_audios": 0,
        "upload_note": "可带参考图（图片理解）；本地图优先 Files API。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_glm_5_3_flash",
        "label": "GLM-5.3-Flash",
        "model": "glm-5-3-flash-260828",
        "desc": "GLM-5 系列原生多模态",
        "kind": "text",
        "volcengine": True,
        "multimodal": True,
        "agent_capable": True,
        "max_images": 8,
        "max_videos": 0,
        "max_audios": 0,
        "upload_note": "可带参考图 jpeg/png/webp；本地图优先 Files API。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_ds_v4_pro",
        "label": "DeepSeek-V4-Pro",
        "model": "deepseek-v4-pro-ga-260813",
        "desc": "面向复杂 Agent（GA）",
        "kind": "text",
        "volcengine": True,
        "multimodal": False,
        "agent_capable": True,
        "max_images": 0,
        "upload_note": "纯文本，不接收参考图。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_ds_v4_flash",
        "label": "DeepSeek-V4-Flash",
        "model": "deepseek-v4-flash-ga-260731",
        "desc": "混合推理，精度与速度（GA）",
        "kind": "text",
        "volcengine": True,
        "multimodal": False,
        "agent_capable": True,
        "max_images": 0,
        "upload_note": "纯文本，不接收参考图。",
        "params_note": "必填 model、messages。",
    },
    {
        "id": "volc_glm_5_2",
        "label": "GLM-5.2",
        "model": "glm-5-2-260617",
        "desc": "编码能力升级，适合复杂开发",
        "kind": "text",
        "volcengine": True,
        "multimodal": False,
        "agent_capable": True,
        "max_images": 0,
        "upload_note": "纯文本，不接收参考图。",
        "params_note": "必填 model、messages。",
    },
]

VOLCENGINE_VOICE_MODELS: list[dict[str, Any]] = [
    {
        "id": "volc_seed_audio_1_0",
        "label": "Doubao-音频生成-1.0",
        "model": "seed-audio-1.0",
        "desc": "全要素影视级音频生成",
        "kind": "voice",
        "volcengine": True,
        "canvas": True,
        "voice_role": "generate",
        "max_images": 1,
        "max_videos": 0,
        "max_audios": 3,
        "upload_note": "可参考 1 张图或最多 3 段音频（mp3/wav）。本地拖入画布后牵线。走 openspeech /api/v3/tts/create。",
    },
    {
        "id": "volc_speech_podcast",
        "label": "Doubao-语音播客",
        "model": "doubao-seed-podcast",
        "desc": "对话自然，秒级创作",
        "kind": "voice",
        "volcengine": True,
        "canvas": True,
        "voice_role": "generate",
        "max_images": 0,
        "max_videos": 0,
        "max_audios": 1,
        "upload_note": "文本生成播客语音；可选参考音色音频 mp3/wav。走 openspeech TTS。",
    },
    {
        "id": "volc_voice_design",
        "label": "Doubao-音色设计",
        "model": "doubao-voice-design",
        "desc": "支持通过文本或图片快速生成定制化音色",
        "kind": "voice",
        "volcengine": True,
        "canvas": False,
        "voice_role": "design",
        "max_images": 1,
        "max_audios": 1,
        "upload_note": "音色设计接口，不是画布「语音生成」节点。",
    },
    {
        "id": "volc_asr_stream",
        "label": "Doubao-流式语音识别",
        "model": "doubao-streaming-asr",
        "desc": "豆包语音识别模型，支持流式语音识别",
        "kind": "voice",
        "volcengine": True,
        "canvas": False,
        "voice_role": "asr",
        "upload_note": "识别，不生成。画布语音节点不会调用。",
    },
    {
        "id": "volc_asr_file_2",
        "label": "Doubao-录音文件识别2.0",
        "model": "doubao-asr-file-2.0",
        "desc": "支持多模态信息理解能力",
        "kind": "voice",
        "volcengine": True,
        "canvas": False,
        "voice_role": "asr",
        "upload_note": "识别已有录音文件（wav/mp3/m4a），不生成。",
    },
    {
        "id": "volc_realtime_voice",
        "label": "Doubao-实时语音交互",
        "model": "doubao-realtime-voice",
        "desc": "更拟人和更低时延的语音对话效果",
        "kind": "voice",
        "volcengine": True,
        "canvas": False,
        "voice_role": "realtime",
        "upload_note": "实时双向语音，不是画布单次生成。",
    },
]

_BY_ID: dict[str, dict[str, Any]] = {}
for _lst in (
    VOLCENGINE_IMAGE_MODELS,
    VOLCENGINE_VIDEO_MODELS,
    VOLCENGINE_TEXT_MODELS,
    VOLCENGINE_VOICE_MODELS,
):
    for _m in _lst:
        _BY_ID[str(_m["id"])] = _m


def is_volcengine_model(model_id: str | None) -> bool:
    mid = str(model_id or "").strip()
    if not mid:
        return False
    if mid in _BY_ID:
        return True
    return mid.startswith("volc_") or mid.startswith("volcengine_")


def get_model(model_id: str | None) -> dict[str, Any] | None:
    mid = str(model_id or "").strip()
    if not mid:
        return None
    hit = _BY_ID.get(mid)
    return dict(hit) if hit else None


def list_models(kind: str | None = None) -> list[dict[str, Any]]:
    k = str(kind or "").strip().lower()
    if k == "image":
        return [dict(m) for m in VOLCENGINE_IMAGE_MODELS]
    if k == "video":
        return [dict(m) for m in VOLCENGINE_VIDEO_MODELS]
    if k == "text":
        return [dict(m) for m in VOLCENGINE_TEXT_MODELS]
    if k == "voice":
        return [dict(m) for m in VOLCENGINE_VOICE_MODELS if m.get("canvas", True)]
    out: list[dict[str, Any]] = []
    for lst in (
        VOLCENGINE_IMAGE_MODELS,
        VOLCENGINE_VIDEO_MODELS,
        VOLCENGINE_TEXT_MODELS,
        VOLCENGINE_VOICE_MODELS,
    ):
        out.extend(dict(m) for m in lst)
    return out


def picker_entry(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": m["id"],
        "label": m.get("label") or m.get("name") or m["id"],
        "name": m.get("label") or m.get("name") or m["id"],
        "kind": m.get("kind"),
        "volcengine": True,
        "desc": m.get("desc") or "",
        "duration_min": m.get("duration_min"),
        "duration_max": m.get("duration_max"),
        "ratios": list(m.get("ratios") or []),
        "resolutions": list(m.get("resolutions") or []),
        "max_images": m.get("max_images"),
        "max_videos": m.get("max_videos"),
        "max_audios": m.get("max_audios"),
        "upload_note": m.get("upload_note") or "",
        "params_note": m.get("params_note") or "",
        "multimodal": bool(m.get("multimodal")),
        "agent_capable": bool(m.get("agent_capable")),
        "canvas": bool(m.get("canvas", True)),
        "voice_role": m.get("voice_role") or "",
    }
