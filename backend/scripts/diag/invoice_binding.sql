-- Invoice master data bound to the DeepSeek voucher (read-only)
SELECT id, invoice_number, invoice_date, amount_incl_tax, entry_id, source
FROM invoices
WHERE company_id = 'c_f9c4c18df32d'
  AND (entry_id = 'd30ee0b3-8a4b-49b0-aee6-ada53a405dde'
       OR invoice_number = '26332000004655508586');
