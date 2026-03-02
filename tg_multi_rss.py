import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Message
from feedgen.feed import FeedGenerator
from zoneinfo import ZoneInfo


def normalise_source(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^t\.me/", "", s)
    s = s.strip("/")
    return s


def message_link(username: str, msg_id: int) -> str:
    return f"https://t.me/{username}/{msg_id}"


def safe_text(m: Message) -> str:
    return (m.message or "").strip()


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def parse_briefing_window(
    now_utc: datetime, tz_name: str, briefing_time_hhmm: str
) -> tuple[datetime, datetime]:
    """
    Non-overlapping daily window:
      since = end - 24h
      until = most recent occurrence of BRIEFING_TIME in TIMEZONE
    """
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)

    hh, mm = map(int, briefing_time_hhmm.split(":"))
    end_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if now_local < end_local:
        end_local = end_local - timedelta(days=1)

    start_local = end_local - timedelta(days=1)

    since_utc = start_local.astimezone(timezone.utc)
    until_utc = end_local.astimezone(timezone.utc)
    return since_utc, until_utc


async def fetch_messages(
    client: TelegramClient,
    sources: List[str],
    since_utc: datetime,
    until_utc: datetime,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for src in sources:
        entity = await client.get_entity(src)
        username = getattr(entity, "username", None) or normalise_source(src)

        # Start from the window end so we don't waste time on "too-new" posts.
        # NO LIMIT: iterate until we break by date.
        async for m in client.iter_messages(entity, offset_date=until_utc):
            if not getattr(m, "date", None):
                continue

            msg_dt = to_utc(m.date)

            # Too old: stop this source (we're iterating backwards in time)
            if msg_dt < since_utc:
                break

            txt = safe_text(m)
            if not txt:
                continue

            link = message_link(username, m.id)
            uid = f"tg:{username}:{m.id}"

            items.append(
                {
                    "uid": uid,
                    "source": username,
                    "title": (txt.splitlines()[0][:120] if txt else f"Post {m.id}"),
                    "content": txt,
                    "link": link,
                    "published": msg_dt,
                }
            )

    # Newest first
    items.sort(key=lambda x: x["published"], reverse=True)

    # Dedupe by uid (no cap)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        if it["uid"] in seen:
            continue
        seen.add(it["uid"])
        deduped.append(it)

    return deduped


def build_rss(
    items: List[Dict[str, Any]],
    feed_title: str,
    feed_link: str,
    feed_description: str,
) -> FeedGenerator:
    fg = FeedGenerator()
    fg.title(feed_title)
    fg.link(href=feed_link, rel="alternate")
    fg.description(feed_description)
    fg.language("en")

    for it in items:
        fe = fg.add_entry()
        fe.id(it["uid"])
        fe.title(f"[{it['source']}] {it['title']}")
        fe.link(href=it["link"])
        fe.published(it["published"])
        fe.updated(it["published"])

        body = (
            f"<p><strong>Source:</strong> {it['source']}</p>"
            f"<p><strong>Published (UTC):</strong> {it['published'].isoformat()}</p>"
            f"<p><a href='{it['link']}'>Open in Telegram</a></p>"
            f"<pre>{escape_html(it['content'])}</pre>"
        )
        fe.content(body, type="html")

    return fg


async def main() -> None:
    from pathlib import Path
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ.get("TG_SESSION", "multi_rss_session")

    sources_raw = os.environ.get("TG_SOURCES", "")
    if not sources_raw.strip():
        raise RuntimeError("TG_SOURCES is empty")
    sources = [normalise_source(s) for s in sources_raw.split(",") if s.strip()]

    tz_name = os.environ.get("TIMEZONE", "Europe/London")
    briefing_time = os.environ.get("BRIEFING_TIME", "05:45")

    now_utc = datetime.now(timezone.utc)
    since_utc, until_utc = parse_briefing_window(now_utc, tz_name, briefing_time)

    rss_filename = os.environ.get("RSS_FILENAME", "rss.xml")
    feed_title = os.environ.get("FEED_TITLE", "Telegram Daily Briefing")
    feed_link = os.environ.get("FEED_LINK", "https://example.com")
    base_desc = os.environ.get("FEED_DESCRIPTION", "Daily non-overlapping Telegram briefing (windowed)")
    feed_description = f"{base_desc} | Window: {since_utc.isoformat()} → {until_utc.isoformat()} (UTC)"

    async with TelegramClient(session, api_id, api_hash) as client:
        items = await fetch_messages(client, sources, since_utc, until_utc)
        fg = build_rss(items, feed_title, feed_link, feed_description)
        fg.rss_file(rss_filename, pretty=True)

    print(f"Window (UTC): {since_utc.isoformat()} -> {until_utc.isoformat()}")
    print(f"Wrote {rss_filename} with {len(items)} items.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())