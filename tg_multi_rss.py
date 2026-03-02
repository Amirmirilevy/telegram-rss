import os, re, asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from feedgen.feed import FeedGenerator
from zoneinfo import ZoneInfo

# Optional local .env support (safe in GitHub Actions too)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


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

    # If it's before today's briefing time, use yesterday at briefing time
    if now_local < end_local:
        end_local -= timedelta(days=1)

    start_local = end_local - timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def message_text(m) -> str:
    txt = (m.message or "").strip()
    if txt:
        return txt

    # Include media only posts too
    if getattr(m, "media", None):
        kind = type(m.media).__name__
        fname = getattr(getattr(m, "file", None), "name", None)
        if fname:
            return f"[media: {kind}] {fname}"
        return f"[media: {kind}]"

    return ""


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(
            f"Missing environment variable: {name}\n"
            f"If running locally, put it in a .env file.\n"
            f"If running on GitHub Actions, add it to repo Secrets."
        )
    return val


async def main():
    api_id = int(require_env("TG_API_ID"))
    api_hash = require_env("TG_API_HASH")
    sess = StringSession(require_env("TG_SESSION_STRING"))

    sources_raw = require_env("TG_SOURCES")
    sources = [norm(x) for x in sources_raw.split(",") if x.strip()]

    tz_name = os.environ.get("TZ_NAME", "Europe/London")
    briefing_time = os.environ.get("BRIEFING_TIME", "05:45")
    since, until = window(datetime.now(timezone.utc), tz_name, briefing_time)

    items = []
    per_source = {s: 0 for s in sources}

    async with TelegramClient(sess, api_id, api_hash) as c:
        for s in sources:
            try:
                ent = await c.get_entity(s)
            except Exception as e:
                print(f"[WARN] Cannot resolve source '{s}': {e}")
                continue

            uname = getattr(ent, "username", None)
            src_name = uname or s

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

                if uname:
                    link = f"https://t.me/{uname}/{m.id}"
                else:
                    link = "https://t.me/"

                items.append((d, src_name, m.id, txt, link))
                per_source[s] = per_source.get(s, 0) + 1

    items.sort(key=lambda x: x[0], reverse=True)

    # Console output (local visibility)
    print(f"Window (UTC): {since.isoformat()} -> {until.isoformat()}")
    for s in sources:
        print(f"{s}: {per_source.get(s, 0)} items")

    # Summary line embedded into RSS description
    counts_str = ", ".join([f"{s}: {per_source.get(s, 0)}" for s in sources])
    summary_line = f"{since.isoformat()} -> {until.isoformat()} | {counts_str}"

    fg = FeedGenerator()
    fg.title("Telegram Daily Briefing")
    fg.link(href=os.environ.get("FEED_HOME", "https://example.com"))
    fg.description(summary_line)

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
