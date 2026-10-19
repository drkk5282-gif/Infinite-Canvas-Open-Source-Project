from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AccountStatus(str, Enum):
    offline = "offline"
    logging_in = "logging_in"
    online = "online"
    error = "error"


class Account(BaseModel):
    id: str
    name: str
    status: AccountStatus = AccountStatus.offline
    created_at: str
    last_used_at: Optional[str] = None
    video_count_today: int = 0
    image_count_today: int = 0
    last_error: Optional[str] = None
    note: Optional[str] = None
    cooldown_until: Optional[str] = None
    video_cooldown_until: Optional[str] = None
    doubao_uid: Optional[str] = None
    session_fp: Optional[str] = None
    # "doubao" | "dola" | "gpt"
    provider: str = "doubao"
    # Doubao/Dola membership: "regular" (普通版) | "upgrade" (升级版, extended video duration)
    doubao_tier: str = "regular"
    # Sidebar label e.g. 标准套餐 / 专业版套餐
    doubao_plan_label: Optional[str] = None
    # Ownership: admin (管理员号池) | shared_all (所有共享) | user (归属某内部账号)
    owner_mode: str = "admin"
    owner_user_id: Optional[str] = None
    # Legacy per-account share list (unused; pool shares live in account_pool_shares.json)
    shared_with_user_ids: list[str] = Field(default_factory=list)


class AccountCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=50)
    provider: Optional[str] = Field(default="doubao", description="doubao | dola | gpt")


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None


class AccountOwnerUpdate(BaseModel):
    owner_mode: str = Field(description="admin | shared_all | user")
    owner_user_id: Optional[str] = None


class PoolShareRequest(BaseModel):
    username: Optional[str] = None
    user_id: Optional[str] = None


class QRLoginResponse(BaseModel):
    status: str
    qr_image_base64: Optional[str] = None
    message: Optional[str] = None


class MediaReference(BaseModel):
    kind: str = Field(description="image | video | audio")
    url: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    ratio: str = "自动"
    account_id: Optional[str] = None
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    node_id: Optional[str] = None
    model: Optional[str] = None
    duration: Optional[int] = None
    resolution: Optional[str] = None
    reference_images: Optional[list[str]] = None
    references: Optional[list[MediaReference]] = None
    # When true: use 升级版 accounts only and allow extended Seedance duration (2.0→15s, 2.5→30s).
    use_upgrade: bool = False
    # UI channel: doubao | doubao_s | dola | gpt | myapi
    #（dola_s 已废弃，统一映射为 dola）
    channel: Optional[str] = None
    # Account provider override (doubao | dola | gpt)
    provider: Optional[str] = None


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    ratio: str = "自动"
    account_id: Optional[str] = None
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    node_id: Optional[str] = None
    reference_images: Optional[list[str]] = None
    references: Optional[list[MediaReference]] = None
    model: Optional[str] = None
    style: Optional[str] = None
    # MyAPI clarity tier: 默认|1K|2K|4K (ignored by browser / BenDi channels)
    resolution: Optional[str] = None
    channel: Optional[str] = None
    provider: Optional[str] = None
    use_upgrade: bool = False


VideoGenerateRequest = GenerateRequest


class VideoToolRequest(BaseModel):
    op: str = Field(
        description="extract_audio|extract_video|extract_frame|trim|trim_audio|convert_audio|crop|compress|reverse|to_gif|upscale"
    )
    url: str = Field(min_length=1)
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    ratio: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    format: Optional[str] = None
    quality: Optional[str] = None
    scale_mode: Optional[str] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    scale: Optional[int] = None  # upscale factor 2/3/4
    model: Optional[str] = None  # realesrgan model name
    mode: Optional[str] = None  # ai | basic
    algo: Optional[str] = None  # v2 | v3
    engine_tier: Optional[str] = None  # standard | pro
    target_res: Optional[str] = None  # 720 | 1080 | 2k | 4k
    # Legacy UI fields sometimes leak into video tool requests; ignored by runner.
    direction: Optional[str] = None
    degrees: Optional[int] = None

    def tool_payload(self) -> dict:
        """Payload for job worker — drop board_id and unused None fields."""
        data = self.model_dump(exclude_none=True)
        data.pop("board_id", None)
        data.pop("workbench_id", None)
        data.pop("direction", None)
        data.pop("degrees", None)
        return data

class ImageToolRequest(BaseModel):
    op: str = Field(description="image_crop|image_compress|image_mirror|image_rotate|image_ai_mark")
    url: str = Field(min_length=1)
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    ratio: Optional[str] = None
    scale_mode: Optional[str] = None
    quality: Optional[str] = None
    direction: Optional[str] = None
    degrees: Optional[int] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None

    def tool_payload(self) -> dict:
        data = self.model_dump(exclude_none=True)
        data.pop("board_id", None)
        data.pop("workbench_id", None)
        return data


class EnsureBrowserVideoRequest(BaseModel):
    url: str = Field(min_length=1)
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    force: bool = False


class TaskStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    completed = "completed"
    failed = "failed"


VideoTaskStatus = TaskStatus


class MediaTask(BaseModel):
    id: str
    kind: str = "video"  # video | image | text | voice
    account_id: str
    account_name: str
    prompt: str
    ratio: str
    status: TaskStatus
    created_at: str
    completed_at: Optional[str] = None
    video_url: Optional[str] = None
    cover_url: Optional[str] = None
    thumb_url: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    share_link: Optional[str] = None
    text: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None
    # Rechecking keeps the task ID; stale callbacks from earlier checks are ignored.
    poll_attempt: int = 0
    # Bind back to canvas node / workbench clip after refresh.
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    node_id: Optional[str] = None
    # Browser Doubao/Dola: True after webpage send click — pause is no longer allowed.
    prompt_sent: bool = False
    # Browser paid-quota gate: ask user 继续(消耗付费) / 跳过(换号).
    # kind: "paid_quota_confirm"; action: "" | "continue" | "skip"
    user_prompt_kind: Optional[str] = None
    user_action: Optional[str] = None
    user_prompt_message: Optional[str] = None


VideoTask = MediaTask


class TextGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    ratio: str = "自动"
    account_id: Optional[str] = None
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    node_id: Optional[str] = None
    reference_images: Optional[list[str]] = None
    references: Optional[list[MediaReference]] = None
    model: Optional[str] = None
    channel: Optional[str] = None
    provider: Optional[str] = None
    use_upgrade: bool = False


class VoiceGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    account_id: Optional[str] = None
    board_id: Optional[str] = None
    workbench_id: Optional[str] = None
    node_id: Optional[str] = None
    reference_images: Optional[list[str]] = None
    references: Optional[list[MediaReference]] = None
    model: Optional[str] = None
    channel: Optional[str] = None
    provider: Optional[str] = None
    use_upgrade: bool = False

