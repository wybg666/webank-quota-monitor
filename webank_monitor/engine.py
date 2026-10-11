"""Single-flight polling and explicit, opt-in alert dispatch."""
from __future__ import annotations
import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from .config import atomic_json, load_settings, redacted, validate_settings
from .notify import dispatch

CST = timezone(timedelta(hours=8))

def now_text():
    return datetime.now(CST).isoformat(timespec="seconds")

def is_paused(settings, now=None):
    start, end = settings["pause_start"], settings["pause_end"]
    if not start or not end or start == end:
        return False
    text = (now or datetime.now(CST)).strftime("%H:%M")
    return start <= text < end if start < end else (text >= start or text < end)

class Monitor:
    def __init__(self, client, data_dir, *, allow_external=False, demo=False):
        self.client, self.data_dir = client, data_dir
        self.allow_external = bool(allow_external and not demo)
        self.demo = demo
        self.lock = threading.RLock()
        self.flight = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.settings_path = data_dir / "settings.json"
        self.settings = load_settings(self.settings_path)
        self.running = False
        self.busy = False
        self.last_error = ""
        self.failures = 0
        self.next_run = None
        self.status = "未启动；可先查询一次"
        self.result = None
        self.events = []
        history = data_dir / "events.json"
        if history.exists() and history.stat().st_size <= 2_000_000:
            try:
                items = json.loads(history.read_text(encoding="utf-8"))
                if isinstance(items, list): self.events = items[-200:]
            except (OSError, UnicodeError, json.JSONDecodeError):
                self.status = "历史记录不可读，当前会话从空记录开始"
        self.previous = {}
        self.last_attempt = {}
        self.baseline = set(self.settings["known_products"])
        self.baseline_ready = bool(self.baseline)

    def get_settings(self):
        with self.lock: return redacted(self.settings)

    def save_settings(self, value):
        with self.lock:
            updated = validate_settings(value, self.settings)
            atomic_json(self.settings_path, updated)
            if updated["known_products"] != self.settings["known_products"]:
                self.baseline = set(updated["known_products"])
                self.baseline_ready = bool(self.baseline)
            if updated["target_products"] != self.settings["target_products"]:
                self.previous = {}
            self.settings = updated
            return redacted(updated)

    def snapshot(self):
        with self.lock:
            return {"ok": True, "demo": self.demo, "notifications_allowed": self.allow_external,
                "running": self.running, "busy": self.busy, "status": self.status,
                "last_error": self.last_error, "failures": self.failures, "next_run": self.next_run,
                "data": copy.deepcopy(self.result), "stale": bool(self.result and self.last_error),
                "events": copy.deepcopy(self.events[-100:][::-1])}

    def _event(self, event):
        with self.lock:
            self.events.append(event)
            self.events = self.events[-200:]
            try: atomic_json(self.data_dir / "events.json", self.events)
            except OSError: self.status = "事件记录仅保存在内存（磁盘写入失败）"

    def _query(self, *, alert=False, respect_stop=False):
        if not self.flight.acquire(blocking=False): return False
        with self.lock: self.busy = True
        try:
            result = self.client.fetch()
            if respect_stop and self.stop_event.is_set(): return False
            with self.lock:
                settings = copy.deepcopy(self.settings)
                self.result = result
                self.last_error = ""
                self.failures = 0
                self.status = "最近查询成功"
                products = result["products"]
                names = {p["name"] for p in products}
                new_names = names - self.baseline if self.baseline_ready else set()
                if not self.baseline_ready:
                    self.baseline = names
                self.baseline_ready = True
            for product in products:
                if respect_stop and self.stop_event.is_set(): break
                matched = product["name"] in settings["target_products"]
                new = settings["new_products_enabled"] and product["name"] in new_names
                eligible = product["sale_status"] == "12" and (not settings["require_positive_quota"] or (product["available_quota"] is not None and product["available_quota"] > 0))
                signature = (eligible, product["sale_status"], product["available_quota"])
                key = product["id"]
                old = self.previous.get(key)
                changed = old is None or not old[0] or (settings["notify_quota_changes"] and old != signature)
                due = time.monotonic() - self.last_attempt.get(key, -1e12) >= settings["notification_cooldown"]
                # Manual inspection does not consume monitoring transitions or cooldowns.
                if alert and (matched or new) and eligible and changed and due:
                    event = {"timestamp": now_text(), "name": product["name"], "available_quota": product["available_quota"],
                             "sale_status": product["sale_status"], "kind": "新品提醒" if new and not matched else "目标额度提醒"}
                    event["delivery"] = dispatch(event, settings["notifications"], allow_external=self.allow_external)
                    self._event(event)
                    self.last_attempt[key] = time.monotonic()
                if alert and (not changed or due or not eligible): self.previous[key] = signature
            return True
        except Exception as exc:
            from .client import ClientError
            message = str(exc) if isinstance(exc, ClientError) else f"查询处理失败（{type(exc).__name__}）"
            with self.lock:
                self.last_error = message[:300]
                self.failures += 1
                self.status = "查询失败，保留上次结果（若有）"
            return False
        finally:
            with self.lock: self.busy = False
            self.flight.release()

    def query_once(self):
        with self.lock:
            if self.running or self.busy: return False
            self.busy = True
        def job(): self._query(alert=False)
        threading.Thread(target=job, name="quota-once", daemon=True).start()
        return True

    def start(self):
        with self.lock:
            if self.running or self.busy: return False
            self.running = True
            self.stop_event.clear()
            self.failures = 0
            self.status = "监控已启动"
            self.thread = threading.Thread(target=self._loop, name="quota-monitor", daemon=True)
            self.thread.start()
        return True

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                with self.lock: settings = copy.deepcopy(self.settings)
                if is_paused(settings):
                    with self.lock:
                        self.status = "暂停时段内，不请求接口；停止监控后可手动查询"
                        self.next_run = time.time() + 5
                    if self.stop_event.wait(5): break
                    continue
                self._query(alert=True, respect_stop=True)
                with self.lock:
                    if self.failures >= settings["failure_limit"]:
                        self.status = "连续失败达到上限，监控已停止；检查网络或接口后再启动"
                        break
                    interval = settings["interval_seconds"]
                    delay = min(max(300, interval), interval * (2 ** min(self.failures, 4)))
                    self.next_run = time.time() + delay
                if self.stop_event.wait(delay): break
        finally:
            with self.lock:
                self.running = False
                self.next_run = None
                if self.stop_event.is_set():
                    self.status = "监控已停止"

    def stop(self):
        self.stop_event.set()
        with self.lock: self.status = "正在停止；已发起的网络请求最多等待超时" if self.busy else "监控已停止"

    def shutdown(self):
        self.stop()
        if self.thread: self.thread.join(timeout=2)
