"""dpm.duckdb 唯讀瀏覽工具（爬蟲運行中也可安全查詢）。

所有連線皆為 read_only，並複用 dpm_store 的檔案鎖重試，不會影響爬蟲寫入。

用法：
    python -m scripts.query_dpm                          # 各表列數總覽
    python -m scripts.query_dpm --meta                   # 爬蟲狀態（階段／進度）
    python -m scripts.query_dpm --schema matches         # 查看表結構
    python -m scripts.query_dpm --table accounts -n 5    # 瀏覽前 5 列
    python -m scripts.query_dpm --table matches \
        --where "queue_id = 420" --order "start_ts DESC"
    python -m scripts.query_dpm --table match_players \
        --cols match_id,champion_name,kills,deaths,assists,dpm_score
    python -m scripts.query_dpm --sql "SELECT platform, COUNT(*) \
FROM matches GROUP BY platform ORDER BY 2 DESC"
"""
from __future__ import annotations

import argparse

from backend import config, dpm_store

# 純文字表格預設欄寬上限（過長內容截斷，--wide 可放寬）
DEFAULT_MAX_WIDTH = 48
WIDE_MAX_WIDTH = 120
# 預設每個長 JSON 欄位只留預覽長度
_TABLES = ("accounts", "pros", "matches", "match_players", "scrape_meta")


# ---------------------------------------------------------------------------
# 輸出格式化
# ---------------------------------------------------------------------------
def _fmt_cell(value, max_width: int) -> str:
    """把欄位值轉成單行字串，過長（如 raw_json）截斷並加省略號。"""
    if value is None:
        return "NULL"
    text = str(value).replace("\n", "\\n").replace("\r", "")
    if len(text) > max_width:
        return text[:max_width - 1] + "…"
    return text


def render_table(columns: list[str], rows: list[tuple],
                 max_width: int) -> str:
    """以等寬文字表格輸出查詢結果（無第三方依賴）。"""
    matrix = [[_fmt_cell(v, max_width) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in matrix:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    sep = "-+-".join("-" * w for w in widths)
    out = [line(columns), sep]
    out.extend(line(row) for row in matrix)
    out.append(f"（{len(matrix)} 列）")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 查詢動作
# ---------------------------------------------------------------------------
def _open_ro():
    """開唯讀連線前先確認檔案存在，避免 FileNotFoundError。"""
    if not config.DPM_WAREHOUSE_PATH.exists():
        raise SystemExit(
            f"找不到資料庫：{config.DPM_WAREHOUSE_PATH}\n"
            "請先執行 python -m scripts.scrape_dpm 建立倉儲。")
    return dpm_store.connect(read_only=True)


def _existing_tables(con) -> set[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'").fetchall()
    return {r[0] for r in rows}


def show_overview(con) -> None:
    """印出各業務表列數與爬蟲最新進度。"""
    tables = [t for t in _TABLES if t in _existing_tables(con)]
    counts = [(t, con.execute(
        f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables]
    width = max(len(t) for t, _ in counts)
    print("資料庫：", config.DPM_WAREHOUSE_PATH)
    for name, cnt in counts:
        print(f"  {name.ljust(width)}  {cnt:>7} 列")
    print("\n爬蟲狀態（--meta 看完整鍵值）：")
    for key in ("stage", "stage_progress", "last_success_at", "last_error"):
        row = con.execute(
            "SELECT value FROM scrape_meta WHERE key = ?", [key]).fetchone()
        if row:
            print(f"  {key:<16} = {row[0]}")


def show_meta(con) -> None:
    """傾印 scrape_meta 全部鍵值。"""
    rows = con.execute(
        "SELECT key, value, updated_at FROM scrape_meta "
        "ORDER BY key").fetchall()
    print(render_table(["key", "value", "updated_at"], rows, WIDE_MAX_WIDTH))


def show_schema(con, table: str) -> None:
    """以 DESCRIBE 印出欄位名稱／型別／是否可空／主鍵。"""
    if table not in _existing_tables(con):
        raise SystemExit(f"找不到資料表：{table}")
    rows = con.execute(f'DESCRIBE "{table}"').fetchall()
    print(f"-- {table} 結構")
    print(render_table(
        ["column", "type", "null", "key", "default", "extra"],
        rows, DEFAULT_MAX_WIDTH))


def _validate_columns(con, table: str, cols: str) -> list[str]:
    """檢查 --cols 指定的欄位都存在，防呆打字錯誤。"""
    # PRAGMA table_info 欄序：cid, name, type, notnull, dflt_value, pk
    valid = {r[1] for r in con.execute(
        f'PRAGMA table_info("{table}")').fetchall()}
    picked = [c.strip() for c in cols.split(",") if c.strip()]
    bad = [c for c in picked if c not in valid]
    if bad:
        raise SystemExit(f"{table} 沒有這些欄位：{', '.join(bad)}\n"
                         f"可用欄位：{', '.join(sorted(valid))}")
    return picked


def browse_table(con, args, max_width: int) -> None:
    """依 --table／--where／--order／--cols 組成 SELECT 瀏覽列。"""
    table = args.table
    if table not in _existing_tables(con):
        raise SystemExit(f"找不到資料表：{table}（可用："
                         f"{', '.join(sorted(_existing_tables(con)))}）")
    cols = (_validate_columns(con, table, args.cols)
            if args.cols else ["*"])
    # 星號為 SQL 萬用字元不可加引號；一般欄位名才加引號
    select_cols = ", ".join("*" if c == "*" else f'"{c}"' for c in cols)
    sql = f'SELECT {select_cols} FROM "{table}"'
    params: list = []
    # WHERE／ORDER 為操作員下達的 SQL 片段（本地工具，信任使用者）
    if args.where:
        sql += f" WHERE {args.where}"
    if args.order:
        sql += f" ORDER BY {args.order}"
    sql += " LIMIT ?"
    params.append(args.limit)
    rows = con.execute(sql, params).fetchall()
    result_cols = [d[0] for d in con.description]
    print(render_table(result_cols, rows, max_width))


def run_sql(con, sql: str, max_width: int) -> None:
    """執行任意唯讀 SQL；有結果集就印表，否則提示列數。"""
    cursor = con.execute(sql)
    if cursor.description is None:
        print("敘述執行完成（唯讀連線，不會寫入資料）")
        return
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    print(render_table(cols, rows, max_width))


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dpm.duckdb 唯讀瀏覽工具")
    parser.add_argument("--table", help="瀏覽指定資料表")
    parser.add_argument("--cols", help="只看部分欄位，逗號分隔，如 "
                                       "match_id,champion_name,dpm_score")
    parser.add_argument("--where", help="WHERE 條件（SQL 片段）")
    parser.add_argument("--order", help="ORDER BY（SQL 片段）")
    parser.add_argument("-n", "--limit", type=int, default=20,
                        help="每表最多顯示列數（預設 20）")
    parser.add_argument("--schema", help="查看指定表結構（DESCRIBE）")
    parser.add_argument("--meta", action="store_true",
                        help="傾印爬蟲狀態 scrape_meta")
    parser.add_argument("--sql", help="直接執行任意 SELECT（唯讀連線）")
    parser.add_argument("--wide", action="store_true",
                        help="放寬欄寬上限（48 → 120 字元）")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    max_width = WIDE_MAX_WIDTH if args.wide else DEFAULT_MAX_WIDTH
    con = _open_ro()
    try:
        if args.sql:
            run_sql(con, args.sql, max_width)
        elif args.schema:
            show_schema(con, args.schema)
        elif args.meta:
            show_meta(con)
        elif args.table:
            browse_table(con, args, max_width)
        else:
            show_overview(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
