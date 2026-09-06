"""Blog system backend for I Love Tools.

Blogs are stored in the GitHub repo (branch `main`) so GitHub Pages serves
each post as a real, SEO-friendly page at https://ilovetools.pro/blogs/<slug>.html

Required env vars on Vercel:
  GH_TOKEN         - GitHub Personal Access Token (classic PAT, repo scope)
  BLOG_ADMIN_USER  - admin username for the hidden editor
  BLOG_ADMIN_PASS  - admin password (also used as HMAC secret for login tokens)
Optional:
  GH_OWNER / GH_REPO - defaults to silent5441/tools-site
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
import urllib.error
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional

from fastapi.responses import HTMLResponse

OWNER = os.environ.get("GH_OWNER", "silent5441")
REPO = os.environ.get("GH_REPO", "tools-site")
BRANCH = "main"
GH_TOKEN = os.environ.get("GH_TOKEN", "")
SECRET = os.environ.get("BLOG_ADMIN_PASS", "change-me-blog-admin")
ADMIN_USER = os.environ.get("BLOG_ADMIN_USER", "admin")
SITE = "https://ilovetools.pro"
API_BASE = "https://tools-site-backend.vercel.app"

router = APIRouter()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


# ---------------------------------------------------------------- github api
def gh(method, url, data=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer %s" % GH_TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as r:
            raw = r.read()
        try:
            return json.loads(raw.decode()), None
        except Exception:
            return {"_raw": raw.decode()}, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:400]
        except Exception:
            detail = ""
        return None, "HTTP %s: %s" % (e.code, detail)


def gh_commit(files, message):
    """Commit `files` (list of {"path": str, "content": str}) in one commit.

    Supports deletion with content=None (sha null in the tree)."""
    if not GH_TOKEN:
        raise HTTPException(status_code=500, detail="GH_TOKEN is not configured on the server")
    api = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
    last_err = None
    for _attempt in range(3):
        try:
            committed = _gh_commit_once(api, files, message)
            return committed
        except HTTPException as e:
            last_err = e
            if "GitRPC::BadObjectState" in str(e.detail) or "BadObjectState" in str(e.detail):
                time.sleep(1.2)
                continue
            raise
    raise last_err


def _gh_commit_once(api, files, message):
    head, err = gh("GET", "%s/git/refs/heads/%s" % (api, BRANCH))
    if err:
        raise HTTPException(status_code=500, detail="GitHub ref error: %s" % err)
    head_sha = head["object"]["sha"]
    commit, err = gh("GET", "%s/git/commits/%s" % (api, head_sha))
    if err:
        raise HTTPException(status_code=500, detail="GitHub commit error: %s" % err)
    base_tree = commit["tree"]["sha"]
    blobs = []
    for f in files:
        if f.get("content") is None:
            continue
        if f.get("is_b64"):
            body = {"content": f["content"], "encoding": "base64"}
        else:
            body = {"content": base64.b64encode(f["content"].encode()).decode(), "encoding": "base64"}
        blob, err = gh(
            "POST",
            "%s/git/blobs" % api,
            body,
        )
        if err:
            raise HTTPException(status_code=500, detail="GitHub blob error: %s" % err)
        blobs.append({"path": f["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for f in files:
        if f.get("content") is None:
            blobs.append({"path": f["path"], "mode": "100644", "type": "blob", "sha": None})
    tree, err = gh("POST", "%s/git/trees" % api, {"base_tree": base_tree, "tree": blobs})
    if err:
        raise HTTPException(status_code=500, detail="GitHub tree error: %s" % err)
    new_commit, err = gh(
        "POST",
        "%s/git/commits" % api,
        {"message": message, "tree": tree["sha"], "parents": [head_sha]},
    )
    if err:
        raise HTTPException(status_code=500, detail="GitHub commit error: %s" % err)
    _, err = gh(
        "PATCH",
        "%s/git/refs/heads/%s" % (api, BRANCH),
        {"sha": new_commit["sha"], "force": False},
    )
    if err:
        raise HTTPException(status_code=500, detail="GitHub ref update error: %s" % err)
    return new_commit["sha"]


def read_repo_file(path):
    j, err = gh(
        "GET", "https://api.github.com/repos/%s/%s/contents/%s" % (OWNER, REPO, quote(path))
    )
    if err:
        if "404" in err:
            return None
        raise HTTPException(status_code=500, detail="GitHub read error: %s" % err)
    try:
        return base64.b64decode(j.get("content", "")).decode("utf-8")
    except Exception:
        return j.get("content", "")


def read_index():
    data = read_repo_file("blogs/index.json")
    if not data:
        return []
    try:
        posts = json.loads(data)
        if not isinstance(posts, list):
            return []
        return posts
    except Exception:
        return []


# ---------------------------------------------------------------- auth
def make_token(username):
    exp = int(time.time()) + 60 * 60 * 24 * 30
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "e": exp}).encode()
    ).decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return payload + "." + sig


def verify_token(token):
    try:
        payload, sig = token.split(".")
        exp_sig = base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(sig, exp_sig):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data.get("e", 0) > int(time.time())
    except Exception:
        return False


def require_admin(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin token")
    if not verify_token(authorization[7:]):
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/admin/login")
def admin_login(body: LoginBody):
    if SECRET == "change-me-blog-admin":
        raise HTTPException(status_code=500, detail="BLOG_ADMIN_PASS not configured on server")
    if body.username == ADMIN_USER and hmac.compare_digest(body.password, SECRET):
        return {"token": make_token(body.username), "user": body.username, "expires_in": 60 * 60 * 24 * 30}
    raise HTTPException(status_code=401, detail="Invalid username or password")


# ---------------------------------------------------------------- helpers
def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:60] if s else "post").rstrip("-")


def gather_assets(slug, cover, blocks):
    """Replace base64 data-URIs with committed asset files.

    Returns (new_cover, new_blocks, asset_paths, asset_files)."""
    map_src = {}
    files = []
    candidates = []
    if cover and cover.startswith("data:"):
        candidates.append(("cover", cover))
    for b in blocks:
        if b.get("type") in ("image",) and isinstance(b.get("src"), str) and b["src"].startswith("data:"):
            candidates.append(("image", b["src"]))
        if b.get("type") == "gallery":
            for img in b.get("images", []):
                if isinstance(img.get("src"), str) and img["src"].startswith("data:"):
                    candidates.append(("gallery", img["src"]))
    counter = 0
    for kind, src in candidates:
        if src in map_src:
            continue
        try:
            hdr, b64 = src.split(",", 1)
            if ";base64" not in hdr:
                continue
            mime = hdr.split(";")[0].split(":")[1]
            ext = MIME_EXT.get(mime, "png")
            raw = base64.b64decode(b64)
        except Exception:
            continue
        counter += 1
        name = ("cover" if kind == "cover" else "img%d" % counter) + "." + ext
        path = "blogs/assets/%s/%s" % (slug, name)
        map_src[src] = "/%s" % path
        files.append({"path": path, "content": b64, "is_b64": True})
    new_cover = map_src.get(cover, cover) if cover else None
    new_blocks = []
    for b in blocks:
        b = dict(b)
        if b.get("type") == "image" and b.get("src") in map_src:
            b["src"] = map_src[b["src"]]
        if b.get("type") == "gallery":
            imgs = []
            for img in b.get("images", []):
                img = dict(img)
                if img.get("src") in map_src:
                    img["src"] = map_src[img["src"]]
                imgs.append(img)
            b["images"] = imgs
        new_blocks.append(b)
    asset_paths = [f["path"] for f in files]
    return new_cover, new_blocks, asset_paths, files


def render_post(meta, blocks, cover_url):
    accent = meta.get("design", {}).get("accent", "#6c5ce7")
    accent2 = shade2(accent)
    body_html, toc = render_blocks(blocks, accent)
    title = meta["title"]
    excerpt = meta.get("excerpt", "") or ""
    slug = meta["slug"]
    date = meta.get("date", "")
    site = SITE
    url = "%s/blogs/%s.html" % (site, slug)
    cat = meta.get("category", "Blog")

    date_parts = date.split("-")[:3]
    try:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        nice_date = "%s %s, %s" % (months[int(date_parts[1]) - 1], int(date_parts[2]), date_parts[0])
    except Exception:
        nice_date = date

    cover_html = ""
    if cover_url:
        cover_html = '<img class="cover" src="%s" alt="%s">' % (
            html_escape(cover_url),
            html_escape(title),
        )

    tags_html = "".join(
        '<span class="tag">%s</span>' % html_escape(t) for t in meta.get("tags", [])
    )

    design = meta.get("design", {}) or {}
    seo_title = html_escape((design.get("metaTitle") or "").strip() or title)
    seo_desc = html_escape((design.get("metaDesc") or "").strip() or excerpt or ("%s - I Love Tools" % title))

    return ARTICLE_TEMPLATE.format(
        title=seo_title,
        description=seo_desc,
        url=url,
        site=site,
        accent=accent,
        accent2=accent2,
        cat=html_escape(cat),
        display_cat=html_escape(cat),
        nice_date=nice_date,
        author=html_escape(meta.get("author", "I Love Tools")),
        tags=tags_html,
        cover=cover_html,
        slug=slug,
        content=body_html,
        site_title="I" + "\u2764" + "Tools Blog",
        api=API_BASE,
    )


def shade2(hexcol):
    try:
        h = hexcol.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r = min(255, r + 40)
        g = min(255, g + 40)
        b = min(255, b + 40)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return "#00b4d8"


def html_escape(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def inline_md(text):
    text = html_escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


ICON_SVG = {
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "tip": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/>',
    "success": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/>',
    "warning": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "idea": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/>',
    "heart": '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "rocket": '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    "code": '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "cloud": '<path d="M17.5 19a4.5 4.5 0 1 0-2.4-8.3 6.5 6.5 0 1 0-12.05 4.4A4 4 0 0 0 6 19z"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><polyline points="22,6 12,13 2,6"/>',
    "bell": '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "fire": '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3 1.072-2.143 2.5-4 2.492-6 .166 1.978 1.882 3.143 3 4.5 1.5 1.84 2.03 4.5 1.48 6.2a6.5 6.5 0 1 1-8.472.8z"/>',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>',
    "palette": '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
    "camera": '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    "mic": '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>',
    "gamepad": '<line x1="6" y1="12" x2="10" y2="12"/><line x1="8" y1="10" x2="8" y2="14"/><line x1="15" y1="13" x2="15.01" y2="13"/><line x1="18" y1="11" x2="18.01" y2="11"/><rect x="2" y="6" width="20" height="12" rx="2"/>',
    "cart": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "puzzle": '<path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073-.05-.336.062-.676.303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02z"/>',
    "key": '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
    "settings": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "snowflake": '<line x1="12" y1="2" x2="12" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><path d="m4.93 4.93 14.14 14.14"/><path d="m19.07 4.93-14.14 14.14"/>',
    "dollar": '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "gift": '<polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/>',
    "film": '<rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/>',
    "wifi": '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "external": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
}


def collect_headings(blocks):
    seen = {}
    out = []
    for b in blocks:
        if b.get("type") != "heading":
            out.append(None)
            continue
        plain = "-".join(re.findall(r"[a-z0-9]+", str(b.get("text", "")).lower()))
        plain = (plain or "section")[:42].rstrip("-")
        n = seen.get(plain, 0) + 1
        seen[plain] = n
        hid = "s-%s%s" % (plain, "" if n == 1 else "-%d" % n)
        out.append({"id": hid, "level": min(6, max(1, int(b.get("level", 2)))), "text": b.get("text", "")})
    return out


def render_callout_icon(icon):
    if not icon:
        return ""
    inner = ICON_SVG.get(icon)
    if inner:
        return (
            '<svg class="co-svg" width="26" height="26" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">%s</svg>' % inner
        )
    return html_escape(icon)


def render_blocks(blocks, accent):
    html = []
    headings = collect_headings(blocks)
    hi = 0
    for b in blocks:
        t = b.get("type")
        try:
            if t == "heading":
                lvl = min(6, max(1, int(b.get("level", 2))))
                align = b.get("align", "left")
                hinfo = headings[hi]
                hid = ' id="%s"' % hinfo["id"] if hinfo else ""
                txt = inline_md(b.get("text", ""))
                html.append(
                    '<h%d class="blog-h%d"%s style="text-align:%s">%s</h%d>' % (lvl, lvl, hid, align, txt, lvl)
                )
                hi += 1
            elif t == "text":
                p = inline_md(b.get("text", ""))
                if p:
                    html.append('<p class="blog-p">%s</p>' % p)
            elif t == "image":
                src = b.get("src", "")
                cap = html_escape(b.get("caption", "") or "")
                cap_html = "<figcaption>%s</figcaption>" % cap if cap else ""
                align = b.get("align", "center")
                width = b.get("width", "full")
                figs = ["blog-fig"]
                if width in ("natural", "wide"):
                    figs.append("blog-fig-" + width)
                if align in ("left", "right"):
                    figs.append("blog-fig-" + align)
                html.append(
                    '<figure class="%s" style="text-align:%s"><img loading="lazy" src="%s" alt="%s">%s</figure>'
                    % (" ".join(figs), align, html_escape(src), html_escape(b.get("alt", "") or ""), cap_html)
                )
            elif t == "code":
                lang = html_escape(b.get("lang", "")) or "code"
                fname = html_escape(b.get("filename", "") or "")
                fh = '<span>%s</span>' % fname if fname else '<span>%s</span>' % lang
                html.append(
                    '<div class="blog-code"><div class="code-head">%s<button class="copy-btn" onclick="copyCode(this)">Copy</button></div><pre><code>%s</code></pre></div>'
                    % (fh, html_escape(b.get("code", "")))
                )
            elif t == "video":
                vid = youtube_embed(b.get("url", ""))
                if vid:
                    html.append('<div class="video-box"><iframe src="%s" loading="lazy" allowfullscreen></iframe></div>' % vid)
            elif t == "list":
                items = b.get("items", [])
                tag = "ol" if b.get("ordered") else "ul"
                lis = "".join("<li>%s</li>" % inline_md(str(i)) for i in items)
                html.append('<%s class="blog-list">%s</%s>' % (tag, lis, tag))
            elif t == "quote":
                q = inline_md(b.get("text", ""))
                cite = html_escape(b.get("cite", "") or "")
                c = "<div class=\"quote-cite\">%s</div>" % cite if cite else ""
                html.append('<blockquote class="blog-quote">%s%s</blockquote>' % (q, c))
            elif t == "divider":
                html.append('<hr class="blog-divider">')
            elif t == "link":
                text = inline_md(b.get("text", "") or b.get("url", ""))
                href = html_escape(b.get("url", ""))
                style = b.get("style", "primary")
                html.append(
                    '<div class="blog-link-line"><a class="blog-btn %s" href="%s" target="_blank" rel="noopener">%s</a></div>'
                    % (style, href, text)
                )
            elif t == "gallery":
                imgs = "".join(
                    '<img loading="lazy" src="%s" alt="%s">' % (html_escape(i.get("src", "")), html_escape(i.get("alt", "") or ""))
                    for i in b.get("images", [])
                )
                html.append('<div class="blog-gallery" style="grid-template-columns:repeat(%s,1fr)">%s</div>' % (min(3, max(1, int(b.get("columns", 2)))), imgs))
            elif t == "html":
                html.append('<div class="raw-embed">%s</div>' % b.get("code", ""))
            elif t == "callout":
                txt = inline_md(b.get("text", ""))
                icon = render_callout_icon(b.get("icon", ""))
                html.append('<div class="blog-callout"><span class="co-icon">%s</span><div>%s</div></div>' % (icon, txt))
            elif t == "table":
                head = b.get("head", []) or []
                rows = b.get("rows", []) or []
                th = "".join("<th>%s</th>" % html_escape(str(c)) for c in head)
                trs = "".join(
                    "<tr>%s</tr>" % "".join("<td>%s</td>" % inline_md(str(c)) for c in r)
                    for r in rows
                )
                html.append(
                    '<div class="blog-table-wrap"><table class="blog-table"><thead><tr>%s</tr></thead>'
                    "%s</table></div>" % (th, "<tbody>%s</tbody>" % trs if trs else "")
                )
            elif t == "faq":
                items = b.get("items", []) or []
                faqs = "".join(
                    '<details class="blog-faq"><summary>%s</summary><div class="faq-body">%s</div></details>'
                    % (inline_md(o.get("q", "") or "Question"), inline_md(o.get("a", "") or ""))
                    for o in items
                )
                html.append('<div class="blog-faqs">%s</div>' % faqs)
            elif t == "steps":
                items = [str(i) for i in (b.get("items", []) or [])]
                lis = "".join("<li>%s</li>" % inline_md(i) for i in items if i.strip())
                html.append('<ol class="blog-steps">%s</ol>' % lis)
            elif t == "stats":
                items = b.get("items", []) or []
                cards = ""
                for it in items:
                    icon = render_callout_icon(it.get("icon", ""))
                    ico = '<div class="st-ico">%s</div>' % icon if icon else ""
                    cards += '<div class="blog-stat">%s<div class="sv">%s</div><div class="sl">%s</div></div>' % (
                        ico,
                        inline_md(str(it.get("value", "")) or "—"),
                        html_escape(str(it.get("label", "")) or ""),
                    )
                html.append('<div class="blog-stats">%s</div>' % cards)
            elif t == "embed":
                url = html_escape(b.get("url", "") or "")
                if url.startswith("http"):
                    html.append(
                        '<div class="blog-embed"><iframe src="%s" loading="lazy" allowfullscreen referrerpolicy="no-referrer"></iframe></div>' % url
                    )
            elif t == "toc":
                links = []
                for h in headings:
                    if not h:
                        continue
                    lvl = h["level"]
                    links.append(
                        '<a class="lvl%d" href="#%s">%s</a>' % (min(3, lvl), h["id"], inline_md(h["text"]))
                    )
                if links:
                    title = html_escape(b.get("title", "") or "On this page")
                    html.append(
                        '<div class="blog-toc"><div class="toc-title">%s</div>%s</div>' % (title, "".join(links))
                    )
        except Exception:
            pass
    return "".join(html), None


def youtube_embed(url):
    if not url:
        return None
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{6,15})", url)
    if m:
        return "https://www.youtube-nocookie.com/embed/%s" % m.group(1)
    if "youtube.com/embed/" in url:
        return url.replace("http://", "https://")
    return None


# ---------------------------------------------------------------- endpoints
class BlogCreate(BaseModel):
    title: str
    slug: Optional[str] = None
    excerpt: str = ""
    category: str = "Blog"
    tags: list = []
    author: str = "I Love Tools"
    cover: Optional[str] = None
    design: dict = {}
    blocks: list = []
    draft: bool = False


@router.get("/api/blogs")
def list_blogs():
    posts = read_index()
    posts.sort(key=lambda p: p.get("date", ""), reverse=True)
    return posts


@router.get("/api/blogs/{slug}/comments")
def get_comments(slug: str):
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    data = read_repo_file("blogs/comments/%s.json" % slug)
    if not data:
        return []
    try:
        out = json.loads(data)
        return out if isinstance(out, list) else []
    except Exception:
        return []


class CommentBody(BaseModel):
    name: str
    text: str


@router.post("/api/blogs/{slug}/comments")
def post_comment(slug: str, body: CommentBody):
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    name = (body.name or "").strip()[:60]
    text = (body.text or "").strip()[:1000]
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not text:
        raise HTTPException(status_code=400, detail="Comment text is required")
    exists = any(p.get("slug") == slug for p in read_index())
    if not exists:
        raise HTTPException(status_code=404, detail="Blog not found")
    file_path = "blogs/comments/%s.json" % slug
    existing = read_repo_file(file_path)
    try:
        comments = json.loads(existing) if existing else []
        if not isinstance(comments, list):
            comments = []
    except Exception:
        comments = []
    comments.append(
        {
            "id": int(time.time() * 1000),
            "name": name,
            "text": text,
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    gh_commit(
        [{"path": file_path, "content": json.dumps(comments, indent=2)}],
        "Add comment to " + slug,
    )
    return {"ok": True, "count": len(comments)}


@router.delete("/api/blogs/{slug}/comments/{cid}")
def delete_comment(
    slug: str, cid: int, authorization: Optional[str] = Header(None)
):
    require_admin(authorization)
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    file_path = "blogs/comments/%s.json" % slug
    existing = read_repo_file(file_path)
    try:
        comments = json.loads(existing) if existing else []
        if not isinstance(comments, list):
            comments = []
    except Exception:
        comments = []
    before = len(comments)
    comments = [c for c in comments if int(c.get("id", 0)) != int(cid)]
    if len(comments) == before:
        raise HTTPException(status_code=404, detail="Comment not found")
    payload = [{"path": file_path, "content": None}] if not comments else [
        {"path": file_path, "content": json.dumps(comments, indent=2)}
    ]
    gh_commit(payload, "Delete comment from " + slug)
    return {"ok": True, "count": len(comments)}


@router.post("/api/blogs")
def create_blog(body: BlogCreate, authorization: Optional[str] = Header(None)):
    require_admin(authorization)
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    slug = body.slug or slugify(title)
    slug = re.sub(r"[^a-z0-9-]", "", slug.lower())[:60].strip("-") or slugify(title)
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug (use a-z, 0-9, -)")

    idx = read_index()
    for p in idx:
        if p.get("slug") == slug:
            return update_blog_inner(slug, body)

    new_cover, new_blocks, asset_paths, asset_files = gather_assets(
        slug, body.cover, body.blocks
    )
    meta = {
        "slug": slug,
        "title": title,
        "excerpt": (body.excerpt or "")[:300],
        "category": (body.category or "Blog")[:40],
        "tags": [str(t)[:30] for t in body.tags][:8],
        "author": (body.author or "I Love Tools")[:60],
        "date": time.strftime("%Y-%m-%d"),
        "updated": time.strftime("%Y-%m-%d"),
        "cover": new_cover,
        "design": body.design or {},
        "assets": asset_paths,
        "blocks": new_blocks,
        "draft": bool(body.draft),
    }
    html = render_post(meta, new_blocks, new_cover)
    idx.append(meta)
    files = asset_files + [
        {"path": "blogs/index.json", "content": json.dumps(idx, indent=2)},
        {"path": "blogs/%s.html" % slug, "content": html},
    ]
    gh_commit(files, "Post blog: %s" % title)
    return {"ok": True, "slug": slug, "url": "%s/blogs/%s.html" % (SITE, slug)}


@router.put("/api/blogs/{slug}")
def update_blog(slug: str, body: BlogCreate, authorization: Optional[str] = Header(None)):
    require_admin(authorization)
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    return update_blog_inner(slug, body)


def update_blog_inner(slug, body):
    idx = read_index()
    found = None
    for p in idx:
        if p.get("slug") == slug:
            found = p
            break
    if not found:
        raise HTTPException(status_code=404, detail="Blog not found")
    title = (body.title or "").strip() or found["title"]

    new_cover, new_blocks, asset_paths, asset_files = gather_assets(
        slug, body.cover, body.blocks
    )
    old_assets = found.get("assets", [])
    for old in old_assets:
        if old not in asset_paths:
            asset_files.append({"path": old, "content": None})
    meta = dict(found)
    meta.update(
        {
            "title": title,
            "excerpt": (body.excerpt or "")[:300],
            "category": (body.category or "Blog")[:40],
            "tags": [str(t)[:30] for t in body.tags][:8],
            "author": (body.author or "I Love Tools")[:60],
            "updated": time.strftime("%Y-%m-%d"),
            "cover": new_cover,
            "design": body.design or {},
            "assets": asset_paths,
            "blocks": new_blocks,
            "draft": bool(body.draft),
        }
    )
    html = render_post(meta, new_blocks, new_cover)
    idx = [p for p in idx if p.get("slug") != slug]
    idx.append(meta)
    files = asset_files + [
        {"path": "blogs/index.json", "content": json.dumps(idx, indent=2)},
        {"path": "blogs/%s.html" % slug, "content": html},
    ]
    gh_commit(files, "Update blog: %s" % title)
    return {"ok": True, "slug": slug, "url": "%s/blogs/%s.html" % (SITE, slug)}


@router.delete("/api/blogs/{slug}")
def delete_blog(slug: str, authorization: Optional[str] = Header(None)):
    require_admin(authorization)
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    idx = read_index()
    found = None
    for p in idx:
        if p.get("slug") == slug:
            found = p
            break
    if not found:
        raise HTTPException(status_code=404, detail="Blog not found")
    files = [{"path": "blogs/%s.html" % slug, "content": None}]
    cmts_path = "blogs/comments/%s.json" % slug
    if read_repo_file(cmts_path) is not None:
        files.append({"path": cmts_path, "content": None})
    for a in found.get("assets", []):
        files.append({"path": a, "content": None})
    idx = [p for p in idx if p.get("slug") != slug]
    files.append({"path": "blogs/index.json", "content": json.dumps(idx, indent=2)})
    gh_commit(files, "Delete blog: %s" % found["title"])
    return {"ok": True}


@router.get("/api/blogs/{slug}")
def get_blog(slug: str, format: str = "json"):
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug")
    idx = read_index()
    for p in idx:
        if p.get("slug") == slug:
            if format == "html":
                data = read_repo_file("blogs/%s.html" % slug)
                if data is None:
                    raise HTTPException(status_code=404, detail="Blog not found")
                return data
            return p
    raise HTTPException(status_code=404, detail="Blog not found")


ARTICLE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - I❤Tools Blog</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="I❤Tools.pro">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/x-icon" href="/favicon.ico"><link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--bg:#f8f9fc;--card:#ffffff;--border:#e2e5ec;--text:#1a1d2e;--muted:#6b7085;--accent:{accent};--accent2:{accent2};--gradient:linear-gradient(135deg,{accent},{accent2});--radius:16px;--shadow:0 8px 32px rgba(0,0,0,.08)}}
[data-theme=dark]{{--bg:#0f1117;--card:#1a1d2e;--border:#2a2d3e;--text:#e8eaf0;--muted:#8b8fa3;--shadow:0 8px 32px rgba(0,0,0,.3)}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.7}}
a{{color:{accent};text-decoration:none}}
img{{max-width:100%}}
.nav{{position:sticky;top:0;z-index:100;background:rgba(248,249,252,.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border)}}
[data-theme=dark] .nav{{background:rgba(15,17,23,.85)}}
.nav-inner{{max-width:1000px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:60px;padding:0 20px}}
.logo{{font-weight:800;font-size:1.15rem;color:var(--text);display:flex;align-items:center;gap:8px}}
.logo .lg{{width:32px;height:32px;border-radius:9px;background:var(--gradient);display:flex;align-items:center;justify-content:center;color:#fff;font-size:1rem}}
.nav-links{{display:flex;gap:4px;align-items:center}}
.nav-links a{{font-size:.85rem;font-weight:600;padding:7px 14px;border-radius:8px;color:var(--muted)}}
.nav-links a:hover{{color:var(--text);background:rgba(108,92,231,.08)}}
.theme-toggle{{width:38px;height:38px;border-radius:50%;border:1px solid var(--border);background:var(--card);cursor:pointer;display:flex;align-items:center;justify-content:center;margin-left:6px}}
.thumb{{max-width:1000px;margin:0 auto;padding:36px 20px 80px}}
.back{{display:inline-flex;align-items:center;gap:6px;font-size:.85rem;color:var(--muted);margin-bottom:22px;font-weight:600}}
.back:hover{{color:var(--accent)}}
.cat-pill{{display:inline-block;background:var(--gradient);color:#fff;font-size:.75rem;font-weight:700;padding:5px 14px;border-radius:50px;letter-spacing:.4px}}
h1.title{{font-size:clamp(1.7rem,4.5vw,2.6rem);line-height:1.25;font-weight:800;margin:16px 0 12px}}
.meta{{color:var(--muted);font-size:.86rem;display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}}
.tags{{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 20px}}
.tag{{font-size:.72rem;font-weight:600;background:var(--card);border:1px solid var(--border);color:var(--muted);padding:4px 11px;border-radius:50px}}
.cover{{width:100%;border-radius:var(--radius);box-shadow:var(--shadow);margin:18px 0 26px;display:block;object-fit:cover;max-height:520px}}
.blog-body{{font-size:1.06rem}}
.blog-body .blog-h1,.blog-h2,.blog-h3,.blog-h4{{font-weight:800;line-height:1.3;margin:28px 0 12px}}
.blog-body ::selection{{background:rgba(108,92,231,.2)}}
.blog-p{{margin:14px 0;color:var(--text)}}
.blog-inline-code,.blog-body code{{font-family:'JetBrains Mono',monospace;font-size:.88em;background:var(--border);padding:2px 7px;border-radius:6px;opacity:.92}}
.blog-fig{{margin:22px 0;text-align:center}}
.blog-fig img{{border-radius:var(--radius);box-shadow:var(--shadow);max-height:520px;object-fit:contain}}
.blog-fig figcaption{{font-size:.82rem;color:var(--muted);margin-top:8px}}
.blog-code{{background:#0f1117;border-radius:14px;margin:20px 0;overflow:hidden;border:1px solid #1f2333}}
[data-theme=light] .blog-code{{background:#1a1d2e}}
.code-head{{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;background:rgba(255,255,255,.04);font-family:'JetBrains Mono',monospace;font-size:.78rem;color:#8b8fa3;border-bottom:1px solid rgba(255,255,255,.06)}}
.copy-btn{{background:var(--gradient);border:none;color:#fff;font-size:.75rem;font-weight:700;padding:6px 14px;border-radius:50px;cursor:pointer}}
.blog-code pre{{padding:16px;overflow-x:auto}}
.blog-code code{{background:none;color:#d6e2ff;font-family:'JetBrains Mono',monospace;font-size:.86rem;line-height:1.65;white-space:pre}}
.video-box{{position:relative;padding-bottom:56.25%;height:0;margin:24px 0;border-radius:14px;overflow:hidden;box-shadow:var(--shadow);background:#000}}
.video-box iframe{{position:absolute;inset:0;width:100%;height:100%;border:0}}
.blog-list{{margin:14px 0 14px 24px}}
.blog-list li{{margin:7px 0}}
.blog-quote{{margin:22px 0;padding:18px 22px;border-left:4px solid {accent};background:var(--card);border-radius:0 12px 12px 0;font-style:italic;font-size:1.05rem;box-shadow:var(--shadow-sm,0 2px 8px rgba(0,0,0,.04))}}
.quote-cite{{font-style:normal;font-size:.82rem;color:var(--muted);margin-top:8px}}
.blog-divider{{border:none;border-top:1px dashed var(--border);margin:30px 0}}
.blog-link-line{{margin:20px 0}}
.blog-btn{{display:inline-block;font-weight:700;padding:12px 26px;border-radius:50px;font-size:.95rem}}
.blog-btn.primary{{background:var(--gradient);color:#fff}}
.blog-btn.secondary{{background:var(--card);color:var(--accent);border:1.5px solid var(--accent)}}
.blog-gallery{{display:grid;gap:14px;margin:22px 0}}
.blog-gallery img{{width:100%;height:220px;object-fit:cover;border-radius:12px;box-shadow:var(--shadow)}}
.raw-embed{{margin:22px 0}}
.blog-callout{{display:flex;gap:14px;align-items:flex-start;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin:22px 0}}
.co-icon{{font-size:1.5rem;display:inline-flex}}
.co-svg{{width:26px;height:26px;color:var(--accent)}}
.blog-table-wrap{{overflow-x:auto;margin:22px 0;border:1px solid var(--border);border-radius:12px}}
.blog-table{{width:100%;border-collapse:collapse;font-size:.95rem;min-width:440px}}
.blog-table th,.blog-table td{{padding:11px 14px;text-align:left;border-bottom:1px solid var(--border);vertical-align:top}}
.blog-table th{{background:var(--card);font-weight:700}}
.blog-table tr:last-child td{{border-bottom:none}}
.blog-faqs{{margin:20px 0}}
.blog-faq{{border:1px solid var(--border);border-radius:12px;margin:12px 0;background:var(--card);overflow:hidden}}
.blog-faq summary{{list-style:none;cursor:pointer;font-weight:700;padding:14px 16px;display:flex;align-items:center;gap:10px}}
.blog-faq summary::-webkit-details-marker{{display:none}}
.blog-faq summary::before{{content:'+';font-weight:700;color:var(--accent);font-size:1.1rem}}
.blog-faq[open] summary{{border-bottom:1px solid var(--border);background:rgba(108,92,231,.05)}}
.blog-faq[open] summary::before{{content:'-'}}
.blog-faq .faq-body{{padding:4px 16px 16px;color:var(--muted);margin-top:10px}}
.blog-steps{{list-style:none;counter-reset:step;margin:22px 0;padding:0}}
.blog-steps li{{counter-increment:step;position:relative;padding:0 0 22px 52px}}
.blog-steps li:last-child{{padding-bottom:4px}}
.blog-steps li::before{{content:counter(step);position:absolute;left:0;top:-4px;width:34px;height:34px;border-radius:50%;background:var(--gradient);color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center;font-size:.9rem}}
.blog-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:22px 0}}
.blog-stat{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 12px;text-align:center}}
.blog-stat .st-ico{{display:flex;justify-content:center;margin-bottom:8px}}
.blog-stat .sv{{font-size:1.5rem;font-weight:800}}
.blog-stat .sl{{font-size:.74rem;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.5px}}
.blog-embed{{position:relative;margin:22px 0;border-radius:14px;overflow:hidden;box-shadow:var(--shadow);height:0;padding-bottom:56.25%;background:#000}}
.blog-embed iframe{{position:absolute;inset:0;width:100%;height:100%;border:0}}
.blog-toc{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 20px;margin:22px 0}}
.blog-toc .toc-title{{font-weight:800;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.blog-toc a{{display:block;color:var(--muted);text-decoration:none;padding:5px 0;font-size:.92rem;border-bottom:1px dashed var(--border);transition:.15s}}
.blog-toc a:last-child{{border-bottom:none}}
.blog-toc a:hover{{color:var(--accent);padding-left:4px}}
.blog-toc .lvl1,.blog-toc .lvl2{{font-weight:700;color:var(--text)}}
.blog-toc .lvl3{{padding-left:18px}}
.blog-fig-natural img{{height:auto}}
.blog-fig-natural img{{width:auto;max-width:100%;max-height:440px;display:inline-block}}
.blog-fig-wide img{{width:100%;object-fit:cover}}
.blog-fig-left img{{float:left;margin:4px 18px 12px 0;max-width:55%}}
.blog-fig-right img{{float:right;margin:4px 0 12px 18px;max-width:55%}}
.blog-body .blog-h2,.blog-body .blog-h3{{scroll-margin-top:100px}}
@media(max-width:640px){{.blog-fig-left img,.blog-fig-right img{{float:none;max-width:100%;margin:0 0 12px}}}}
.share{{display:flex;gap:10px;align-items:center;margin:38px 0 8px;flex-wrap:wrap}}
.share .sh-label{{font-size:.8rem;color:var(--muted);font-weight:700;margin-right:4px}}
.share-btn{{width:42px;height:42px;border-radius:50%;border:none;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fff}}
.cmt-section{{margin-top:46px;padding-top:26px;border-top:1px solid var(--border)}}
.cmt-head{{font-size:1.2rem;font-weight:800;margin-bottom:16px}}
.cmt-grid{{display:grid;grid-template-columns:1fr;gap:24px}}
.cmt-box{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px}}
.cmt-box input,.cmt-box textarea{{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:11px 14px;font-size:.92rem;color:var(--text);font-family:inherit;outline:none;margin-bottom:10px}}
.cmt-box input:focus,.cmt-box textarea:focus{{border-color:var(--accent)}}
.cmt-btn{{background:var(--gradient);color:#fff;border:none;font-weight:700;padding:11px 22px;border-radius:50px;cursor:pointer}}
.cmts-list{{display:flex;flex-direction:column;gap:12px}}
.cmt-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px}}
.cmt-name{{font-weight:700;font-size:.9rem;color:var(--accent)}}
.cmt-date{{float:right;font-size:.72rem;color:var(--muted)}}
.cmt-text{{font-size:.93rem;margin-top:4px;word-wrap:break-word}}
.cmt-empty{{color:var(--muted);font-size:.9rem}}
.footer{{border-top:1px solid var(--border);padding:26px 20px;text-align:center;font-size:.82rem;color:var(--muted)}}
@media(max-width:640px){{.blog-gallery{{grid-template-columns:1fr!important}}}}
</style>
</head>
<body>
<script>(function(){{var s=localStorage.getItem('theme');if(s==='dark')document.documentElement.setAttribute('data-theme','dark')}})()</script>
<nav class="nav"><div class="nav-inner">
  <a class="logo" href="/"><span class="lg">IT</span><span>I<span style="color:var(--accent)">&#10084;</span>Tools</span></a>
  <div style="display:flex;align-items:center;gap:2px">
    <div class="nav-links"><a href="/blogs.html">Blogs</a><a href="/#text">Tools</a></div>
    <button class="theme-toggle" onclick="toggleTheme()" title="Dark/Light" aria-label="Toggle theme"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg></button>
  </div>
</div></nav>

<article class="thumb">
  <a class="back" href="/blogs.html">← Back to Blogs & News</a>
  <div class="cat-pill">{display_cat}</div>
  <h1 class="title">{title}</h1>
  <div class="meta"><span>{nice_date}</span><span>•</span><span>{author}</span></div>
  {tags}
  {cover}
  <div class="blog-body">{content}</div>

  <div class="share">
    <span class="sh-label">Share:</span>
    <button class="share-btn" style="background:#25D366" onclick="shareUrl('wa')" title="WhatsApp"><svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M17.5 14.4c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.89-.79-1.49-1.77-1.66-2.07-.17-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.07-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.49 0 1.47 1.07 2.89 1.22 3.09.15.2 2.1 3.2 5.08 4.49.71.31 1.26.49 1.69.63.71.22 1.36.19 1.87.12.57-.09 1.76-.72 2-1.41.25-.7.25-1.29.17-1.42-.07-.13-.27-.2-.57-.35zM12.04 21.5h-.01a9.4 9.4 0 01-4.79-1.31l-.34-.2-3.56.93.95-3.47-.22-.36A9.43 9.43 0 0112.04 2.56a9.4 9.4 0 010 18.81z"/></svg></button>
    <button class="share-btn" style="background:#1DA1F2" onclick="shareUrl('tw')" title="X / Twitter"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></button>
    <button class="share-btn" style="background:{accent}" onclick="shareUrl('link')" title="Copy link"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg></button>
  </div>

  <div class="cmt-section">
    <div class="cmt-head">💬 Comments</div>
    <div class="cmt-grid">
      <div class="cmt-box">
        <input id="cmtName" placeholder="Your name" maxlength="60">
        <textarea id="cmtText" rows="3" placeholder="Write a comment..." maxlength="1000"></textarea>
        <button class="cmt-btn" onclick="postCmt(this)">Post Comment</button>
      </div>
      <div class="cmts-list" id="cmts"></div>
    </div>
  </div>
</article>

<footer class="footer">© 2026 I❤Tools.pro · <a href="/blogs.html">Blogs & News</a> · <a href="/">Home</a></footer>

<script>
var POST_SLUG="{slug}";var API="{api}";
function esc(s){{return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}}
function loadCmt(){{fetch(API+"/api/blogs/"+POST_SLUG+"/comments").then(function(r){{return r.json()}}).then(function(c){{var el=document.getElementById("cmts");if(!Array.isArray(c)||!c.length){{el.innerHTML='<div class="cmt-empty">No comments yet. Be the first!</div>';return}}el.innerHTML=c.map(function(x){{return '<div class="cmt-card"><span class="cmt-name">'+esc(x.name)+'</span><span class="cmt-date">'+esc((x.date||"").slice(0,10))+'</span><div class="cmt-text">'+esc(x.text)+'</div></div>'}}).join("")}}).catch(function(){{}})}}
function postCmt(btn){{var n=document.getElementById("cmtName").value.trim();var t=document.getElementById("cmtText").value.trim();if(!n||!t)return;btn.disabled=true;fetch(API+"/api/blogs/"+POST_SLUG+"/comments",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{name:n,text:t}})}}).then(function(r){{return r.json()}}).then(function(){{document.getElementById("cmtText").value="";loadCmt();btn.disabled=false}}).catch(function(){{btn.disabled=false}})}}setTimeout(loadCmt,400);
function shareUrl(k){{var u=encodeURIComponent(location.href);var t=encodeURIComponent(document.title);var url = k==="wa"?"https://wa.me/?text="+t+" "+u:(k==="tw"?"https://twitter.com/intent/tweet?url="+u+"&text="+t:"")||"";if(k==="link"){{navigator.clipboard&&navigator.clipboard.writeText(location.href);showToast("Link copied")}}else{{window.open(url,"_blank","width=600,height=500")}}}}
function toggleTheme(){{var d=document.documentElement.getAttribute("data-theme")==="dark";if(d){{document.documentElement.removeAttribute("data-theme");localStorage.setItem("theme","light")}}else{{document.documentElement.setAttribute("data-theme","dark");localStorage.setItem("theme","dark")}}}}
function copyCode(btn){{var c=btn.parentElement.nextElementSibling.textContent;navigator.clipboard&&navigator.clipboard.writeText(c);var o=btn.textContent;btn.textContent="Copied ✓";setTimeout(function(){{btn.textContent=o}},1400)}}
var _t;function showToast(m){{var t=document.createElement("div");t.style.cssText="position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--text);color:var(--bg);padding:10px 20px;border-radius:50px;font-size:.85rem;font-weight:700;z-index:999;box-shadow:0 8px 24px rgba(0,0,0,.2)";t.textContent=m;document.body.appendChild(t);clearTimeout(_t);_t=setTimeout(function(){{t.remove()}},1800)}}
</script>
</body>
</html>
"""