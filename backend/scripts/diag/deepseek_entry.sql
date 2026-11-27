-- The -500 DeepSeek payment voucher: full lines + invoice binding (read-only)
SELECT l.line_no, l.account_code, l.account_name, l.direction, l.amount, l.auxiliary::text
FROM journal_lines l
WHERE l.company_id = 'c_f9c4c18df32d' AND l.journal_entry_id = 'd30ee0b3-8a4b-49b0-aee6-ada53a405dde'
ORDER BY l.line_no;

SELECT e.metadata::text
FROM journal_entries e
WHERE e.company_id = 'c_f9c4c18df32d' AND e.id = 'd30ee0b3-8a4b-49b0-aee6-ada53a405dde';

SELECT column_name FROM information_schema.columns WHERE table_name = 'invoices' ORDER BY ordinal_position;
