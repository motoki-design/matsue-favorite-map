# 松江のお気に入りマップ ── 自動更新

Jotform に寄せられた「松江のお気に入り」投稿を、毎朝 06:00（JST）に取得して
uMap が読めるかたちで公開する仕組みです。人の操作は不要です。

- 公開地図：https://umap.openstreetmap.fr/ja/map/map_1430611
- 投稿フォーム：https://form.jotform.com/261802572611048
- 生成物：`docs/favorites.geojson`（uMap のリモートデータ）・`docs/thumbs/*.jpg`（幅400pxのサムネイル）・`docs/status.json`（実行結果）

## 流れ

```
Jotform API ──(GitHub Actions・毎朝06:00)──▶ docs/ を更新して commit ──▶ GitHub Pages
                                                                              ▲
uMap のレイヤ「松江のお気に入り」── リモートデータURL（動的取得）──────────────┘
```

## 掲載規則（`build.py`）

- Jotform 上で削除された投稿（status ≠ ACTIVE）は載せない → **不適切な投稿は Jotform で削除すれば翌朝の実行で地図からも消える**
- 「写真の利用に同意する」が明示的に非同意の投稿は載せない
- 位置情報が無い、または松江市の範囲（緯度35.1〜35.7・経度132.7〜133.4）の外はスキップ
- 同じ写真・同じ文の二重投稿は最初の1件に統合
- ポップアップに出すのは写真・場所の名前・好きな理由・天気のみ（属性・年代・メールは出さない）

## 運用

- 状況の確認：`docs/status.json` の `counts`（active／published／thumb_failed など）と Actions のログ
- すぐ反映したいとき：Actions → build → Run workflow
- フォームの質問を変えたとき：`build.py` の `Q_*`（質問ID）を合わせる。`status.json` の `fields` に現在の質問文が出る
- 鍵：Jotform の API キー（Read Only）を リポジトリ Secrets `JOTFORM_API_KEY` に置く

## uMap 側の設定（一度だけ）

レイヤ「松江のお気に入り」の設定 → リモートデータ →
URL `https://motoki-design.github.io/matsue-favorite-map/favorites.geojson`、形式 geojson、「動的」をオン。
既存のピンはレイヤから削除しておく（リモートデータと二重になるため）。

## ローカルで試す

```
pip install -r requirements.txt
JOTFORM_API_KEY=... python build.py
```
