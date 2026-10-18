version 14
clear all
set more off
* Place summary_live.csv in Stata's current working folder.
* These are descriptive feasibility outputs, not a powered hypothesis test.
import delimited using "summary_live.csv", varnames(1) encoding("UTF-8") clear
keep if mode == "live"
count
if r(N)==0 {
    display "No LIVE invitations are present. TEST responses are exported separately."
    exit
}
foreach x in refresher changed baseline_n baseline_accuracy retention_n retention_accuracy work_n work_accuracy retention_change {
    capture destring `x', replace
}
tabulate arm phase, missing
count if baseline_n==8
count if retention_n==8
count if work_n==8
tabstat baseline_accuracy retention_accuracy work_accuracy retention_change, by(arm) statistics(n mean sd)
* Keep incomplete invitations in the count; missing follow-up scores remain missing.
generate byte invitation_count = 1
collapse (sum) invited=invitation_count (count) retention_observed=retention_accuracy work_observed=work_accuracy ///
    (mean) baseline_mean=baseline_accuracy retention_mean=retention_accuracy work_mean=work_accuracy, by(arm)
export delimited using "pilot_descriptive_summary.csv", replace
