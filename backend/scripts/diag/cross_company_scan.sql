-- Cross-tenant scan: matched statements whose entry lacks a confirmed link (read-only)
WITH matched AS (
  SELECT b.company_id, b.id AS statement_id, b.matched_entry_id
  FROM bank_statements b
  WHERE b.matched_entry_id IS NOT NULL AND b.matched_entry_id <> ''
),
unlinked AS (
  SELECT m.company_id, count(*) AS stale_statements
  FROM matched m
  WHERE NOT EXISTS (
    SELECT 1 FROM reconciliation_links rl
    WHERE rl.company_id = m.company_id
      AND rl.journal_entry_id = m.matched_entry_id
      AND rl.status = 'confirmed'
      AND EXISTS (
        SELECT 1 FROM source_transactions st
        WHERE st.id = rl.source_transaction_id
          AND st.source_type IN ('bank', 'bank_statement')
          AND st.status <> 'void')
  )
  GROUP BY m.company_id
)
SELECT u.company_id, c.payload->>'name' AS company_name, u.stale_statements
FROM unlinked u LEFT JOIN auth_companies c ON c.company_id = u.company_id
ORDER BY u.stale_statements DESC;
