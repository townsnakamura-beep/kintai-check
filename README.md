# 勤怠チェックシステム

カンパニーからエクスポートしたPDF・XLSを読み込み、  
有給・欠勤・時間超過などのイレギュラーを自動検出するWebアプリです。

---

## Render.com へのデプロイ手順

### 1. GitHubにリポジトリを作る

1. https://github.com を開いてログイン
2. 右上の「+」→「New repository」
3. Repository name: `kintai-check`
4. Privateを選択（社内データを扱うため）
5. 「Create repository」をクリック

### 2. このフォルダをGitHubにアップロード

ターミナル（Mac: Terminal / Win: Git Bash）で以下を実行:

```bash
# このフォルダに移動
cd kintai_app

# Gitの初期設定
git init
git add .
git commit -m "初回コミット"

# GitHubに接続してpush（URLは自分のものに変更）
git remote add origin https://github.com/あなたのID/kintai-check.git
git branch -M main
git push -u origin main
```

### 3. Render.com でWebサービスを作る

1. https://render.com を開いてGitHubアカウントでサインアップ
2. ダッシュボードで「New +」→「Web Service」
3. 「Connect a repository」→ `kintai-check` を選択
4. 以下の設定を確認（自動で読み込まれるはず）:
   - **Runtime**: Docker
   - **Branch**: main
5. 「Create Web Service」をクリック
6. ビルドが始まる（5〜10分待つ）
7. 完了したら `https://kintai-check.onrender.com` のようなURLが発行される

### 4. 動作確認

発行されたURLをブラウザで開く →  
PDF・XLSをアップロードして「解析を開始する」ボタンを押す

---

## ローカルでの起動方法（開発・テスト用）

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# tesseract（Mac）
brew install tesseract tesseract-lang

# tesseract（Ubuntu）
sudo apt install tesseract-ocr tesseract-ocr-jpn poppler-utils

# 起動
export TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
uvicorn app:app --reload
# → http://localhost:8000 で開く
```

---

## ファイル構成

```
kintai_app/
├── app.py          # FastAPI メインアプリ
├── parser.py       # PDF OCR解析・XLS読取・イレギュラー検出
├── report.py       # Excelレポート生成
├── requirements.txt
├── Dockerfile      # Render.com用
├── render.yaml     # Render設定
├── templates/
│   └── index.html  # フロントエンドHTML
└── static/         # 静的ファイル置き場
```

---

## 注意事項

- Render.com の無料プランは **15分間アクセスがないとスリープ** します。  
  最初のアクセス時に30秒〜1分ほど起動待ちが発生します。  
  デモ直前にブラウザで開いておくと安全です。
- OCR処理は1ファイルあたり **1〜3分** かかります（PDF枚数による）。
- アップロードされたファイルはサーバー上に一時保存されます。  
  本番運用時はRender.comの有料プランまたはGoogle Cloud Runへの移行を推奨します。
