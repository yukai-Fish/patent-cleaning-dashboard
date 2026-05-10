* 生成引用截尾标准化变量：两遍小汇总表法
* 输入目录: F:\专利研究\lite输出
* 输出目录: F:\专利研究\reg_ready
*
* 核心原则:
* 1. 不 append 全样本个体专利大表。
* 2. 第一遍逐个打开年份或切片，执行与最终样本一致的基础清洗和去重。
* 3. 第一遍只保存 app_year x ipc_subclass 层面的 sum 和 count 小汇总表。
* 4. 第二步 append 小汇总表，再用总 sum / 总 count 得到加权均值。
* 5. 第三遍逐个打开原始文件，执行同样口径，merge 均值表，生成 feature 文件。

version 16
clear all
set more off

global root "F:\专利研究"
global in   "$root\lite输出"
global out  "$root\reg_ready"

cap mkdir "$out"
cap mkdir "$out\stats_parts"
cap mkdir "$out\feature_parts"

capture log close _all
log using "$out\生成引用截尾标准化变量.log", replace text

*------------------------------------------------------------
* 统一基础清洗口径：第一遍和第三遍必须都调用这个程序
*------------------------------------------------------------
capture program drop prepare_analysis_sample
program define prepare_analysis_sample
    version 16

    capture drop __row_no
    gen long __row_no = _n

    * 清理可能重复生成的变量，保证可重复运行。
    capture drop file_year app_date app_year grant_date grant_year
    capture drop ipc_section ipc_section_id ipc_class ipc_subclass
    capture drop type_rank dup_key
    capture drop y_total y_internal y_external y_family external_bias
    capture drop rel_total rel_external rel_internal
    capture drop ln_rel_total ln_rel_external ln_rel_internal
    capture drop mean_total mean_external mean_internal

    * 引用变量转为数值型；缺失按 0 处理。
    local citevars 引证次数 被引证次数 自引次数 他引次数 被自引次数 被他引次数 家族引证次数 家族被引证次数
    foreach v of local citevars {
        capture confirm variable `v'
        if _rc {
            gen double `v' = 0
        }
        else {
            capture confirm string variable `v'
            if !_rc {
                replace `v' = ustrtrim(`v')
                replace `v' = "0" if `v' == ""
                replace `v' = subinstr(`v', "，", "", .)
                replace `v' = subinstr(`v', ",", "", .)
                capture destring `v', replace force
                capture confirm numeric variable `v'
                if _rc {
                    tempvar __num
                    gen double `__num' = real(subinstr(subinstr(ustrtrim(`v'), "，", "", .), ",", "", .))
                    replace `__num' = 0 if missing(`__num')
                    drop `v'
                    rename `__num' `v'
                }
            }
            replace `v' = 0 if missing(`v')
            capture recast double `v'
        }
    }

    * app_year：优先由申请日生成；如果申请日不可用，则用已有年份。
    capture confirm variable 申请日
    if !_rc {
        capture confirm string variable 申请日
        if !_rc {
            tempvar __app_s
            gen str32 `__app_s' = substr(ustrtrim(申请日), 1, 32)
            replace `__app_s' = subinstr(`__app_s', "年", "-", .)
            replace `__app_s' = subinstr(`__app_s', "月", "-", .)
            replace `__app_s' = subinstr(`__app_s', "日", "", .)
            gen double app_date = date(`__app_s', "YMD")
        }
        else {
            gen double app_date = 申请日
            quietly count if app_date > td(01jan2100) & !missing(app_date)
            if r(N) > 0 {
                tempvar __app_n_s
                tostring 申请日, gen(`__app_n_s') usedisplayformat force
                replace app_date = date(`__app_n_s', "YMD") if app_date > td(01jan2100) & !missing(app_date)
            }
        }
        format app_date %td
        gen int app_year = year(app_date)
    }
    else {
        gen int app_year = .
    }

    capture confirm variable 年份
    if !_rc {
        capture replace app_year = 年份 if missing(app_year)
    }
    capture confirm variable file_year
    if _rc {
        gen int file_year = app_year
    }

    * 授权日期变量；如果无法识别，则保留为空，保证第三遍 keep 不报错。
    capture confirm variable 授权公告日
    if !_rc {
        capture confirm string variable 授权公告日
        if !_rc {
            tempvar __grant_s
            gen str32 `__grant_s' = substr(ustrtrim(授权公告日), 1, 32)
            replace `__grant_s' = subinstr(`__grant_s', "年", "-", .)
            replace `__grant_s' = subinstr(`__grant_s', "月", "-", .)
            replace `__grant_s' = subinstr(`__grant_s', "日", "", .)
            gen double grant_date = date(`__grant_s', "YMD")
        }
        else {
            gen double grant_date = 授权公告日
            quietly count if grant_date > td(01jan2100) & !missing(grant_date)
            if r(N) > 0 {
                tempvar __grant_n_s
                tostring 授权公告日, gen(`__grant_n_s') usedisplayformat force
                replace grant_date = date(`__grant_n_s', "YMD") if grant_date > td(01jan2100) & !missing(grant_date)
            }
        }
    }
    else {
        gen double grant_date = .
    }
    format grant_date %td
    gen int grant_year = year(grant_date)

    * IPC 小类：前四位。
    capture confirm variable IPC主分类
    if !_rc {
        capture confirm string variable IPC主分类
        if !_rc {
            gen str1 ipc_section = substr(ustrtrim(IPC主分类), 1, 1)
            gen str3 ipc_class = substr(ustrtrim(IPC主分类), 1, 3)
            gen str4 ipc_subclass = substr(ustrtrim(IPC主分类), 1, 4)
        }
        else {
            tempvar __ipc_s
            tostring IPC主分类, gen(`__ipc_s') usedisplayformat force
            gen str1 ipc_section = substr(ustrtrim(`__ipc_s'), 1, 1)
            gen str3 ipc_class = substr(ustrtrim(`__ipc_s'), 1, 3)
            gen str4 ipc_subclass = substr(ustrtrim(`__ipc_s'), 1, 4)
        }
    }
    else {
        gen str1 ipc_section = ""
        gen str3 ipc_class = ""
        gen str4 ipc_subclass = ""
    }

    gen byte ipc_section_id = .
    replace ipc_section_id = 1 if ipc_section == "A"
    replace ipc_section_id = 2 if ipc_section == "B"
    replace ipc_section_id = 3 if ipc_section == "C"
    replace ipc_section_id = 4 if ipc_section == "D"
    replace ipc_section_id = 5 if ipc_section == "E"
    replace ipc_section_id = 6 if ipc_section == "F"
    replace ipc_section_id = 7 if ipc_section == "G"
    replace ipc_section_id = 8 if ipc_section == "H"
    replace ipc_section_id = 9 if missing(ipc_section_id) & ipc_section != ""

    * 专利类型优先级：数值越小越优先保留。
    gen byte type_rank = 9
    capture confirm variable 专利类型
    if !_rc {
        capture confirm string variable 专利类型
        if !_rc {
            replace type_rank = 1 if ustrtrim(专利类型) == "发明授权"
            replace type_rank = 2 if ustrtrim(专利类型) == "发明申请"
            replace type_rank = 3 if ustrtrim(专利类型) == "实用新型"
            replace type_rank = 4 if ustrtrim(专利类型) == "外观设计"
        }
    }

    * dup_key：优先申请号，申请号缺失时使用 newipzlid。
    gen str244 dup_key = ""
    capture confirm variable 申请号
    if !_rc {
        capture confirm string variable 申请号
        if !_rc {
            replace dup_key = substr(ustrtrim(申请号), 1, 244)
        }
        else {
            tempvar __appno_s
            tostring 申请号, gen(`__appno_s') usedisplayformat force
            replace dup_key = substr(ustrtrim(`__appno_s'), 1, 244)
        }
    }
    capture confirm variable newipzlid
    if !_rc {
        capture confirm string variable newipzlid
        if !_rc {
            replace dup_key = substr(ustrtrim(newipzlid), 1, 244) if dup_key == "" | dup_key == "."
        }
        else {
            tempvar __id_s
            tostring newipzlid, gen(`__id_s') usedisplayformat force
            replace dup_key = substr(ustrtrim(`__id_s'), 1, 244) if dup_key == "" | dup_key == "."
        }
    }
    replace dup_key = "missing_" + string(app_year) + "_" + string(__row_no, "%12.0f") if dup_key == "" | dup_key == "."

    * 与最终分析样本一致：先按 dup_key 去重，再参与均值计算和 feature 输出。
    sort dup_key type_rank file_year __row_no
    by dup_key: keep if _n == 1

    drop if missing(app_year)
    drop if missing(ipc_subclass) | ipc_subclass == ""

    gen double y_total    = ln(1 + 被引证次数)
    gen double y_internal = ln(1 + 被自引次数)
    gen double y_external = ln(1 + 被他引次数)
    gen double y_family   = ln(1 + 家族被引证次数)
    gen double external_bias = y_external - y_internal
end

*------------------------------------------------------------
* 文件清单：1985-2019 单文件；2020-2024 分片文件
*------------------------------------------------------------
capture program drop make_file_list
program define make_file_list
    version 16
    tempname handle
    postfile `handle' int year str300 path str120 stem using "$out\_input_files.dta", replace

    forvalues y = 1985/2019 {
        local f "$in\patent_`y'_lite.dta"
        capture confirm file "`f'"
        if !_rc {
            post `handle' (`y') (`"`f'"') (`"patent_`y'_lite"')
        }
    }

    forvalues y = 2020/2024 {
        local partdir "$in\patent_`y'_lite_parts"
        local files : dir "`partdir'" files "*.dta"
        foreach file of local files {
            local f "`partdir'\`file'"
            local stem = subinstr("`file'", ".dta", "", .)
            post `handle' (`y') (`"`f'"') (`"`stem'"')
        }
    }

    postclose `handle'
end

make_file_list

*------------------------------------------------------------
* 第一遍：逐文件清洗去重，然后 collapse 成小汇总表
*------------------------------------------------------------
use "$out\_input_files.dta", clear
local nfiles = _N
if `nfiles' == 0 {
    display as error "未找到任何输入 dta。请检查 $in"
    exit 601
}

forvalues i = 1/`nfiles' {
    use "$out\_input_files.dta", clear
    local f = path[`i']
    local stem = stem[`i']

    display as text "第一遍小汇总: `f'"
    use "`f'", clear
    prepare_analysis_sample

    gen double __n_total = 1
    collapse ///
        (sum) sum_total=被引证次数 ///
              sum_external=被他引次数 ///
              sum_internal=被自引次数 ///
              n_total=__n_total, ///
        by(app_year ipc_subclass)

    gen double n_external = n_total
    gen double n_internal = n_total
    compress
    save "$out\stats_parts\`stem'_stats.dta", replace
}

*------------------------------------------------------------
* 第二步：append 小汇总表，再求总 sum / 总 count
*------------------------------------------------------------
use "$out\_input_files.dta", clear
local nfiles = _N
local first_stats = 1
forvalues i = 1/`nfiles' {
    use "$out\_input_files.dta", clear
    local stem = stem[`i']
    if `first_stats' {
        use "$out\stats_parts\`stem'_stats.dta", clear
        save "$out\_all_stats.dta", replace
        local first_stats = 0
    }
    else {
        use "$out\_all_stats.dta", clear
        append using "$out\stats_parts\`stem'_stats.dta"
        save "$out\_all_stats.dta", replace
    }
}

use "$out\_all_stats.dta", clear
drop if missing(app_year) | ipc_subclass == ""
collapse (sum) sum_total sum_external sum_internal n_total n_external n_internal, ///
    by(app_year ipc_subclass)

gen double mean_total = sum_total / n_total if n_total > 0
gen double mean_external = sum_external / n_external if n_external > 0
gen double mean_internal = sum_internal / n_internal if n_internal > 0

label var mean_total "全样本同年同IPC平均被引证次数"
label var mean_external "全样本同年同IPC平均被他引次数"
label var mean_internal "全样本同年同IPC平均被自引次数"

keep app_year ipc_subclass mean_total mean_external mean_internal ///
     sum_total sum_external sum_internal n_total n_external n_internal
compress
save "$out\ipc_year_citation_means.dta", replace

*------------------------------------------------------------
* 第三遍：逐文件同口径清洗去重，merge 均值表并保存 feature 文件
*------------------------------------------------------------
use "$out\_input_files.dta", clear
local nfiles = _N
forvalues i = 1/`nfiles' {
    use "$out\_input_files.dta", clear
    local y = year[`i']
    local f = path[`i']
    local stem = stem[`i']

    local outdir "$out\feature_parts\patent_`y'_feature_parts"
    cap mkdir "`outdir'"
    local outfile "`outdir'\`stem'_feature.dta"

    display as text "第三遍生成 feature: `f'"
    use "`f'", clear
    prepare_analysis_sample

    merge m:1 app_year ipc_subclass using "$out\ipc_year_citation_means.dta", nogen keep(master match)

    gen double rel_total = 被引证次数 / mean_total if mean_total > 0
    gen double rel_external = 被他引次数 / mean_external if mean_external > 0
    gen double rel_internal = 被自引次数 / mean_internal if mean_internal > 0

    gen double ln_rel_total = ln(1 + rel_total)
    gen double ln_rel_external = ln(1 + rel_external)
    gen double ln_rel_internal = ln(1 + rel_internal)

    label var rel_total "被引证次数/全样本同年同IPC平均被引证次数"
    label var rel_external "被他引次数/全样本同年同IPC平均被他引次数"
    label var rel_internal "被自引次数/全样本同年同IPC平均被自引次数"
    label var ln_rel_total "ln(1+rel_total)"
    label var ln_rel_external "ln(1+rel_external)"
    label var ln_rel_internal "ln(1+rel_internal)"

    * 只保留回归必要变量，避免标题、摘要、权利要求等长文本占内存。
    keep newipzlid 年份 file_year app_year 申请日 app_date 申请号 dup_key ///
        公开公告号 公开公告日 授权公告号 授权公告日 grant_date grant_year ///
        专利类型 type_rank 申请人 当前权利人 申请人类型 applicant_num ///
        发明人 inventor_num lead_inventor IPC主分类 IPC ipc_section ipc_section_id ipc_class ipc_subclass ///
        省 省代码 市 市代码 县 县代码 ///
        引证次数 被引证次数 自引次数 他引次数 被自引次数 被他引次数 家族引证次数 家族被引证次数 ///
        title_len abstract_len claim1_len indepclaim_len 文献页数 ///
        y_total y_internal y_external y_family external_bias ///
        mean_total mean_external mean_internal ///
        rel_total rel_external rel_internal ln_rel_total ln_rel_external ln_rel_internal

    compress
    save "`outfile'", replace
}

display as result "完成。均值表: $out\ipc_year_citation_means.dta"
display as result "feature 输出目录: $out\feature_parts"
log close
