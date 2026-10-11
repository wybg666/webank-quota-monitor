"""Explicit offline demonstration, never used as fallback for failed live data."""
from datetime import datetime, timedelta, timezone

class DemoClient:
    def __init__(self):
        self.count = 0

    def fetch(self):
        self.count += 1
        quota = [0, 50000, 50000, 120000][(self.count - 1) % 4]
        return {"products": [
            {"id": "demo-a", "name": "示例产品 A（非真实）", "sale_status": "12", "available_quota": quota, "total_quota": 1000000},
            {"id": "demo-b", "name": "示例产品 B（非真实）", "sale_status": "00", "available_quota": 0, "total_quota": 2000000},
            {"id": "demo-c", "name": "示例产品 C（非真实）", "sale_status": "12", "available_quota": None, "total_quota": None},
        ], "fetched_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "source": "离线示例（非真实行情）", "warnings": ["演示模式绝不发送通知，数据不可作为产品信息或交易依据。"], "scope": "3 条合成示例"}
