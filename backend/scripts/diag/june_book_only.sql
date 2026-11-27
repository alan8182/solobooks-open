-- June 2026 book_only candidates for c_f9c4c18df32d (read-only)
SELECT e.id, e.voucher_no, e.occurred_on, l.direction, l.amount, e.summary
FROM journal_entries e
JOIN journal_lines l ON l.journal_entry_id = e.id AND l.company_id = e.company_id
WHERE e.company_id = 'c_f9c4c18df32d'
  AND e.period_start = '2026-06-01'
  AND e.status IN ('posted', 'reversed')
  AND l.account_code = '1002'
  AND COALESCE(e.metadata->>'is_archive_only', e.metadata->>'isArchiveOnly', 'false') = 'false'
  AND e.entry_type <> 'reversal'
  AND e.id NOT IN (
    SELECT reversal_of_id FROM journal_entries
    WHERE company_id = 'c_f9c4c18df32d' AND entry_type = 'reversal' AND reversal_of_id IS NOT NULL)
  AND e.id NOT IN (
    SELECT journal_entry_id FROM reconciliation_links
    WHERE company_id = 'c_f9c4c18df32d' AND status = 'confirmed')
ORDER BY e.occurred_on;
