-- Inspect the 4 suspicious June entries + their links (read-only)
SELECT e.id, e.voucher_no, e.period_start, e.occurred_on, e.summary, e.status, e.source_transaction_id
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d'
  AND e.id IN ('0728ab57-d071-4f25-a734-60f81b2675f7', '8ac20b58-618b-4e67-89af-79def7bfca6b',
               'a7ce2a80-02df-406f-bc4d-4e219c61f0be', '01b98cd7-e232-493d-89d4-46b3c731481c');

SELECT l.journal_entry_id, l.line_no, l.account_code, l.account_name, l.direction, l.amount
FROM journal_lines l
WHERE l.company_id = 'c_f9c4c18df32d'
  AND l.journal_entry_id IN ('0728ab57-d071-4f25-a734-60f81b2675f7', '8ac20b58-618b-4e67-89af-79def7bfca6b',
                             'a7ce2a80-02df-406f-bc4d-4e219c61f0be', '01b98cd7-e232-493d-89d4-46b3c731481c')
ORDER BY l.journal_entry_id, l.line_no;

SELECT rl.journal_entry_id, rl.source_transaction_id, rl.status, rl.amount, st.source_type, st.external_key
FROM reconciliation_links rl
JOIN source_transactions st ON st.id = rl.source_transaction_id
WHERE rl.company_id = 'c_f9c4c18df32d'
  AND rl.journal_entry_id IN ('0728ab57-d071-4f25-a734-60f81b2675f7', '8ac20b58-618b-4e67-89af-79def7bfca6b',
                              'a7ce2a80-02df-406f-bc4d-4e219c61f0be', '01b98cd7-e232-493d-89d4-46b3c731481c');
