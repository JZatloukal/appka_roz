from __future__ import annotations

import csv
import hashlib
import json
import os
import queue
import random
import secrets
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Any
from flask import (
    Flask,
    Response,
    abort,
    has_request_context,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None
    Jsonb = None

app = Flask(__name__)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_redirect_target(target: str | None, fallback: str) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


IS_PRODUCTION = (
    os.environ.get("APP_ENV", "").strip().lower() == "production"
    or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
)

app.secret_key = os.environ.get("SECRET_KEY", "dev-change-this-secret-key")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION),
    SEND_FILE_MAX_AGE_DEFAULT=3600,
)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


@app.before_request
def protect_post_requests():
    if request.method != "POST":
        return None

    expected = session.get("_csrf_token")
    submitted = request.form.get("_csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        log_event("csrf_failed", {"endpoint": request.endpoint})
        abort(403)

    return None

DATA_FILE = Path(__file__).with_name("sales_data.json")
ACHIEVEMENTS_FILE = Path(__file__).with_name("achievements_data.json")
CHALLENGES_FILE = Path(__file__).with_name("challenges_data.json")
REWARDS_FILE = Path(__file__).with_name("rewards_data.json")
APPEARANCE_FILE = Path(__file__).with_name("appearance_data.json")
XP_FILE = Path(__file__).with_name("xp_data.json")
ANALYTICS_FILE = Path(__file__).with_name("analytics_events.json")
VOICE_AUDIO_DIR = Path(__file__).with_name("static") / "audio" / "voice"
VOICE_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}
VOICE_MAX_PLAYS_PER_SALE = 2
VOICE_SALES_REQUIRED = 3
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

ACHIEVEMENT_LABELS = {
    "first_sale": "První prodej",
    "five_in_day": "Pět prodejů za den",
    "quick_pair": "Rychlá dvojice",
    "personal_record": "Nové osobní maximum",
    "hundred_sales": "100. zaznamenaný prodej",
}

ACHIEVEMENT_CATALOG = {
    "first_sale": {
        "label": "První krok",
        "mark": "1",
        "description": "První zaznamenaný prodej otevřel tvoji sbírku momentů.",
        "xp": 50,
        "theme": "rose",
    },
    "five_in_day": {
        "label": "Silný den",
        "mark": "5",
        "description": "Pět prodejů během jediného dne.",
        "xp": 150,
        "theme": "sunset",
    },
    "quick_pair": {
        "label": "Rychlá dvojice",
        "mark": "2×",
        "description": "Dva prodeje zaznamenané během deseti minut.",
        "xp": 100,
        "theme": "violet",
    },
    "personal_record": {
        "label": "Osobní rekord",
        "mark": "↑",
        "description": "Nový nejlepší počet prodejů za jeden den.",
        "xp": 200,
        "theme": "ruby",
    },
    "hundred_sales": {
        "label": "Stovka",
        "mark": "100",
        "description": "Sto zaznamenaných prodejů. Výjimečný dlouhodobý výsledek.",
        "xp": 500,
        "theme": "gold",
    },
}

XP_LEVEL_ROADMAP = [
    {"level": 1, "xp": 0, "title": "Základní přehled", "description": "Výchozí funkce aplikace.", "kind": "base"},
    {"level": 2, "xp": 300, "title": "Soft Blush", "description": "Světlý růžový motiv aplikace.", "kind": "theme"},
    {"level": 3, "xp": 750, "title": "Historie XP", "description": "Přehled jednotlivých přírůstků XP.", "kind": "feature"},
    {"level": 4, "xp": 1300, "title": "Trend výkonu", "description": "Porovnání týdnů a dlouhodobě nejsilnější pracovní den.", "kind": "feature"},
    {"level": 5, "xp": 2000, "title": "Měsíční rekapitulace", "description": "Porovnání měsíců, aktivní dny a export výsledků.", "kind": "feature"},
    {"level": 6, "xp": 2850, "title": "Osobní rekordy", "description": "Nejvyšší prodej, nejsilnější den a nejdelší série.", "kind": "feature"},
    {"level": 7, "xp": 3850, "title": "Lavender Mist", "description": "Světlý levandulovo-krémový motiv aplikace.", "kind": "theme"},
    {"level": 8, "xp": 5000, "title": "Co už jsi dokázala", "description": "Dlouhodobý souhrn prodejů, výzev, milníků a měsíců.", "kind": "feature"},
    {"level": 9, "xp": 6300, "title": "Finální výzva", "description": "20 prodejů a čtyři aktivní dny během pracovního týdne.", "kind": "challenge"},
    {"level": 10, "xp": 7800, "title": "Velká pochvala", "description": "Samostatná závěrečná stránka věnovaná celé cestě.", "kind": "message"},
]

XP_SOURCE_OVERVIEW = [
    {"value": "+10 XP", "title": "Každý prodej", "description": "Stejná hodnota bez ohledu na částku."},
    {"value": "+25–50 XP", "title": "Denní výzva", "description": "Bonus podle náročnosti cíle."},
    {"value": "+100–150 XP", "title": "Týdenní výzva", "description": "Odměna za dlouhodobější pravidelnost."},
    {"value": "+50–500 XP", "title": "Nový milník", "description": "Jednorázový bonus při prvním dosažení."},
]

DAILY_CHALLENGE_CATALOG = [
    {"id": "first_sale", "title": "První prodej dne", "description": "Začni den prvním záznamem.", "icon": "sale", "kind": "count", "target": 1, "xp": 25},
    {"id": "three_sales", "title": "Tři prodeje za den", "description": "Tři soustředěné kroky během dne.", "icon": "sale", "kind": "count", "target": 3, "xp": 35},
    {"id": "five_sales", "title": "Pět prodejů za den", "description": "Výrazný den postavený na pravidelnosti.", "icon": "sales5", "kind": "count", "target": 5, "xp": 50},
    {"id": "morning_sale", "title": "Ranní tempo", "description": "Zapiš prodej před 10:00.", "icon": "morning", "kind": "before_hour", "target": 1, "hour": 10, "xp": 30},
    {"id": "late_sale", "title": "Silný závěr", "description": "Zapiš prodej po 16:00.", "icon": "late", "kind": "after_hour", "target": 1, "hour": 16, "xp": 30},
    {"id": "quick_hour", "title": "Dvojice během hodiny", "description": "Dva prodeje v rozmezí 60 minut.", "icon": "quick", "kind": "rolling_hour", "target": 2, "xp": 45},
    {"id": "daily_revenue", "title": "Denní obrat 1 000 Kč", "description": "Nasbírej za den hodnotu 1 000 Kč.", "icon": "revenue", "kind": "revenue", "target": 1000, "xp": 50},
    {"id": "average_300", "title": "Průměr alespoň 300 Kč", "description": "Udrž dnešní průměrnou hodnotu prodeje.", "icon": "average", "kind": "average", "target": 300, "xp": 40},
    {"id": "three_clients", "title": "Tři různí klienti", "description": "Zaznamenej prodeje třem klientům.", "icon": "clients", "kind": "unique_clients", "target": 3, "xp": 40},
    {"id": "three_notes", "title": "Tři užitečné poznámky", "description": "Přidej poznámku ke třem prodejům.", "icon": "notes", "kind": "notes", "target": 3, "xp": 30},
    {"id": "bonus_window", "title": "Odpolední bonus", "description": "Tři prodeje mezi 14:00–17:00.", "icon": "clock", "kind": "hour_window", "target": 3, "start": 14, "end": 17, "xp": 50},
]

WEEKLY_CHALLENGE_CATALOG = [
    {"id": "ten_sales", "title": "10 prodejů za týden", "description": "Tempo, které vytváří skutečný posun.", "icon": "sale", "kind": "count", "target": 10, "xp": 100},
    {"id": "twenty_sales", "title": "20 prodejů za týden", "description": "Ambiciózní týdenní hranice.", "icon": "sales5", "kind": "count", "target": 20, "xp": 150},
    {"id": "four_days", "title": "Čtyři aktivní dny", "description": "Prodej alespoň ve čtyřech dnech týdne.", "icon": "active_days", "kind": "active_days", "target": 4, "xp": 120},
    {"id": "weekly_revenue", "title": "Týdenní obrat 5 000 Kč", "description": "Dosáhni za týden hodnoty 5 000 Kč.", "icon": "revenue", "kind": "revenue", "target": 5000, "xp": 150},
    {"id": "beat_last_week", "title": "Překonej minulý týden", "description": "Zaznamenej více prodejů než minulý týden.", "icon": "record", "kind": "beat_previous", "target": 1, "xp": 130},
    {"id": "three_day_streak", "title": "Třídenní série", "description": "Prodej ve třech po sobě jdoucích dnech.", "icon": "streak", "kind": "period_streak", "target": 3, "xp": 120},
    {"id": "five_one_day", "title": "Jeden mimořádný den", "description": "Pět prodejů během jediného dne.", "icon": "best_day", "kind": "best_day_count", "target": 5, "xp": 140},
    {"id": "weekly_average", "title": "Průměr 350 Kč", "description": "Udrž týdenní průměrnou hodnotu prodeje.", "icon": "average", "kind": "average", "target": 350, "xp": 110},
]

REWARD_CATALOG = [
    {
        "id": "drink",
        "target": 30,
        "title": "Oblíbený nápoj",
        "description": "Vanilkové cappuccino nebo jiný oblíbený nápoj do práce či domů.",
        "icon": "drink",
    },
    {
        "id": "detective",
        "target": 90,
        "title": "Detektivní večer",
        "description": "Večeře a nový případ od Detektivo.cz, najdeme spolu vraha?",
        "icon": "case",
    },
    {
        "id": "cinema",
        "target": 150,
        "title": "Kino a nachos",
        "description": "Pozvání do kina na film podle vlastního výběru, nachosky a cola jsou samozřejmostí.",
        "icon": "ticket",
    },
    {
        "id": "book",
        "target": 210,
        "title": "Knížka podle výběru",
        "description": "Jedna knížka z knihkupectví. Červená knihovna je povolena.",
        "icon": "book",
    },
    {
        "id": "mystery",
        "target": 280,
        "title": "Mystery box",
        "description": "Obsah sestavený na míru podle mých znalostí tebe a tvých preferencí.",
        "icon": "box",
    },
    {
        "id": "cosmetics",
        "target": 360,
        "title": "Kosmetika podle výběru",
        "description": "Kosmetika podle vlastního výběru v hodnotě do 2 000 Kč.",
        "icon": "spark",
    },
]

# Lightweight JSON storage for the first usable prototype.
SALES: list[dict[str, Any]] = []

DEMO_USER = {
    "username": os.environ.get("APP_USERNAME", "local-user"),
    "password": os.environ.get("APP_PASSWORD", "local-password-change-me"),
    "display_name": os.environ.get("APP_DISPLAY_NAME", "Týnuš"),
}


def login_required(view=None, *, hide: bool = False):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if "user" not in session:
                if hide:
                    abort(404)
                return redirect(url_for("login", next=request.path))
            if request.method == "GET" and not hide and request.endpoint not in {"logout", "download_monthly_report"}:
                log_event("page_view", {"endpoint": request.endpoint})
            return func(*args, **kwargs)

        return wrapped

    if view is None:
        return decorator
    return decorator(view)


def default_error_action() -> tuple[str, str]:
    if "user" in session:
        return url_for("dashboard"), "Zpět na Přehled"
    return url_for("login"), "Přejít na přihlášení"


def db_enabled() -> bool:
    return bool(DATABASE_URL and psycopg is not None)


def db_unavailable_reason() -> str | None:
    if not DATABASE_URL:
        return "DATABASE_URL is not set"
    if psycopg is None:
        return "psycopg is not installed"
    return None


_db_lock = threading.RLock()
_db_conn = None
_db_last_used = 0.0
DB_PING_AFTER_SECONDS = 60


class _SharedDbConnection:
    # Nove TLS pripojeni k Neonu stoji ~1 s, proto se jedno pripojeni sdili pro cely proces.
    # Chova se jako `with psycopg.connect(...)`: commit pri uspechu, rollback pri vyjimce,
    # jen se pripojeni na konci bloku nezavira.

    def __enter__(self):
        global _db_conn, _db_last_used
        _db_lock.acquire()
        try:
            if _db_conn is not None and not _db_conn.closed:
                if time.monotonic() - _db_last_used > DB_PING_AFTER_SECONDS:
                    try:
                        _db_conn.execute("select 1")
                    except Exception:
                        try:
                            _db_conn.close()
                        except Exception:
                            pass
                        _db_conn = None
            if _db_conn is None or _db_conn.closed:
                _db_conn = psycopg.connect(DATABASE_URL)
            _db_last_used = time.monotonic()
            return _db_conn
        except BaseException:
            _db_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        global _db_conn
        try:
            if _db_conn is not None and not _db_conn.closed:
                if exc_type is None:
                    _db_conn.commit()
                else:
                    _db_conn.rollback()
        except Exception:
            try:
                _db_conn.close()
            except Exception:
                pass
            _db_conn = None
        finally:
            _db_lock.release()
        return False


def db_connect():
    if not db_enabled():
        raise RuntimeError(db_unavailable_reason() or "Database is not enabled")
    return _SharedDbConnection()


def as_plain_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def parse_datetime_value(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback or datetime.now()
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def jsonb(value: Any) -> Any:
    if Jsonb is None:
        return value
    return Jsonb(as_plain_json(value))


def load_sales() -> list[dict[str, Any]]:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select id, customer, amount, note, created_at
                        from sales
                        order by created_at
                        """
                    )
                    return [
                        {
                            "id": int(row[0]),
                            "customer": str(row[1]),
                            "amount": int(row[2]),
                            "note": str(row[3] or ""),
                            "created_at": parse_datetime_value(row[4]),
                        }
                        for row in cur.fetchall()
                    ]
        except Exception:
            app.logger.exception("Database load_sales failed; falling back to JSON.")

    if not DATA_FILE.exists():
        return []

    try:
        raw_sales = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    loaded_sales: list[dict[str, Any]] = []
    for index, sale in enumerate(raw_sales, start=1):
        loaded_sales.append(
            {
                "id": int(sale.get("id", index)),
                "customer": str(sale.get("customer", "Neznámý klient")),
                "amount": int(sale.get("amount", 0)),
                "note": str(sale.get("note", "")),
                "created_at": parse_datetime_value(sale.get("created_at")),
            }
        )

    return loaded_sales


def write_json(path: Path, data: Any) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def hash_for_analytics(value: str | None) -> str | None:
    if not value:
        return None
    secret = app.secret_key or "analytics"
    return hashlib.sha256(f"{secret}:{value}".encode("utf-8")).hexdigest()[:24]


def read_analytics_events() -> list[dict[str, Any]]:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select id, event_type, app_user, path, method, ip_hash, user_agent, metadata, created_at
                        from analytics_events
                        order by id
                        """
                    )
                    return [
                        {
                            "id": int(row[0]),
                            "event_type": row[1],
                            "user": row[2],
                            "path": row[3],
                            "method": row[4],
                            "ip_hash": row[5],
                            "user_agent": row[6],
                            "metadata": row[7] or {},
                            "created_at": parse_datetime_value(row[8]).isoformat(),
                        }
                        for row in cur.fetchall()
                    ]
        except Exception:
            app.logger.exception("Database read_analytics_events failed; falling back to JSON.")

    if not ANALYTICS_FILE.exists():
        return []
    try:
        data = json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_analytics_event(event: dict[str, Any]) -> None:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into analytics_events
                            (event_type, app_user, path, method, ip_hash, user_agent, metadata, created_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event["event_type"],
                            event["user"],
                            event["path"],
                            event["method"],
                            event["ip_hash"],
                            event["user_agent"],
                            jsonb(event["metadata"]),
                            parse_datetime_value(event["created_at"]),
                        ),
                    )
            return
        except Exception:
            app.logger.exception("Database log_event failed; falling back to JSON.")

    events = read_analytics_events()
    event["id"] = (events[-1]["id"] + 1) if events and isinstance(events[-1].get("id"), int) else 1
    events.append(event)
    write_json(ANALYTICS_FILE, events)


_analytics_queue: queue.Queue = queue.Queue()


def _analytics_worker() -> None:
    while True:
        event = _analytics_queue.get()
        try:
            _write_analytics_event(event)
        except Exception:
            app.logger.exception("Unable to write analytics event: %s", event.get("event_type"))
        finally:
            _analytics_queue.task_done()


threading.Thread(target=_analytics_worker, daemon=True, name="analytics-writer").start()


def log_event(
    event_type: str,
    metadata: dict[str, Any] | None = None,
    *,
    user: str | None = None,
) -> None:
    # zapis probiha asynchronne ve vlakne, aby na analytiku necekal zadny request;
    # data z requestu se musi vycist tady (na pozadi uz request context neexistuje)
    try:
        metadata_payload = as_plain_json(metadata or {})
        event: dict[str, Any] = {
            "event_type": event_type,
            "created_at": datetime.now().isoformat(),
            "user": user,
            "path": None,
            "method": None,
            "ip_hash": None,
            "user_agent": None,
            "metadata": metadata_payload,
        }

        if has_request_context():
            forwarded_for = request.headers.get("X-Forwarded-For", "")
            client_ip = forwarded_for.split(",")[0].strip() or request.remote_addr
            event.update(
                {
                    "user": user or session.get("user"),
                    "path": request.path,
                    "method": request.method,
                    "ip_hash": hash_for_analytics(client_ip),
                    "user_agent": request.headers.get("User-Agent", "")[:240],
                }
            )

        _analytics_queue.put(event)
    except Exception:
        app.logger.exception("Unable to queue analytics event: %s", event_type)


def save_sales() -> None:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    sale_ids = [int(sale["id"]) for sale in SALES]
                    if SALES:
                        # executemany posila radky v jedne davce - N dotazu po siti by bylo N x latence
                        cur.executemany(
                            """
                            insert into sales (id, customer, amount, note, created_at)
                            values (%s, %s, %s, %s, %s)
                            on conflict (id) do update set
                                customer = excluded.customer,
                                amount = excluded.amount,
                                note = excluded.note,
                                created_at = excluded.created_at
                            """,
                            [
                                (
                                    int(sale["id"]),
                                    sale["customer"],
                                    int(sale["amount"]),
                                    sale["note"],
                                    sale["created_at"],
                                )
                                for sale in SALES
                            ],
                        )
                    if sale_ids:
                        cur.execute("delete from sales where not (id = any(%s))", (sale_ids,))
                    else:
                        cur.execute("delete from sales")
            return
        except Exception:
            app.logger.exception("Database save_sales failed; falling back to JSON.")

    write_json(
        DATA_FILE,
        [
            {
                "id": sale["id"],
                "customer": sale["customer"],
                "amount": sale["amount"],
                "note": sale["note"],
                "created_at": sale["created_at"].isoformat(),
            }
            for sale in SALES
        ],
    )


def empty_achievements() -> dict[str, dict[str, Any] | None]:
    return {key: None for key in ACHIEVEMENT_LABELS}


def load_achievements() -> dict[str, dict[str, Any] | None]:
    if db_enabled():
        try:
            achievements = empty_achievements()
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select achievement_key, unlocked_at, value, minutes, reflection, metadata
                        from achievements
                        """
                    )
                    for key, unlocked_at, value, minutes, reflection, metadata in cur.fetchall():
                        if key not in achievements:
                            continue
                        if unlocked_at is None:
                            achievements[key] = None
                            continue
                        record: dict[str, Any] = dict(metadata or {})
                        record["unlocked_at"] = parse_datetime_value(unlocked_at).isoformat()
                        if value is not None:
                            record["value"] = int(value)
                        if minutes is not None:
                            record["minutes"] = float(minutes)
                        if reflection:
                            record["reflection"] = reflection
                        achievements[key] = record
            return achievements
        except Exception:
            app.logger.exception("Database load_achievements failed; falling back to JSON.")

    if not ACHIEVEMENTS_FILE.exists():
        return empty_achievements()
    try:
        loaded = json.loads(ACHIEVEMENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_achievements()
    achievements = empty_achievements()
    if isinstance(loaded, dict):
        for key in achievements:
            value = loaded.get(key)
            achievements[key] = value if isinstance(value, dict) else None
    return achievements


def save_achievements() -> None:
    if db_enabled():
        try:
            rows = []
            for key, achievement in ACHIEVEMENTS.items():
                if achievement is None:
                    rows.append((key, None, None, None, None, jsonb({})))
                    continue
                metadata = {
                    k: v
                    for k, v in achievement.items()
                    if k not in {"unlocked_at", "value", "minutes", "reflection"}
                }
                rows.append(
                    (
                        key,
                        parse_datetime_value(achievement.get("unlocked_at")) if achievement.get("unlocked_at") else None,
                        achievement.get("value"),
                        achievement.get("minutes"),
                        achievement.get("reflection"),
                        jsonb(metadata),
                    )
                )
            with db_connect() as conn:
                with conn.cursor() as cur:
                    if rows:
                        cur.executemany(
                            """
                            insert into achievements
                                (achievement_key, unlocked_at, value, minutes, reflection, metadata, updated_at)
                            values (%s, %s, %s, %s, %s, %s, now())
                            on conflict (achievement_key) do update set
                                unlocked_at = excluded.unlocked_at,
                                value = excluded.value,
                                minutes = excluded.minutes,
                                reflection = excluded.reflection,
                                metadata = excluded.metadata,
                                updated_at = now()
                            """,
                            rows,
                        )
            return
        except Exception:
            app.logger.exception("Database save_achievements failed; falling back to JSON.")

    write_json(ACHIEVEMENTS_FILE, ACHIEVEMENTS)


def load_challenges() -> dict[str, dict[str, Any]]:
    if db_enabled():
        try:
            completed: dict[str, Any] = {}
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select completion_key, challenge_id, title, period, completed_at, xp, metadata
                        from completed_challenges
                        """
                    )
                    for completion_key, challenge_id, title, period, completed_at, xp, metadata in cur.fetchall():
                        record = dict(metadata or {})
                        record.update(
                            {
                                "completion_key": completion_key,
                                "challenge_id": challenge_id,
                                "title": title,
                                "period": period,
                                "completed_at": parse_datetime_value(completed_at).isoformat(),
                                "xp": int(xp or 0),
                            }
                        )
                        completed[completion_key] = record
            return {"completed": completed}
        except Exception:
            app.logger.exception("Database load_challenges failed; falling back to JSON.")

    if not CHALLENGES_FILE.exists():
        return {"completed": {}}
    try:
        loaded = json.loads(CHALLENGES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"completed": {}}
    completed = loaded.get("completed", {}) if isinstance(loaded, dict) else {}
    return {"completed": completed if isinstance(completed, dict) else {}}


def save_challenges() -> None:
    if db_enabled():
        try:
            rows = []
            for key, challenge in CHALLENGES.get("completed", {}).items():
                metadata = {
                    k: v
                    for k, v in challenge.items()
                    if k not in {"completion_key", "challenge_id", "title", "period", "completed_at", "xp"}
                }
                rows.append(
                    (
                        challenge.get("completion_key", key),
                        challenge.get("challenge_id", ""),
                        challenge.get("title", ""),
                        challenge.get("period", "daily"),
                        parse_datetime_value(challenge.get("completed_at")),
                        int(challenge.get("xp", 0)),
                        jsonb(metadata),
                    )
                )
            with db_connect() as conn:
                with conn.cursor() as cur:
                    keys = list(CHALLENGES.get("completed", {}).keys())
                    if rows:
                        cur.executemany(
                            """
                            insert into completed_challenges
                                (completion_key, challenge_id, title, period, completed_at, xp, metadata)
                            values (%s, %s, %s, %s, %s, %s, %s)
                            on conflict (completion_key) do update set
                                challenge_id = excluded.challenge_id,
                                title = excluded.title,
                                period = excluded.period,
                                completed_at = excluded.completed_at,
                                xp = excluded.xp,
                                metadata = excluded.metadata
                            """,
                            rows,
                        )
                    if keys:
                        cur.execute("delete from completed_challenges where not (completion_key = any(%s))", (keys,))
                    else:
                        cur.execute("delete from completed_challenges")
            return
        except Exception:
            app.logger.exception("Database save_challenges failed; falling back to JSON.")

    write_json(CHALLENGES_FILE, CHALLENGES)


def load_rewards() -> dict[str, dict[str, Any]]:
    if db_enabled():
        try:
            rewards: dict[str, Any] = {}
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select reward_id, status, unlocked_at, requested_at, delivered_at, email_sent_at, metadata
                        from rewards
                        """
                    )
                    for reward_id, status, unlocked_at, requested_at, delivered_at, email_sent_at, metadata in cur.fetchall():
                        record = dict(metadata or {})
                        record["status"] = status
                        if unlocked_at:
                            record["unlocked_at"] = parse_datetime_value(unlocked_at).isoformat()
                        if requested_at:
                            record["requested_at"] = parse_datetime_value(requested_at).isoformat()
                        if delivered_at:
                            record["delivered_at"] = parse_datetime_value(delivered_at).isoformat()
                        if email_sent_at:
                            record["email_sent_at"] = parse_datetime_value(email_sent_at).isoformat()
                        rewards[reward_id] = record
            return {"rewards": rewards}
        except Exception:
            app.logger.exception("Database load_rewards failed; falling back to JSON.")

    if not REWARDS_FILE.exists():
        return {"rewards": {}}
    try:
        loaded = json.loads(REWARDS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"rewards": {}}
    rewards = loaded.get("rewards", {}) if isinstance(loaded, dict) else {}
    return {"rewards": rewards if isinstance(rewards, dict) else {}}


def save_rewards() -> None:
    if db_enabled():
        try:
            rows = []
            for reward_id, reward in REWARDS.get("rewards", {}).items():
                metadata = {
                    k: v
                    for k, v in reward.items()
                    if k not in {"status", "unlocked_at", "requested_at", "delivered_at", "email_sent_at"}
                }
                rows.append(
                    (
                        reward_id,
                        reward.get("status", "locked"),
                        parse_datetime_value(reward.get("unlocked_at")) if reward.get("unlocked_at") else None,
                        parse_datetime_value(reward.get("requested_at")) if reward.get("requested_at") else None,
                        parse_datetime_value(reward.get("delivered_at")) if reward.get("delivered_at") else None,
                        parse_datetime_value(reward.get("email_sent_at")) if reward.get("email_sent_at") else None,
                        jsonb(metadata),
                    )
                )
            with db_connect() as conn:
                with conn.cursor() as cur:
                    ids = list(REWARDS.get("rewards", {}).keys())
                    if rows:
                        cur.executemany(
                            """
                            insert into rewards
                                (reward_id, status, unlocked_at, requested_at, delivered_at, email_sent_at, metadata, updated_at)
                            values (%s, %s, %s, %s, %s, %s, %s, now())
                            on conflict (reward_id) do update set
                                status = excluded.status,
                                unlocked_at = excluded.unlocked_at,
                                requested_at = excluded.requested_at,
                                delivered_at = excluded.delivered_at,
                                email_sent_at = excluded.email_sent_at,
                                metadata = excluded.metadata,
                                updated_at = now()
                            """,
                            rows,
                        )
                    if ids:
                        cur.execute("delete from rewards where not (reward_id = any(%s))", (ids,))
                    else:
                        cur.execute("delete from rewards")
            return
        except Exception:
            app.logger.exception("Database save_rewards failed; falling back to JSON.")

    write_json(REWARDS_FILE, REWARDS)


def load_appearance() -> dict[str, str]:
    default = {"theme": "rose"}
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("select theme from appearance where user_key = 'default'")
                    row = cur.fetchone()
                    if row:
                        theme = row[0]
                        if theme == "midnight-plum":
                            theme = "lavender-mist"
                        return {"theme": theme if theme in {"rose", "soft-blush", "lavender-mist"} else "rose"}
        except Exception:
            app.logger.exception("Database load_appearance failed; falling back to JSON.")

    if not APPEARANCE_FILE.exists():
        return default
    try:
        loaded = json.loads(APPEARANCE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    theme = loaded.get("theme") if isinstance(loaded, dict) else None
    if theme == "midnight-plum":
        theme = "lavender-mist"
    return {"theme": theme if theme in {"rose", "soft-blush", "lavender-mist"} else "rose"}


def save_appearance() -> None:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into appearance (user_key, theme, updated_at)
                        values ('default', %s, now())
                        on conflict (user_key) do update set
                            theme = excluded.theme,
                            updated_at = now()
                        """,
                        (APPEARANCE.get("theme", "rose"),),
                    )
            return
        except Exception:
            app.logger.exception("Database save_appearance failed; falling back to JSON.")

    write_json(APPEARANCE_FILE, APPEARANCE)


def load_xp_ledger() -> dict[str, list[dict[str, Any]]]:
    if db_enabled():
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select id, title, xp, category, created_at, metadata
                        from xp_entries
                        order by created_at, id
                        """
                    )
                    return {
                        "entries": [
                            {
                                **dict(metadata or {}),
                                "id": row_id,
                                "title": title,
                                "xp": int(xp or 0),
                                "category": category,
                                "created_at": parse_datetime_value(created_at).isoformat(),
                            }
                            for row_id, title, xp, category, created_at, metadata in cur.fetchall()
                        ]
                    }
        except Exception:
            app.logger.exception("Database load_xp_ledger failed; falling back to JSON.")

    if not XP_FILE.exists():
        return {"entries": []}
    try:
        loaded = json.loads(XP_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": []}
    entries = loaded.get("entries", []) if isinstance(loaded, dict) else []
    return {"entries": entries if isinstance(entries, list) else []}


def save_xp_ledger() -> None:
    if db_enabled():
        try:
            rows = []
            for entry in XP_LEDGER.get("entries", []):
                metadata = {
                    k: v
                    for k, v in entry.items()
                    if k not in {"id", "title", "xp", "category", "created_at"}
                }
                rows.append(
                    (
                        entry["id"],
                        entry.get("title", ""),
                        int(entry.get("xp", 0)),
                        entry.get("category", ""),
                        parse_datetime_value(entry.get("created_at")),
                        jsonb(metadata),
                    )
                )
            with db_connect() as conn:
                with conn.cursor() as cur:
                    ids = [entry["id"] for entry in XP_LEDGER.get("entries", []) if "id" in entry]
                    if rows:
                        cur.executemany(
                            """
                            insert into xp_entries (id, title, xp, category, created_at, metadata)
                            values (%s, %s, %s, %s, %s, %s)
                            on conflict (id) do update set
                                title = excluded.title,
                                xp = excluded.xp,
                                category = excluded.category,
                                created_at = excluded.created_at,
                                metadata = excluded.metadata
                            """,
                            rows,
                        )
                    if ids:
                        cur.execute("delete from xp_entries where not (id = any(%s))", (ids,))
                    else:
                        cur.execute("delete from xp_entries")
            return
        except Exception:
            app.logger.exception("Database save_xp_ledger failed; falling back to JSON.")

    write_json(XP_FILE, XP_LEDGER)


def get_achievement_bonus_xp() -> int:
    return sum(
        ACHIEVEMENT_CATALOG[key]["xp"]
        for key, achievement in ACHIEVEMENTS.items()
        if achievement is not None
    )


def get_achievement_views() -> list[dict[str, Any]]:
    views = []
    for key, catalog_item in ACHIEVEMENT_CATALOG.items():
        achievement = ACHIEVEMENTS[key]
        unlocked_at = None
        if achievement:
            try:
                unlocked_at = datetime.fromisoformat(achievement["unlocked_at"]).strftime(
                    "%d.%m.%Y"
                )
            except (KeyError, TypeError, ValueError):
                unlocked_at = "Datum není dostupné"
        views.append(
            {
                "key": key,
                **catalog_item,
                "unlocked": achievement is not None,
                "unlocked_at": unlocked_at,
                "reflection": achievement.get("reflection", "") if achievement else "",
                "value": achievement.get("value") if achievement else None,
            }
        )
    return views


def get_challenge_bonus_xp() -> int:
    total = 0
    for completion in CHALLENGES["completed"].values():
        raw_xp = int(completion.get("xp", 0))
        if completion.get("period") == "weekly":
            total += min(150, max(100, raw_xp))
        else:
            total += min(50, raw_xp)
    return total


def next_sale_id() -> int:
    if not SALES:
        return 1
    return max(sale["id"] for sale in SALES) + 1


def format_czk(amount: int) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def get_voice_clips() -> list[dict[str, str]]:
    if not VOICE_AUDIO_DIR.exists():
        return []

    clips = []
    for file_path in sorted(VOICE_AUDIO_DIR.iterdir(), key=lambda path: path.name.lower()):
        if not file_path.is_file() or file_path.suffix.lower() not in VOICE_AUDIO_EXTENSIONS:
            continue
        clips.append(
            {
                "filename": file_path.name,
            }
        )
    return clips


def prepare_next_voice_clip() -> None:
    clips = get_voice_clips()
    if not clips:
        session.pop("voice_clip_index", None)
        session.pop("voice_play_count", None)
        return

    previous_index = session.get("voice_last_clip_index")
    available_indexes = list(range(len(clips)))
    if isinstance(previous_index, int) and len(available_indexes) > 1:
        available_indexes = [index for index in available_indexes if index != previous_index]

    next_index = random.choice(available_indexes)
    session["voice_clip_index"] = next_index
    session["voice_last_clip_index"] = next_index
    session["voice_play_count"] = 0
    session["voice_unlocked"] = True


def register_sale_for_voice_unlock() -> None:
    if session.get("voice_unlocked"):
        return

    sales_since_unlock = int(session.get("voice_sales_since_unlock", 0)) + 1
    if sales_since_unlock >= VOICE_SALES_REQUIRED:
        prepare_next_voice_clip()
        session["voice_sales_since_unlock"] = 0
    else:
        session["voice_sales_since_unlock"] = sales_since_unlock
        session["voice_unlocked"] = False


def clear_voice_unlock_state() -> None:
    for key in ("voice_unlocked", "voice_clip_index", "voice_play_count"):
        session.pop(key, None)


def sanitize_voice_session(total_sales: int) -> None:
    if total_sales < VOICE_SALES_REQUIRED:
        clear_voice_unlock_state()
        session["voice_sales_since_unlock"] = min(
            total_sales,
            int(session.get("voice_sales_since_unlock", total_sales)),
        )


def get_current_voice_clip() -> dict[str, str] | None:
    clips = get_voice_clips()
    if not clips or not session.get("voice_unlocked"):
        return None
    if int(session.get("voice_play_count", 0)) >= VOICE_MAX_PLAYS_PER_SALE:
        return None
    return clips[int(session.get("voice_clip_index", 0)) % len(clips)]


def get_voice_plays_remaining() -> int:
    if not session.get("voice_unlocked"):
        return 0
    return max(0, VOICE_MAX_PLAYS_PER_SALE - int(session.get("voice_play_count", 0)))


def get_voice_unlock_progress() -> dict[str, int]:
    if session.get("voice_unlocked"):
        completed = VOICE_SALES_REQUIRED
    else:
        completed = min(
            VOICE_SALES_REQUIRED,
            max(0, int(session.get("voice_sales_since_unlock", 0))),
        )
    remaining = max(0, VOICE_SALES_REQUIRED - completed)
    return {
        "completed": completed,
        "required": VOICE_SALES_REQUIRED,
        "remaining": remaining,
        "percent": round((completed / VOICE_SALES_REQUIRED) * 100),
    }


def is_workday(day: Any | None = None) -> bool:
    checked_day = day or datetime.now().date()
    if isinstance(checked_day, datetime):
        checked_day = checked_day.date()
    return checked_day.weekday() < 5


def is_counted_workday(day: Any | None = None) -> bool:
    checked_day = day or datetime.now().date()
    checked_date = checked_day.date() if isinstance(checked_day, datetime) else checked_day
    return is_workday(checked_date)


def previous_workday(day: Any) -> Any:
    previous = day - timedelta(days=1)
    while not is_workday(previous):
        previous -= timedelta(days=1)
    return previous


def calculate_streak(sales: list[dict[str, Any]]) -> int:
    sale_dates = {
        sale["created_at"].date()
        for sale in sales
        if is_counted_workday(sale["created_at"])
    }
    if not sale_dates:
        return 0

    today = datetime.now().date()
    current_workday = today if is_counted_workday(today) else previous_workday(today + timedelta(days=1))
    if current_workday in sale_dates:
        current_date = current_workday
    elif previous_workday(current_workday) in sale_dates:
        current_date = previous_workday(current_workday)
    else:
        return 0

    streak = 0
    while current_date in sale_dates:
        streak += 1
        current_date = previous_workday(current_date)
    return streak


def get_today_sales() -> list[dict[str, Any]]:
    today = datetime.now().date()
    if not is_counted_workday(today):
        return []
    return [sale for sale in SALES if sale["created_at"].date() == today]


def get_week_sales() -> list[dict[str, Any]]:
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    return [
        sale
        for sale in SALES
        if week_start <= sale["created_at"].date() <= today
        and is_counted_workday(sale["created_at"])
    ]


def get_month_sales() -> list[dict[str, Any]]:
    now = datetime.now()
    return [
        sale
        for sale in SALES
        if sale["created_at"].year == now.year and sale["created_at"].month == now.month
        and is_counted_workday(sale["created_at"])
    ]


def get_total_revenue(sales: list[dict[str, Any]] | None = None) -> int:
    source = (
        [sale for sale in SALES if is_counted_workday(sale["created_at"])]
        if sales is None
        else sales
    )
    return sum(sale["amount"] for sale in source)


def get_average_sale() -> int:
    workday_sales = [sale for sale in SALES if is_counted_workday(sale["created_at"])]
    return round(get_total_revenue(workday_sales) / len(workday_sales)) if workday_sales else 0


def get_best_day() -> dict[str, Any] | None:
    workday_sales = [sale for sale in SALES if is_counted_workday(sale["created_at"])]
    if not workday_sales:
        return None
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for sale in workday_sales:
        grouped[sale["created_at"].date()].append(sale)
    day, sales = max(grouped.items(), key=lambda item: (len(item[1]), get_total_revenue(item[1])))
    return {"date": day, "count": len(sales), "revenue": get_total_revenue(sales)}


def get_best_hour() -> int | None:
    workday_sales = [sale for sale in SALES if is_counted_workday(sale["created_at"])]
    if not workday_sales:
        return None
    return Counter(sale["created_at"].hour for sale in workday_sales).most_common(1)[0][0]


def get_last_7_days_counts() -> list[dict[str, Any]]:
    today = datetime.now().date()
    counts = Counter(sale["created_at"].date() for sale in SALES)
    weekday_labels = ("Po", "Út", "St", "Čt", "Pá", "So", "Ne")
    week_start = today - timedelta(days=today.weekday())
    workdays = [week_start + timedelta(days=offset) for offset in range(5)]
    days = [
        {"date": day, "label": weekday_labels[day.weekday()], "count": counts[day]}
        for day in workdays
    ]
    maximum = max((day["count"] for day in days), default=0)
    for day in days:
        day["height"] = max(8, round(day["count"] / maximum * 100)) if maximum else 8
    return days


def get_fun_item_progress(total_revenue: int, price: int) -> dict[str, int]:
    remainder = total_revenue % price
    return {
        "count": total_revenue // price,
        "price": price,
        "remaining": price - remainder if remainder else price,
        "progress": round(remainder / price * 100),
    }


def get_long_term_goal_progress(total_revenue: int, price: int) -> dict[str, Any]:
    raw_progress = min(100, total_revenue / price * 100) if price else 0
    if raw_progress == 0:
        progress_label = "0 %"
    elif raw_progress < 1:
        progress_label = f"{raw_progress:.2f} %".replace(".", ",")
    else:
        progress_label = f"{raw_progress:.1f} %".replace(".", ",")
    return {
        "price": price,
        "current": min(total_revenue, price),
        "remaining": max(0, price - total_revenue),
        "progress": raw_progress,
        "visual_progress": max(1, raw_progress) if total_revenue else 0,
        "progress_label": progress_label,
        "complete": total_revenue >= price,
    }


def filter_sales(query: str, period: str) -> list[dict[str, Any]]:
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    filtered = [sale for sale in SALES if is_counted_workday(sale["created_at"])]
    if period == "today":
        filtered = [sale for sale in filtered if sale["created_at"].date() == today]
    elif period == "week":
        filtered = [sale for sale in filtered if sale["created_at"].date() >= week_start]
    elif period == "month":
        filtered = [
            sale
            for sale in filtered
            if sale["created_at"].year == now.year and sale["created_at"].month == now.month
        ]
    if query:
        needle = query.casefold()
        filtered = [
            sale
            for sale in filtered
            if needle in sale["customer"].casefold() or needle in sale["note"].casefold()
        ]
    return sorted(filtered, key=lambda sale: sale["created_at"], reverse=True)


def get_sales_summary() -> dict[str, Any]:
    workday_sales = [sale for sale in SALES if is_counted_workday(sale["created_at"])]
    today_sales = get_today_sales()
    week_sales = get_week_sales()
    month_sales = get_month_sales()
    return {
        "total_sales": len(workday_sales),
        "total_revenue": get_total_revenue(),
        "today_items": today_sales,
        "today_sales": len(today_sales),
        "today_revenue": get_total_revenue(today_sales),
        "week_items": week_sales,
        "week_sales": len(week_sales),
        "week_revenue": get_total_revenue(week_sales),
        "month_items": month_sales,
        "month_sales": len(month_sales),
        "month_revenue": get_total_revenue(month_sales),
    }


def percentage_change(current: int, previous: int) -> int | None:
    if previous == 0:
        return None if current == 0 else 100
    return round((current - previous) / previous * 100)


def get_progress_analytics() -> dict[str, Any]:
    workday_sales = sorted(
        (sale for sale in SALES if is_counted_workday(sale["created_at"])),
        key=lambda sale: sale["created_at"],
    )
    today = datetime.now().date()
    this_week_start = today - timedelta(days=today.weekday())
    previous_week_start = this_week_start - timedelta(days=7)
    previous_week_end = this_week_start - timedelta(days=1)
    this_week = [sale for sale in workday_sales if sale["created_at"].date() >= this_week_start]
    previous_week = [
        sale for sale in workday_sales
        if previous_week_start <= sale["created_at"].date() <= previous_week_end
    ]

    current_month_start = today.replace(day=1)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    current_month = [
        sale for sale in workday_sales
        if sale["created_at"].date() >= current_month_start
    ]
    previous_month = [
        sale for sale in workday_sales
        if previous_month_start <= sale["created_at"].date() <= previous_month_end
    ]

    by_day: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    by_month: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    weekday_counts: Counter[int] = Counter()
    for sale in workday_sales:
        date = sale["created_at"].date()
        by_day[date].append(sale)
        by_month[(date.year, date.month)].append(sale)
        weekday_counts[date.weekday()] += 1

    weekday_names = [
        "Pondělí",
        "Úterý",
        "Středa",
        "Čtvrtek",
        "Pátek",
        "Sobota",
        "Neděle",
    ]
    strongest_weekday = (
        weekday_names[weekday_counts.most_common(1)[0][0]]
        if weekday_counts else None
    )

    month_weeks: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for sale in current_month:
        iso = sale["created_at"].isocalendar()
        month_weeks[(iso.year, iso.week)].append(sale)
    best_month_week = max(month_weeks.values(), key=len, default=[])

    highest_sale = max(workday_sales, key=lambda sale: sale["amount"], default=None)
    best_count_day = max(by_day.items(), key=lambda item: len(item[1]), default=None)
    best_revenue_day = max(
        by_day.items(),
        key=lambda item: sum(sale["amount"] for sale in item[1]),
        default=None,
    )

    longest_streak = 0
    longest_streak_end = None
    current_streak = 0
    previous_date = None
    for date in sorted(by_day):
        if previous_date is not None and previous_workday(date) == previous_date:
            current_streak += 1
        else:
            current_streak = 1
        if current_streak > longest_streak:
            longest_streak = current_streak
            longest_streak_end = date
        previous_date = date

    strongest_month = max(
        by_month.items(),
        key=lambda item: (len(item[1]), sum(sale["amount"] for sale in item[1])),
        default=None,
    )
    strongest_month_view = (
        {
            "label": f"{strongest_month[0][1]:02d}/{strongest_month[0][0]}",
            "sales": len(strongest_month[1]),
            "revenue": get_total_revenue(strongest_month[1]),
        }
        if strongest_month else None
    )
    active_month_days = len({sale["created_at"].date() for sale in current_month})
    current_month_revenue = get_total_revenue(current_month)
    previous_month_revenue = get_total_revenue(previous_month)

    return {
        "week": {
            "sales": len(this_week),
            "revenue": get_total_revenue(this_week),
            "previous_sales": len(previous_week),
            "previous_revenue": get_total_revenue(previous_week),
            "sales_change": percentage_change(len(this_week), len(previous_week)),
            "revenue_change": percentage_change(get_total_revenue(this_week), get_total_revenue(previous_week)),
            "strongest_weekday": strongest_weekday,
            "active_days": len({sale["created_at"].date() for sale in this_week}),
        },
        "month": {
            "sales": len(current_month),
            "revenue": current_month_revenue,
            "previous_sales": len(previous_month),
            "previous_revenue": previous_month_revenue,
            "sales_change": percentage_change(len(current_month), len(previous_month)),
            "revenue_change": percentage_change(current_month_revenue, previous_month_revenue),
            "active_days": active_month_days,
            "average_per_active_day": round(current_month_revenue / active_month_days) if active_month_days else 0,
            "best_week_sales": len(best_month_week),
            "best_week_revenue": get_total_revenue(best_month_week),
        },
        "records": {
            "highest_sale": highest_sale,
            "best_count_day": best_count_day,
            "best_revenue_day": best_revenue_day,
            "longest_streak": longest_streak,
            "longest_streak_end": longest_streak_end,
        },
        "journey": {
            "active_days": len(by_day),
            "total_sales": len(workday_sales),
            "total_revenue": get_total_revenue(workday_sales),
            "completed_challenges": len(CHALLENGES["completed"]),
            "unlocked_achievements": sum(value is not None for value in ACHIEVEMENTS.values()),
            "strongest_month": strongest_month_view,
            "average_sale": round(get_total_revenue(workday_sales) / len(workday_sales)) if workday_sales else 0,
        },
    }


SALES = load_sales()
ACHIEVEMENTS = load_achievements()
CHALLENGES = load_challenges()
REWARDS = load_rewards()
APPEARANCE = load_appearance()
XP_LEDGER = load_xp_ledger()


def get_legacy_xp_history() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    workday_sales = [
        sale for sale in SALES if is_counted_workday(sale["created_at"])
    ]
    for sale in workday_sales:
        entries.append(
            {
                "id": f"sale:{sale['id']}",
                "title": f"Prodej · {sale['customer']}",
                "xp": 10,
                "category": "Prodej",
                "created_at": sale["created_at"],
            }
        )

    for key, achievement in ACHIEVEMENTS.items():
        if not achievement or key not in ACHIEVEMENT_CATALOG:
            continue
        try:
            created_at = datetime.fromisoformat(achievement["unlocked_at"])
        except (KeyError, TypeError, ValueError):
            created_at = datetime.now()
        catalog = ACHIEVEMENT_CATALOG[key]
        entries.append(
            {
                "id": f"achievement:{key}",
                "title": catalog["label"],
                "xp": catalog["xp"],
                "category": "Milník",
                "created_at": created_at,
            }
        )

    for key, completion in CHALLENGES["completed"].items():
        try:
            created_at = datetime.fromisoformat(completion["completed_at"])
        except (KeyError, TypeError, ValueError):
            created_at = datetime.now()
        period = completion.get("period", "daily")
        raw_xp = int(completion.get("xp", 0))
        xp_value = min(raw_xp, 50) if period == "daily" else min(150, max(100, raw_xp))
        entries.append(
            {
                "id": f"challenge:{key}",
                "title": str(completion.get("title", "Splněná výzva")),
                "xp": xp_value,
                "category": "Denní výzva" if period == "daily" else "Týdenní výzva",
                "created_at": created_at,
            }
        )

    entries.sort(key=lambda entry: entry["created_at"], reverse=True)
    for entry in entries:
        entry["date_display"] = entry["created_at"].strftime("%d.%m.%Y · %H:%M")
    return entries


def migrate_xp_ledger() -> None:
    if XP_FILE.exists():
        return
    XP_LEDGER["entries"] = [
        {
            "id": entry["id"],
            "title": entry["title"],
            "xp": entry["xp"],
            "category": entry["category"],
            "created_at": entry["created_at"].isoformat(),
        }
        for entry in get_legacy_xp_history()
    ]
    save_xp_ledger()


def award_xp(
    entry_id: str,
    title: str,
    xp: int,
    category: str,
    created_at: datetime | None = None,
) -> bool:
    if any(entry.get("id") == entry_id for entry in XP_LEDGER["entries"]):
        return False
    XP_LEDGER["entries"].append(
        {
            "id": entry_id,
            "title": title,
            "xp": int(xp),
            "category": category,
            "created_at": (created_at or datetime.now()).isoformat(),
        }
    )
    save_xp_ledger()
    return True


def get_xp_history() -> list[dict[str, Any]]:
    entries = []
    for stored in XP_LEDGER["entries"]:
        try:
            created_at = datetime.fromisoformat(str(stored["created_at"]))
            xp = int(stored["xp"])
        except (KeyError, TypeError, ValueError):
            continue
        if xp <= 0:
            continue
        entries.append(
            {
                **stored,
                "xp": xp,
                "created_at": created_at,
                "date_display": created_at.strftime("%d.%m.%Y · %H:%M"),
            }
        )
    return sorted(entries, key=lambda entry: entry["created_at"], reverse=True)


def get_real_xp() -> int:
    return sum(entry["xp"] for entry in get_xp_history())


def is_final_challenge_complete() -> bool:
    return any(
        entry.get("id") == "special:final-challenge"
        for entry in XP_LEDGER["entries"]
    )


def sync_final_challenge() -> bool:
    if is_final_challenge_complete() or get_real_xp() < XP_LEVEL_ROADMAP[8]["xp"]:
        return False
    week = get_progress_analytics()["week"]
    if week["sales"] < 20 or week["active_days"] < 4:
        return False
    return award_xp(
        "special:final-challenge",
        "Finální výzva",
        0,
        "Dokončení cesty",
    )


def get_effective_xp() -> int:
    return get_real_xp()


def get_level_state(xp: int | None = None) -> dict[str, Any]:
    current_xp = get_effective_xp() if xp is None else xp
    current_step = max(
        (step for step in XP_LEVEL_ROADMAP if step["xp"] <= current_xp),
        key=lambda step: step["level"],
    )
    next_step = next(
        (step for step in XP_LEVEL_ROADMAP if step["xp"] > current_xp),
        None,
    )
    final_required = (
        current_step["level"] >= 10
        and not is_final_challenge_complete()
    )
    if final_required:
        current_step = XP_LEVEL_ROADMAP[8]
        next_step = XP_LEVEL_ROADMAP[9]
    if next_step:
        level_span = next_step["xp"] - current_step["xp"]
        progress = min(
            100,
            max(0, round((current_xp - current_step["xp"]) / level_span * 100)),
        )
        remaining = max(0, next_step["xp"] - current_xp)
    else:
        progress = 100
        remaining = 0
    return {
        "xp": current_xp,
        "real_xp": get_real_xp(),
        "current_step": current_step,
        "next_step": next_step,
        "level": current_step["level"],
        "progress": progress,
        "remaining": remaining,
        "final_required": final_required,
    }


@app.context_processor
def inject_active_theme() -> dict[str, str]:
    return {"active_theme": APPEARANCE["theme"]}


def sync_achievements() -> list[str]:
    if not SALES:
        return []

    newly_unlocked: list[str] = []
    data_changed = False
    ordered_sales = sorted(SALES, key=lambda sale: sale["created_at"])
    sales_by_day: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for sale in ordered_sales:
        sales_by_day[sale["created_at"].date()].append(sale)

    def unlock(key: str, unlocked_at: datetime, **metadata: Any) -> None:
        nonlocal data_changed
        if ACHIEVEMENTS[key] is None:
            ACHIEVEMENTS[key] = {"unlocked_at": unlocked_at.isoformat(), **metadata}
            newly_unlocked.append(key)
            data_changed = True

    unlock("first_sale", ordered_sales[0]["created_at"])

    for day in sorted(sales_by_day):
        day_sales = sales_by_day[day]
        if len(day_sales) >= 5:
            unlock("five_in_day", day_sales[4]["created_at"], value=5)
            break

    for previous, current in zip(ordered_sales, ordered_sales[1:]):
        same_day = previous["created_at"].date() == current["created_at"].date()
        difference = (current["created_at"] - previous["created_at"]).total_seconds()
        if same_day and 0 <= difference <= 600:
            unlock("quick_pair", current["created_at"], minutes=round(difference / 60, 1))
            break

    sorted_days = sorted(sales_by_day)
    if len(sorted_days) > 1:
        previous_best = len(sales_by_day[sorted_days[0]])
        for day in sorted_days[1:]:
            day_count = len(sales_by_day[day])
            if day_count > previous_best:
                unlock(
                    "personal_record",
                    sales_by_day[day][-1]["created_at"],
                    value=day_count,
                )
            previous_best = max(previous_best, day_count)

        current_record = max(len(day_sales) for day_sales in sales_by_day.values())
        personal_record = ACHIEVEMENTS["personal_record"]
        if personal_record and current_record > int(personal_record.get("value", 0)):
            personal_record["value"] = current_record
            personal_record["recorded_at"] = sales_by_day[
                max(sales_by_day, key=lambda day: len(sales_by_day[day]))
            ][-1]["created_at"].isoformat()
            data_changed = True

    if len(ordered_sales) >= 100:
        unlock("hundred_sales", ordered_sales[99]["created_at"], value=100)

    if data_changed:
        save_achievements()
    for key in newly_unlocked:
        catalog = ACHIEVEMENT_CATALOG[key]
        unlocked_at = datetime.fromisoformat(ACHIEVEMENTS[key]["unlocked_at"])
        award_xp(
            f"achievement:{key}",
            catalog["label"],
            catalog["xp"],
            "Milník",
            unlocked_at,
        )
    return newly_unlocked


def select_challenges(
    catalog: list[dict[str, Any]], seed: str, count: int = 3
) -> list[dict[str, Any]]:
    return sorted(
        catalog,
        key=lambda challenge: hashlib.sha256(
            f"{seed}:{challenge['id']}".encode("utf-8")
        ).hexdigest(),
    )[:count]


def get_max_rolling_hour(sales: list[dict[str, Any]]) -> int:
    ordered = sorted(sale["created_at"] for sale in sales)
    maximum = 0
    start = 0
    for end, current in enumerate(ordered):
        while current - ordered[start] > timedelta(hours=1):
            start += 1
        maximum = max(maximum, end - start + 1)
    return maximum


def get_period_streak(sales: list[dict[str, Any]]) -> int:
    dates = sorted(
        {
            sale["created_at"].date()
            for sale in sales
            if is_counted_workday(sale["created_at"])
        }
    )
    best = current = 0
    previous = None
    for day in dates:
        current = current + 1 if previous and previous == previous_workday(day) else 1
        best = max(best, current)
        previous = day
    return best


def evaluate_challenge(
    challenge: dict[str, Any],
    sales: list[dict[str, Any]],
    previous_period_sales: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    kind = challenge["kind"]
    target = int(challenge["target"])
    if kind == "count":
        current = len(sales)
    elif kind == "revenue":
        current = get_total_revenue(sales)
    elif kind == "average":
        current = round(get_total_revenue(sales) / len(sales)) if sales else 0
    elif kind == "unique_clients":
        current = len({sale["customer"].strip().casefold() for sale in sales})
    elif kind == "notes":
        current = sum(bool(sale["note"].strip()) for sale in sales)
    elif kind == "before_hour":
        current = sum(sale["created_at"].hour < challenge["hour"] for sale in sales)
    elif kind == "after_hour":
        current = sum(sale["created_at"].hour >= challenge["hour"] for sale in sales)
    elif kind == "hour_window":
        current = sum(
            challenge["start"] <= sale["created_at"].hour < challenge["end"]
            for sale in sales
        )
    elif kind == "rolling_hour":
        current = get_max_rolling_hour(sales)
    elif kind == "active_days":
        current = len({sale["created_at"].date() for sale in sales})
    elif kind == "period_streak":
        current = get_period_streak(sales)
    elif kind == "best_day_count":
        counts = Counter(sale["created_at"].date() for sale in sales)
        current = max(counts.values(), default=0)
    elif kind == "beat_previous":
        previous_count = len(previous_period_sales or [])
        target = previous_count + 1
        current = len(sales)
    else:
        current = 0

    progress = min(100, round(current / target * 100)) if target else 100
    if kind in {"revenue", "average"}:
        progress_text = f"{format_czk(current)} / {format_czk(target)} Kč"
    else:
        progress_text = f"{min(current, target)} / {target}"
    return {
        **challenge,
        "current": current,
        "target": target,
        "progress": progress,
        "progress_text": progress_text,
        "complete": current >= target,
    }


def get_active_challenges() -> dict[str, Any]:
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    previous_week_start = week_start - timedelta(days=7)
    previous_week_sales = [
        sale
        for sale in SALES
        if previous_week_start <= sale["created_at"].date() < week_start
        and is_counted_workday(sale["created_at"])
    ]
    day_seed = today.isoformat()
    week_seed = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    daily = (
        [
            evaluate_challenge(challenge, get_today_sales())
            for challenge in select_challenges(DAILY_CHALLENGE_CATALOG, day_seed)
        ]
        if is_counted_workday(today)
        else []
    )
    weekly = [
        evaluate_challenge(challenge, get_week_sales(), previous_week_sales)
        for challenge in select_challenges(WEEKLY_CHALLENGE_CATALOG, week_seed)
    ]
    for period, period_key, challenges in (
        ("daily", day_seed, daily),
        ("weekly", week_seed, weekly),
    ):
        for challenge in challenges:
            completion_key = f"{period}:{period_key}:{challenge['id']}"
            challenge["completion_key"] = completion_key
            challenge["complete"] = (
                completion_key in CHALLENGES["completed"] or challenge["complete"]
            )
    return {
        "daily": daily,
        "weekly": weekly,
        "day_key": day_seed,
        "week_key": week_seed,
    }


def sync_challenges() -> list[dict[str, Any]]:
    active = get_active_challenges()
    newly_completed = []
    for period in ("daily", "weekly"):
        for challenge in active[period]:
            key = challenge["completion_key"]
            if challenge["current"] >= challenge["target"] and key not in CHALLENGES["completed"]:
                completion = {
                    "completion_key": key,
                    "challenge_id": challenge["id"],
                    "title": challenge["title"],
                    "period": period,
                    "completed_at": datetime.now().isoformat(),
                    "xp": challenge["xp"],
                }
                CHALLENGES["completed"][key] = completion
                newly_completed.append(completion)
    if newly_completed:
        save_challenges()
    for completion in newly_completed:
        period = completion["period"]
        raw_xp = int(completion["xp"])
        xp_value = (
            min(raw_xp, 50)
            if period == "daily"
            else min(150, max(100, raw_xp))
        )
        award_xp(
            f"challenge:{completion['completion_key']}",
            completion["title"],
            xp_value,
            "Denní výzva" if period == "daily" else "Týdenní výzva",
            datetime.fromisoformat(completion["completed_at"]),
        )
    return newly_completed


def sync_rewards(total_sales: int | None = None) -> str | None:
    sales_count = get_sales_summary()["total_sales"] if total_sales is None else total_sales
    changed = False
    newly_available = None
    for reward in REWARD_CATALOG:
        record = REWARDS["rewards"].get(reward["id"])
        if record and record.get("status") == "delivered":
            continue
        if sales_count >= reward["target"]:
            if not record:
                REWARDS["rewards"][reward["id"]] = {
                    "status": "available",
                    "unlocked_at": datetime.now().isoformat(),
                }
                changed = True
                newly_available = reward["id"]
            elif record.get("status") not in {"available", "discussing"}:
                record["status"] = "available"
                record.setdefault("unlocked_at", datetime.now().isoformat())
                changed = True
                newly_available = reward["id"]
        break
    if changed:
        save_rewards()
    return newly_available


def get_reward_views(total_sales: int) -> dict[str, Any]:
    sync_rewards(total_sales)
    active_id = next(
        (
            reward["id"]
            for reward in REWARD_CATALOG
            if REWARDS["rewards"].get(reward["id"], {}).get("status") != "delivered"
        ),
        None,
    )
    views = []
    def display_date(value: Any) -> str | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return None

    for reward in REWARD_CATALOG:
        record = REWARDS["rewards"].get(reward["id"], {})
        stored_status = record.get("status")
        if stored_status == "delivered":
            status = "delivered"
        elif reward["id"] == active_id:
            status = stored_status or "in_progress"
        elif total_sales >= reward["target"]:
            status = "queued"
        else:
            status = "locked"
        views.append(
            {
                **reward,
                "status": status,
                "progress": min(100, round(total_sales / reward["target"] * 100)),
                "remaining": max(0, reward["target"] - total_sales),
                "unlocked_at": record.get("unlocked_at"),
                "requested_at": record.get("requested_at"),
                "delivered_at": record.get("delivered_at"),
                "unlocked_display": display_date(record.get("unlocked_at")),
                "delivered_display": display_date(record.get("delivered_at")),
            }
        )
    active_reward = next((reward for reward in views if reward["id"] == active_id), None)
    history = [reward for reward in views if reward["status"] == "delivered"]
    return {"rewards": views, "active_reward": active_reward, "history": history}


migrate_xp_ledger()
sync_achievements()
sync_challenges()
sync_final_challenge()
sync_rewards()


@app.errorhandler(403)
def forbidden(error):
    action_url, action_label = default_error_action()
    return render_template(
        "error.html",
        code=403,
        title="Tady se pokračovat nedá.",
        text="Akce nebyla ověřená. Vrať se zpět a zkus ji provést znovu.",
        action_url=action_url,
        action_label=action_label,
    ), 403


@app.errorhandler(404)
def not_found(error):
    action_url, action_label = default_error_action()
    return render_template(
        "error.html",
        code=404,
        title="Tahle stránka tu není.",
        text="Odkaz je neplatný, nebo stránka už neexistuje.",
        action_url=action_url,
        action_label=action_label,
    ), 404


@app.errorhandler(500)
def server_error(error):
    action_url, action_label = default_error_action()
    return render_template(
        "error.html",
        code=500,
        title="Něco se pokazilo.",
        text="Aplikace narazila na chybu. Zkus to prosím znovu za chvíli.",
        action_url=action_url,
        action_label=action_label,
    ), 500


@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))

    error = None
    next_url = safe_redirect_target(request.values.get("next"), url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == DEMO_USER["username"] and password == DEMO_USER["password"]:
            session["user"] = DEMO_USER["display_name"]
            log_event("login_success", user=DEMO_USER["display_name"])
            return redirect(next_url)

        log_event("login_failed", {"username_length": len(username)})
        error = "Nesprávné uživatelské jméno nebo heslo."

    return render_template("login.html", error=error, next_url=next_url)


@app.route("/logout")
@login_required
def logout():
    log_event("logout")
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    new_achievements = [
        key
        for key in session.pop("new_achievements", [])
        if key in ACHIEVEMENT_CATALOG
    ]
    summary = get_sales_summary()
    total_sales = summary["total_sales"]
    sanitize_voice_session(total_sales)
    achievement_bonus_xp = get_achievement_bonus_xp()
    challenge_bonus_xp = get_challenge_bonus_xp()
    level_state = get_level_state()
    streak = calculate_streak(SALES)
    voice_clips = get_voice_clips()
    voice_clip = get_current_voice_clip()
    voice_plays_remaining = get_voice_plays_remaining() if voice_clip else 0
    voice_unlock_progress = get_voice_unlock_progress()

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        user=session["user"],
        sales=filter_sales("", "all")[:5],
        current_time=datetime.now().strftime("%H:%M"),
        is_workday_today=is_counted_workday(),
        daily_goal=5,
        daily_progress=min(100, summary["today_sales"] * 20),
        remaining_sales=max(0, 5 - summary["today_sales"]),
        streak=streak,
        format_czk=format_czk,
        xp=level_state["xp"],
        level=level_state["level"],
        next_level_xp=level_state["next_step"]["xp"] if level_state["next_step"] else level_state["xp"],
        progress=level_state["progress"],
        achievement_bonus_xp=achievement_bonus_xp,
        challenge_bonus_xp=challenge_bonus_xp,
        achievement_catalog=ACHIEVEMENT_CATALOG,
        new_achievements=new_achievements,
        voice_clips_count=len(voice_clips),
        voice_clip=voice_clip,
        voice_plays_remaining=voice_plays_remaining,
        voice_unlock_progress=voice_unlock_progress,
        **summary,
    )


@app.route("/sales-page")
@login_required
def sales_page():
    query = request.args.get("q", "").strip()
    period = request.args.get("period", "all")
    return render_template(
        "sales_page.html",
        active_page="sales",
        sales=filter_sales(query, period),
        query=query,
        period=period,
        format_czk=format_czk,
        **get_sales_summary(),
    )


@app.route("/stats")
@login_required
def stats_page():
    summary = get_sales_summary()
    total_revenue = summary["total_revenue"]
    return render_template(
        "stats.html",
        active_page="stats",
        average_sale=get_average_sale(),
        best_day=get_best_day(),
        best_hour=get_best_hour(),
        last_7_days=get_last_7_days_counts(),
        fun_items={
            "rolls": get_fun_item_progress(total_revenue, 3),
            "coffee": get_fun_item_progress(total_revenue, 70),
            "pizza": get_fun_item_progress(total_revenue, 250),
            "cinema": get_fun_item_progress(total_revenue, 230),
            "perfume": get_fun_item_progress(total_revenue, 2700),
        },
        long_term_goal=get_long_term_goal_progress(total_revenue, 10_000_000),
        format_czk=format_czk,
        **summary,
    )


@app.route("/progress")
@login_required
def progress_page():
    if sync_final_challenge():
        session["progress_message"] = "Finální výzva je splněná. Úroveň 10 je odemčená."
        log_event("final_challenge_completed")
    level_state = get_level_state()
    effective_level = level_state["level"]
    analytics = get_progress_analytics()
    final_target = 20
    final_days_target = 4
    final_progress = min(final_target, analytics["week"]["sales"])
    final_days_progress = min(final_days_target, analytics["week"]["active_days"])

    return render_template(
        "progress.html",
        active_page="progress",
        xp=level_state["xp"],
        real_xp=level_state["real_xp"],
        current_step=level_state["current_step"],
        next_step=level_state["next_step"],
        level_progress=level_state["progress"],
        xp_remaining=level_state["remaining"],
        final_required=level_state["final_required"],
        effective_level=effective_level,
        level_roadmap=XP_LEVEL_ROADMAP,
        xp_sources=XP_SOURCE_OVERVIEW,
        xp_history=get_xp_history()[:20],
        analytics=analytics,
        final_target=final_target,
        final_progress=final_progress,
        final_percent=min(100, int(final_progress / final_target * 100)),
        final_days_target=final_days_target,
        final_days_progress=final_days_progress,
        final_days_percent=min(100, int(final_days_progress / final_days_target * 100)),
        format_czk=format_czk,
        progress_message=session.pop("progress_message", None),
    )


@app.route("/progress/theme/<theme_id>", methods=["POST"])
@login_required
def activate_progress_theme(theme_id: str):
    requirements = {"rose": 1, "soft-blush": 2, "lavender-mist": 7}
    required_level = requirements.get(theme_id)
    if required_level is None or get_level_state()["level"] < required_level:
        session["progress_message"] = "Tento motiv zatím není odemčený."
        return redirect(url_for("progress_page") + "#themes")
    APPEARANCE["theme"] = theme_id
    save_appearance()
    session["progress_message"] = "Vybraný motiv je nyní aktivní."
    log_event("theme_activated", {"theme": theme_id})
    return redirect(url_for("progress_page") + "#themes")


@app.route("/progress/finale")
@login_required
def progress_finale():
    if get_level_state()["level"] < 10:
        session["progress_message"] = "Závěrečná stránka se odemkne na úrovni 10."
        return redirect(url_for("progress_page"))
    return render_template(
        "progress_finale.html",
        active_page="progress",
        format_czk=format_czk,
        streak=calculate_streak(SALES),
        **get_sales_summary(),
    )


@app.route("/progress/monthly-report.csv")
@login_required
def download_monthly_report():
    if get_level_state()["level"] < 5:
        session["progress_message"] = "Měsíční rekapitulace se odemkne na úrovni 5."
        return redirect(url_for("progress_page"))
    log_event("monthly_report_downloaded")
    analytics = get_progress_analytics()["month"]
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Měsíční rekapitulace", datetime.now().strftime("%m/%Y")])
    writer.writerow(["Prodeje", analytics["sales"]])
    writer.writerow(["Hodnota", analytics["revenue"]])
    writer.writerow(["Aktivní pracovní dny", analytics["active_days"]])
    writer.writerow(["Průměr na aktivní den", analytics["average_per_active_day"]])
    writer.writerow(["Nejlepší týden – prodeje", analytics["best_week_sales"]])
    writer.writerow(["Nejlepší týden – hodnota", analytics["best_week_revenue"]])
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=rekapitulace-{datetime.now():%Y-%m}.csv"},
    )


@app.route("/challenges")
@login_required
def challenges_page():
    newly_completed = sync_challenges()
    if newly_completed:
        session["new_challenges"] = [challenge["title"] for challenge in newly_completed]
        for challenge in newly_completed:
            log_event(
                "challenge_completed",
                {
                    "challenge_id": challenge["challenge_id"],
                    "title": challenge["title"],
                    "xp": challenge["xp"],
                },
            )
    active_challenges = get_active_challenges()
    return render_template(
        "challenges.html",
        active_page="challenges",
        achievements=ACHIEVEMENTS,
        achievement_catalog=ACHIEVEMENT_CATALOG,
        achievement_views=get_achievement_views(),
        achievement_bonus_xp=get_achievement_bonus_xp(),
        challenge_bonus_xp=get_challenge_bonus_xp(),
        daily_challenges=active_challenges["daily"],
        weekly_challenges=active_challenges["weekly"],
        challenge_history_count=len(CHALLENGES["completed"]),
        format_czk=format_czk,
        **get_sales_summary(),
    )


@app.route("/rewards")
@login_required
def rewards_page():
    total_sales = get_sales_summary()["total_sales"]
    reward_data = get_reward_views(total_sales)
    return render_template(
        "rewards.html",
        active_page="rewards",
        total_sales=total_sales,
        **reward_data,
    )


@app.route("/rewards/<reward_id>/interest", methods=["POST"])
@login_required
def request_reward(reward_id: str):
    total_sales = get_sales_summary()["total_sales"]
    reward_data = get_reward_views(total_sales)
    active_reward = reward_data["active_reward"]
    if (
        active_reward
        and active_reward["id"] == reward_id
        and active_reward["status"] == "available"
    ):
        record = REWARDS["rewards"][reward_id]
        record["status"] = "discussing"
        record["requested_at"] = datetime.now().isoformat()
        save_rewards()
        session["reward_message"] = "Odměna je připravená k domluvě."
        log_event("reward_requested", {"reward_id": reward_id, "reward_title": active_reward["title"]})
    return redirect(url_for("rewards_page"))


@app.route("/rewards/<reward_id>/deliver", methods=["POST"])
@login_required
def deliver_reward(reward_id: str):
    total_sales = get_sales_summary()["total_sales"]
    reward_data = get_reward_views(total_sales)
    active_reward = reward_data["active_reward"]
    if (
        active_reward
        and active_reward["id"] == reward_id
        and active_reward["status"] in {"available", "discussing"}
    ):
        record = REWARDS["rewards"][reward_id]
        record["status"] = "delivered"
        record["delivered_at"] = datetime.now().isoformat()
        save_rewards()
        sync_rewards(total_sales)
        session["reward_message"] = "Odměna byla označena jako předaná."
        log_event("reward_delivered", {"reward_id": reward_id, "reward_title": active_reward["title"]})
    return redirect(url_for("rewards_page"))


@app.route("/sales", methods=["POST"])
@login_required
def add_sale():
    if not is_counted_workday():
        session["weekend_sale_blocked"] = True
        log_event("sale_blocked_weekend")
        return redirect(url_for("dashboard"))
    sanitize_voice_session(get_sales_summary()["total_sales"])

    customer = request.form.get("customer", "").strip() or "Neznámý klient"
    note = request.form.get("note", "").strip()

    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        amount = 0

    if amount > 0:
        created_at = datetime.now()
        sale = {
            "id": next_sale_id(),
            "customer": customer,
            "amount": amount,
            "note": note,
            "created_at": created_at,
        }
        SALES.append(sale)
        save_sales()
        log_event(
            "sale_created",
            {"sale_id": sale["id"], "amount": sale["amount"], "has_note": bool(note)},
        )
        award_xp(
            f"sale:{sale['id']}",
            f"Prodej · {sale['customer']}",
            10,
            "Prodej",
            created_at,
        )
        newly_unlocked = sync_achievements()
        newly_completed = sync_challenges()
        final_challenge_completed = sync_final_challenge()
        newly_available_reward = sync_rewards()
        session["last_sale_added"] = True
        register_sale_for_voice_unlock()
        if newly_unlocked:
            session["new_achievements"] = newly_unlocked
            for achievement_key in newly_unlocked:
                log_event("achievement_unlocked", {"achievement_key": achievement_key})
        if newly_completed:
            session["new_challenges"] = [
                challenge["title"] for challenge in newly_completed
            ]
            for challenge in newly_completed:
                log_event(
                    "challenge_completed",
                    {
                        "challenge_id": challenge["challenge_id"],
                        "title": challenge["title"],
                        "xp": challenge["xp"],
                    },
                )
        if newly_available_reward:
            session["reward_unlocked"] = next(
                reward["title"]
                for reward in REWARD_CATALOG
                if reward["id"] == newly_available_reward
            )
            log_event("reward_unlocked", {"reward_id": newly_available_reward})
        if final_challenge_completed:
            session["progress_message"] = "Finální výzva je splněná. Úroveň 10 je odemčená."
            log_event("final_challenge_completed")
    return redirect(url_for("dashboard"))


@app.route("/sales/<int:sale_id>/delete", methods=["POST"])
@login_required
def delete_sale(sale_id: int):
    global SALES
    existed = any(sale["id"] == sale_id for sale in SALES)
    SALES = [sale for sale in SALES if sale["id"] != sale_id]
    save_sales()
    session["last_sale_deleted"] = True
    log_event("sale_deleted", {"sale_id": sale_id, "existed": existed})
    return redirect(url_for("dashboard"))


@app.route("/achievements/<achievement_key>/reflection", methods=["POST"])
@login_required
def save_achievement_reflection(achievement_key: str):
    achievement = ACHIEVEMENTS.get(achievement_key)
    if achievement is None or achievement_key not in ACHIEVEMENT_CATALOG:
        return redirect(url_for("challenges_page"))
    achievement["reflection"] = request.form.get("reflection", "").strip()[:300]
    save_achievements()
    session["reflection_saved"] = True
    log_event("achievement_reflection_saved", {"achievement_key": achievement_key})
    return redirect(url_for("challenges_page") + "#moments")


@app.route("/voice-audio")
@login_required(hide=True)
def voice_audio():
    clip = get_current_voice_clip()
    if not clip:
        abort(404)

    file_path = VOICE_AUDIO_DIR / clip["filename"]
    if not file_path.exists() or not file_path.is_file():
        abort(404)

    # stejna URL servuje pokazde jiny klip, cache by prehravala stary
    response = send_file(file_path, conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/voice-played", methods=["POST"])
@login_required(hide=True)
def voice_played():
    if not get_current_voice_clip():
        return {"locked": True, "remaining": 0}

    play_count = int(session.get("voice_play_count", 0)) + 1
    session["voice_play_count"] = play_count
    remaining = max(0, VOICE_MAX_PLAYS_PER_SALE - play_count)
    if remaining <= 0:
        session["voice_unlocked"] = False

    log_event("voice_played", {"remaining": remaining, "locked": remaining <= 0})
    return {"locked": remaining <= 0, "remaining": remaining}


if __name__ == "__main__":
    app.run(debug=True)
