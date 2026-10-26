"""Отпечаток устройства для HELLO (opcode 6) — точные поля сняты с живого кадра.

HELLO payload = { userAgent: {...}, deviceId: "<uuid>" }. deviceId делать
ПОСТОЯННЫМ (тот же, что в браузере, — берётся из localStorage `__oneme_device_id`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class Device:
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_type: str = "WEB"
    push_device_type: str = "WEBPUSH"
    locale: str = "ru"
    device_locale: str = "ru"
    os_version: str = "Linux"
    device_name: str = "Chrome"
    header_user_agent: str = DEFAULT_UA
    is_pwa: bool = False
    app_version: str = "26.8.10"
    screen: str = "1000x1280 1.0x"
    timezone: str = "Europe/Moscow"

    def user_agent(self) -> dict:
        """Вложенный объект userAgent (ключи как у веб-клиента)."""
        return {
            "deviceType": self.device_type,
            "pushDeviceType": self.push_device_type,
            "locale": self.locale,
            "deviceLocale": self.device_locale,
            "osVersion": self.os_version,
            "deviceName": self.device_name,
            "headerUserAgent": self.header_user_agent,
            "isPwa": self.is_pwa,
            "appVersion": self.app_version,
            "screen": self.screen,
            "timezone": self.timezone,
        }

    def hello_payload(self) -> dict:
        """Полный payload HELLO: {userAgent, deviceId}."""
        return {"userAgent": self.user_agent(), "deviceId": self.device_id}

    @classmethod
    def load_or_create(cls, path) -> "Device":
        """Постоянный отпечаток из JSON-файла (создаёт, если нет)."""
        import dataclasses
        import json
        import pathlib

        p = pathlib.Path(path)
        if p.exists():
            return cls(**json.loads(p.read_text()))
        dev = cls()
        p.write_text(json.dumps(dataclasses.asdict(dev), ensure_ascii=False, indent=2))
        return dev
