"""银行流水 LLM 提示词"""

BANK_COLUMN_MAPPING_PROMPT = """你是一个银行流水Excel导入助手。用户上传了一个银行流水Excel文件，包含以下列名：
{columns}

请将这些列映射到系统所需的字段：
- transaction_date: 交易日期
- description: 摘要/描述
- amount: 金额（正数为收入，负数为支出）
- balance: 余额

请返回JSON数组，每个元素包含：
- "column": 原始列名
- "mapping": 映射到的系统字段名，如果无法映射则填 null

只返回JSON，不要其他文字。"""

BANK_ROW_PARSE_PROMPT = """你是一个银行流水数据解析助手。以下是一行银行流水数据：
{row}

请将其解析为以下字段（返回JSON对象）：
- transaction_date: 交易日期（格式 YYYY-MM-DD）
- description: 摘要/描述
- amount: 金额（正数）
- balance: 余额

只返回JSON，不要其他文字。"""
