#!/usr/bin/env python3
"""Generate site/stats.json from Metricool pull. Snapshot for the Statistics tab.
Raw rows were pulled via the Metricool MCP for brand 4497778 (Yogalina),
period 2026-04-02 .. 2026-06-30. To refresh: re-pull and replace the arrays."""
import json, statistics
from datetime import datetime, timezone, timedelta

_IL = timezone(timedelta(hours=3))  # Asia/Jerusalem
_now = datetime.now(_IL)
PERIOD = {
    "from": "2026-04-02", "to": "2026-06-30", "label": "Apr 2 – Jun 30, 2026",
    "generatedAt": _now.isoformat(timespec="seconds"),
    "generatedAtLabel": _now.strftime("%b %-d, %Y · %H:%M"),
}

def f(v):  # parse "12.0"/None -> float/None
    return None if v is None else float(v)

# ---- Instagram: [followers, reach, reelViews, interactions, accountsEngaged, views, date] ----
IG = [["1287","45",None,None,"0","94","20260509"],["1286","596","3118","24","13","911","20260618"],["1286","146","221","6","2","233","20260419"],["1289","99",None,None,"4","387","20260508"],["1283","1211","3102","58","41","2279","20260617"],["1303","330","237","10","9","495","20260629"],["1285","147","231","4","4","224","20260418"],["1289","27",None,None,"1","43","20260507"],["1282","209","403","5","6","277","20260616"],["1303","411","227","11","13","612","20260628"],["1284","150",None,"4","3","262","20260417"],["1291","96","142","3","2","205","20260429"],["1279","127","163","8","4","205","20260615"],["1291","45",None,None,"1","91","20260506"],["1284","127","304","5","4","198","20260416"],["1302","430",None,None,"5","717","20260627"],["1279","253","396","16","9","357","20260614"],["1291","119","104","7","8","221","20260428"],["1285","188","216","7","6","533","20260415"],["1291","67","163","8","6","125","20260505"],["1281","127","214","4","3","148","20260609"],["1280","290",None,None,"3","405","20260613"],["1286","83","256","11","7","110","20260519"],["1292","85","83","4","3","155","20260430"],["1302","509",None,None,"9","865","20260626"],["1285","56",None,None,"6","223","20260414"],["1280","43",None,None,"0","64","20260608"],["1286","146","191","3","7","202","20260518"],["1285","47",None,None,"1","99","20260515"],["1288","1031","268","5","20","1784","20260619"],["1279","265","228","5","6","402","20260612"],["1291","106","120","4","4","199","20260427"],["1287","173","259","7","0","214","20260517"],["1285","185","235","7","9","402","20260409"],["1288","86","14","1","6","303","20260514"],["1285","130","220","3","1","167","20260413"],["1295","139","301","19","5","258","20260504"],["1285","178","214","7","2","213","20260412"],["1287","68","25","3","4","167","20260511"],["1285","131",None,None,"4","147","20260516"],["1281","21",None,None,"0","53","20260607"],["1288","35","15","2","0","48","20260513"],["1279","0","229","2","15","986","20260611"],["1302","1068","219","7","27","1903","20260625"],["1280","481","2035","39","8","626","20260610"],["1287","30","23","4","2","45","20260510"],["1286","216","350","4","2","339","20260408"],["1287","36","24","5","5","73","20260512"],["1291","262","89","3","7","388","20260423"],["1285","43",None,None,"5","68","20260411"],[None,"237","158","5","7","348","20260630"],["1284","40",None,None,"2","81","20260529"],["1284","25",None,None,"3","39","20260522"],["1290","1187","3050","42","29","2673","20260621"],["1291","101","114","5","3","182","20260426"],["1286","237","341","8","7","318","20260405"],["1285","31",None,None,"1","59","20260410"],["1281","23",None,None,"0","33","20260606"],["1290","286","125","7","12","447","20260422"],["1296","79","241","10","6","167","20260503"],["1285","56","118","5","4","91","20260521"],["1283","138","186","6","3","195","20260526"],["1286","212","297","15","14","743","20260407"],["1283","170","263","11","6","244","20260531"],["1289","733",None,None,"10","1211","20260620"],["1301","1292","4159","52","24","2121","20260624"],["1285","72","125","7","2","95","20260520"],["1281","94","195","4","5","150","20260603"],["1284","123","223","10","7","230","20260528"],["1283","50",None,None,"2","94","20260530"],["1290","1374","3979","108","59","2218","20260421"],["1291","147","80","6","9","232","20260425"],["1281","41",None,None,"0","63","20260605"],["1286","179","221","4","7","260","20260404"],["1288","180","239","8","6","234","20260406"],["1290","98",None,None,"10","233","20260420"],["1295","77","84","7","5","118","20260502"],["1284","179","404","8","6","280","20260527"],["1284","94","198","5","4","141","20260525"],["1281","64","130","4","3","97","20260604"],["1297","1615","269","9","47","2779","20260623"],["1281","295","404","5","2","392","20260602"],["1291","176","92","5","5","275","20260424"],["1284","245",None,None,"6","401","20260403"],["1293","111","111","6","6","208","20260501"],["1284","64","110","3","4","99","20260524"],["1295","794","2941","40","15","1360","20260622"],["1282","101","186","8","8","130","20260601"],["1284","417","264","6","11","633","20260402"],["1284","27",None,None,"2","53","20260523"]]

# ---- Facebook: [followers, posts, interactions, reach(null), reelViews, gained, lost, date] ----
FB = [["1821","1","1",None,"11","1","0","20260511"],["1822","1",None,None,"14","0","0","20260423"],["1822","1","4",None,"276","2","0","20260519"],["1823","1",None,None,"7","0","0","20260430"],["1827","1","1",None,"39","0","0","20260629"],["1820","1","1",None,"14","0","0","20260518"],["1820","1","2",None,"153","0","0","20260409"],["1827","1","1",None,"9","0","0","20260630"],["1822","1","1",None,"18","0","0","20260422"],["1820","2","1",None,"37","0","1","20260517"],["1827","1","2",None,"22","0","0","20260628"],["1820","2","3",None,"50","0","0","20260619"],["1820","1","1",None,"256","1","1","20260620"],["1821","1","1",None,"25","0","0","20260408"],["1822","1","2",None,"166","0","0","20260421"],["1826","1","3",None,"15","0","0","20260625"],["1820","1","1",None,"314","0","0","20260531"],["1819","1","3",None,"296","0","0","20260603"],["1821","1",None,None,"213","0","0","20260416"],["1820","1","1",None,"209","0","0","20260602"],["1820","1","2",None,"257","0","0","20260527"],["1820","1","1",None,"225","0","0","20260601"],["1826","1","8",None,"453","5","0","20260623"],["1820","1","2",None,"347","1","0","20260604"],["1820","4","13",None,"336","2","1","20260617"],["1821","1","1",None,"269","0","0","20260611"],["1821","1","22",None,"1572","1","0","20260622"],["1821","1","1",None,"231","0","0","20260406"],["1821","1","1",None,"165","0","0","20260413"]]

# ---- TikTok: [followers, videos, views, interactions, reach, newFollowers, profileViews, date] ----
TK = [["315",None,None,None,None,"0","0","20260509"],["304",None,None,None,None,"0","0","20260629"],["320",None,None,None,None,"0","0","20260402"],["304",None,None,None,None,"0",None,"20260630"],["318",None,None,None,None,"0","0","20260409"],["313",None,None,None,None,"0","0","20260520"],["308",None,None,None,None,"0","0","20260601"]]

# ---- YouTube (channel UCwaTl-mBfjXqLsxFWojL6EQ): [videoViews, subsGained, subsLost] per day ----
YT_SUBSCRIBERS = 993  # current subscribers (YTEV01, LAST)
YT = [[17,0,0],[116,0,0],[638,3,1],[12,0,0],[287,0,0],[10,0,0],[391,0,0],[240,3,0],[19,0,0],[16,0,0],[43,0,0],[485,0,0],[125,2,0],[17,0,0],[32,0,0],[770,0,1],[6,0,0],[771,2,0],[424,0,0],[24,0,0],[302,0,0],[174,0,0],[86,0,0],[16,0,0],[464,1,0],[51,0,0],[934,1,1],[61,0,0],[964,2,0],[122,0,0],[41,0,0],[1087,3,2],[31,0,0],[206,1,0],[56,0,0],[688,0,0],[26,0,0],[1929,7,1],[303,1,0],[184,2,0],[1372,0,0],[439,0,0],[123,1,0],[181,0,0],[166,2,0],[202,0,0],[258,0,0],[251,0,0],[360,1,1],[296,4,0],[1401,5,0],[668,3,0],[250,1,0],[535,4,0],[524,2,0],[258,0,0],[151,0,0],[53,0,0],[544,2,2],[715,1,0],[334,0,0],[142,0,0],[385,0,0],[362,1,0],[355,0,0],[203,0,0],[81,0,0],[349,1,0],[32,0,0],[426,0,0],[1168,1,1],[1402,8,0],[198,0,0],[0,0,0],[76,0,0],[201,0,0],[551,1,1],[342,0,0],[206,0,0],[277,1,0],[871,3,0],[563,1,0],[766,1,0],[23,1,0],[519,1,0],[237,1,1],[260,0,0],[120,0,0],[141,0,0]]

# ---- Top posts: [network, datetime, impressions, interactions, engagement, type] ----
POSTS = [["INSTAGRAM","20260624190049","4159","52",None,"REEL"],["INSTAGRAM","20260621080042","3050","42",None,"REEL"],["INSTAGRAM","20260618190045","3118","24",None,"REEL"],["INSTAGRAM","20260622190228","2941","40",None,"REEL"],["INSTAGRAM","20260617145222","2737","51",None,"REEL"],["INSTAGRAM","20260610193144","2035","39",None,"REEL"],["INSTAGRAM","20260629190317","237","10",None,"REEL"],["INSTAGRAM","20260628190257","227","11",None,"REEL"],["INSTAGRAM","20260616190143","403","5",None,"REEL"],["INSTAGRAM","20260602080223","404","5",None,"REEL"],["FACEBOOK","20260622194932","1572","22",None,"REEL"],["FACEBOOK","20260623194754","453","8",None,"REEL"],["FACEBOOK","20260604082536","347","2",None,"REEL"]]

def chrono(rows):
    return sorted(rows, key=lambda r: r[-1])

def first_last(rows, idx):
    vals = [(r[-1], f(r[idx])) for r in chrono(rows) if f(r[idx]) is not None]
    return (vals[0][1], vals[-1][1]) if vals else (None, None)

def total(rows, idx):
    return sum(f(r[idx]) or 0 for r in rows)

# Instagram summary
ig_first, ig_last = first_last(IG, 0)
ig = {
    "followers": int(ig_last), "followersChange": int(ig_last - ig_first),
    "reach": int(total(IG, 1)), "reelViews": int(total(IG, 2)),
    "interactions": int(total(IG, 3)),
    "posts": sum(1 for p in POSTS if p[0] == "INSTAGRAM"),  # from top-post pull (30d sample)
}
# Facebook
fb_first, fb_last = first_last(FB, 0)
fb = {
    "followers": int(fb_last), "followersChange": int(fb_last - fb_first),
    "reelViews": int(total(FB, 4)), "interactions": int(total(FB, 2)),
    "posts": int(total(FB, 1)),
}
# TikTok
tk_first, tk_last = first_last(TK, 0)
tk = {
    "followers": int(tk_last), "followersChange": int(tk_last - tk_first),
    "views": int(total(TK, 2)), "posts": int(total(TK, 1)),
}
# YouTube
yt = {
    "subscribers": YT_SUBSCRIBERS,
    "subscribersChange": int(sum(r[1] for r in YT) - sum(r[2] for r in YT)),
    "videoViews": int(sum(r[0] for r in YT)),
}

# Top posts (all networks) sorted by impressions
top = sorted(POSTS, key=lambda p: f(p[2]) or 0, reverse=True)[:6]
top_posts = [{
    "network": p[0].title(), "date": p[1][:8], "impressions": int(f(p[2])),
    "interactions": int(f(p[3]) or 0), "type": p[5].title(),
    "engRate": round((f(p[3]) or 0) / f(p[2]) * 100, 1) if f(p[2]) else 0,
} for p in top]

networks = [
    {"key": "instagram", "name": "Instagram", "handle": "@yogalina_yoga", "icon": "IG",
     "status": "strong", "statusLabel": "Working",
     "verdict": "22K reach against 1,300 followers means most of your reach is strangers finding you through the algorithm — and 3–4 breakout Reels drove the bulk of it while the rest idled. Distribution isn't the problem; conversion is. Reach barely moved your follower count (+19), so reverse-engineer what made those few Reels travel (the hook, the first 3 seconds, the topic), turn it into a repeatable series, and give viewers one clear reason to follow.",
     "metrics": [
        {"label": "Followers", "value": ig["followers"], "change": ig["followersChange"]},
        {"label": "Reach (90d)", "value": ig["reach"]},
        {"label": "Reel views (90d)", "value": ig["reelViews"]},
        {"label": "Interactions (90d)", "value": ig["interactions"]},
     ]},
    {"key": "youtube", "name": "YouTube", "handle": "Yogalina channel", "icon": "YT",
     "status": "strong", "statusLabel": "Growing",
     "verdict": "The quiet surprise: 31.5K views and +63 net subscribers in 90 days — more real growth than Instagram, with almost no new uploads. That means your back catalogue is being found through search and recommendations and keeps earning views for months, the opposite of a Reel that spikes and dies in 48 hours. This is compounding, evergreen reach. Lever: publish more searchable, class-length videos ('20-minute morning yoga', 'yoga for lower-back pain') — each one becomes a small annuity, not a one-day event.",
     "metrics": [
        {"label": "Subscribers", "value": yt["subscribers"], "change": yt["subscribersChange"]},
        {"label": "Video views (90d)", "value": yt["videoViews"]},
     ]},
    {"key": "facebook", "name": "Facebook", "handle": "Yogalina Page", "icon": "FB",
     "status": "warn", "statusLabel": "Underused",
     "verdict": "Your largest audience (1,827) yet barely 80 interactions in a quarter — it's real but dormant, and Facebook actively throttles cross-posted Reels so they land at a fraction of Instagram's reach. This isn't worth fresh energy: keep the auto-crosspost running for the free residual reach, but treat Facebook as an archive, not a channel to grow. Reviving it would mean native posts and Stories, not Instagram reruns.",
     "metrics": [
        {"label": "Followers", "value": fb["followers"], "change": fb["followersChange"]},
        {"label": "Reel views (90d)", "value": fb["reelViews"]},
        {"label": "Interactions (90d)", "value": fb["interactions"]},
        {"label": "Posts (90d)", "value": fb["posts"]},
     ]},
    {"key": "tiktok", "name": "TikTok", "handle": "@alinainnermastery", "icon": "TT",
     "status": "bad", "statusLabel": "Declining",
     "verdict": "You lost 16 followers by going silent — zero videos in 90 days. This is your biggest missed opportunity, not your weakest platform: TikTok rewards small, consistent accounts more than anywhere else, and you already produce the Reels. Cross-posting them 3–4× a week costs almost nothing and would likely out-reach Instagram in the calm-yoga niche. Either commit to that cadence or formally close it — a shrinking ghost account only dilutes the brand.",
     "metrics": [
        {"label": "Followers", "value": tk["followers"], "change": tk["followersChange"]},
        {"label": "Videos (90d)", "value": tk["posts"]},
        {"label": "Views (90d)", "value": tk["views"]},
     ]},
]

# Best time to post (Instagram) — peak is 20:00 every day
best_time = {"network": "Instagram", "peakHour": "20:00", "window": "19:00–21:00",
             "city": "Jerusalem", "timezone": "Israel · Jerusalem time",
             "note": "Every day of the week peaks in the evening. Mornings before 5am are dead.",
             "why": "A Reel's first hour decides how far it spreads. Posting when your audience is already online earns the early views, saves and watch-time that tell the algorithm to push it onto Explore."}

out = {
    "period": PERIOD,
    "headline": "You have two engines running — Instagram's reach and YouTube's quiet, compounding growth. The one real gap is TikTok, sitting idle while it would travel furthest.",
    "networks": networks,
    "topPosts": top_posts,
    "bestTime": best_time,
}

with open("site/stats.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
