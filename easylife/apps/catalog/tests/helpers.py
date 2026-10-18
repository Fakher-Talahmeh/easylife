"""Factories shared by the catalog / inventory / storefront tests."""

from decimal import Decimal
from itertools import count

from apps.catalog.models import PriceList, Product, ProductPrice, ProductStatus, ProductVariant
from apps.currencies.models import Currency
from apps.inventory import services as inventory
from apps.inventory.models import MovementType, Warehouse

_n = count(1)


def price_list(code="ILS", default=True, name=None):
    cur = Currency.objects.get(code=code)
    return PriceList.objects.get_or_create(
        name=name or f"Retail {code}", defaults={"currency": cur, "is_default": default})[0]


def warehouse(code=None, **kw):
    code = code or f"W{next(_n)}"
    return Warehouse.objects.create(code=code, name=f"Warehouse {code}", **kw)


def _stock_warehouse():
    return Warehouse.objects.get_or_create(code="STOCK", defaults={"name": "Stock warehouse"})[0]


def product(name=None, *, sku=None, status=ProductStatus.ACTIVE, visible=True, track=True,
            price=None, currency="ILS", variants=1, stock=0, **kw):
    """A product with `variants` variants; first gets `sku`. Optionally priced in `currency`
    and given `stock` units of every variant (in a shared active warehouse; the storefront hides empty ones)."""
    i = next(_n)
    p = Product.objects.create(
        name=name or f"Product {i}", slug=f"product-{i}", status=status,
        is_visible_on_store=visible, track_inventory=track, **kw)
    out = []
    for k in range(variants):
        v = ProductVariant.objects.create(
            product=p, sku=(sku if (k == 0 and sku) else f"SKU-{i}-{k}"), name=f"Var {k}")
        if price is not None:
            ProductPrice.objects.create(variant=v, price_list=price_list(currency), price=Decimal(str(price)))
        if stock and track:
            inventory.move_stock(variant=v, warehouse=_stock_warehouse(), quantity=stock, type=MovementType.PURCHASE_IN)
        out.append(v)
    p.test_variants = out
    return p
