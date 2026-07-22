#!/usr/bin/env python3
"""
parse_theme_pools.py - Parse theme observation pool markdown files and
write structured data to reports.db.

Input: ~/wiki/finance/concepts/*观察池*.md
Output: reports.db table theme_pool_stocks
"""

import csv
import io
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

WIKI_DIR = Path.home() / "wiki/finance/concepts"
REPORTS_DB = Path.home() / "code/dashboard/data/reports.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS theme_pool_stocks (
    theme TEXT,
    segment TEXT,
    symbol TEXT,
    name TEXT,
    price REAL,
    change_pct REAL,
    volume REAL,
    source TEXT,
    comment TEXT,
    update_date TEXT,
    PRIMARY KEY (theme, symbol)
);
"""


def parse_change_pct(raw: str) -> float:
    """Parse change percent like '5.38%', '-3.02% 🔻', '**+5.38%** 🚀'."""
    if not raw or raw.strip() in ("—", "-", "", "--"):
        return 0.0
    # Strip markdown bold markers and emoji suffixes
    s = raw.replace("**", "").strip()
    s = re.sub(r'[🔻🚀🔥📈📉💥⭐✨]+', '', s).strip()
    # Remove the percent sign and parse
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def parse_price(raw: str) -> float:
    """Parse price string, return 0.0 for missing values."""
    if not raw or raw.strip() in ("—", "-", "", "--"):
        return 0.0
    try:
        return float(raw.strip().replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def parse_volume(raw: str) -> float:
    """Parse volume (成交额亿) string."""
    if not raw or raw.strip() in ("—", "-", "", "--"):
        return 0.0
    try:
        return float(raw.strip().replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def extract_theme_name(filename: str) -> str:
    """Extract theme name from filename like 'AI产业链-观察池.md' → 'AI产业链'."""
    basename = os.path.basename(filename)
    # Remove extension
    name = basename.replace(".md", "")
    # Remove '-观察池' or ' 观察池' suffix
    name = re.sub(r'[-_ 　]*观察池$', '', name)
    return name.strip()


def extract_segment_name(header_line: str) -> str:
    """Extract segment name from ## header like '## 上游-存储芯片 (5只)' → '上游-存储芯片'."""
    # Remove leading ## and whitespace
    s = re.sub(r'^#+\s*', '', header_line)
    # Remove trailing count like '(5只)', '(N只)'
    s = re.sub(r'\s*\(\d+只\)\s*$', '', s)
    return s.strip()


def extract_comment(block_lines: list) -> str:
    """Combine > blockquote lines into a single comment string."""
    parts = []
    for line in block_lines:
        # Remove leading '> ' or '>' prefix
        cleaned = re.sub(r'^>\s*', '', line)
        parts.append(cleaned.strip())
    return " | ".join(parts).strip()


def identify_columns(header_cells: list) -> dict:
    """Map expected column names to indices based on header row.
    
    Expected: 代码, 名称, 现价, 涨跌, 成交额(亿), 来源
    Returns: dict mapping column_key -> index or None
    """
    col_map = {
        "symbol": None,   # 代码
        "name": None,     # 名称
        "price": None,    # 现价
        "change_pct": None,  # 涨跌
        "volume": None,   # 成交额(亿)
        "source": None,   # 来源
    }
    
    # Clean header cells
    cells = [c.strip().replace("|", "") for c in header_cells]
    
    for i, cell in enumerate(cells):
        if cell == "代码":
            col_map["symbol"] = i
        elif cell == "名称":
            col_map["name"] = i
        elif cell == "现价":
            col_map["price"] = i
        elif cell == "涨跌":
            col_map["change_pct"] = i
        elif "成交额" in cell:
            col_map["volume"] = i
        elif cell == "来源":
            col_map["source"] = i
    
    return col_map


def parse_markdown_table(lines: list, start_idx: int) -> tuple:
    """Parse a markdown table starting at start_idx.
    
    Returns: (header_indices_dict, rows_list, new_idx)
    """
    i = start_idx
    # Skip to table header (first row containing | 代码 |)
    while i < len(lines) and "|" not in lines[i]:
        i += 1
    if i >= len(lines):
        return None, [], i
    
    header_line = lines[i].strip()
    if not header_line.startswith("|"):
        # Try to find a line with |
        while i < len(lines) and "| 代码" not in lines[i]:
            i += 1
        if i >= len(lines):
            return None, [], i
        header_line = lines[i].strip()
    
    # Parse header cells
    header_cells = [c.strip() for c in header_line.split("|") if c.strip()]
    col_map = identify_columns(header_cells)
    
    # Skip separator line (---|---|---)
    i += 1
    
    # Parse data rows
    rows = []
    i += 1
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("|") or line.startswith("|---"):
            break
        cells = [c.strip() for c in line.split("|")[1:-1]]  # Skip leading/trailing empty
        if len(cells) < 2:
            i += 1
            continue
        
        row = {}
        for key, idx in col_map.items():
            if idx is not None and idx < len(cells):
                row[key] = cells[idx]
            else:
                row[key] = ""
        
        rows.append(row)
        i += 1
    
    return col_map, rows, i


def process_file(filepath: Path) -> list:
    """Process a single observation pool markdown file.
    
    Returns: list of dicts with keys for theme_pool_stocks insertion.
    """
    print(f"  Processing: {filepath.name}")
    theme = extract_theme_name(str(filepath))
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    lines = content.split("\n")
    
    today = date.today().isoformat()
    records = []
    current_segment = ""
    current_comment = ""
    in_table = False
    in_comment_block = False
    comment_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect segment header
        if line.startswith("## ") and "|" not in line:
            current_segment = extract_segment_name(line)
            current_comment = ""
            comment_lines = []
            in_comment_block = False
            i += 1
            continue
        
        # Detect table start
        if "| 代码" in line or "|代码" in line:
            # Check if this actually has the expected columns
            # We need to look at the header
            col_map, rows, next_i = parse_markdown_table(lines, i)
            
            if rows:
                for row in rows:
                    record = {
                        "theme": theme,
                        "segment": current_segment,
                        "symbol": row.get("symbol", ""),
                        "name": row.get("name", ""),
                        "price": parse_price(row.get("price", "")),
                        "change_pct": parse_change_pct(row.get("change_pct", "")),
                        "volume": parse_volume(row.get("volume", "")),
                        "source": row.get("source", ""),
                        "comment": current_comment,
                        "update_date": today,
                    }
                    if record["symbol"]:  # Only add if we have a symbol
                        records.append(record)
                
                # Advance i past the table
                i = next_i
                continue
            
            i += 1
            continue
        
        # Detect comment block (>) after a table
        if line.startswith(">") and not line.startswith("> 更新:") and not line.startswith("> 覆盖:") and not line.startswith("> 标的:"):
            # Check it's not a table section comment
            comment_lines.append(line)
            in_comment_block = True
            i += 1
            continue
        
        if in_comment_block and line.strip() == "":
            # End of comment block
            current_comment = extract_comment(comment_lines)
            comment_lines = []
            in_comment_block = False
            # Update all records in current segment with this comment
            for record in reversed(records):
                if record["segment"] == current_segment and not record["comment"]:
                    record["comment"] = current_comment
                else:
                    break
        
        if in_comment_block and not line.startswith(">"):
            current_comment = extract_comment(comment_lines)
            comment_lines = []
            in_comment_block = False
            for record in reversed(records):
                if record["segment"] == current_segment and not record["comment"]:
                    record["comment"] = current_comment
                else:
                    break
        
        i += 1
    
    # Handle trailing comment if any
    if comment_lines:
        current_comment = extract_comment(comment_lines)
        for record in reversed(records):
            if record["segment"] == current_segment and not record["comment"]:
                record["comment"] = current_comment
            else:
                break
    
    print(f"    Theme: {theme}, Records: {len(records)}")
    return records


def main():
    print("=" * 60)
    print("parse_theme_pools.py - Parse theme observation pools")
    print("=" * 60)
    
    # Find all observation pool markdown files
    md_files = sorted(WIKI_DIR.glob("*观察池*.md"))
    if not md_files:
        print(f"  No *观察池*.md files found in {WIKI_DIR}")
        sys.exit(1)
    
    print(f"\nFound {len(md_files)} observation pool files:")
    for f in md_files:
        print(f"  - {f.name}")
    
    # Process all files
    all_records = []
    for filepath in md_files:
        try:
            records = process_file(filepath)
            all_records.extend(records)
        except Exception as e:
            print(f"  ERROR processing {filepath.name}: {e}")
    
    print(f"\nTotal records parsed: {len(all_records)}")
    
    if not all_records:
        print("No records to insert. Exiting.")
        return
    
    # Write to reports.db
    print(f"\nWriting to reports.db: {REPORTS_DB}")
    conn = sqlite3.connect(str(REPORTS_DB))
    cursor = conn.cursor()
    
    cursor.execute(CREATE_SQL)
    
    # Group records by theme for deletion
    themes = set(r["theme"] for r in all_records)
    for theme in themes:
        cursor.execute("DELETE FROM theme_pool_stocks WHERE theme = ?", (theme,))
        print(f"  Deleted existing records for theme '{theme}'")
    
    # Insert records
    insert_sql = """
        INSERT OR REPLACE INTO theme_pool_stocks
        (theme, segment, symbol, name, price, change_pct, volume, source, comment, update_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    inserted = 0
    for record in all_records:
        try:
            cursor.execute(insert_sql, (
                record["theme"],
                record["segment"],
                record["symbol"],
                record["name"],
                record["price"],
                record["change_pct"],
                record["volume"],
                record["source"],
                record["comment"],
                record["update_date"],
            ))
            inserted += 1
        except Exception as e:
            print(f"  ERROR inserting {record['symbol']}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n{'=' * 60}")
    print(f"Summary:")
    print(f"  Files processed: {len(md_files)}")
    print(f"  Records inserted: {inserted}")
    print(f"  Themes: {', '.join(sorted(themes))}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
