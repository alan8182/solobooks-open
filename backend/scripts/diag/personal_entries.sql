-- Personal-account related entries in the book (read-only)
SELECT e.period_start, count(*) AS cnt,
       sum(CASE WHEN e.metadata->>'payer' = 'boss_personal' THEN 1 ELSE 0 END) AS boss_personal,
       sum(CASE WHEN e.metadata->>'payer' LIKE 'employee_personal%' THEN 1 ELSE 0 END) AS employee_personal
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d' AND e.status IN ('posted','reversed')
GROUP BY e.period_start ORDER BY e.period_start;

SELECT e.period_start, e.voucher_no, e.occurred_on, e.summary, e.metadata->>'payer' AS payer, e.metadata->>'category' AS category
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d'
  AND e.status IN ('posted','reversed')
  AND (e.metadata->>'payer' = 'boss_personal' OR e.metadata->>'payer' LIKE 'employee_personal%')
ORDER BY e.period_start, e.occurred_on;
