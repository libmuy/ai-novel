# -*- coding: utf-8 -*-
"""播客 RSS（不变，从旧版整体搬迁）。"""
import email.utils
from xml.sax.saxutils import escape as xml_escape

from . import model


def render_feed(entries, title, base) -> bytes:
    items = []
    for e in entries:
        for scene, path in e.audio_units():
            slug = f"/{e.path3()}" + (f"/{scene}" if scene else "")
            url = f"{base}/audio{slug}.mp3"
            size = model._safe_size(path)
            dur = (e.duration_s() if scene is None
                   else model._mp3_duration_seconds(path, size))
            it_title = f"卷{e.vol:02d} 第 {e.ch} 章" + (f" · 场 {scene}" if scene else "")
            items.append(
                "<item>"
                f"<title>{xml_escape(it_title)}</title>"
                f"<guid isPermaLink=\"false\">{xml_escape(base + slug)}</guid>"
                f"<pubDate>{email.utils.formatdate(e.pubdate_ts(scene), usegmt=True)}</pubDate>"
                f"<enclosure url=\"{xml_escape(url)}\" length=\"{size}\" type=\"audio/mpeg\"/>"
                f"<itunes:duration>{model._fmt_hms(dur)}</itunes:duration>"
                "<itunes:explicit>false</itunes:explicit>"
                "</item>"
            )
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\" xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\"><channel>"
        f"<title>{xml_escape(title)}</title><link>{xml_escape(base)}/</link>"
        f"<language>zh-cn</language>"
        f"<description>{xml_escape(title)} 章节 TTS 配音（听稿校对用）</description>"
        f"<itunes:author>{xml_escape(title)}</itunes:author>"
        f"<lastBuildDate>{email.utils.formatdate(usegmt=True)}</lastBuildDate>"
        f"{''.join(items)}</channel></rss>"
    )
    return xml.encode("utf-8")
