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

    return ARTICLE_TEMPLATE.format(
        title=html_escape(title),
        description=html_escape(excerpt or ("%s - I Love Tools" % title)),
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


def render_blocks(blocks, accent):
    html = []
    for b in blocks:
        t = b.get("type")
        try:
            if t == "heading":
                lvl = min(6, max(1, int(b.get("level", 2))))
                align = b.get("align", "left")
                txt = inline_md(b.get("text", ""))
                html.append(
                    '<h%d class="blog-h%d" style="text-align:%s">%s</h%d>' % (lvl, lvl, align, txt, lvl)
                )
            elif t == "text":
                p = inline_md(b.get("text", ""))
                if p:
                    html.append('<p class="blog-p">%s</p>' % p)
            elif t == "image":
                src = b.get("src", "")
                cap = html_escape(b.get("caption", "") or "")
                cap_html = "<figcaption>%s</figcaption>" % cap if cap else ""
                html.append(
                    '<figure class="blog-fig"><img loading="lazy" src="%s" alt="%s">%s</figure>'
                    % (html_escape(src), html_escape(b.get("alt", "") or ""), cap_html)
                )
            elif t == "code":
                code = b.get("code", "")
                lang = html_escape(b.get("lang", "")) or "code"
                fname = html_escape(b.get("filename", "") or "")
                fh = '<div class="code-head"><span>%s</span><button class="copy-btn" onclick="copyCode(this)">Copy</button></div>' % fname
                html.append(
                    '<div class="blog-code"><div class="code-head"><span>%s</span><button class="copy-btn" onclick="copyCode(this)">Copy</button></div><pre><code>%s</code></pre></div>'
                    % (lang, html_escape(code))
                )
            elif t == "video":
                src = b.get("url", "")
                vid = youtube_embed(src)
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
                icon = html_escape(b.get("icon", "")) or "💡"
                txt = inline_md(b.get("text", ""))
                html.append('<div class="blog-callout"><span class="co-icon">%s</span><div>%s</div></div>' % (icon, txt))
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
.co-icon{{font-size:1.5rem}}
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