"""Single source of truth for permission codes (`module.action`) and starter roles.

`manage.py sync_permissions` upserts these into the DB. Admins then edit roles freely;
system role templates are only (re)created when missing, never overwritten.
"""

PERMISSIONS = {
    "products": ["view", "create", "update", "delete", "publish", "toggle_visibility",
                 "archive", "import", "export", "manage_prices", "manage_cost"],
    "categories": ["view", "manage"],
    "brands": ["view", "manage"],
    "storefront": ["view", "manage"],  # About page, contact, location, social accounts
    "inventory": ["view", "adjust", "transfer", "stocktake", "view_cost", "manage_warehouses"],
    "orders": ["view", "create", "update", "confirm", "process", "ship", "deliver",
               "cancel", "return", "apply_discount"],
    "payments": ["view", "record", "refund"],
    "customers": ["view", "create", "update", "delete", "view_balance"],
    "purchasing": ["view", "create", "approve", "receive", "pay_supplier", "manage_suppliers"],
    "accounting": ["view", "create_entry", "post_entry", "reverse_entry", "manage_accounts",
                   "close_period", "manage_tax", "manage_currencies"],
    "sales": ["pos", "view", "discount", "refund", "credit"],  # credit: leave (part of) a sale as a customer debt
    "reports": ["sales", "inventory", "financial", "export"],
    "users": ["view", "create", "update", "deactivate", "manage_roles", "manage_permissions"],
    "settings": ["view", "manage", "view_audit_log"],
}

ALL_CODES = [f"{module}.{action}" for module, actions in PERMISSIONS.items() for action in actions]

# Sensitive codes that starter roles never include.
_ADMIN_ONLY = {c for c in ALL_CODES if c.startswith("users.")} | {"settings.manage", "accounting.close_period"}


def _view_codes():
    return [c for c in ALL_CODES if c.endswith(".view") and c not in _ADMIN_ONLY]


ROLE_TEMPLATES = {
    "accountant": {
        "description": "Accounting and financial reports",
        "permissions": [c for c in ALL_CODES if c.startswith(("accounting.", "reports.")) and c not in _ADMIN_ONLY]
        + _view_codes(),
    },
    "warehouse": {
        "description": "Inventory, receiving and shipping",
        "permissions": [c for c in ALL_CODES if c.startswith("inventory.") and c != "inventory.view_cost"]
        + ["products.view", "orders.view", "orders.process", "orders.ship", "purchasing.receive"],
    },
    "sales": {
        "description": "Orders, customers and payments",
        "permissions": [c for c in ALL_CODES if c.startswith(("orders.", "customers."))
                        if c != "orders.cancel"]
        + ["payments.record", "products.view"],
    },
    "cashier": {
        "description": "Point of sale: open a shift, sell (also on a customer's account), see own sales",
        "permissions": ["sales.pos", "sales.credit"],
    },
    "store_manager": {
        "description": "Catalog management",
        "permissions": [c for c in ALL_CODES if c.startswith(("products.", "categories.", "brands.", "storefront."))
                        and c != "products.manage_cost"]
        + ["orders.view"],
    },
}

assert not any(set(t["permissions"]) & _ADMIN_ONLY for t in ROLE_TEMPLATES.values())
