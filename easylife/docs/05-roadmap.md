# خطة التنفيذ (Roadmap)

كل مرحلة تنتهي بـ: migrations + services + API + اختبارات + تحديث الوثائق.

## M0 — الأساس
- [ ] إعدادات منفصلة (base/dev/prod)، `.env`، django-environ
- [ ] Docker Compose (postgres)، pytest، ruff، pre-commit
- [ ] `apps/core`: TimeStamped, SoftDelete, UUID mixin, AuditLog, DocumentSequence
- [x] `apps/accounts`: Custom User (هوية = رقم واتساب، `user_type`, `is_superadmin`)، JWT، دخول واتساب OTP فقط
- [ ] **نظام الصلاحيات:** Permission (كتالوج مُزروع عبر `sync_permissions`)، Role/RolePermission/UserRole/UserPermissionOverride، النطاقات، حساب الصلاحية الفعلية + كاش، `HasPermission` في DRF، API إدارة الموظفين والأدوار، قوالب الأدوار الجاهزة
- [ ] اختبار CI يفشل إن وُجد endpoint بدون `required_permission`
- [ ] إعداد drf-spectacular
- [ ] **أمان (انظر 06-security.md):** JWT قصير مع rotation/blacklist ✔، OTP مشفّر بحدود محاولات وإرسال ✔؛ المتبقي: Step-up OTP للعمليات الحساسة، ربط الجلسة بالجهاز، تنبيه دخول جديد للأدمن
- [ ] **أمان:** Throttling (cache)، CORS ضيّق، رؤوس الأمان/HSTS، `DEFAULT_PERMISSION_CLASSES` رفض افتراضي
- [ ] **أمان:** AuditLog append-only (صلاحيات DB/trigger)، حسابا DB (app/migrator)
- [ ] **أمان في CI:** bandit، pip-audit، detect-secrets، lock للتبعيات
- [ ] إطار اختبارات الصلاحيات (دور × endpoint) وIDOR كقالب يُعاد استخدامه

## M1 — العملات والكتالوج
- [x] Currency, ExchangeRate (+ خدمة التحويل `convert(amount, from, to, date)`)
- [x] Category, Product, ProductVariant, ProductImage, PriceList, ProductPrice
- [x] إخفاء/إظهار/أرشفة/حذف محمي  _(استيراد/تصدير CSV: لم يُنفَّذ بعد)_
- [x] Admin API كامل + فلترة وبحث

## M2 — المخزون
- [x] Warehouse, StockLevel, StockMovement (append-only)
- [x] `move_stock`, `reserve`, `release` مع الأقفال
- [x] Adjustment، Transfer، StockTake (جرد)، reorder_level وتنبيهات
- [x] حماية التزامن: أقفال صفوف + قيود CHECK في قاعدة البيانات + اختبارات  _(اختبار تزامن حقيقي متعدد الخيوط يحتاج PostgreSQL)_

## M3 — المحاسبة (النواة)
**قرارات معتمدة:** العملة الأساسية للدفاتر **شيكل (ILS)** والدولار (USD) والدينار (JOD) مدعومان في المعاملات بسعر صرف يوم القيد؛ أسماء الحسابات بثلاث لغات (عربي/إنجليزي/عبري)؛ **Maker–Checker** (من أنشأ القيد اليدوي لا يرحّله، والأدمن مستثنى)؛ الفترات المالية **شهرية** (12 في السنة). يعتمد على جدولي العملات وأسعار الصرف من M1.
- [x] Account (شجرة) + شجرة افتراضية seed
- [x] FiscalPeriod، JournalEntry/Line، ترحيل وعكس
- [x] TaxRate، AccountMapping، دعم العملات في القيود  _(فروقات الصرف المحققة/إعادة التقييم: لم تُنفَّذ)_
- [x] تقارير: ميزان المراجعة، كشف الحساب، قائمة الدخل، الميزانية، ملخص اللوحة، تصدير CSV  _(أعمار الذمم وملخص الضريبة: لاحقاً)_
- [x] اختبارات: توازن القيد، منع الترحيل بفترة مغلقة

## M4 — المبيعات والطلبيات
- [ ] Customer, Cart, Order, OrderLine, OrderStatusHistory + state machine
- [ ] حجز عند التأكيد، خصم عند الشحن، إلغاء
- [ ] Invoice, Payment (جزئية، عملات)، SalesReturn
- [ ] ربط القيود التلقائي (مبيعات، تكلفة، ضريبة، دفعات)

## M5 — Storefront API
- [ ] كتالوج عام (فلترة، بحث، ترتيب، cache)
- [ ] سلة، checkout (idempotent)، حساب الزبون وطلباته
- [ ] Throttling وحماية

## M6 — المشتريات
- [ ] Supplier, PurchaseOrder, استلام، PurchaseInvoice, SupplierPayment
- [ ] تحديث المتوسط المرجّح للتكلفة، قيود المشتريات

## M7 — التقارير والإنهاء
- [ ] قائمة الدخل، الميزانية، كشف حساب، أعمار الذمم
- [ ] تقارير المخزون (تقييم، حركة، نفاد)
- [ ] صلاحيات دقيقة، مراجعة أمنية، أداء (فهارس/EXPLAIN)
- [ ] توثيق OpenAPI نهائي

## لاحقاً
لوحة إدارة (Frontend)، واجهة المتجر، بوابات دفع، شحن، كوبونات وخصومات، Multi-company.

## مهام أمنية لكل مرحلة
- **M2:** constraints المخزون + trigger منع تعديل `StockMovement`
- **M3:** constraint trigger لتوازن القيد، منع تعديل المرحّل، Maker–Checker للترحيل
- **M4/M5:** اختبارات IDOR وmass assignment، تسعير من الخادم فقط، CAPTCHA، webhooks موقّعة
- **M7:** مراجعة أمنية شاملة (OWASP ASVS L2/API Top 10)، اختبار استعادة النسخ، Sentry + تنبيهات

## تعريف "منتهي" لكل مرحلة
اختبارات خضراء (بما فيها اختبارات القسم 9 في 06-security.md)، لا N+1 في الـ API الرئيسية، الوثائق محدّثة، لا تغيير في الدفاتر خارج services.
