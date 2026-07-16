import os
import json
import time
import logging

logger = logging.getLogger(__name__)

STATS_FILE = "email_weekly_stats.json"


class EmailWeeklyStats:
    def __init__(self, filepath: str = STATS_FILE):
        self.filepath = filepath
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            with open(self.filepath) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"days": []}
        self._prune(data)
        return data

    def _save(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save email stats: %s", e)

    def _prune(self, data: dict):
        cutoff = time.time() - 7 * 86400
        data["days"] = [d for d in data.get("days", []) if d.get("date", 0) >= cutoff]

    def _today_key(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _get_or_create_today(self) -> dict:
        key = self._today_key()
        for d in self._data["days"]:
            if d["date"] == key:
                return d
        entry = {"date": key, "gmail_api": 0, "smtp": 0, "resend": 0, "total": 0}
        self._data["days"].append(entry)
        return entry

    def record_send(self, method: str = "unknown"):
        today = self._get_or_create_today()
        method_key = method if method in ("gmail_api", "smtp", "resend") else "other"
        today[method_key] = today.get(method_key, 0) + 1
        today["total"] = today.get("total", 0) + 1
        self._save()

    def get_weekly(self) -> list:
        self._prune(self._data)
        return sorted(self._data.get("days", []), key=lambda d: d.get("date", ""))

    def get_today(self) -> dict:
        key = self._today_key()
        for d in self._data.get("days", []):
            if d["date"] == key:
                return d
        return {"date": key, "gmail_api": 0, "smtp": 0, "resend": 0, "total": 0}

    def get_week_total(self) -> dict:
        days = self.get_weekly()
        total = {"gmail_api": 0, "smtp": 0, "resend": 0, "total": 0}
        for d in days:
            for k in total:
                total[k] += d.get(k, 0)
        return total


_stats_instance: EmailWeeklyStats = None


def get_email_weekly_stats() -> EmailWeeklyStats:
    global _stats_instance
    if _stats_instance is None:
        _stats_instance = EmailWeeklyStats()
    return _stats_instance
