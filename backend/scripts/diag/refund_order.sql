-- Refund diagnosis for SB261118185354265DC8 (read-only)
SELECT out_trade_no, user_id, order_type, status, plan_code, amount, token_amount, payment_channel, created_at
FROM billing_orders
WHERE out_trade_no = 'SB261118185354265DC8';

SELECT out_trade_no, order_type, status, plan_code, amount, token_amount, payment_channel, created_at
FROM billing_orders
WHERE user_id = (SELECT user_id FROM billing_orders WHERE out_trade_no = 'SB261118185354265DC8')
ORDER BY created_at;

SELECT user_id, plan_code, plan_expires_at, token_balance
FROM auth_users
WHERE user_id = (SELECT user_id FROM billing_orders WHERE out_trade_no = 'SB261118185354265DC8');
