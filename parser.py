"""
勤怠データ解析エンジン
- PDFをOCRで読み取り社員ごとの月次データを抽出
- XLSから所定労働時間マスタを読み取り
- イレギュラー（時間超過・欠勤・有休変更など）を検出
"""

import os
import re
import xlrd
import pytesseract
from PIL import Image
from pathlib import Path


# ─── XLS マスタ読取 ───────────────────────────────────────────

def parse_master_xls(xls_path: str) -> dict:
    """週間残業マスタXLSを読取 → {社員番号: {...}} を返す"""
    wb = xlrd.open_workbook(xls_path)
    ws = wb.sheet_by_index(0)

    master = {}
    headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]

    for r in range(1, ws.nrows):
        row = {headers[c]: ws.cell_value(r, c) for c in range(ws.ncols)}
        code = str(int(row.get("社員番号", 0))) if row.get("社員番号") else None
        if not code or code == "0":
            continue

        # 日あたり所定時間: 日・時間×10 ÷ 10 → 時間(float)
        day_minutes_x10 = row.get("日・時間×10", 0)
        week_minutes_x10 = row.get("週・時間×10", 0)
        week_days = row.get("週・日数", 0)

        master[code] = {
            "氏名":       row.get("社員名称", "").replace("\u3000", " ").strip(),
            "店舗":       row.get("所属名称", "").replace("\u3000", " ").strip(),
            "身分":       row.get("身分名称", "").replace("\u3000", " ").strip(),
            "部門":       row.get("部門", "").strip(),
            "所定日時間": day_minutes_x10 / 10.0 if day_minutes_x10 else 0,
            "所定週時間": week_minutes_x10 / 10.0 if week_minutes_x10 else 0,
            "所定週日数": float(week_days) if week_days else 0,
        }
    return master


# ─── PDF OCR 解析 ───────────────────────────────────────────

def ocr_image(img_path: str) -> str:
    img = Image.open(img_path)
    return pytesseract.image_to_string(img, lang="jpn", config="--psm 3 --dpi 300")


def parse_employee_from_text(text: str, page_file: str) -> dict | None:
    """OCRテキストから社員1人分のデータを抽出"""
    if "個人コード" not in text:
        return None

    emp = {"_page": page_file}

    # 個人コード・氏名
    m = re.search(r"個人コード\s*[：:]\s*(\d{8})\s+(\S+\s+\S+)", text)
    if m:
        emp["個人コード"] = m.group(1)
        emp["氏名_ocr"] = m.group(2).strip()

    if "個人コード" not in emp:
        return None

    # 雇用区分
    for t in ["正社員", "アルバイト", "地域社員", "ロングタイマー",
              "社保ミドルタイマ", "ミドルタイマー", "学生アルバイト"]:
        if t in text:
            emp["雇用区分"] = t
            break

    # 店舗名
    m = re.search(r"T\s*\d+\S+店", text)
    if m:
        emp["店舗_ocr"] = m.group(0).replace(" ", "")

    # ─ 月次集計 ─
    def extract_int(pattern):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    def extract_str(pattern):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    emp["出勤日数"]   = extract_int(r"出勤日数\s+(\d+)")
    emp["有休日数"]   = extract_int(r"有休日数\s+(\d+)")
    emp["病休日数"]   = extract_int(r"病休日数\s+(\d+)")
    emp["欠勤日数"]   = extract_int(r"欠勤日数\s+(\d+)")
    emp["有休残日数"] = extract_int(r"有休残日数\s+(\d+)")
    emp["出勤時間"]   = extract_str(r"出勤時間\s+([\d:]+)")
    emp["実働時間"]   = extract_str(r"実働時間\s+([\d:]+)")

    # 普通残業(管)
    m = re.search(r"普通残業[（(]管[）)]\s*([\d:.]+)", text)
    if not m:
        m = re.search(r"普通残業\s+([\d:.]+)", text)
    if m:
        emp["普通残業"] = m.group(1)

    # 日別勤務区分カウント
    emp["年次有給_日数"] = len(re.findall(r"年次有給休暇", text))
    emp["欠勤_日数"]     = len(re.findall(r"(?<![^\s])欠勤(?!\d)", text))
    emp["病休_日数"]     = len(re.findall(r"病休|私傷病", text))

    # 時間外合計（時間外時間列の合計）
    times = re.findall(r"時間外時間\s+([\d:]+)", text)
    emp["時間外時間"] = times[0] if times else None

    return emp


def parse_pdf_images(img_dir: str, prefix: str) -> list[dict]:
    """指定ディレクトリ内の画像をOCRして社員リストを返す"""
    files = sorted(
        f for f in os.listdir(img_dir)
        if f.startswith(prefix) and f.endswith(".jpg")
    )
    results = []
    for fname in files:
        text = ocr_image(os.path.join(img_dir, fname))
        emp = parse_employee_from_text(text, fname)
        if emp:
            results.append(emp)
    return results


# ─── 時間文字列変換 ─────────────────────────────────────────

def hhmm_to_hours(s: str | None) -> float | None:
    """'166:15' → 166.25"""
    if not s:
        return None
    parts = str(s).split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return round(h + m / 60, 2)
    except Exception:
        return None


# ─── イレギュラー検出 ───────────────────────────────────────

IRREGULAR_RULES = {
    "欠勤あり":     lambda e, m: (e.get("欠勤日数") or 0) > 0,
    "有休残少（5日以下）": lambda e, m: (
        e.get("有休残日数") is not None and e["有休残日数"] <= 5
    ),
    "週所定超過（実働）": lambda e, m: _check_overtime(e, m),
    "病休あり":     lambda e, m: (e.get("病休日数") or 0) > 0,
    "時間外あり":   lambda e, m: hhmm_to_hours(e.get("時間外時間")) not in (None, 0.0),
}


def _check_overtime(emp: dict, master_rec: dict | None) -> bool:
    """実働時間が所定週時間×(期間週数4〜5)を超えているか"""
    if not master_rec:
        return False
    jisseki = hhmm_to_hours(emp.get("実働時間") or emp.get("出勤時間"))
    if jisseki is None:
        return False
    # 処理期間は約4週（5/16〜6/15）= 4.3週
    allowance = master_rec["所定週時間"] * 4.3
    return jisseki > allowance


def detect_irregulars(employees: list[dict], master: dict) -> list[dict]:
    """各社員のイレギュラーフラグを付けて返す"""
    result = []
    for emp in employees:
        code = emp.get("個人コード", "")
        master_rec = master.get(code)
        flags = []
        for label, check_fn in IRREGULAR_RULES.items():
            try:
                if check_fn(emp, master_rec):
                    flags.append(label)
            except Exception:
                pass

        rec = dict(emp)
        rec["イレギュラー"] = flags
        rec["イレギュラー数"] = len(flags)
        # マスタ情報をマージ
        if master_rec:
            rec["氏名"]     = master_rec["氏名"]
            rec["店舗"]     = master_rec["店舗"]
            rec["身分"]     = master_rec["身分"]
            rec["所定日時間"] = master_rec["所定日時間"]
            rec["所定週時間"] = master_rec["所定週時間"]
        result.append(rec)

    # イレギュラー数降順で並び替え
    result.sort(key=lambda x: x["イレギュラー数"], reverse=True)
    return result


# ─── メイン解析エントリーポイント ───────────────────────────

def analyze(
    pdf_before_images: list[str] | None = None,
    pdf_after_images:  list[str] | None = None,
    xls_path: str | None = None,
    img_dir: str = "/tmp",
    before_prefix: str = "before_p-",
    after_prefix:  str = "after_p-",
) -> dict:
    """
    解析を実行して結果dictを返す
    Returns:
        {
          "master": {code: {...}},
          "before": [...],
          "after":  [...],
          "before_irregular": [...],
          "after_irregular":  [...],
          "diff": [...],
        }
    """
    # マスタ
    master = {}
    if xls_path and os.path.exists(xls_path):
        master = parse_master_xls(xls_path)

    # PDF OCR
    before = parse_pdf_images(img_dir, before_prefix)
    after  = parse_pdf_images(img_dir, after_prefix) if after_prefix else []

    # イレギュラー検出
    before_irr = detect_irregulars(before, master)
    after_irr  = detect_irregulars(after,  master)

    # 差分（修正前後で値が変わった社員）
    before_idx = {e["個人コード"]: e for e in before if e.get("個人コード")}
    after_idx  = {e["個人コード"]: e for e in after  if e.get("個人コード")}
    diff_fields = ["出勤日数", "有休日数", "病休日数", "欠勤日数", "有休残日数", "実働時間", "普通残業"]

    diffs = []
    all_codes = sorted(set(before_idx) | set(after_idx))
    for code in all_codes:
        b = before_idx.get(code, {})
        a = after_idx.get(code, {})
        changed = []
        for f in diff_fields:
            bv = str(b.get(f, "")) if b.get(f) is not None else "—"
            av = str(a.get(f, "")) if a.get(f) is not None else "—"
            if bv != av and not (bv == "—" and av == "—"):
                changed.append({"項目": f, "修正前": bv, "修正後": av})
        master_rec = master.get(code, {})
        diffs.append({
            "個人コード": code,
            "氏名":  master_rec.get("氏名") or b.get("氏名_ocr") or a.get("氏名_ocr", "?"),
            "店舗":  master_rec.get("店舗") or b.get("店舗_ocr") or a.get("店舗_ocr", ""),
            "身分":  master_rec.get("身分") or b.get("雇用区分") or a.get("雇用区分", ""),
            "変更数": len(changed),
            "変更内容": changed,
        })
    diffs.sort(key=lambda x: x["変更数"], reverse=True)

    return {
        "master":          master,
        "before":          before,
        "after":           after,
        "before_irregular": before_irr,
        "after_irregular":  after_irr,
        "diff":            diffs,
        "summary": {
            "社員数_before":      len(before),
            "社員数_after":       len(after),
            "イレギュラー者数":   sum(1 for e in after_irr if e["イレギュラー数"] > 0),
            "差分あり者数":       sum(1 for d in diffs if d["変更数"] > 0),
            "マスタ登録数":       len(master),
        }
    }
