-- Identify the two extra legacy-matched entries (read-only)
SELECT e.id, e.voucher_no, e.period_start, e.occurred_on, e.summary, e.status
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d'
  AND e.id IN ('c229d5a3-e09c-4040-af54-b29d57a0a7af', '61a74819-918f-45f3-b49d-f7f93d99b594');

SELECT l.journal_entry_id, l.line_no, l.account_code, l.direction, l.amount
FROM journal_lines l
WHERE l.company_id = 'c_f9c4c18df32d'
  AND l.journal_entry_id IN ('c229d5a3-e09c-4040-af54-b29d57a0a7af', '61a74819-918f-45f3-b49d-f7f93d99b594')
ORDER BY l.journal_entry_id, l.line_no;
