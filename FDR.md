> **Problem Statement**
> Managing personal finances manually is time-consuming, repetitive, and prone to maintenance fatigue. Traditional personal tracking setups force users to constantly update complex formulas, extend data ranges, build custom charts, and calculate period-over-period metrics as time goes on. Without a streamlined solution, tracking daily expenses becomes a friction-heavy chore, mixing raw transaction data with configuration settings, increasing the risk of data corruption, and failing to provide immediate, automated visibility into spending habits, anomalies, and threshold alerts.
> The core challenge is creating a long-term personal finance solution where daily effort is reduced strictly to entering transactions, while all background transformations, date modeling, historical trends, anomaly detection, and interactive visual reporting happen automatically without ongoing manual upkeep.

---

### Functional Requirements

* **Automated Data Processing**
* Auto-generate all analytical time attributes (Month, Year, Quarter, Week, Day, Weekday/Weekend) without manual calculation or formula maintenance in the raw log.
* Support continuous, multi-year logging starting from September 2026 onward without needing model redesigns or structural changes.
* Automatically append new entries and refresh all connected metrics and visual panels seamlessly.


* **Data Entry & Validation Rules**
* Provide a streamlined transaction log restricted strictly to 6 primary input fields: Date, Amount (formatted in INR), Category, Sub-category, Description, and Payment Method.
* Enforce data integrity through standardized drop-down selections for Categories, Sub-categories, and Payment Methods (Cash, UPI, Debit, Others).
* Isolate master lookup configurations (categories, sub-categories, payment methods, threshold settings) from the primary transaction table to prevent accidental modification or data corruption.
* Include automated data-quality checks to flag missing fields, invalid non-numeric values, negative entries, or duplicate records.


* **Analytics & Metrics Engine**
* Compute core financial KPIs: Total Expenses, Current Month Expenses, Today's Expenses, Average Daily/Monthly Expense, Highest/Lowest Spending Days, Highest/Lowest Spending Categories, and Highest/Lowest Spending Months.
* Provide period-over-period comparisons (Month-over-Month absolute and percentage changes).
* Calculate categorical distribution (Category Percentage of Total) and transaction metrics (Transaction Count, Average Transaction Amount).
* Support flexible dynamic filtering across custom date ranges, years, months, categories, sub-categories, payment methods, and days of the week.


* **Automated Anomaly Detection & Threshold Monitoring**
* Automatically flag statistical spending anomalies (e.g., unusually high single transactions, spike days, sudden category spikes, or abnormal monthly totals) compared to historical baselines.
* Support an optional, non-intrusive spending limit framework (Daily, Monthly, and Category-specific) displaying statuses: *Within Limit*, *Approaching Limit*, or *Exceeded*.


* **Dynamic Insights Engine**
* Generate dynamic textual narrative observations that evaluate the active filter context (e.g., top spending drivers, period comparisons, daily averages, and dominant payment methods) updated in real time.



---

### Dashboard & Reporting Requirements

* **Multi-Page Analytical Structure**
* **Overview / Executive Page:** High-level executive summary featuring top KPI cards, category distribution (with automated grouping for minor items), monthly/daily spending trends, category rankings, payment breakdown, and contextual insights.
* **Daily & Weekly Analysis Page:** Granular daily/weekly spending trends, day-of-week comparisons, weekday vs. weekend splits, heatmaps showing spending intensity, and top spending days.
* **Monthly Analysis Page:** Month-over-month trend analysis, historical month comparisons, transaction volume activity, and automated monthly variation explanations.
* **Category Analysis Page:** Full category rankings, percentage contributions, sub-category breakdowns with multi-level drill-downs, top category highlighting, and temporal category trends.
* **Payment Analysis Page:** Expenditure and transaction breakdown across payment types, highlighting average transaction values and primary spending vehicles.
* **All-Time Analysis Page:** Long-term historical trend tracking from September 2026 onward, cumulative expenditure, multi-year comparisons, and all-time high benchmarks.


* **User Experience & Interaction Standards**
* Global navigation interface allowing intuitive switching between pages, resetting active filters, and toggling themes.
* Seamless cross-filtering and drill-down capabilities across visuals (e.g., Category $\rightarrow$ Sub-category $\rightarrow$ Item Description).
* Rich contextual tooltips displaying detailed breakdowns (Amount, Share %, Transaction Count, Averages, Prior-period comparison) on hover without cluttering main views.
* Dual-theme presentation: a clean **Light Premium** aesthetic and a high-contrast **Dark Finance** aesthetic with consistent theme-switching controls across all elements.
* Dedicated onboarding/instruction panel detailing daily logging habits, filter controls, drill-down mechanics, and manual refresh options.