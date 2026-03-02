import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from feedgen.feed import FeedGenerator


def norm(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^t\.me/", "", s)
    s = s.strip("/").strip()
    if s.startswith("@"):
        s = s[1:]
    return s


def compute_window(now_utc: datetime, tz_name: str, hhmm: str):
    tz = ZoneInfo(tz_name)
    h, m = map(int, hhmm.split(":"))

    now_local = now_utc.astimezone(tz)
    end_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

    # If current local time is before today's briefing time, end is yesterday at briefing time
    if now_local < end_local:
        end_local -= timedelta(days=1)

    start_local = end_local - timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def message_text(m) -> str:
    txt = (m.message or "").strip()
    if txt:
        return txt

    # Include media only posts
    if getattr(m, "media", None):
        kind = type(m.media).__name__
        fname = getattr(getattr(m, "file", None), "name", None)
        if fname:
            return f"[media: {kind}] {fname}"
        return f"[media: {kind}]"

    return ""


def escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


async def main():
    # Local: load .env if present. GitHub Actions: env vars are already set.
    load_dotenv()

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session_string = os.environ["TG_SESSION_STRING"]
    sess = StringSession(session_string)

    sources = [norm(x) for x in os.environ.get("TG_SOURCES", "").split(",") if x.strip()]
    if not sources:
        raise RuntimeError("TG_SOURCES is empty")

    tz_name = os.environ.get("TZ_NAME", "Europe/London")
    briefing_time = os.environ.get("BRIEFING_TIME", "05:45")

    since_utc, until_utc = compute_window(datetime.now(timezone.utc), tz_name, briefing_time)

    feed_home = os.environ.get("FEED_HOME", "https://example.com")
    feed_title = os.environ.get("FEED_TITLE", "Telegram Daily Briefing")
    rss_filename = os.environ.get("RSS_FILENAME", "rss.xml")

    items = []
    per_source_counts = {s: 0 for s in sources}

    async with TelegramClient(sess, api_id, api_hash) as c:
        for s in sources:
            try:
                ent = await c.get_entity(s)
            except Exception as e:
                print(f"[WARN] Cannot resolve source '{s}': {e}")
                continue

            uname = getattr(ent, "username", None)
            display_name = uname or s

            channel_id = getattr(ent, "id", None)

            async for m in c.iter_messages(ent):
                if not getattr(m, "date", None):
                    continue

                d = m.date.astimezone(timezone.utc)

                # iter_messages gives newest to oldest
                if d < since_utc:
                    break
                if d >= until_utc:
                    continue

                txt = message_text(m)
                if not txt:
                    continue

                if uname:
                    link = f"https://t.me/{uname}/{m.id}"
                else:
                    # Best effort for channels without username
                    link = f"https://t.me/c/{channel_id}/{m.id}" if channel_id else "https://t.me/"

                items.append((d, display_name, m.id, txt, link))
                per_source_counts[s] = per_source_counts.get(s, 0) + 1

    # Newest first
    items.sort(key=lambda x: x[0], reverse=True)

    # Summary line for the RSS top
    counts_part = ", ".join(f"{s}: {per_source_counts.get(s, 0)}" for s in sources)
    window_part = f"{since_utc.isoformat()} \u2192 {until_utc.isoformat()}"
    summary_line = f"{window_part} | {counts_part}"

    # Local visibility
    print(f"Window (UTC): {since_utc.isoformat()} -> {until_utc.isoformat()}")
    for s in sources:
        print(f"  {s}: {per_source_counts.get(s, 0)} items")
    print(f"Total items: {len(items)}")

    fg = FeedGenerator()
    fg.title(feed_title)
    fg.link(href=feed_home, rel="alternate")
    fg.description(summary_line)
    fg.language("en")

    for d, src_name, mid, txt, link in items:
        e = fg.add_entry()
        e.id(f"tg:{src_name}:{mid}")
        e.title(f"[{src_name}] {txt.splitlines()[0][:120]}")
        e.link(href=link)
        e.published(d)
        e.updated(d)

        body = (
            f"<p><a href='{link}'>Open</a></p>"
            f"<p><strong>Published (UTC):</strong> {escape_html(d.isoformat())}</p>"
            f"<pre>{escape_html(txt)}</pre>"
        )
        e.content(body, type="html")

    fg.rss_file(rss_filename, pretty=True)
    print(f"Wrote {rss_filename} with {len(items)} items.")


if __name__ == "__main__":
    asyncio.run(main())
