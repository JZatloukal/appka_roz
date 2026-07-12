from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover - helper script dependency guard
    raise SystemExit(
        "Missing dependency: psycopg. Install it with `pip install psycopg[binary]` "
        "before running this migration."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]


def read_json(filename: str, fallback: Any) -> Any:
    path = ROOT / filename
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def migrate_sales(conn) -> None:
    sales = read_json("sales_data.json", [])
    with conn.cursor() as cur:
        for index, sale in enumerate(sales, start=1):
            created_at = parse_datetime(sale.get("created_at")) or datetime.now()
            cur.execute(
                """
                insert into sales (id, customer, amount, note, created_at)
                values (%s, %s, %s, %s, %s)
                on conflict (id) do update set
                    customer = excluded.customer,
                    amount = excluded.amount,
                    note = excluded.note,
                    created_at = excluded.created_at
                """,
                (
                    int(sale.get("id", index)),
                    str(sale.get("customer", "Neznámý klient")),
                    int(sale.get("amount", 0)),
                    str(sale.get("note", "")),
                    created_at,
                ),
            )


def migrate_achievements(conn) -> None:
    achievements = read_json("achievements_data.json", {})
    with conn.cursor() as cur:
        for key, value in achievements.items():
            if value is None:
                cur.execute(
                    """
                    insert into achievements (achievement_key, metadata)
                    values (%s, '{}'::jsonb)
                    on conflict (achievement_key) do nothing
                    """,
                    (key,),
                )
                continue
            metadata = {
                k: v
                for k, v in value.items()
                if k not in {"unlocked_at", "value", "minutes", "reflection"}
            }
            cur.execute(
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
                (
                    key,
                    parse_datetime(value.get("unlocked_at")),
                    value.get("value"),
                    value.get("minutes"),
                    value.get("reflection"),
                    Jsonb(metadata),
                ),
            )


def migrate_challenges(conn) -> None:
    challenges = read_json("challenges_data.json", {"completed": {}})
    completed = challenges.get("completed", {}) if isinstance(challenges, dict) else {}
    with conn.cursor() as cur:
        for key, challenge in completed.items():
            metadata = {
                k: v
                for k, v in challenge.items()
                if k not in {"completion_key", "challenge_id", "title", "period", "completed_at", "xp"}
            }
            cur.execute(
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
                (
                    challenge.get("completion_key", key),
                    challenge.get("challenge_id", ""),
                    challenge.get("title", ""),
                    challenge.get("period", "daily"),
                    parse_datetime(challenge.get("completed_at")) or datetime.now(),
                    int(challenge.get("xp", 0)),
                    Jsonb(metadata),
                ),
            )


def migrate_rewards(conn) -> None:
    rewards = read_json("rewards_data.json", {"rewards": {}})
    records = rewards.get("rewards", {}) if isinstance(rewards, dict) else {}
    with conn.cursor() as cur:
        for reward_id, reward in records.items():
            metadata = {
                k: v
                for k, v in reward.items()
                if k not in {"status", "unlocked_at", "requested_at", "delivered_at", "email_sent_at"}
            }
            cur.execute(
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
                (
                    reward_id,
                    reward.get("status", "locked"),
                    parse_datetime(reward.get("unlocked_at")),
                    parse_datetime(reward.get("requested_at")),
                    parse_datetime(reward.get("delivered_at")),
                    parse_datetime(reward.get("email_sent_at")),
                    Jsonb(metadata),
                ),
            )


def migrate_xp_entries(conn) -> None:
    xp_data = read_json("xp_data.json", {"entries": []})
    entries = xp_data.get("entries", []) if isinstance(xp_data, dict) else []
    with conn.cursor() as cur:
        for entry in entries:
            metadata = {
                k: v
                for k, v in entry.items()
                if k not in {"id", "title", "xp", "category", "created_at"}
            }
            cur.execute(
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
                (
                    entry["id"],
                    entry.get("title", ""),
                    int(entry.get("xp", 0)),
                    entry.get("category", ""),
                    parse_datetime(entry.get("created_at")) or datetime.now(),
                    Jsonb(metadata),
                ),
            )


def migrate_appearance(conn) -> None:
    appearance = read_json("appearance_data.json", {})
    theme = appearance.get("theme", "rose") if isinstance(appearance, dict) else "rose"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into appearance (user_key, theme, updated_at)
            values ('default', %s, now())
            on conflict (user_key) do update set theme = excluded.theme, updated_at = now()
            """,
            (theme,),
        )


def migrate_analytics(conn) -> None:
    events = read_json("analytics_events.json", [])
    with conn.cursor() as cur:
        for event in events:
            cur.execute(
                """
                insert into analytics_events
                    (event_type, app_user, path, method, ip_hash, user_agent, metadata, created_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.get("event_type", ""),
                    event.get("user"),
                    event.get("path"),
                    event.get("method"),
                    event.get("ip_hash"),
                    event.get("user_agent"),
                    Jsonb(event.get("metadata", {})),
                    parse_datetime(event.get("created_at")) or datetime.now(),
                ),
            )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is missing.")

    with psycopg.connect(database_url) as conn:
        migrate_sales(conn)
        migrate_achievements(conn)
        migrate_challenges(conn)
        migrate_rewards(conn)
        migrate_xp_entries(conn)
        migrate_appearance(conn)
        migrate_analytics(conn)
        conn.commit()

    print("JSON data migrated to Postgres.")


if __name__ == "__main__":
    main()
