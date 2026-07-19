"""
scripts/sync_sheets.py

從 Google Sheets 抓取 HLDPwn（Haiang Learner's Dictionary of Paiwan）的詞典資料，
輸出成 data/*.csv，供 index.html 在瀏覽器端 fetch 使用。

使用 gspread（而非 Sheets API 的 UNFORMATTED_VALUE）讀取儲存格「顯示文字」，
避免像方言代碼這種需要保留前導零的欄位被讀成數字後把 0 吃掉。

執行方式：由 GitHub Actions 排程呼叫（見 .github/workflows/sync-sheets.yml），
使用 Service Account 憑證（GOOGLE_CREDENTIALS）讀取，不需要互動式登入。

HLD-Etymology 這份資料跨辭典共用（跟 HLDDru 讀同一份試算表），
其餘三份（Lemmata/Senses/Examples）是 HLDPwn 專屬的資料。
"""

import csv
import json
import os
import sys
import time

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
]

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # 秒；每次重試延遲加倍（5, 10, 20...）

# ── 資料來源設定 ──────────────────────────────────────────────
# id_env    : 存放該 Sheet ID 的 GitHub Secret 名稱
# sheet_name: Google Sheet 內的分頁（工作表）名稱
# output    : 輸出到 repo 內的哪個檔案
SHEETS = [
    {
        'id_env': 'SPREADSHEET_ID_LEMMATA',
        'sheet_name': '07-Lemmata',
        'output': 'data/07-Lemmata.csv',
    },
    {
        'id_env': 'SPREADSHEET_ID_SENSES',
        'sheet_name': '07-Senses',
        'output': 'data/07-Senses.csv',
    },
    {
        'id_env': 'SPREADSHEET_ID_ETYMOLOGY',
        'sheet_name': 'HLD-Etymology',
        'output': 'data/HLD-Etymology.csv',
    },
    {
        'id_env': 'SPREADSHEET_ID_EXAMPLES',
        'sheet_name': '07-Examples',
        'output': 'data/07-Examples.csv',
    },
]


def get_client():
    raw = os.environ.get('GOOGLE_CREDENTIALS')
    if not raw:
        print('錯誤：找不到環境變數 GOOGLE_CREDENTIALS', file=sys.stderr)
        sys.exit(1)
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def fetch_sheet_values(gc, spreadsheet_id, sheet_name):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            sh = gc.open_by_key(spreadsheet_id)
            ws = sh.worksheet(sheet_name)
            return ws.get_all_values()  # 保留儲存格顯示文字（含前導零），不轉型成數字
        except gspread.exceptions.APIError as e:
            last_err = e
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            # 只針對暫時性錯誤（5xx／429限流）重試，其他錯誤（如權限、找不到分頁）直接失敗
            if status is not None and status not in (429, 500, 502, 503, 504):
                raise
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f'  ⚠ 第{attempt}次抓取失敗（{e}），{delay}秒後重試...')
                time.sleep(delay)
    raise last_err


def write_csv(values, output_path):
    # 去掉尾端完全空白的列（Google Sheet 常見的格線殘留空列）
    while values and all(cell.strip() == '' for cell in values[-1]):
        values.pop()

    if not values:
        print(f'  ⚠ 沒有資料，略過寫入 {output_path}')
        return

    header = values[0]
    col_count = len(header)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        for row in values:
            padded = row + [''] * (col_count - len(row))
            writer.writerow(padded[:col_count])

    print(f'  ✓ 寫入 {output_path}（{len(values) - 1} 筆資料列）')


def main():
    gc = get_client()
    failures = []

    for sheet_cfg in SHEETS:
        spreadsheet_id = os.environ.get(sheet_cfg['id_env'])
        if not spreadsheet_id:
            print(f"  ⚠ 找不到環境變數 {sheet_cfg['id_env']}，略過此份資料")
            continue

        print(f"同步 {sheet_cfg['output']} ← Sheet({sheet_cfg['id_env']})...")
        try:
            values = fetch_sheet_values(gc, spreadsheet_id, sheet_cfg['sheet_name'])
            write_csv(values, sheet_cfg['output'])
        except Exception as e:
            print(f"  ✗ {sheet_cfg['output']} 同步失敗：{e}", file=sys.stderr)
            failures.append(sheet_cfg['output'])

    if failures:
        print(
            f"\n⚠ 有 {len(failures)} 份資料本次同步失敗，維持上次成功同步的版本："
            + '、'.join(failures),
            file=sys.stderr,
        )
        print('其餘成功的部分仍會照常寫入，下次排程會自動重試失敗的部分。', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
