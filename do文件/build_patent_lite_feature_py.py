"""
Python version of the patent lite cleaning step.

Default behavior:
    python do文件/build_patent_lite_feature_py.py --start-year 2013 --end-year 2024

The script writes one Stata file per year:
    lite输出/patent_YYYY_lite.dta

Each year is first written to a temporary file and then atomically renamed, so
completed years can be skipped safely after an interruption.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyreadstat
except ImportError:  # pragma: no cover - optional fast writer
    pyreadstat = None


GENERATED_PREFIXES = ("inv_", "__")

GENERATED_COLUMNS = [
    "file_year",
    "app_date",
    "app_year",
    "grant_date",
    "grant_year",
    "title_len",
    "abstract_len",
    "claim1_len",
    "indepclaim_len",
    "ipc_section",
    "ipc_section_id",
    "ipc_class",
    "ipc_subclass",
    "inventor_str",
    "inventor_num",
    "lead_inventor",
    "applicant_str",
    "applicant_num",
    "type_rank",
    "dup_key",
    "y_total",
    "y_internal",
    "y_external",
    "y_family",
    "external_bias",
]

CITE_COLUMNS = [
    "引证次数",
    "被引证次数",
    "自引次数",
    "他引次数",
    "被自引次数",
    "被他引次数",
    "家族引证次数",
    "家族被引证次数",
]

DROP_COLUMNS = [
    "标题",
    "摘要",
    "首项权利要求",
    "独立权利要求",
    "公开国别",
    "洛迦诺分类号",
    "申请人国家_地区",
    "申请人地址",
    "当前专利权人地址",
    "工商注册地址",
    "工商公司类型",
    "工商成立日期",
    "工商统一社会信用代码",
    "工商注册号",
    "工商上市代码",
    "工商企业状态",
    "优先权信息",
    "优先权号",
    "优先权日",
]

FIRST_COLUMNS = [
    "newipzlid",
    "年份",
    "file_year",
    "申请日",
    "app_date",
    "app_year",
    "申请号",
    "dup_key",
    "公开公告号",
    "公开公告日",
    "授权公告号",
    "授权公告日",
    "grant_date",
    "grant_year",
    "专利类型",
    "type_rank",
    "申请人",
    "当前权利人",
    "申请人类型",
    "applicant_str",
    "applicant_num",
    "发明人",
    "inventor_str",
    "inventor_num",
    "lead_inventor",
    "IPC主分类",
    "IPC",
    "ipc_section",
    "ipc_section_id",
    "ipc_class",
    "ipc_subclass",
    "省",
    "省代码",
    "市",
    "市代码",
    "县",
    "县代码",
    "引证次数",
    "被引证次数",
    "自引次数",
    "他引次数",
    "被自引次数",
    "被他引次数",
    "家族引证次数",
    "家族被引证次数",
    "title_len",
    "abstract_len",
    "claim1_len",
    "indepclaim_len",
    "y_total",
    "y_internal",
    "y_external",
    "y_family",
    "external_bias",
]

TYPE_RANK = {
    "发明授权": 1,
    "发明申请": 2,
    "实用新型": 3,
    "外观设计": 4,
}

IPC_SECTION_ID = {
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
}

NAME_SEPARATOR_RE = re.compile(r"[；、，,]")


def find_default_dirs(cwd: Path) -> tuple[Path, Path]:
    db_dir = None
    out_dir = None
    for child in cwd.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("数据库"):
            db_dir = child
        elif "lite" in child.name.lower() or child.name.startswith("lite"):
            out_dir = child

    if db_dir is None:
        raise FileNotFoundError("Could not find the database directory under the current workspace.")
    if out_dir is None:
        out_dir = cwd / "lite输出"
        out_dir.mkdir(parents=True, exist_ok=True)
    return db_dir, out_dir


def find_year_file(root: Path, year: int) -> Path | None:
    direct = root / f"{year}.dta"
    nested = root / f"{year}.dta" / f"{year}.dta"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested

    matches = [p for p in root.rglob(f"{year}.dta") if p.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_size, reverse=True)
    return matches[0]


def setup_logging(out_dir: Path) -> None:
    log_file = out_dir / "patent_python_cleaning.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def drop_generated_columns(df: pd.DataFrame) -> pd.DataFrame:
    to_drop = [
        col
        for col in df.columns
        if col in GENERATED_COLUMNS or any(col.startswith(prefix) for prefix in GENERATED_PREFIXES)
    ]
    return df.drop(columns=to_drop, errors="ignore")


def text_len(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(pd.NA, index=[])
    return series.astype("string").str.len().astype("Int32")


def ensure_text(df: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="string")
    return df[column].astype("string").fillna(default)


def clean_numeric(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(0.0, index=index, dtype="float64")
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0).astype("float64")
    cleaned = (
        series.astype("string")
        .fillna("")
        .str.strip()
        .str.replace("，", "", regex=False)
        .str.replace(",", "", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0).astype("float64")


def normalize_date(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(pd.NaT, index=index, dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        # Large numeric values are usually display-formatted dates such as 20130101.
        as_text = numeric.round().astype("Int64").astype("string")
        parsed_text = pd.to_datetime(as_text, format="%Y%m%d", errors="coerce")
        stata_origin = pd.Timestamp("1960-01-01")
        parsed_stata = stata_origin + pd.to_timedelta(numeric, unit="D")
        return parsed_text.fillna(parsed_stata)

    text = (
        series.astype("string")
        .fillna("")
        .str.strip()
        .str.slice(0, 32)
        .str.replace("年", "-", regex=False)
        .str.replace("月", "-", regex=False)
        .str.replace("日", "", regex=False)
    )
    return pd.to_datetime(text, errors="coerce")


def normalize_people(series: pd.Series) -> pd.Series:
    text = series.astype("string").fillna("").str.strip()
    text = text.map(lambda value: NAME_SEPARATOR_RE.sub(";", value))
    text = text.str.replace(r"[ ]*;[ ]*", ";", regex=True)
    text = text.str.replace(r";+", ";", regex=True)
    text = text.str.replace(r"^;|;$", "", regex=True)
    return text


def count_people(series: pd.Series) -> pd.Series:
    stripped = series.astype("string").fillna("").str.strip()
    counts = stripped.str.count(";").fillna(0).astype("Int32") + 1
    return counts.where(stripped.ne(""), 0).astype("Int32")


def first_person(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.split(";", n=1).str[0].str.strip()


def make_dup_key(df: pd.DataFrame, year: int) -> pd.Series:
    dup_key = ensure_text(df, "申请号").str.strip().str.slice(0, 244)
    if "newipzlid" in df.columns:
        fallback = ensure_text(df, "newipzlid").str.strip().str.slice(0, 244)
        dup_key = dup_key.mask(dup_key.eq("") | dup_key.eq("."), fallback)
    missing = dup_key.eq("") | dup_key.eq(".") | dup_key.isna()
    if missing.any():
        row_numbers = pd.Series(np.arange(1, len(df) + 1), index=df.index).astype("string")
        dup_key = dup_key.mask(missing, f"missing_{year}_" + row_numbers)
    return dup_key.astype("string")


def clean_chunk(df: pd.DataFrame, year: int) -> pd.DataFrame:
    df = drop_generated_columns(df.copy())
    index = df.index

    df["file_year"] = np.int16(year)

    for source, target in [
        ("标题", "title_len"),
        ("摘要", "abstract_len"),
        ("首项权利要求", "claim1_len"),
        ("独立权利要求", "indepclaim_len"),
    ]:
        if source in df.columns:
            df[target] = df[source].astype("string").str.len().astype("Int32")
        else:
            df[target] = pd.Series(pd.NA, index=index, dtype="Int32")

    for col in CITE_COLUMNS:
        df[col] = clean_numeric(df[col] if col in df.columns else None, index)

    df["app_date"] = normalize_date(df["申请日"] if "申请日" in df.columns else None, index)
    df["app_year"] = df["app_date"].dt.year.astype("Int32")
    df["grant_date"] = normalize_date(df["授权公告日"] if "授权公告日" in df.columns else None, index)
    df["grant_year"] = df["grant_date"].dt.year.astype("Int32")

    ipc_main = ensure_text(df, "IPC主分类").str.strip()
    df["ipc_section"] = ipc_main.str.slice(0, 1)
    df["ipc_class"] = ipc_main.str.slice(0, 3)
    df["ipc_subclass"] = ipc_main.str.slice(0, 4)
    section_id = df["ipc_section"].map(IPC_SECTION_ID)
    section_id = section_id.where(section_id.notna() | df["ipc_section"].eq(""), 9)
    df["ipc_section_id"] = section_id.astype("Int8")

    df["inventor_str"] = normalize_people(ensure_text(df, "发明人"))
    df["inventor_num"] = count_people(df["inventor_str"])
    df["lead_inventor"] = first_person(df["inventor_str"])

    df["applicant_str"] = normalize_people(ensure_text(df, "申请人"))
    df["applicant_num"] = count_people(df["applicant_str"])

    if "专利类型" not in df.columns:
        df["专利类型"] = ""
    patent_type = ensure_text(df, "专利类型").str.strip()
    df["type_rank"] = patent_type.map(TYPE_RANK).fillna(9).astype("Int8")

    df["dup_key"] = make_dup_key(df, year)

    df["y_total"] = np.log1p(df["被引证次数"].astype("float64"))
    df["y_internal"] = np.log1p(df["被自引次数"].astype("float64"))
    df["y_external"] = np.log1p(df["被他引次数"].astype("float64"))
    df["y_family"] = np.log1p(df["家族被引证次数"].astype("float64"))
    df["external_bias"] = df["y_external"] - df["y_internal"]

    df = df.drop(columns=DROP_COLUMNS, errors="ignore")
    df = df[[col for col in FIRST_COLUMNS if col in df.columns] + [col for col in df.columns if col not in FIRST_COLUMNS]]
    return df


def downcast_for_stata(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if pd.api.types.is_integer_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], downcast="integer")
        elif pd.api.types.is_float_dtype(df[col]) and col not in {
            "y_total",
            "y_internal",
            "y_external",
            "y_family",
            "external_bias",
        }:
            non_missing = df[col].dropna()
            if not non_missing.empty and np.all(np.isclose(non_missing, np.round(non_missing))):
                df[col] = pd.to_numeric(df[col], downcast="integer")
            else:
                df[col] = pd.to_numeric(df[col], downcast="float")
    return df


def stata_date_days(series: pd.Series) -> pd.Series:
    origin = pd.Timestamp("1960-01-01")
    dates = pd.to_datetime(series, errors="coerce")
    return (dates - origin).dt.days.astype("float64")


def prepare_for_pyreadstat(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    out = df.copy()
    variable_format: dict[str, str] = {}
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = stata_date_days(out[col])
            variable_format[col] = "%td"
        elif pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].astype("object").where(out[col].notna(), "")
        elif pd.api.types.is_integer_dtype(out[col]) and str(out[col].dtype).startswith(("Int", "UInt")):
            out[col] = out[col].astype("float64")
    return out, variable_format


def write_stata(df: pd.DataFrame, path: Path) -> None:
    if pyreadstat is not None:
        write_df, variable_format = prepare_for_pyreadstat(df)
        pyreadstat.write_dta(
            write_df,
            path,
            version=15,
            variable_format=variable_format,
        )
        return

    convert_dates = {
        col: "td"
        for col in ["申请日", "app_date", "grant_date"]
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col])
    }
    if "授权公告日" in df.columns and pd.api.types.is_datetime64_any_dtype(df["授权公告日"]):
        convert_dates["授权公告日"] = "td"
    df.to_stata(
        path,
        write_index=False,
        version=118,
        convert_dates=convert_dates,
    )


def clean_one_year(
    year: int,
    root: Path,
    out_dir: Path,
    chunksize: int,
    overwrite: bool,
    max_rows: int | None,
) -> None:
    outfile = out_dir / f"patent_{year}_lite.dta"
    tmpfile = out_dir / f"patent_{year}_lite.tmp.dta"
    if outfile.exists() and not overwrite:
        logging.info("Year %s already exists, skipping: %s", year, outfile)
        return

    infile = find_year_file(root, year)
    if infile is None:
        logging.warning("Year %s input file not found under %s, skipping.", year, root)
        return

    logging.info("=" * 60)
    logging.info("Cleaning year %s", year)
    logging.info("Input: %s", infile)
    logging.info("Output: %s", outfile)

    started = time.time()
    chunks: list[pd.DataFrame] = []
    rows_raw = 0
    reader = pd.read_stata(str(infile), chunksize=chunksize, convert_categoricals=False)
    for chunk_no, chunk in enumerate(reader, start=1):
        if max_rows is not None:
            remaining = max_rows - rows_raw
            if remaining <= 0:
                break
            chunk = chunk.head(remaining)
        rows_raw += len(chunk)
        chunks.append(clean_chunk(chunk, year))
        logging.info("Year %s chunk %s: raw rows processed = %s", year, chunk_no, rows_raw)
        if max_rows is not None and rows_raw >= max_rows:
            break

    if not chunks:
        logging.warning("Year %s has no rows after reading.", year)
        return

    df = pd.concat(chunks, ignore_index=True)
    rows_before = len(df)
    logging.info("Year %s before single-year dedup rows = %s", year, rows_before)

    df = df.sort_values(["dup_key", "type_rank", "file_year"], kind="mergesort")
    df = df.drop_duplicates(subset=["dup_key"], keep="first").reset_index(drop=True)
    rows_after = len(df)
    logging.info("Year %s after single-year dedup rows = %s", year, rows_after)

    df = downcast_for_stata(df)
    if tmpfile.exists():
        tmpfile.unlink()
    write_stata(df, tmpfile)
    os.replace(tmpfile, outfile)

    elapsed = time.time() - started
    logging.info(
        "Year %s complete: raw=%s before_dedup=%s after_dedup=%s elapsed_min=%.2f",
        year,
        rows_raw,
        rows_before,
        rows_after,
        elapsed / 60,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean patent .dta files into yearly lite .dta files.")
    parser.add_argument("--start-year", type=int, default=2013)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--root", type=Path, default=None, help="Directory containing yearly raw .dta files.")
    parser.add_argument("--out", type=Path, default=None, help="Directory for yearly lite .dta files.")
    parser.add_argument("--chunksize", type=int, default=100_000)
    parser.add_argument("--overwrite", action="store_true", help="Rebuild years whose output files already exist.")
    parser.add_argument("--max-rows", type=int, default=None, help="Debug option: process only the first N rows per year.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()
    default_root, default_out = find_default_dirs(cwd)
    root = args.root or default_root
    out_dir = args.out or default_out
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir)

    logging.info("Root directory: %s", root)
    logging.info("Output directory: %s", out_dir)
    logging.info("Year range: %s-%s", args.start_year, args.end_year)

    for year in range(args.start_year, args.end_year + 1):
        clean_one_year(
            year=year,
            root=root,
            out_dir=out_dir,
            chunksize=args.chunksize,
            overwrite=args.overwrite,
            max_rows=args.max_rows,
        )
    logging.info("All requested years finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
