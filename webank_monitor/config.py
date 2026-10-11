"""Validated settings; private runtime files never belong in the source tree."""
from __future__ import annotations
import copy
import json
import os
import re
from pathlib import Path

MASK = "********"
DEFAULTS = {
    "interval_seconds": 15,
    "failure_limit": 3,
    "pause_start": "00:00",
    "pause_end": "07:00",
    "target_products": [],
    "known_products": [],
    "new_products_enabled": False,
    "require_positive_quota": True,
    "notify_quota_changes": True,
    "notification_cooldown": 60,
    "notifications": {
        "voice": {"enabled": False},
        "email": {"enabled": False, "host": "", "port": 465, "security": "ssl", "username": "", "password": "", "sender": "", "recipients": []},
        "bark": {"enabled": False, "device_key": ""},
    },
}


def data_directory(demo=False):
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    default = base / ("WebankQuotaMonitorPython-Demo" if demo else "WebankQuotaMonitorPython")
    custom = os.environ.get("WEBANK_MONITOR_DATA_DIR")
    result = Path(custom or default).expanduser().resolve()
    if demo and custom:
        result = result.with_name(result.name + "-Demo")
    result.mkdir(parents=True, exist_ok=True)
    return result


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label}须为 {low}–{high} 的整数")
    return value


def _text(value, limit, label, *, trim=True):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label}内容或长度不正确")
    return value.strip() if trim else value


def _names(value, label):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError(f"{label}最多 200 项")
    result = []
    for item in value:
        item = _text(item, 150, label)
        if item and item not in result:
            result.append(item)
    return result


def validate_settings(value, previous=None):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("未知或无效配置项")
    result = copy.deepcopy(previous or DEFAULTS)
    for key, item in value.items():
        if key == "notifications":
            if not isinstance(item, dict) or set(item) - set(DEFAULTS[key]):
                raise ValueError("通知渠道无效")
            for channel, fields in item.items():
                if not isinstance(fields, dict) or set(fields) - set(DEFAULTS[key][channel]):
                    raise ValueError("通知字段无效")
                for field, field_value in fields.items():
                    if (channel, field) in {("email", "password"), ("bark", "device_key")} and field_value == MASK:
                        continue
                    result[key][channel][field] = field_value
        else:
            result[key] = item
    for key, low, high in (("interval_seconds", 5, 3600), ("failure_limit", 1, 20), ("notification_cooldown", 10, 86400)):
        result[key] = _integer(result[key], low, high, key)
    for key in ("new_products_enabled", "require_positive_quota", "notify_quota_changes"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key}须为布尔值")
    for key in ("pause_start", "pause_end"):
        item = result[key]
        if not isinstance(item, str) or (item and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item)):
            raise ValueError("暂停时间须为 HH:MM，或同时留空")
    if bool(result["pause_start"]) != bool(result["pause_end"]):
        raise ValueError("暂停起止时间须同时填写或同时留空")
    for key in ("target_products", "known_products"):
        result[key] = _names(result[key], key)
    for channel in result["notifications"].values():
        if not isinstance(channel["enabled"], bool):
            raise ValueError("通知开关须为布尔值")
    email = result["notifications"]["email"]
    for key in ("host", "username", "password", "sender"):
        email[key] = _text(email[key], 512, "邮件" + key, trim=key != "password")
    email["port"] = _integer(email["port"], 1, 65535, "邮件端口")
    if email["security"] not in ("ssl", "starttls"):
        raise ValueError("邮件仅支持 SSL 或 STARTTLS")
    email["recipients"] = _names(email["recipients"], "收件人")
    bark = result["notifications"]["bark"]
    bark["device_key"] = _text(bark["device_key"], 256, "Bark 密钥", trim=False)
    return result


def redacted(settings):
    result = copy.deepcopy(settings)
    for channel, key in (("email", "password"), ("bark", "device_key")):
        result["notifications"][channel][key] = MASK if result["notifications"][channel][key] else ""
    return result


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def load_settings(path):
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    if path.stat().st_size > 65536:
        raise ValueError("配置文件过大")
    try:
        return validate_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("本机配置文件不可读或格式错误，请保留备份后检查") from exc
