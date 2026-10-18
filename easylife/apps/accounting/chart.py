"""Default chart of accounts and default account mappings (seeded by `seed_accounting`).

Hebrew / English names are proposals: have the company's accountant review the terminology.
Columns: code, type, parent code, postable, name_ar, name_en, name_he
"""

CHART = [
    ("1000", "asset", None, False, "الأصول", "Assets", "נכסים"),
    ("1110", "asset", "1000", True, "الصندوق", "Cash", "קופה"),
    ("1120", "asset", "1000", True, "البنك", "Bank", "בנק"),
    ("1130", "asset", "1000", True, "ذمم العملاء", "Accounts Receivable", "לקוחות"),
    ("1140", "asset", "1000", True, "المخزون", "Inventory", "מלאי"),
    ("1150", "asset", "1000", True, "ضريبة مدفوعة (مشتريات)", "Input Tax", "מע״מ תשומות"),
    ("2000", "liability", None, False, "الالتزامات", "Liabilities", "התחייבויות"),
    ("2110", "liability", "2000", True, "ذمم الموردين", "Accounts Payable", "ספקים"),
    ("2120", "liability", "2000", True, "ضريبة مستحقة (مبيعات)", "Output Tax", "מע״מ עסקאות"),
    ("3000", "equity", None, False, "حقوق الملكية", "Equity", "הון עצמי"),
    ("3100", "equity", "3000", True, "رأس المال", "Capital", "הון"),
    ("3200", "equity", "3000", True, "أرباح محتجزة", "Retained Earnings", "יתרת עודפים"),
    ("4000", "revenue", None, False, "الإيرادات", "Revenue", "הכנסות"),
    ("4100", "revenue", "4000", True, "المبيعات", "Sales", "מכירות"),
    ("4200", "revenue", "4000", True, "مرتجعات المبيعات", "Sales Returns", "החזרות מכירות"),
    ("4300", "revenue", "4000", True, "أرباح فروقات الصرف", "FX Gains", "רווחי שער"),
    ("5000", "expense", None, False, "المصروفات", "Expenses", "הוצאות"),
    ("5100", "expense", "5000", True, "تكلفة المبيعات", "Cost of Goods Sold", "עלות המכר"),
    ("5200", "expense", "5000", True, "فروقات الجرد", "Inventory Differences", "הפרשי מלאי"),
    ("5300", "expense", "5000", True, "خسائر فروقات الصرف", "FX Losses", "הפסדי שער"),
    ("5400", "expense", "5000", True, "مصروفات الشحن", "Shipping Expenses", "הוצאות משלוח"),
]

DEFAULT_MAPPINGS = {
    "sales": "4100", "cogs": "5100", "inventory": "1140", "receivable": "1130", "payable": "2110",
    "cash": "1110", "bank": "1120", "tax_collected": "2120", "tax_paid": "1150",
    "inventory_adjustment": "5200", "fx_gain": "4300", "fx_loss": "5300",
}
