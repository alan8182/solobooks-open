-- Boss private account (小私账) records in the book (read-only)
SELECT id, title, amount, direction, category, occur_date, payer, owner_name,
       private_account, is_available, invoice_status, substitute_entry_id, created_at
FROM private_expenses
WHERE company_id = 'c_f9c4c18df32d'
ORDER BY occur_date, created_at;
