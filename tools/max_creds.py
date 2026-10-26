#!/usr/bin/env python3
"""Одноразовый вход в MAX и автоматическое обновление кредов.

Логин (телефон → капча → SMS) автоматизировать нельзя — капча. Но после
одного ручного входа в отдельном persistent-профиле Playwright скрипт сам
читает token/deviceId/viewerId из localStorage web.max.ru и обновляет
creds.json + device.json.

    python tools/max_creds.py login   # первый раз: откроется браузер
    python tools/max_creds.py get     # дальше headless, без логина

Файлы по умолчанию:
    ~/.local/share/maxmessenger/browser      persistent-профиль Playwright
    ~/.config/maxmessenger/creds.json        token / deviceId / viewerId (0600)
    ~/.config/maxmessenger/device.json       отпечаток для maxmessenger.Device
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_URL = "https://web.max.ru/"


def _path(value: str) -> Path:
    return Path(value).expanduser()


DEFAULT_PROFILE = _path(
    os.environ.get("MAXMESSENGER_BROWSER_DIR", "~/.local/share/maxmessenger/browser")
)
DEFAULT_CREDS = _path(
    os.environ.get("MAXMESSENGER_CREDS", "~/.config/maxmessenger/creds.json")
)
DEFAULT_DEVICE = _path(
    os.environ.get("MAXMESSENGER_DEVICE", "~/.config/maxmessenger/device.json")
)

READ_JS = r"""
() => {
  const raw = localStorage.getItem('__oneme_auth');
  let a = {};
  try { a = JSON.parse(raw || '{}'); } catch (e) {}
  const token = a.token || a.accessToken || a.access_token || null;
  const viewerId = a.viewerId || a.viewer_id || a.userId || a.user_id || null;
  const dpr = window.devicePixelRatio || 1;
  return {
    token,
    viewerId,
    deviceId: localStorage.getItem('__oneme_device_id'),
    userAgent: navigator.userAgent,
    screen: `${window.screen.width}x${window.screen.height} ${dpr}x`,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
    locale: (navigator.language || 'ru').split('-')[0],
    href: location.href,
  };
}
"""


def mask(token: str | None) -> str:
    if not token:
        return "—"
    if len(token) <= 12:
        return token[:4] + "…"
    return token[:8] + "…" + token[-4:]


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def creds_from(data: dict) -> dict:
    return {
        "token": data["token"],
        "deviceId": data["deviceId"],
        "viewerId": data.get("viewerId"),
        "userAgent": data.get("userAgent"),
        "screen": data.get("screen"),
        "timezone": data.get("timezone"),
        "locale": data.get("locale"),
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def save(creds: dict, creds_path: Path, device_path: Path) -> None:
    # creds сохраняем первыми: даже если maxmessenger/device.json не обновится,
    # токен уже не потеряется.
    write_private_json(creds_path, creds)
    try:
        from maxmessenger.device import Device
    except Exception as exc:  # noqa: BLE001
        print(f"creds сохранены, device.json не обновлён: {exc}", file=sys.stderr)
        return

    if device_path.exists():
        try:
            dev = Device(**json.loads(device_path.read_text()))
        except Exception:
            dev = Device()
    else:
        dev = Device()
    dev.device_id = creds["deviceId"]
    if creds.get("userAgent"):
        dev.header_user_agent = creds["userAgent"]
    if creds.get("screen"):
        dev.screen = creds["screen"]
    if creds.get("timezone"):
        dev.timezone = creds["timezone"]
    if creds.get("locale"):
        dev.locale = creds["locale"]
        dev.device_locale = creds["locale"]
    write_private_json(device_path, dataclasses.asdict(dev))


def read_page(page) -> dict:
    if page.is_closed():
        return {}
    try:
        return page.evaluate(READ_JS)
    except Exception as exc:  # noqa: BLE001
        print(f"не удалось прочитать localStorage: {exc}", file=sys.stderr)
        return {}


def launch(playwright, headless: bool, profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport=None if headless else {"width": 1280, "height": 860},
        args=["--no-first-run", "--no-default-browser-check"],
    )


def cmd_login(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            context = launch(p, headless=False, profile_dir=args.profile_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"не удалось запустить браузер: {exc}", file=sys.stderr)
            print(
                "запускай из графической сессии (нужны DISPLAY/WAYLAND_DISPLAY)",
                file=sys.stderr,
            )
            return 1

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(MAX_URL, wait_until="domcontentloaded", timeout=60_000)
            print("Войди в MAX в открытом окне (телефон → капча → SMS).")
            print(f"Жду до {args.timeout} сек...")

            deadline = time.time() + args.timeout
            last: dict = {}
            while time.time() < deadline:
                if page.is_closed():
                    print("окно браузера закрыто", file=sys.stderr)
                    break
                last = read_page(page)
                if last.get("token") and last.get("deviceId"):
                    creds = creds_from(last)
                    save(creds, args.creds, args.device)
                    print(f"готово: {args.creds}")
                    print(
                        f"token: {mask(creds['token'])}  deviceId: {creds['deviceId']}"
                    )
                    return 0
                page.wait_for_timeout(2000)
            print(
                "токен так и не появился в localStorage. Залогинился?", file=sys.stderr
            )
            if last.get("href"):
                print(f"последний URL: {last['href']}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001
            print(f"ошибка входа: {exc}", file=sys.stderr)
            return 1
        finally:
            context.close()


def cmd_get(args) -> int:
    from playwright.sync_api import sync_playwright

    if not args.profile_dir.exists():
        print(
            f"нет профиля {args.profile_dir}; сначала: python tools/max_creds.py login",
            file=sys.stderr,
        )
        return 2

    with sync_playwright() as p:
        try:
            context = launch(p, headless=not args.headed, profile_dir=args.profile_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"не удалось запустить браузер: {exc}", file=sys.stderr)
            print(
                "если профиль занят — закрой старый браузер или попробуй --headed",
                file=sys.stderr,
            )
            return 1

        data: dict = {}
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(MAX_URL, wait_until="domcontentloaded", timeout=60_000)
            data = read_page(page)
        except Exception as exc:  # noqa: BLE001
            print(f"не удалось открыть MAX: {exc}", file=sys.stderr)
            return 1
        finally:
            context.close()

    if not data.get("token") or not data.get("deviceId"):
        print(
            "в профиле нет __oneme_auth / __oneme_device_id — нужен login",
            file=sys.stderr,
        )
        return 2

    creds = creds_from(data)
    save(creds, args.creds, args.device)
    print(f"обновлено: {args.creds}")
    print(f"token: {mask(creds['token'])}  deviceId: {creds['deviceId']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--profile-dir", type=_path, default=DEFAULT_PROFILE)
    ap.add_argument("--creds", type=_path, default=DEFAULT_CREDS)
    ap.add_argument("--device", type=_path, default=DEFAULT_DEVICE)

    sub = ap.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser(
        "login", help="открыть браузер и сохранить креды после ручного входа"
    )
    login.add_argument(
        "--timeout", type=int, default=600, help="сколько ждать логин, сек (600)"
    )
    login.set_defaults(func=cmd_login)

    get = sub.add_parser(
        "get", help="забрать/обновить креды из уже залогиненного persistent-профиля"
    )
    get.add_argument(
        "--headed",
        action="store_true",
        help="показать окно браузера (по умолчанию headless)",
    )
    get.set_defaults(func=cmd_get)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
