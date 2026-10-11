"""Opt-in notifications using only the Python standard library.

Nothing is sent unless both the channel's ``enabled`` flag and the caller's
``allow_external`` flag are literally ``True``.  This module never interacts
with WeChat, purchases products, or returns remote exception text.
"""

from __future__ import annotations

import ipaddress
import json
import re
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage
from urllib import request

_CHANNELS = ("voice", "email", "bark")
_BARK_URL = "https://api.day.app/push"
_MAX_RESPONSE_BYTES = 64 * 1024
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_LOCAL_PART = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+\Z")
_DEVICE_KEY = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_VOICE_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false); "
    "$message = [Console]::In.ReadToEnd(); "
    "$voice = New-Object -ComObject SAPI.SpVoice; "
    "[void]$voice.Speak($message)"
)


class _InvalidConfiguration(ValueError):
    """Internal validation failure; never include untrusted values."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The device key must never be forwarded to a redirect destination.
        return None


def _result(channel: str, ok: bool, skipped: bool, message: str) -> dict:
    return {"channel": channel, "ok": ok, "skipped": skipped, "message": message}


def _string(value: object, max_length: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        raise _InvalidConfiguration()
    if not empty and not value:
        raise _InvalidConfiguration()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _InvalidConfiguration()
    return value


def _host(value: object) -> str:
    host = _string(value, 253)
    # SMTP accepts a host, not a URL, user-info, path, or host:port pair.
    if host != host.strip() or any(char in host for char in "/\\@?#[]"):
        raise _InvalidConfiguration()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not all(_DNS_LABEL.fullmatch(label) for label in host.split(".")):
        raise _InvalidConfiguration()
    return host


def _address(value: object) -> str:
    address = _string(value, 254)
    # Intentionally accept plain ASCII mailboxes only, not display names or
    # comma-separated header fragments.  Each recipient is a separate item.
    if address.count("@") != 1 or not address.isascii():
        raise _InvalidConfiguration()
    local, domain = address.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or not _LOCAL_PART.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or ":" in domain
    ):
        raise _InvalidConfiguration()
    _host(domain)
    return address


def _display(value: object, limit: int = 100) -> str:
    if value is None:
        return "未提供"
    if not isinstance(value, (str, int, float, bool)):
        return "未提供"
    # Notification text is bounded and cannot inject mail headers, terminal
    # control sequences, or extra lines.  It is never inserted into shell code.
    text = str(value)[:limit]
    return "".join(char if char.isprintable() else " " for char in text)


def _event_text(event: dict) -> tuple[str, str, str]:
    if not isinstance(event, dict):
        raise _InvalidConfiguration()
    name = _display(event.get("name"), 100)
    quota = _display(event.get("available_quota"), 64)
    sale_status = _display(event.get("sale_status"), 64)
    kind = _display(event.get("kind"), 64)
    timestamp = _display(event.get("timestamp"), 64)
    title = "微众银行额度通知"
    body = (
        f"产品：{name}\n可用额度：{quota}\n销售状态：{sale_status}"
        f"\n事件类型：{kind}\n监测时间：{timestamp}"
    )
    voice = f"微众银行额度通知。产品：{name}。可用额度：{quota}。销售状态：{sale_status}。"
    return title, body, voice


def _send_email(config: dict, title: str, body: str) -> bool:
    host = _host(config.get("host"))
    port = config.get("port", 465)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise _InvalidConfiguration()
    security = config.get("security", "ssl")
    if security not in ("ssl", "starttls"):
        raise _InvalidConfiguration()
    username = _string(config.get("username", ""), 320, empty=True)
    password = _string(config.get("password", ""), 4096, empty=True)
    if bool(username) != bool(password):
        raise _InvalidConfiguration()
    sender = _address(config.get("sender") or username)
    raw_recipients = config.get("recipients")
    if not isinstance(raw_recipients, list) or not 1 <= len(raw_recipients) <= 100:
        raise _InvalidConfiguration()
    recipients = list(dict.fromkeys(_address(value) for value in raw_recipients))

    message = EmailMessage()
    message["Subject"] = title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    context = ssl.create_default_context()
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=10, context=context) as client:
            if username:
                client.login(username, password)
            refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
    else:
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.ehlo()
            # Authentication and message delivery happen only after verified TLS.
            client.starttls(context=context)
            client.ehlo()
            if username:
                client.login(username, password)
            refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
    return not bool(refused)


def _send_bark(config: dict, title: str, body: str) -> bool:
    device_key = _string(config.get("device_key"), 256)
    if not _DEVICE_KEY.fullmatch(device_key):
        raise _InvalidConfiguration()
    payload = json.dumps(
        {"device_key": device_key, "title": title, "body": body, "sound": "default"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        _BARK_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = request.build_opener(
        _NoRedirect(), request.HTTPSHandler(context=ssl.create_default_context())
    )
    with opener.open(req, timeout=10) as response:
        if response.getcode() != 200:
            return False
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        return False
    parsed = json.loads(raw.decode("utf-8"))
    return isinstance(parsed, dict) and parsed.get("code") == 200


def _send_voice(text: str) -> None:
    subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle",
            "Hidden", "-Command", _VOICE_SCRIPT,
        ],
        input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def dispatch(event: dict, settings: dict, *, allow_external: bool = False) -> list[dict]:
    """Deliver an event to explicitly enabled, explicitly permitted channels.

    ``settings`` is the notifications dictionary (not the whole app config).
    One result is always returned for each of voice/email/bark.  Skipped
    channels have ``ok=True`` because no delivery was attempted.  Invalid
    configuration or delivery failures have ``ok=False, skipped=False``.
    Remote exceptions, server responses, addresses and credentials never
    appear in result messages.  A channel failure does not stop the others.
    """
    results = []
    for channel in _CHANNELS:
        config = settings.get(channel, {}) if isinstance(settings, dict) else {}
        if not isinstance(config, dict) or config.get("enabled") is not True:
            results.append(_result(channel, True, True, "通知渠道未启用"))
            continue
        if allow_external is not True:
            results.append(_result(channel, True, True, "未允许外部通知，已跳过"))
            continue
        if channel == "voice" and sys.platform != "win32":
            results.append(_result(channel, False, False, "语音通知仅支持 Windows"))
            continue
        try:
            title, body, voice = _event_text(event)
            if channel == "voice":
                _send_voice(voice)
                ok = True
            elif channel == "email":
                ok = _send_email(config, title, body)
            else:
                ok = _send_bark(config, title, body)
            results.append(_result(
                channel, ok, False, "通知发送成功" if ok else "通知服务未确认成功"
            ))
        except _InvalidConfiguration:
            results.append(_result(channel, False, False, "通知配置或事件格式无效"))
        except Exception:
            # Do not expose error strings: SMTP/Bark failures may echo secrets.
            results.append(_result(channel, False, False, "通知发送失败，请检查配置或网络"))
    return results
