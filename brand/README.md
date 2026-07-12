# Hikeshi ブランドキット

このプロダクトのロゴとプロダクトカラーの**正本（source of truth）**。色・ロゴ仕様は [`exports/index.html`](exports/index.html) の `:root` と一致。

モチーフ＝**纏（まとい）**（江戸の火消しの旗印）。墨の纏＋朱の菱形バッジ。

## プロダクトカラー（3色）

| 役割 | 和名 | HEX | 用途 |
|---|---|---|---|
| 地色 | 生成り（kinari） | `#F4EFE6` | 背景・余白 |
| 文字/主色 | 墨（sumi） | `#1C1C1C` | 本文・見出し・ロゴ纏 |
| アクセント | 朱（shu） | `#B8392C` | 強調・CTA・ロゴバッジ（濃朱 `#8E2B21`＝反転文字を載せる地） |

補助色（キット定義）：ライン `#E3DCCD` ／ muted `#8A7F70`・`#6B6253`。

## ロゴ

| ファイル | 種別 | 使う地 |
|---|---|---|
| `exports/svg/hikeshi-mark.svg`（＋`png/hikeshi-mark-16..1024.png`） | **primary**（墨纏＋朱バッジ） | 明るい地（生成り/白） |
| `exports/svg/hikeshi-mark-mono.svg` | mono（全墨・単色） | 単色印刷・極小サイズ |
| `exports/svg/hikeshi-mark-reversed.svg`（＋`png/...reversed-128..1024.png`） | reversed（全生成り） | 濃い地（墨地/朱地/写真） |
| `exports/app-icon/app-{shu,sumi,kinari}-1024.png`（朱地/墨地/生成り地・shuは512も） | アプリアイコン | iOS/Android 等（角丸） |
| `exports/favicon/{favicon-16,32,48,apple-touch-icon-180}.png` | favicon | Web |

## ファイル構成

```
brand/
  README.md                 # 本書（パレット・ロゴ仕様の正本）
  Hikeshi ロゴキット.html      # キットHTML（下記 jsx をブラウザで描画）
  marks.jsx                 # ロゴマーク（纏/バッジ）コンポーネント
  matoi_v2.jsx              # 纏の作図コンポーネント
  logokit.jsx               # キット画面（色見本・ロゴ一覧）コンポーネント
  exports/                  # 静的エクスポート一式（描画不要で確認可）
    index.html              #   色見本＋ロゴ一覧
    svg/  png/  app-icon/  favicon/
```

## 使い方・注意

- **設計デッキ**（`deck/`・gitignore）は primary PNG（128/512）を `deck/assets/` に**複製**して埋め込み済み（`deck/design.js`）。色定数も本パレットに一致。差し替え時は `deck/assets/` 側も更新する。
- 今後の用途：公開 README のロゴ／favicon 配線／demo の承認UI テーマに本パレットを使用。
- **`Hikeshi ロゴキット.html`** は同ディレクトリの `marks.jsx` / `matoi_v2.jsx` / `logokit.jsx`（**同梱済み**）を読み込むクライアントサイド React アプリ。ブラウザで開けば描画される（React/Babel/Google Fonts を CDN から取得＝**要ネット接続**）。描画不要の静的確認は **`exports/index.html`** を参照。

## provenance

Hikeshi 自作のブランド資産（合成・第三者著作物なし）。ロゴ/favicon は本来公開される識別子のため、公開リポジトリ掲載に適格。
