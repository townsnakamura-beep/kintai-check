"""
勤怠チェックシステム - FastAPI バックエンド（Render.com対応版）
"""
import os
import uuid
import json
import shutil
import asyncio
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

os.environ.setdefault("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata")

BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
CACHE_DIR  = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"
for d in [UPLOAD_DIR, CACHE_DIR, OUTPUT_DIR, STATIC_DIR]:
    d.mkdir(exist_ok=True)

app = FastAPI(title="勤怠チェックシステム")
app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)),  name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


# ─── トップページ ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(BASE_DIR / "templates" / "index.html", encoding="utf-8") as f:
        return f.read()


# ─── ヘルスチェック（Render が使う）────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── アップロード & 解析 ─────────────────────────────────────

@app.post("/api/analyze")
async def analyze_endpoint(
    pdf_before: UploadFile = File(...),
    pdf_after:  UploadFile = File(None),
    master_xls: UploadFile = File(None),
):
    session_id  = str(uuid.uuid4())[:8]
    session_dir = CACHE_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    # ── ファイル保存 ──
    before_path = session_dir / "before.pdf"
    content = await pdf_before.read()
    before_path.write_bytes(content)

    after_path = None
    if pdf_after and pdf_after.filename:
        after_path = session_dir / "after.pdf"
        after_path.write_bytes(await pdf_after.read())

    xls_path = None
    if master_xls and master_xls.filename:
        suffix   = Path(master_xls.filename).suffix or ".xls"
        xls_path = session_dir / f"master{suffix}"
        xls_path.write_bytes(await master_xls.read())

    # ── PDF → 画像変換（subprocess で非同期実行）──
    img_dir = str(session_dir)

    proc = await asyncio.create_subprocess_exec(
        "pdftoppm", "-jpeg", "-r", "150",
        str(before_path), f"{session_dir}/before_p"
    )
    await proc.wait()

    if after_path:
        proc2 = await asyncio.create_subprocess_exec(
            "pdftoppm", "-jpeg", "-r", "150",
            str(after_path), f"{session_dir}/after_p"
        )
        await proc2.wait()

    # ── 解析実行（CPU重いのでスレッドプールへ）──
    from parser import analyze

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: analyze(
        xls_path      = str(xls_path) if xls_path else None,
        img_dir       = img_dir,
        before_prefix = "before_p-",
        after_prefix  = "after_p-" if after_path else None,
    ))

    # ── セッション保存 ──
    (session_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )

    return JSONResponse({"session_id": session_id, "result": result})


# ─── Excel レポート出力 ──────────────────────────────────────

@app.get("/api/export/{session_id}")
async def export_excel(session_id: str):
    # セッションIDに英数字以外が混じらないよう簡易チェック
    if not session_id.replace("-", "").isalnum():
        raise HTTPException(400, "不正なセッションIDです")

    result_json = CACHE_DIR / session_id / "result.json"
    if not result_json.exists():
        raise HTTPException(404, "セッションが見つかりません。再度解析してください。")

    result   = json.loads(result_json.read_text(encoding="utf-8"))
    out_path = OUTPUT_DIR / f"report_{session_id}.xlsx"

    from report import generate_excel
    generate_excel(result, str(out_path))

    return FileResponse(
        str(out_path),
        filename="勤怠チェックレポート.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ─── ローカル起動用 ───────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
