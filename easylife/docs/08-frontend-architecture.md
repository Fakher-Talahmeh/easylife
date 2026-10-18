# هيكلية الواجهة (Frontend)

الواجهة مشروع منفصل: `../easylife-frontend` (Angular 20). **التصميم الملزم** في ملف واحد داخله:
`easylife-frontend/docs/ARCHITECTURE.md`، ويفرضه `npm run check:arch` وملف `CLAUDE.md` هناك.

| البند | القرار |
|---|---|
| التطبيق | تطبيق واحد بثلاث مناطق lazy: `store` (SSR) · `admin` · `auth` |
| الواجهات | نظام تصميم SCSS خاص (tokens)، بلا مكتبة UI |
| اللغات | عربي RTL (افتراضي) · إنجليزي LTR · عبري RTL، تبديل وقت التشغيل |
| الاتصال بالـ backend | REST عبر `API_DOCUMENTATION.md` (الأنواع تطابقه تماماً) |
| CORS | الـ backend يسمح بـ `http://localhost:4200` في التطوير |

أي endpoint جديد في الـ backend يلزم أن يُوثَّق في `API_DOCUMENTATION.md` ثم تُحدَّث أنواع الواجهة (`data/*.model.ts`) والصفحة المقابلة.
