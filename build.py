#!/usr/bin/env python3
"""松江のお気に入りマップ ── Jotform → GeoJSON＋サムネイル 自動生成

Jotform API から投稿を取得し、uMap のリモートデータとして読める GeoJSON と
サムネイル JPEG を docs/ に書き出す。判断を含まない決定的処理のみ。

環境変数:
  JOTFORM_API_KEY  必須（GitHub Actions では Secrets から。ローカルは ~/.jotform_api_key）
  PUBLIC_BASE_URL  サムネイルの公開URLの土台（既定: GitHub Pages のURL）

掲載規則（17_写真収集システム_お気に入り写真/スクリプト/csv_to_geojson.py と同じ）:
  - status が ACTIVE の投稿のみ（Jotform で削除すると次回実行で地図からも消える）
  - 「写真の利用に同意する」が明示的に非同意のものは除く（未回答＝同意欄が必須になる前の投稿は掲載）
  - 位置情報が無い／松江市の範囲外（緯度35.1〜35.7・経度132.7〜133.4）はスキップ
  - 同じ写真・同じ文の二重投稿は最初の1件に統合
  - 属性・年代・メールはポップアップに載せない（2026-07-07 定例会 D-1）
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FORM_ID = "261802572611048"
API = "https://api.jotform.com"
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
THUMBS = DOCS / "thumbs"
GEOJSON = DOCS / "favorites.geojson"
STATUS = DOCS / "status.json"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://motoki-design.github.io/matsue-favorite-map").rstrip("/")

# Jotform の質問ID（フォーム設計に固定。変わったら status.json の fields で気づく）
Q_PHOTO, Q_NAME, Q_REASON, Q_WEATHER, Q_ATTR, Q_AGE, Q_LOCATION, Q_CONSENT = "4", "5", "6", "7", "8", "9", "12", "14"

BBOX = (35.1, 35.7, 132.7, 133.4)
THUMB_MAX_WIDTH = 400
THUMB_QUALITY = 60
JST = timezone(timedelta(hours=9))


def api_get(path: str, key: str, **params) -> dict:
    params["apiKey"] = key
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def fetch_submissions(key: str) -> list[dict]:
    out, offset = [], 0
    while True:
        j = api_get(f"/form/{FORM_ID}/submissions", key, limit=1000, offset=offset, orderby="created_at")
        if j.get("responseCode") != 200:
            sys.exit(f"Jotform API error: {j.get('responseCode')} {j.get('message')}")
        c = j.get("content", [])
        out.extend(c)
        if len(c) < 1000:
            return out
        offset += 1000


def answer(s: dict, qid: str) -> str:
    a = s.get("answers", {}).get(qid, {}).get("answer", "")
    if isinstance(a, list):
        return a[0] if a else ""
    if isinstance(a, dict):
        return " ".join(str(v) for v in a.values())
    return str(a or "")


def parse_location(text: str) -> tuple[float | None, float | None]:
    """GPS Location Widget の値（住所行＋'lat, lon' 行）から座標を取る。"""
    m = re.search(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", text or "")
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


def download(url: str, key: str) -> bytes:
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}apiKey={key}", headers={"User-Agent": "matsue-favorite-map/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def make_thumb(data: bytes, dst: Path) -> None:
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # iPhone の HEIC 対応
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if im.width > THUMB_MAX_WIDTH:
            im = im.resize((THUMB_MAX_WIDTH, int(im.height * THUMB_MAX_WIDTH / im.width)), Image.LANCZOS)
        im.save(dst, format="JPEG", quality=THUMB_QUALITY, optimize=True)


def popup_html(reason: str, weather: str, thumb_url: str) -> str:
    parts = []
    if thumb_url:
        parts.append(f"<img src='{thumb_url}' width='280' style='border-radius:6px'><br>")
    if reason:
        parts.append(f"「{reason}」<br>")
    if weather:
        parts.append(f"好きな天気：{weather}<br>")
    return "".join(parts)


def main() -> int:
    key = os.environ.get("JOTFORM_API_KEY") or (Path.home() / ".jotform_api_key").read_text().strip()
    subs = fetch_submissions(key)
    THUMBS.mkdir(parents=True, exist_ok=True)

    log = {"active": 0, "deleted": 0, "no_consent": 0, "no_location": 0, "out_of_bbox": 0,
           "duplicate": 0, "no_photo": 0, "published": 0, "thumb_new": 0, "thumb_failed": 0}
    seen, features, keep_thumbs = set(), [], set()

    for s in sorted(subs, key=lambda x: x["created_at"]):
        if s.get("status") != "ACTIVE":
            log["deleted"] += 1
            continue
        log["active"] += 1
        consent = answer(s, Q_CONSENT).strip()
        if consent and consent != "同意する":  # 未回答（同意欄が必須になる前の7/15以前の9件）は従来どおり掲載
            log["no_consent"] += 1
            continue
        lat, lon = parse_location(answer(s, Q_LOCATION))
        if lat is None:
            log["no_location"] += 1
            continue
        if not (BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]):
            log["out_of_bbox"] += 1
            continue
        photo_url = answer(s, Q_PHOTO).strip()
        name = answer(s, Q_NAME).strip() or "（名称なし）"
        reason = answer(s, Q_REASON).strip()
        weather = answer(s, Q_WEATHER).strip()
        dup_key = (os.path.basename(urllib.parse.urlparse(photo_url).path), name, reason, answer(s, Q_LOCATION))
        if dup_key in seen:
            log["duplicate"] += 1
            continue
        seen.add(dup_key)

        thumb_url = ""
        if photo_url:
            dst = THUMBS / f"{s['id']}.jpg"
            if not dst.exists():
                try:
                    make_thumb(download(photo_url, key), dst)
                    log["thumb_new"] += 1
                    time.sleep(0.5)
                except Exception as e:  # 写真が取れなくてもピンは出す
                    print(f"  thumb failed {s['id']}: {e}", file=sys.stderr)
                    log["thumb_failed"] += 1
            if dst.exists():
                keep_thumbs.add(dst.name)
                thumb_url = f"{PUBLIC_BASE_URL}/thumbs/{dst.name}"
        else:
            log["no_photo"] += 1

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "description": popup_html(reason, weather, thumb_url),
                           "posted": s["created_at"][:10]},
        })
        log["published"] += 1

    # 削除・非掲載になった投稿のサムネは公開側から外す
    removed = 0
    for p in THUMBS.glob("*.jpg"):
        if p.name not in keep_thumbs:
            p.unlink()
            removed += 1
    log["thumb_removed"] = removed

    GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1), encoding="utf-8")
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S %z")
    fields = {qid: s.get("answers", {}).get(qid, {}).get("text", "") for qid in
              (Q_PHOTO, Q_NAME, Q_REASON, Q_WEATHER, Q_ATTR, Q_AGE, Q_LOCATION, Q_CONSENT)} if subs else {}
    STATUS.write_text(json.dumps({"built_at": now, "counts": log, "fields": fields}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{now}] " + " ".join(f"{k}={v}" for k, v in log.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
