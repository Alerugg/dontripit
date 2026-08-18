from __future__ import annotations

import json
import os

import psycopg2


APPLICATIONS = (
    "dontripit_ygo_multilingual_production_v1",
    "dontripit_ygo_cert_writer",
    "dontripit_ygo_cert_validate",
)


def run() -> dict:
    url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_UNPOOLED or DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        connect_timeout=30,
        application_name="dontripit_ygo_rollout_activity_readonly",
    )
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pid, application_name, state,
                       wait_event_type, wait_event,
                       EXTRACT(EPOCH FROM (clock_timestamp()-xact_start))::bigint AS xact_age_seconds,
                       EXTRACT(EPOCH FROM (clock_timestamp()-query_start))::bigint AS query_age_seconds,
                       backend_xid::text,
                       left(regexp_replace(coalesce(query,''), '\\s+', ' ', 'g'), 500)
                FROM pg_stat_activity
                WHERE datname=current_database()
                  AND application_name = ANY(%s)
                ORDER BY xact_start NULLS LAST, pid
                """,
                (list(APPLICATIONS),),
            )
            activity = [
                {
                    "pid": int(pid),
                    "application_name": app,
                    "state": state,
                    "wait_event_type": wait_type,
                    "wait_event": wait_event,
                    "xact_age_seconds": int(xact_age or 0),
                    "query_age_seconds": int(query_age or 0),
                    "backend_xid": backend_xid,
                    "query": query,
                }
                for pid, app, state, wait_type, wait_event, xact_age, query_age, backend_xid, query in cur.fetchall()
            ]
            pids = [row["pid"] for row in activity]
            locks = []
            if pids:
                cur.execute(
                    """
                    SELECT pid, locktype, mode, granted, count(*)
                    FROM pg_locks
                    WHERE pid = ANY(%s)
                    GROUP BY pid, locktype, mode, granted
                    ORDER BY pid, granted, locktype, mode
                    """,
                    (pids,),
                )
                locks = [
                    {"pid": int(pid), "locktype": locktype, "mode": mode, "granted": bool(granted), "count": int(count)}
                    for pid, locktype, mode, granted, count in cur.fetchall()
                ]

            cur.execute(
                """
                SELECT lower(coalesce(p.language,'')), count(*)
                FROM prints p
                JOIN cards c ON c.id=p.card_id
                JOIN games g ON g.id=c.game_id
                WHERE g.slug='yugioh'
                GROUP BY 1 ORDER BY 1
                """
            )
            languages = {str(language): int(count) for language, count in cur.fetchall()}

            cur.execute(
                """
                SELECT count(*)
                FROM print_localizations l
                JOIN prints p ON p.id=l.print_id
                JOIN cards c ON c.id=p.card_id
                JOIN games g ON g.id=c.game_id
                WHERE g.slug='yugioh' AND lower(l.language) IN ('es','ja')
                """
            )
            localizations_es_ja = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT count(*) FROM cards c JOIN games g ON g.id=c.game_id WHERE g.slug='yugioh'")
            cards = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT count(*) FROM sets s JOIN games g ON g.id=s.game_id WHERE g.slug='yugioh'")
            sets = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cur.fetchone()[0] or 0)
            conn.rollback()
    finally:
        conn.close()

    expected_languages = {"en": 44226, "es": 38249, "ja": 36426}
    rollout_complete = (
        languages == expected_languages
        and localizations_es_ja == 74675
        and cards == 14479
        and sets == 1457
    )
    report = {
        "status": "pass",
        "production_writes": 0,
        "writer_backends_visible": len(activity),
        "activity": activity,
        "locks": locks,
        "languages": languages,
        "print_localizations_es_ja": localizations_es_ja,
        "cards": cards,
        "sets": sets,
        "rollout_complete": rollout_complete,
        "database_bytes": database_bytes,
        "database_mib": round(database_bytes / 1024 / 1024, 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
