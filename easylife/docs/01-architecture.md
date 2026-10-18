# الهيكلية (Architecture)

## النمط: Modular Monolith
تطبيق Django واحد مقسّم إلى apps مستقلة بحدود واضحة. أبسط في النشر والمعاملات (transactions) من الـ microservices، وهو مناسب لأن المخزون والمبيعات والمحاسبة يجب أن تُحفظ **ذرّياً** في معاملة واحدة.

```
easylife/                 # إعدادات المشروع
  settings/{base,dev,prod}.py
  urls.py  asgi.py  wsgi.py
apps/
  core/         Mixins مشتركة، audit log، DocumentSequence (ترقيم)
  accounts/     User مخصص، الأدوار، العناوين
  currencies/   Currency, ExchangeRate
  catalog/      Category, Product, ProductVariant, PriceList, ProductPrice
  inventory/    Warehouse, StockLevel, StockMovement, StockReservation, Adjustment, Transfer
  sales/        Customer, Cart, Order, Invoice, Payment, SalesReturn
  purchasing/   Supplier, PurchaseOrder, PurchaseInvoice, SupplierPayment
  accounting/   Account, FiscalPeriod, JournalEntry/Line, TaxRate, reports
  storefront/   API عام للزبائن (لا نماذج خاصة به غالباً)
docs/  tests/  docker-compose.yml  .env.example
```

## الطبقات داخل كل app
```
models.py      # الحقول والقيود فقط (CheckConstraint, UniqueConstraint, indexes)
selectors.py   # استعلامات القراءة
services.py    # كل منطق الأعمال والكتابة (داخل transaction.atomic)
api/           # serializers, views (رفيعة), filters, permissions, urls
tests/
```
**قواعد صارمة:**
1. لا منطق أعمال في الـ views أو serializers — تستدعي `services` فقط.
2. كل كتابة تمس المخزون أو الدفاتر تمر عبر service واحد (`inventory.services.move_stock`, `accounting.services.post_entry`).
3. التبعيات باتجاه واحد: `sales → inventory → catalog`, و `sales/purchasing/inventory → accounting`. لا تبعية دائرية؛ `accounting` لا يستورد من التطبيقات التشغيلية (يستقبل الأحداث عبر generic reference).
4. الحركات والقيود المرحّلة **append-only**: التصحيح بحركة/قيد عكسي.

## التكامل بين الوحدات
```
Order.confirm()      → inventory.reserve()
Order.ship()         → inventory.move_stock(sale_out) → accounting.post_sale_entries()
PurchaseReceipt      → inventory.move_stock(purchase_in) → accounting.post_purchase_entry()
Payment.create()     → accounting.post_payment_entry()
StockAdjustment      → inventory.move_stock(adjustment) → accounting.post_adjustment_entry()
```
كلها داخل `transaction.atomic()`؛ فشل القيد يُلغي حركة المخزون.

## التزامن (Concurrency)
- `select_for_update()` على صف `StockLevel` قبل أي تعديل، مع ترتيب ثابت للأقفال (variant_id, warehouse_id) لتجنب deadlock.
- CHECK على قاعدة البيانات: `on_hand >= 0`, `reserved >= 0`, `reserved <= on_hand`.
- `Idempotency-Key` في checkout والدفعات لمنع التكرار.
- ترقيم المستندات عبر `DocumentSequence` بقفل صف.

## الأمان
التفاصيل الكاملة في [06-security.md](06-security.md). الخلاصة:
- مصادقة **واتساب OTP فقط** (لا بريد ولا كلمة مرور) + JWT قصير العمر، RBAC + Object-level permissions، رفض افتراضي.
- Storefront API: المنتجات الظاهرة فقط (`status=active AND is_visible_on_store`)، Throttling، التسعير من الخادم.
- Audit log append-only لكل تغيير مالي أو مخزني.
- الأسرار في `.env`/مدير أسرار (django-environ)، لا بيانات بطاقات مخزّنة.
- الثوابت المالية مفروضة في قاعدة البيانات (constraints/triggers) وليس في الكود فقط.

## البنية التشغيلية
- Docker Compose: `postgres`, `web`.
- المهام الدورية (انتهاء الحجوزات، جلب أسعار الصرف، تقارير ثقيلة): management commands تُشغَّل بـ cron، بدون Celery/Redis.
- Cache: في التطوير ذاكرة العملية، وفي الإنتاج `DatabaseCache` (`manage.py createcachetable`) ليكون مشتركاً بين العمليات.
- التوثيق التلقائي: drf-spectacular (OpenAPI).
- الاختبارات: pytest-django؛ تغطية خاصة لتوازن القيود وحساب المخزون والتزامن.
