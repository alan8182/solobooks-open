"""发票导入 LLM 提示词"""

INVOICE_COLUMN_MAPPING_PROMPT = """你是一个发票Excel导入助手。用户上传了一个发票Excel文件，包含以下列名：
{columns}

请将这些列映射到系统所需的字段：
- invoice_number: 发票号码/编号
- invoice_date: 开票日期
- seller_name: 销售方名称（卖家）
- buyer_name: 购买方名称（买家）
- amount: 金额（不含税）
- tax_amount: 税额
- total_amount: 价税合计（含税金额）
- item_name: 货物或应税劳务名称

请返回JSON数组，每个元素包含：
- "column": 原始列名
- "mapping": 映射到的系统字段名，如果无法映射则填 null

只返回JSON，不要其他文字。"""

INVOICE_CLASSIFY_PROMPT = """你是一个会计分类助手。根据发票信息，判断这笔交易的方向和分类。

发票信息：
{seller_name}

以下是可选的分类列表：
{categories}

请返回JSON：
- "direction": "expense"（支出）或"income"（收入）或"owner_transfer"（资本或往来）
- "category": 最匹配的分类名称
- "reason": 选择理由（一句话）

只返回JSON，不要其他文字。"""
