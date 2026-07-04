"""
Metricool scheduling: OAuth token refresh, MCP tool calls, schedule_post_fn.
"""

import modal

from pipeline_core import (
    app, image, tmp_vol, TMP_DIR,
)

# ---------------------------------------------------------------------------
# Metricool scheduling — OAuth 2.1 to the hosted MCP + createScheduledPost
#
# Free-tier path: the MCP (https://ai.metricool.com/mcp) authorizes via OAuth
# (no Advanced-plan API token needed). This backend is the MCP client.
#   /oauth/start    → redirect Alina to Metricool to authorize (one-time)
#   /oauth/callback → exchange code, persist refresh token
#   /schedule       → refresh token → call createScheduledPost over MCP
# ---------------------------------------------------------------------------

MC_MCP_URL     = "https://ai.metricool.com/mcp"
MC_AUTHZ_URL   = "https://app.metricool.com/oauth/authorize"
MC_TOKEN_URL   = "https://app.metricool.com/oauth/token"
MC_CLIENT_ID   = "client_f7eb964add83410caaa9bf16812da4da"  # dynamically registered
MC_REDIRECT    = "https://yotamjacob--hebrew-video-pipeline-api.modal.run/oauth/callback"
MC_SCOPE       = "mcp:read mcp:write"
MC_BLOG_ID     = "4497778"
MC_PROTO       = "2025-06-18"  # MCP protocol version we request

# Persistent store for OAuth PKCE state + tokens (survives redeploys)
oauth_store = modal.Dict.from_name("metricool-oauth", create_if_missing=True)


def _mc_refresh_access_token(uid: str) -> str:
    """Swap a user's stored refresh token for a fresh access token; rotate if
    returned. Tokens are namespaced per uid so each user drives their own
    connected Metricool account."""
    import json, urllib.request, urllib.parse
    key = f"tokens:{uid}"
    toks = oauth_store.get(key)
    if not toks or not toks.get("refresh_token"):
        raise RuntimeError("not_connected")
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": toks["refresh_token"],
        "client_id": MC_CLIENT_ID,
    }).encode()
    req = urllib.request.Request(MC_TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    tr = json.loads(urllib.request.urlopen(req, timeout=30).read())
    access = tr.get("access_token")
    if not access:
        raise RuntimeError("no_access_token")
    if tr.get("refresh_token"):  # rotation
        toks["refresh_token"] = tr["refresh_token"]
        oauth_store[key] = toks
    return access


def _mcp_parse(resp):
    """Parse an MCP Streamable-HTTP response (JSON or SSE)."""
    import json
    ct = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ct:
        obj = None
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                d = line[5:].strip()
                if d and d != "[DONE]":
                    try:
                        obj = json.loads(d)
                    except json.JSONDecodeError:
                        pass
        return obj or {}
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError:
        return {"raw": resp.text}


def _mcp_open(access: str):
    """Open an MCP session: returns (rpc, session_headers). `rpc(payload,
    extra_headers)` posts a JSON-RPC message and returns the raw response."""
    import json, requests
    s = requests.Session()

    def rpc(payload, extra_headers=None):
        h = {
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if extra_headers:
            h.update(extra_headers)
        return s.post(MC_MCP_URL, headers=h, data=json.dumps(payload), timeout=90)

    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": MC_PROTO, "capabilities": {},
        "clientInfo": {"name": "hebrew-video-pipeline", "version": "1.0"}}})
    if init.status_code == 401:
        raise RuntimeError("unauthorized")
    sid = init.headers.get("Mcp-Session-Id") or init.headers.get("mcp-session-id")
    negotiated = (_mcp_parse(init).get("result", {}) or {}).get("protocolVersion", MC_PROTO)
    sess = {"MCP-Protocol-Version": negotiated}
    if sid:
        sess["Mcp-Session-Id"] = sid
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sess)
    return rpc, sess


def _mcp_tool_call(access: str, tool: str, arguments: dict) -> dict:
    """Minimal MCP Streamable-HTTP client: initialize → initialized → tools/call."""
    rpc, sess = _mcp_open(access)
    resp = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}, sess)
    return _mcp_parse(resp)


def _mc_fetch_brands(access: str) -> list:
    """Discover the connected account's brands/blogs so each user posts to
    their OWN Metricool brand (never a shared hardcoded one).

    The brand-listing tool name isn't hardcoded — we read tools/list and pick
    the tool whose name looks like a brand/blog lister, then normalise whatever
    id/label fields it returns to `[{"id": ..., "label": ...}]`. Best-effort:
    returns [] if nothing usable is found."""
    import json
    rpc, sess = _mcp_open(access)
    listed = _mcp_parse(rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, sess))
    tools = (listed.get("result", {}) or {}).get("tools", []) or []
    names = [t.get("name", "") for t in tools]
    # Prefer a read/list tool that mentions brand/blog.
    cand = None
    for n in names:
        low = n.lower()
        if ("brand" in low or "blog" in low) and ("get" in low or "list" in low):
            cand = n
            break
    if not cand:
        for n in names:
            if "brand" in n.lower() or "blog" in n.lower():
                cand = n
                break
    if not cand:
        return []

    res = _mcp_parse(rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": cand, "arguments": {}}}, sess))
    result = res.get("result", {}) or {}
    txt = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    try:
        data = json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        return []
    rows = data if isinstance(data, list) else (data.get("brands") or data.get("blogs") or data.get("data") or [])
    brands = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        bid = r.get("id") or r.get("blogId") or r.get("brandId") or r.get("uid")
        if bid is None:
            continue
        label = r.get("label") or r.get("name") or r.get("title") or str(bid)
        brands.append({"id": str(bid), "label": str(label)})
    return brands


def _build_metricool_info(p: dict) -> dict:
    """Assemble the Metricool `info` object from the site payload."""
    platforms = p.get("platforms", [])
    info = {
        "autoPublish": bool(p.get("autoPublish", False)),  # off = user approves in Metricool
        "draft": bool(p.get("draft", False)),
        "text": p.get("caption", ""),
        "media": [p["videoUrl"]] if p.get("videoUrl") else [],
        "mediaAltText": [],
        "descendants": [],
        "providers": [{"network": n} for n in platforms],
        "publicationDate": {"dateTime": p["dateTime"], "timezone": p.get("timezone", "Asia/Jerusalem")},
    }
    if "instagram" in platforms:
        info["instagramData"] = {"type": "REEL", "showReelOnFeed": True}
    if "facebook" in platforms:
        info["facebookData"] = {"type": "REEL"}
    if "tiktok" in platforms:
        info["tiktokData"] = {"privacyOption": "PUBLIC_TO_EVERYONE"}
    if "youtube" in platforms:
        info["youtubeData"] = {
            "title": p.get("ytTitle", ""), "type": "short",
            "privacy": p.get("ytPrivacy", "public"),
            "madeForKids": bool(p.get("ytKids", False)),
        }
    return info


@app.function(image=image, timeout=120, volumes={TMP_DIR: tmp_vol})
def schedule_post_fn(payload_json: str, uid: str) -> dict:
    import json
    p = json.loads(payload_json)
    try:
        access = _mc_refresh_access_token(uid)
    except Exception as e:
        return {"error": f"auth: {e}"}

    # Resolve the user's OWN Metricool brand — discover it lazily the first
    # time and cache it on their token record. Never fall back to a shared
    # brand id, or one user's posts would land on another's account.
    toks = oauth_store.get(f"tokens:{uid}") or {}
    blog_id = p.get("blogId") or toks.get("blog_id")
    if not blog_id:
        try:
            brands = _mc_fetch_brands(access)
        except Exception as e:
            return {"error": f"brands: {e}"}
        if brands:
            blog_id = brands[0]["id"]
            toks["blog_id"] = blog_id
            toks["brands"] = brands
            oauth_store[f"tokens:{uid}"] = toks
    if not blog_id:
        return {"error": "no_brand"}

    # Build date (with Israel offset) + info
    dt = p["dateTime"]                       # "YYYY-MM-DDTHH:MM:SS"
    date_with_tz = dt + "+03:00"
    info = _build_metricool_info(p)

    try:
        res = _mcp_tool_call(access, "createScheduledPost",
                             {"date": date_with_tz, "blogId": blog_id,
                              "info": json.dumps(info, ensure_ascii=False)})
    except Exception as e:
        return {"error": f"mcp: {e}"}

    if res.get("error"):
        return {"error": f"metricool: {json.dumps(res['error'])[:300]}"}
    result = res.get("result", {})
    if result.get("isError"):
        txt = "".join(c.get("text", "") for c in result.get("content", []))
        return {"error": f"metricool: {txt[:300]}"}
    txt = "".join(c.get("text", "") for c in result.get("content", []))
    try:
        parsed = json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        parsed = {"raw": txt}

    # NOTE: no cleanup here — burned outputs are kept for the History tab and
    # pruned by pipeline_fns.prune_volume() after JOB_RETENTION_DAYS.
    return {"ok": True, "post": parsed}


