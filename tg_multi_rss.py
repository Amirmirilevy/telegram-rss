import os, re, asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from feedgen.feed import FeedGenerator
from zoneinfo import ZoneInfo


def norm(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^t\.me/", "", s)
    s = s.strip().strip("/")
    if s.startswith("@"):
        s = s[1:]
    return s


def window(now_utc: datetime, tz_name: str, hhmm: str):
    tz = ZoneInfo(tz_name)
    h, m = map(int, hhmm.split(":"))

    now_local = now_utc.astimezone(tz)
    end_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

    # If it's before today's briefing time, end_local should be "yesterday at briefing time"
    if now_local < end_local:
        end_local -= timedelta(days=1)

    start_local = end_local - timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def message_text(m) -> str:
    # Prefer any available text/caption
    txt = (m.message or "").strip()
    if txt:
        return txt

    # Include media-only posts too
    if getattr(m, "media", None):
        kind = type(m.media).__name__
        # Try to give a slightly nicer label if possible
        fname = getattr(getattr(m, "file", None), "name", None)
        if fname:
            return f"[media: {kind}] {fname}"
        return f"[media: {kind}]"

    return ""


async def main():
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    sess = StringSession(os.environ["TG_SESSION_STRING"])

    sources = [norm(x) for x in os.environ["TG_SOURCES"].split(",") if x.strip()]
    since, until = window(
        datetime.now(timezone.utc),
        os.environ.get("TZ_NAME", "Europe/London"),
        os.environ.get("BRIEFING_TIME", "05:45"),
    )

    items = []
    per_source = {}

    async with TelegramClient(sess, api_id, api_hash) as c:
        for s in sources:
            per_source[s] = 0
            try:
                ent = await c.get_entity(s)
            except Exception as e:
                print(f"[WARN] Cannot resolve source '{s}': {e}")
                continue

            # Username can be missing (private channels / no public username)
            uname = getattr(ent, "username", None)
            title_name = uname or s

            # Link: only valid if username exists; otherwise fall back to t.me/c/<id>/<msg_id> for channels
            # (t.me/c works only for certain channel types; if it fails, users can still click from inside Telegram)
            channel_id = getattr(ent, "id", None)

            async for m in c.iter_messages(ent):
                if not m.date:
                    continue
                d = m.date.astimezone(timezone.utc)

                if d < since:
                    break
                if d >= until:
                    continue

                txt = message_text(m)
                if not txt:
                    continue

                # Build best-effort link
                if uname:
                    link = f"https://t.me/{uname}/{m.id}"
                else:
                    # Best effort for channels without username:
                    # Telegram internal channel ids are large; t.me/c uses the "internal id" without -100 prefix.
                    # Telethon gives positive id; we strip to last 10-12 digits approach by removing leading 1000000000000-ish.
                    # A safer method is: internal_id = channel_id if already without -100; for many cases:
                    internal_id = channel_id
                    link = f"https://t.me/c/{internal_id}/{m.id}" if internal_id else "https://t.me/"

                items.append((d, title_name, m.id, txt, link))
                per_source[s] += 1

    # Newest first
    items.sort(key=lambda x: x[0], reverse=True)

    print(f"Window (UTC): {since.isoformat()} -> {until.isoformat()}")
    for s in sources:
        print(f"  {s}: {per_source.get(s, 0)} items")

    fg = FeedGenerator()
    fg.title("Telegram Daily Briefing")
    fg.link(href=os.environ.get("FEED_HOME", "https://example.com"))
    fg.description(f"{since.isoformat()} -> {until.isoformat()}")

    for d, src_name, mid, txt, link in items:
        e = fg.add_entry()
        e.id(f"tg:{src_name}:{mid}")
        e.title(f"[{src_name}] {txt.splitlines()[0][:120]}")
        e.link(href=link)
        e.published(d)
        e.updated(d)
        e.description(f"<a href='{link}'>Open</a><pre>{txt}</pre>")

    fg.rss_file("rss.xml")
    print(f"Wrote rss.xml with {len(items)} items.")


if __name__ == "__main__":
    asyncio.run(main())
