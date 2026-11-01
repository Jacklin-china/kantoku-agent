"""读取并校验 YAML 配置，在调用时从环境或 .env 获取密钥。"""

from __future__ import annotations

import ctypes
import hashlib
import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "settings.yaml"
EXAMPLE_PATH = ROOT / "config" / "settings.example.yaml"
ENV_PATH = ROOT / ".env"

_observed_secrets: set[str] = set()
_observed_secrets_lock = RLock()


def remember_secret(value: str) -> None:
    """Keep in-flight and previously rotated values redacted until process exit."""
    if len(value) >= 8:
        with _observed_secrets_lock:
            _observed_secrets.add(value)


def observed_secrets() -> set[str]:
    with _observed_secrets_lock:
        return set(_observed_secrets)


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong),
                ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.c_ulonglong), ("CredentialBlobSize", ctypes.c_ulong),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", ctypes.c_ulong), ("AttributeCount", ctypes.c_ulong),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p)]


def _credential_target(name: str) -> str:
    identity = hashlib.sha256(str(ROOT.resolve()).casefold().encode("utf-8")).hexdigest()
    return f"Kantoku/{identity}/{name}"


def _read_managed_key(name: str) -> str | None:
    if os.name != "nt":
        return None
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.POINTER(_Credential)()
    read = api.CredReadW
    read.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                     ctypes.POINTER(ctypes.POINTER(_Credential))]
    read.restype = ctypes.c_int
    if not read(_credential_target(name), 1, 0, ctypes.byref(ptr)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ConfigError("无法读取本机凭据")
    try:
        return ctypes.string_at(ptr.contents.CredentialBlob,
                                ptr.contents.CredentialBlobSize).decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("本机凭据格式不正确") from None
    finally:
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree(ptr)


def save_managed_text_key(name: str, value: str) -> None:
    if os.name != "nt":
        raise ConfigError("当前系统不支持保存文字模型凭据")
    remember_secret(value)
    data = value.encode("utf-8")
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    credential = _Credential(Type=1, TargetName=_credential_target(name),
                             CredentialBlobSize=len(data), CredentialBlob=blob, Persist=2)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    write = api.CredWriteW
    write.argtypes = [ctypes.POINTER(_Credential), ctypes.c_ulong]
    write.restype = ctypes.c_int
    if not write(ctypes.byref(credential), 0):
        raise ConfigError("无法保存本机凭据")


class AppSettings(BaseModel):
    """应用自身的基础配置。"""

    name: str
    log_level: str


class TextTokenPricing(BaseModel):
    """文本验收按未命中/高峰的保守单价估算；不冒充供应商实际账单。"""

    input_cny: Decimal = Field(gt=0, allow_inf_nan=False)
    output_cny: Decimal = Field(gt=0, allow_inf_nan=False)


class LlmSettings(BaseModel):
    """LLM 配置；字段名与 YAML 中的 ``llm`` 段保持一致。"""

    base_url: str
    model_chat: str
    api_key_env: str
    chat_extra_body: dict[str, Any]

    vision_base_url: str
    model_vision: str
    vision_api_key_env: str
    vision_extra_body: dict[str, Any]
    vision_max_tokens: int = Field(gt=0, strict=True)
    vision_max_image_bytes: int = Field(gt=0, strict=True)
    vision_timeout_s: float | None = Field(default=None, gt=0)
    vision_input_max_side: int = Field(default=2048, ge=256, le=8192, strict=True)
    vision_input_max_bytes: int = Field(default=1048576, ge=65536, le=10485760, strict=True)

    fallback_base_url: str
    fallback_model_chat: str
    fallback_api_key_env: str
    fallback_extra_body: dict[str, Any]

    model_config = ConfigDict(allow_inf_nan=False)

    temperature: float = Field(ge=0, le=2)
    structured_temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0, strict=True)
    timeout_s: float = Field(gt=0)
    retry: int = Field(ge=0, strict=True)
    retry_backoff_s: float = Field(default=1.0, ge=0)
    chat_use_temperature: bool = True
    chat_max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    vision_use_temperature: bool = True
    vision_max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    fallback_use_temperature: bool = True
    fallback_max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    pricing_cny_per_million_by_model: dict[str, TextTokenPricing] = Field(default_factory=dict)

    def chat_api_key(self) -> str:
        """读取主聊天模型的密钥。"""
        return _require_env(self.api_key_env)

    def vision_api_key(self) -> str:
        """读取视觉模型的密钥。"""
        return _require_env(self.vision_api_key_env)

    def fallback_api_key(self) -> str:
        """读取备用聊天模型的密钥。"""
        return _require_env(self.fallback_api_key_env)


class BudgetSettings(BaseModel):
    """付费调用的预算和并发上限。"""

    accounting_utc_offset_hours: int = Field(ge=-12, le=14, strict=True)
    image_credit_cny: Decimal = Field(gt=0, allow_inf_nan=False)
    image_estimated_credits_per_call: int = Field(gt=0, strict=True)
    image_estimated_cny_per_call: Decimal | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    image_daily_cny: Decimal = Field(ge=0, allow_inf_nan=False)
    autonomous_image_daily_cny: Decimal | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    image_project_cny: Decimal | None = Field(ge=0, allow_inf_nan=False)
    image_episode_cny: Decimal | None = Field(ge=0, allow_inf_nan=False)
    image_shot_cny: Decimal | None = Field(ge=0, allow_inf_nan=False)
    image_max_concurrency: int = Field(gt=0, strict=True)
    image_conversation_cny: Decimal | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    autonomous_image_auto_cny: Decimal = Field(
        default=Decimal(0), ge=0, allow_inf_nan=False
    )
    token_daily_limit: int = Field(gt=0, strict=True)


class ImagePricing(BaseModel):
    """生图价格表：每个模型每张图的公示单价（元）；未列出的模型用兜底价。"""

    default_cny_per_image: Decimal = Field(gt=0, allow_inf_nan=False)
    cny_per_image_by_model: dict[str, Decimal] = {}


class ImageSettings(BaseModel):
    """共享生图参数；火山签名字段只供旧即梦适配器使用。"""

    provider: str
    pricing: ImagePricing | None = None
    # Missing protocol preserves existing OpenAI-compatible configurations.
    protocol: Literal["openai-compatible", "dashscope-multimodal"] = "openai-compatible"
    base_url: str
    model: str
    region: str = ""
    service: str = ""
    api_version: str = ""
    submit_action: str = ""
    query_action: str = ""
    access_key_env: str = ""
    secret_key_env: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    quality: str = "medium"
    output_format: str = "png"
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    force_single: bool = Field(strict=True)
    prompt_max_chars: int = Field(gt=0, strict=True)
    timeout_s: float = Field(gt=0, allow_inf_nan=False)
    query_retry: int = Field(ge=0, strict=True)
    query_backoff_s: float = Field(gt=0, allow_inf_nan=False)
    output_dir: Path

    def access_key(self) -> str:
        """仅在真实生图时读取 Access Key。"""
        if not self.access_key_env:
            raise ConfigError("即梦 Access Key 环境变量未配置")
        return _require_compact_credential(self.access_key_env, "Access Key ID")

    def secret_key(self) -> str:
        """仅在真实生图时读取 Secret Key。"""
        if not self.secret_key_env:
            raise ConfigError("即梦 Secret Key 环境变量未配置")
        return _require_compact_credential(self.secret_key_env, "Secret Access Key")

    def api_key(self) -> str:
        """仅在 OpenAI 兼容生图时读取 Bearer API Key。"""
        return _require_compact_credential(self.api_key_env, "API Key")


class StorageSettings(BaseModel):
    """项目数据的存储位置；业务代码只通过 ``get_settings`` 读取。"""

    sqlite_path: Path
    # 运行根目录（承载 data/logs/cache/artifacts/tmp）。留空表示项目根目录。
    # 环境变量 KANTOKU_RUNTIME_ROOT 优先于此字段，用于离线测试与隔离验收。
    runtime_root: Path | None = None


class RuntimeSettings(BaseModel):
    """Graph 与 Batch 的安全上限。"""

    max_reworks: int = Field(default=1, ge=0, le=5, strict=True)
    batch_max_concurrency: int = Field(default=2, gt=0, le=16, strict=True)
    context_token_budget: int = Field(default=18000, ge=2000, le=64000, strict=True)
    context_summary_tokens: int = Field(default=2000, ge=256, le=8000, strict=True)
    context_recall_tokens: int = Field(default=6000, ge=256, le=16000, strict=True)


class VideoSettings(BaseModel):
    """可选视频能力配置；默认关闭。"""

    enabled: bool = False
    provider: str = "mock"
    model: str = "mock-video-v1"
    timeout_s: float = Field(default=30, gt=0, allow_inf_nan=False)
    retry: int = Field(default=1, ge=0, le=3, strict=True)
    estimated_fen: int = Field(default=1, gt=0, strict=True)
    max_fen: int = Field(default=1, ge=0, strict=True)
    output_dir: Path = Path("artifacts/videos")
    access_key_env: str = "VOLC_ACCESS_KEY"
    secret_key_env: str = "VOLC_SECRET_KEY"
    authentication: Literal["access_key", "api_key"] = "access_key"
    api_key_env: str = "VOLC_VISUAL_API_KEY"
    tos_bucket: str = ""
    tos_region: str = "cn-beijing"
    reference_urls: dict[str, str] = Field(default_factory=dict)
    pricing_confirmed: bool = False

    def access_key(self) -> str:
        return _require_env(self.access_key_env)

    def secret_key(self) -> str:
        return _require_env(self.secret_key_env)

    def api_key(self) -> str:
        return _require_compact_credential(self.api_key_env, "API Key")


class CommerceSettings(BaseModel):
    """Commerce 真实能力开关；外部 Source 与 Marketplace 仍保持 Mock。"""

    image_mode: Literal["mock", "real"] = "mock"
    data_mode: Literal["demo", "production"] = "production"
    text_mode: Literal["mock", "real"] = "mock"
    real_image_acceptance_max_fen: int = Field(default=30, ge=0, strict=True)
    marketplace: str = "Ozon Mock Marketplace"


class SearchSettings(BaseModel):
    """首页隐式公开搜索；仅影响普通对话，不改变生产流程。"""

    enabled: bool = True
    timeout_s: float = Field(default=8, gt=0, le=30)
    max_results: int = Field(default=4, ge=1, le=8)


class Settings(BaseSettings):
    """项目配置总入口；业务代码只通过 ``get_settings`` 获取配置。"""

    model_config = SettingsConfigDict(env_prefix="KANTOKU_", extra="ignore")

    app: AppSettings
    llm: LlmSettings
    image: ImageSettings
    budget: BudgetSettings
    storage: StorageSettings
    runtime: RuntimeSettings = RuntimeSettings()
    video: VideoSettings = VideoSettings()
    commerce: CommerceSettings = CommerceSettings()
    search: SearchSettings = SearchSettings()


def _require_env(name: str) -> str:
    """读取必需的环境变量；缺失时转换为项目配置异常。"""
    value = os.environ.get(name)
    if value is None:
        # Only text-model credentials can be stored in the OS credential manager.
        # Other providers retain their existing environment / .env semantics.
        settings = get_settings()
        if managed_text_key_allowed(settings, name):
            value = _read_managed_key(name)
    if value is None:
        try:
            value = dotenv_values(ENV_PATH, encoding="utf-8", interpolate=False).get(name)
        except (OSError, UnicodeError):
            raise ConfigError("无法读取 .env 文件") from None
    if not value or not value.strip():
        raise ConfigError(
            f"环境变量 {name} 未设置",
            detail=(
                f'请在项目根目录的 .env 里配置，或先执行：$env:{name}="你的密钥"（PowerShell）'
            ),
        )
    remember_secret(value)
    return value


def managed_text_key_allowed(settings: Settings, name: str) -> bool:
    """Do not silently replace credentials used by paid asynchronous providers."""
    return bool(name and name == settings.llm.api_key_env and name not in {
        settings.image.api_key_env, settings.image.access_key_env, settings.image.secret_key_env,
        settings.video.api_key_env, settings.video.access_key_env, settings.video.secret_key_env,
    })


def _require_compact_credential(name: str, label: str) -> str:
    """读取签名凭据并拒绝复制时混入的空格或换行。"""
    value = _require_env(name)
    if any(character.isspace() for character in value):
        raise ConfigError(
            f"{label} 格式不合法",
            detail=f"{name} 含有空白字符；请只复制密钥值，不要复制字段名称",
        )
    return value


def _read_yaml() -> dict[str, Any]:
    """优先读取本地配置，不存在时回退到可提交的示例配置。"""
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    if not path.exists():
        raise ConfigError(
            "找不到配置文件",
            detail=f"期望路径：{CONFIG_PATH}；回退路径：{EXAMPLE_PATH}",
        )

    try:
        with path.open(encoding="utf-8") as file:
            raw = yaml.safe_load(file)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(
            "读取配置文件失败", detail=f"文件：{path}；类型：{type(exc).__name__}"
        ) from None

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("配置文件格式错误", detail=f"{path} 的顶层必须是键值映射")
    return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取、校验并缓存整份项目配置。"""
    try:
        return Settings.model_validate(_read_yaml())
    except ValidationError as exc:
        locations = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['type']}"
            for item in exc.errors(include_input=False, include_context=False, include_url=False)
        )
        raise ConfigError("配置内容不合法", detail=locations) from None
