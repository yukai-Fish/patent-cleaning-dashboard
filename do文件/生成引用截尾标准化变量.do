* 生成同年同 IPC 内标准化引用变量
* 输入:  F:\专利研究\lite输出
* 输出:  F:\专利研究\reg_ready
* 说明:  2020-2024 为分片 dta，本脚本先汇总全体分片的 IPC-年份均值，再逐文件生成变量。

version 16
clear all
set more off

global root "F:\专利研究"
global in   "$root\lite输出"
global out  "$root\reg_ready"

cap mkdir "$out"
cap mkdir "$out\parts"

tempfile all_stats ipcyear_stats
clear
set obs 0
gen int app_year = .
gen str20 ipc_subclass = ""
gen double sum_cite_total = .
gen double sum_cite_external = .
gen double sum_cite_internal = .
gen double n_cite_total = .
gen double n_cite_external = .
gen double n_cite_internal = .
save `all_stats', replace

program define add_ipc_stats
    syntax using/
    preserve
        use `"`using'"', clear
        keep app_year ipc_subclass 被引证次数 被他引证次数 被自引次数
        drop if missing(app_year) | missing(ipc_subclass) | ipc_subclass == ""
        collapse ///
            (sum) sum_cite_total=被引证次数 ///
                  sum_cite_external=被他引证次数 ///
                  sum_cite_internal=被自引次数 ///
            (count) n_cite_total=被引证次数 ///
                    n_cite_external=被他引证次数 ///
                    n_cite_internal=被自引次数, ///
            by(app_year ipc_subclass)
        append using `all_stats'
        save `all_stats', replace
    restore
end

di as text "第一遍：汇总 IPC-年份引用均值..."
forvalues y = 1985/2019 {
    local f "$in\patent_`y'_lite.dta"
    cap confirm file "`f'"
    if !_rc {
        di as text "  stats: `f'"
        add_ipc_stats using "`f'"
    }
}

forvalues y = 2020/2024 {
    local partdir "$in\patent_`y'_lite_parts"
    local files : dir "`partdir'" files "*.dta"
    foreach file of local files {
        local f "`partdir'\`file'"
        di as text "  stats: `f'"
        add_ipc_stats using "`f'"
    }
}

use `all_stats', clear
collapse (sum) sum_cite_total sum_cite_external sum_cite_internal ///
               n_cite_total n_cite_external n_cite_internal, ///
    by(app_year ipc_subclass)

gen double mean_cite_total = sum_cite_total / n_cite_total if n_cite_total > 0
gen double mean_cite_external = sum_cite_external / n_cite_external if n_cite_external > 0
gen double mean_cite_internal = sum_cite_internal / n_cite_internal if n_cite_internal > 0
keep app_year ipc_subclass mean_cite_total mean_cite_external mean_cite_internal
compress
save `ipcyear_stats', replace
save "$out\ipc_year_citation_means.dta", replace

program define make_reg_file
    syntax using/, OUTFILE(string)
    use `"`using'"', clear
    merge m:1 app_year ipc_subclass using `ipcyear_stats', nogen keep(master match)

    gen double rel_total = 被引证次数 / mean_cite_total if mean_cite_total > 0
    gen double rel_external = 被他引证次数 / mean_cite_external if mean_cite_external > 0
    gen double rel_internal = 被自引次数 / mean_cite_internal if mean_cite_internal > 0

    gen double ln_rel_total = ln(1 + rel_total)
    gen double ln_rel_external = ln(1 + rel_external)
    gen double ln_rel_internal = ln(1 + rel_internal)

    label var mean_cite_total "同年同IPC平均被引证次数"
    label var mean_cite_external "同年同IPC平均被他引证次数"
    label var mean_cite_internal "同年同IPC平均被自引次数"
    label var rel_total "同年同IPC标准化被引证次数"
    label var rel_external "同年同IPC标准化被他引证次数"
    label var rel_internal "同年同IPC标准化被自引次数"
    label var ln_rel_total "ln(1+同年同IPC标准化被引证次数)"
    label var ln_rel_external "ln(1+同年同IPC标准化被他引证次数)"
    label var ln_rel_internal "ln(1+同年同IPC标准化被自引次数)"

    compress
    save `"`outfile'"', replace
end

di as text "第二遍：逐文件生成回归用变量..."
forvalues y = 1985/2019 {
    local f "$in\patent_`y'_lite.dta"
    cap confirm file "`f'"
    if !_rc {
        local outfile "$out\patent_`y'_reg.dta"
        di as text "  write: `outfile'"
        make_reg_file using "`f'", outfile("`outfile'")
    }
}

forvalues y = 2020/2024 {
    local partdir "$in\patent_`y'_lite_parts"
    local outdir "$out\parts\patent_`y'_reg_parts"
    cap mkdir "`outdir'"
    local files : dir "`partdir'" files "*.dta"
    foreach file of local files {
        local f "`partdir'\`file'"
        local stem = subinstr("`file'", ".dta", "", .)
        local outfile "`outdir'\`stem'_reg.dta"
        di as text "  write: `outfile'"
        make_reg_file using "`f'", outfile("`outfile'")
    }
}

di as result "完成。输出目录: $out"
