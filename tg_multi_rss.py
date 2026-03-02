import os, re, asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from feedgen.feed import FeedGenerator
from zoneinfo import ZoneInfo

def norm(s):
    return re.sub(r"^https?://|^t\.me/","",s.strip()).strip("/")

def window(now, tz_name, hhmm):
    tz=ZoneInfo(tz_name); h,m=map(int,hhmm.split(":"))
    now_l=now.astimezone(tz)
    end=now_l.replace(hour=h,minute=m,second=0,microsecond=0)
    if now_l<end: end-=timedelta(days=1)
    start=end-timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

async def main():
    api_id=int(os.environ["TG_API_ID"])
    api_hash=os.environ["TG_API_HASH"]
    sess=StringSession(os.environ["TG_SESSION_STRING"])
    sources=[norm(x) for x in os.environ["TG_SOURCES"].split(",") if x.strip()]
    since,until=window(datetime.now(timezone.utc),
                       os.environ.get("TZ_NAME","Europe/London"),
                       os.environ.get("BRIEFING_TIME","05:45"))
    items=[]
    async with TelegramClient(sess,api_id,api_hash) as c:
        for s in sources:
            ent=await c.get_entity(s)
            uname=getattr(ent,"username",None) or s
            async for m in c.iter_messages(ent):
                if not m.date: continue
                d=m.date.astimezone(timezone.utc)
                if d<since: break
                if d>=until: continue
                if not m.message: continue
                items.append((d,uname,m.id,m.message))
    items.sort(reverse=True)
    fg=FeedGenerator()
    fg.title("Telegram Daily Briefing")
    fg.link(href="https://example.com")
    fg.description(f"{since.isoformat()} -> {until.isoformat()}")
    for d,u,i,t in items:
        e=fg.add_entry()
        e.id(f"tg:{u}:{i}")
        e.title(f"[{u}] {t.splitlines()[0][:120]}")
        link=f"https://t.me/{u}/{i}"
        e.link(href=link)
        e.published(d); e.updated(d)
        e.description(f"<a href='{link}'>Open</a><pre>{t}</pre>")
    fg.rss_file("rss.xml")

if __name__=="__main__":
    asyncio.run(main())
