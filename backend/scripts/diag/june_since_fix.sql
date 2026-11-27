-- What changed since the 11-19 fix, and period close states (read-only)
SELECT period_start, status, created_at FROM period_close_runs
WHERE company_id = 'c_f9c4c18df32d' ORDER BY created_at DESC LIMIT 5;

SELECT '--- new entries since 11-19 ---';
SELECT e.period_start, e.voucher_no, e.occurred_on, e.summary, e.status, e.created_at
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d' AND e.created_at > '2026-11-19 12:00+00'
ORDER BY e.created_at;

SELECT '--- new bank statements since 11-19 ---';
SELECT transaction_date, description, amount, balance, matched_entry_id, created_at
FROM bank_statements
WHERE company_id = 'c_f9c4c18df32d' AND created_at > '2026-11-19 12:00+00'
ORDER BY transaction_date;

SELECT '--- june statements vs sources now ---';
SELECT b.transaction_date, b.description, b.amount, b.matched_entry_id,
       (SELECT count(*) FROM reconciliation_links rl JOIN source_transactions st ON st.id = rl.source_transaction_id
        WHERE rl.company_id = b.company_id AND rl.journal_entry_id = b.matched_entry_id AND rl.status='confirmed'
          AND st.source_type IN ('bank','bank_statement')) AS has_bank_link
FROM bank_statements b
WHERE b.company_id = 'c_f9c4c18df32d' AND substr(b.transaction_date,1,7) = '2026-06'
ORDER BY b.transaction_date;
