"""Instagram hashtags via Instaloader — https://github.com/instaloader/instaloader"""


def fetch_hashtag(tag, posts_per_tag=15):
    try:
        import instaloader
    except ImportError as e:
        raise RuntimeError("instaloader not installed (pip install instaloader)") from e

    L = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
                                download_video_thumbnails=False, save_metadata=False, compress_json=False)
    tag = tag.lstrip("#")
    out = []
    try:
        hashtag = instaloader.Hashtag.from_name(L.context, tag)
    except Exception as e:
        raise RuntimeError(f"Instagram #{tag}: {e}") from e
    count = 0
    for post in hashtag.get_posts():
        if count >= posts_per_tag:
            break
        count += 1
        caption = post.caption or ""
        out.append({
            "source": "instagram",
            "platform": "instagram",
            "kind": "pain",
            "external_id": str(post.mediaid),
            "url": f"https://www.instagram.com/p/{post.shortcode}/",
            "title": "",
            "text": caption[:4000],
            "author": post.owner_username or "",
            "author_url": f"https://www.instagram.com/{post.owner_username}/" if post.owner_username else "",
            "community": f"#{tag}",
            "created_utc": int(post.date_utc.timestamp()) if post.date_utc else None,
            "engagement": int(post.likes or 0) + int(post.comments or 0),
            "query": tag,
        })
    return out
