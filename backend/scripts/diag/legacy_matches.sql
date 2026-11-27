-- Legacy match rows for May/June statements (read-only)
SELECT statement_id, entry_id, previous_billing_status, created_at
FROM bank_statement_matches
WHERE company_id = 'c_f9c4c18df32d'
  AND statement_id IN ('bs-69a65105cbf3432fa6dc','bs-6e2a447458f94649a620','bs-9f5ec7fac490425e9c7e',
                       'bs-28f5708b171348c0ab2f','bs-ec6b1ee808f346e094aa','bs-7e215741689840299621',
                       'bs-c708bb054b9542f39f75','bs-4298246a87f64628aed3','bs-61aa4be0fbb84e578394',
                       'bs-dd9dd54f345f42049cbc','bs-68c3e49b110347508a95','bs-9a4c20fcc9854b9cad6d',
                       'bs-dd4493fa618f47e281f7','bs-5655bcddb05e432b9a47')
ORDER BY statement_id;
