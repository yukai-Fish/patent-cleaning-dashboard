# Patent Cleaning Dashboard

本项目包含两部分：

- `do文件/build_patent_lite_feature_py.py`：用 Python 清洗 2013-2024 年专利 `.dta` 数据，并逐年输出 lite 文件。
- `进度看板/`：本地实时进度看板，读取清洗日志和输出文件，显示当前年份、年度进度条、整体进度和最近日志。

## 目录要求

脚本默认在项目根目录下寻找：

- `数据库/`：原始年度 `.dta` 数据目录
- `lite输出/`：清洗后年度 lite 文件和日志输出目录

这些数据文件体积很大，不应提交到 GitHub。

## 启动进度看板

```powershell
python ".\进度看板\monitor_dashboard.py"
```

然后打开：

```text
http://127.0.0.1:8765
```

看板每 5 秒自动刷新一次。

## 开始清洗

```powershell
python ".\do文件\build_patent_lite_feature_py.py" --start-year 2013 --end-year 2024
```

脚本会逐年生成：

```text
lite输出/patent_YYYY_lite.dta
```

如果中断后重新运行，已经完成的年份会自动跳过。

## 大年份流式模式

2020 年以后文件较大，推荐使用低内存流式模式：

```powershell
python ".\do文件\build_patent_lite_feature_py.py" --start-year 2020 --end-year 2024 --engine streaming
```

流式模式会先把每个清洗后的数据块写成临时 Parquet，再用 DuckDB 做磁盘级去重，最后输出多个 `.dta` 分片：

```text
lite输出/patent_2020_lite_parts/
  manifest.json
  patent_2020_lite_part001.dta
  patent_2020_lite_part002.dta
  ...
```

这种模式不会把全年数据一直堆在内存里，更适合 2020-2023 这类大年份。

## 日志

Python 清洗日志默认写入：

```text
lite输出/patent_python_cleaning.log
```

进度看板会读取这个日志来计算当前年份和已处理行数。
