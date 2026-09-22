"""出站消息通知 — 可插拔通道，用于把日报/周报推到手机或群里。

支持通道
--------
    stdout      打印到标准输出（永远可用，launchd 日志/终端可见）
    wecom       企业微信群机器人（markdown）
    webhook     通用 webhook（POST JSON：{title, text, source, ts}）
    bark        Bark（iOS 推送，GET /:title/:body）
    serverchan  Server酱（sctapi.ftqq.com）

配置（全部走环境变量，密钥不进仓库）
------------------------------------
    NOUS_NOTIFY_CHANNELS=wecom,stdout        启用哪些通道（默认 stdout）
    NOUS_PUSH_WECOM_KEY=xxxxxxxx            企业微信机器人 key
    NOUS_PUSH_WEBHOOK=https://...           通用 webhook 地址
    NOUS_PUSH_BARK_URL=https://api.day.app/xxxxxxxx
    NOUS_PUSH_SC_KEY=SCTxxxxxxxx
    NOUS_NOTIFY_MAX_CHARS=3000              单条正文上限（超出截断）

用法
----
    from nous.core.notify import configured_channels, send

    for r in send("标题", "正文 **markdown**"):
        print(r.channel, r.status, r.detail)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import quote

import requests

DEFAULT_CHANNELS = ("stdout",)
ALL_CHANNELS = ("stdout", "wecom", "webhook", "bark", "serverchan")
DEFAULT_MAX_CHARS = 3000
DEFAULT_TIMEOUT = 10

_ENV_CHANNELS = "NOUS_NOTIFY_CHANNELS"
_ENV_MAX_CHARS = "NOUS_NOTIFY_MAX_CHARS"
_ENV_WECOM_KEY = "NOUS_PUSH_WECOM_KEY"
_ENV_WEBHOOK = "NOUS_PUSH_WEBHOOK"
_ENV_BARK_URL = "NOUS_PUSH_BARK_URL"
_ENV_SC_KEY = "NOUS_PUSH_SC_KEY"


@dataclass
class NotifyResult:
    channel: str
    status: str  # ok | error | skipped
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _credentials(channel: str) -> str:
    """通道凭据；返回空串表示未配置。"""
    mapping = {
        "wecom": _ENV_WECOM_KEY,
        "webhook": _ENV_WEBHOOK,
        "bark": _ENV_BARK_URL,
        "serverchan": _ENV_SC_KEY,
    }
    if channel == "stdout":
        return "builtin"
    env_name = mapping.get(channel, "")
    return _env(env_name) if env_name else ""


def max_chars() -> int:
    raw = _env(_ENV_MAX_CHARS)
    if not raw:
        return DEFAULT_MAX_CHARS
    try:
        return max(int(raw), 200)
    except ValueError:
        return DEFAULT_MAX_CHARS


def configured_channels(explicit: list[str] | None = None) -> list[str]:
    """按配置解析启用通道；只保留凭据齐备的通道。"""
    if explicit:
        wanted = [c.strip() for c in explicit if c.strip()]
    else:
        raw = _env(_ENV_CHANNELS)
        wanted = [c.strip() for c in raw.split(",") if c.strip()] if raw else list(DEFAULT_CHANNELS)

    out: list[str] = []
    for channel in wanted:
        if channel not in ALL_CHANNELS:
            continue
        if _credentials(channel):
            out.append(channel)
    return out


def deliverable_channels() -> list[str]:
    """已配置的**真实**通道（不含 stdout）——用于判断"是否值得自动推送"。"""
    return [c for c in configured_channels() if c != "stdout"]


def truncate(body: str, limit: int | None = None) -> str:
    limit = limit or max_chars()
    if len(body) <= limit:
        return body
    return body[: limit - 20] + "\n…（已截断，详见周报文件）"


def _post(url: str, *, json_body: dict | None = None, data: dict | None = None) -> str:
    resp = requests.post(url, json=json_body, data=data, timeout=DEFAULT_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    return resp.text[:200]


def _get(url: str) -> str:
    resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    return resp.text[:200]


# ── 各通道实现 ─────────────────────────────────────────────────────────
def _send_stdout(title: str, body: str, _: str) -> str:
    print(f"\n===== [nous notify] {title} =====")
    print(body)
    print("===== [nous notify] end =====\n")
    return "printed"


def _send_wecom(title: str, body: str, key: str) -> str:
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    content = truncate(f"**{title}**\n{body}")
    return _post(url, json_body={"msgtype": "markdown", "markdown": {"content": content}})


def _send_webhook(title: str, body: str, url: str) -> str:
    payload = {
        "title": title,
        "text": truncate(body),
        "source": "nous",
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    return _post(url, json_body=payload)


def _send_bark(title: str, body: str, base: str) -> str:
    base = base.rstrip("/")
    return _get(f"{base}/{quote(title)}/{quote(truncate(body))}?group=nous")


def _send_serverchan(title: str, body: str, key: str) -> str:
    url = f"https://sctapi.ftqq.com/{key}.send"
    return _post(url, data={"title": title, "desp": truncate(body)})


_SENDERS: dict[str, Callable[[str, str, str], str]] = {
    "stdout": _send_stdout,
    "wecom": _send_wecom,
    "webhook": _send_webhook,
    "bark": _send_bark,
    "serverchan": _send_serverchan,
}


def send(
    title: str,
    body: str,
    *,
    channels: list[str] | None = None,
    dry_run: bool = False,
) -> list[NotifyResult]:
    """向所有已配置通道发送。任何通道失败都只记录，不抛异常。"""
    targets = configured_channels(channels)
    results: list[NotifyResult] = []
    for channel in targets:
        credential = _credentials(channel)
        if dry_run:
            results.append(NotifyResult(channel, "skipped", "dry-run"))
            continue
        try:
            detail = _SENDERS[channel](title, body, credential)
            results.append(NotifyResult(channel, "ok", str(detail)[:120]))
        except Exception as exc:  # noqa: BLE001 - 推送失败不能影响主流程
            results.append(NotifyResult(channel, "error", f"{type(exc).__name__}: {exc}"[:160]))
    if not results:
        results.append(NotifyResult("none", "skipped", "未配置任何可用通道（设 NOUS_NOTIFY_CHANNELS）"))
    return results


def describe() -> str:
    """人类可读的通道状态（`nous pv notify` 用）。"""
    lines = []
    current = configured_channels()
    for channel in ALL_CHANNELS:
        has_cred = bool(_credentials(channel))
        enabled = channel in current
        env_name = {
            "wecom": _ENV_WECOM_KEY,
            "webhook": _ENV_WEBHOOK,
            "bark": _ENV_BARK_URL,
            "serverchan": _ENV_SC_KEY,
        }.get(channel, "-")
        lines.append(
            f"{'✓' if enabled else '·'} {channel:<11} 凭据{'已配置' if has_cred else '缺失'}"
            f"  环境变量 {env_name}"
        )
    lines.append(f"启用通道: {', '.join(current) or '(无)'}；正文上限 {max_chars()} 字")
    return "\n".join(lines)


def as_json(body: str) -> str:
    """调试用：确认 webhook 载荷形状。"""
    return json.dumps({"title": "t", "text": truncate(body)}, ensure_ascii=False)[:200]
