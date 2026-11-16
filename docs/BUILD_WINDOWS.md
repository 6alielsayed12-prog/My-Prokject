# دليل البناء على ويندوز — Yawm Jadid Academy (v1.3.0)

> ⚠️ **إفصاح صريح**: جميع أعمال التطوير والاختبار (427 اختبارًا) تُنفّذت على Linux. **ملف EXE لم يُبنَ ولم يُجرَّب على ويندوز فعليًا** — لا يوجد ويندوز في بيئة التطوير الحالية. كل خطوات البناء موثقة أدناه وكل أدوات التحقق جاهزة؛ أول تشغيل على ويندوز هو الذي سيؤكد. لا تعتبري هذا إصدارًا مؤكدًا قبل تنفيذ §2 و§3.

---

## 1. المتطلبات

| المتطلب | الإصدار | ملاحظات |
|---|---|---|
| Windows 10/11 (64-bit) | — | ويب فيو 2 موجود مسبقًا في الأنظمة الحديثة |
| Python | **3.12** حصرًا لبناء الإصدار | من python.org مع تعليم *Add python.exe to PATH* |
| الإنترنت | للتنزيل الأول فقط | التطبيق نفسه يعمل أوفلاين 100% |
| Inno Setup 6 (اختياري) | 6.x | لبناء مثبّت `Setup.exe` — بدونه يخرج مجلد Portable فقط |
| git | اختياري | `.gitattributes` يفرض CRLF على `.bat/.ps1` تلقائيًا |

## 2. البناء الكامل (الطريقة الموصى بها)

```bat
:: 1) فك ضغط المشروع في مسار قصير خالص من OneDrive، مثلاً C:\YawmJadid
cd C:\YawmJadid

:: 2) (اختياري لكن موصى به) اختبارات سريعة قبل البناء
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-core.txt httpx
python -m unittest discover -s tests -t .

:: 3) البناء الكامل: بيئة + اعتماديات + اختبارات + PyInstaller + فحص EXE + فحص مجلد التوزيع + دخان + اختصار + مثبت
build_windows.bat

:: خيارات:
::   build_windows.bat /strict   إيقاف البناء عند أول فشل (اختبار أو دخان)
```

ماذا يفعل السكربت بالترتيب:

1. كشف Python 3.12 (البناء بـ 3.13/3.14 شاذ ولن يُقبَل للإصدار).
2. إنشاء `.venv` وتثبيت `requirements.lock` إن وُجد وإلا `requirements-core.txt` + `requirements-build.txt` + pywebview.
3. تشغيل حزمة الاختبارات كاملة (الفشل تحذير افتراضيًا، إيقاف مع `/strict`).
4. `pyinstaller YawmJadidAcademy.spec --noconfirm` → `dist\YawmJadidAcademy\`.
5. **فحص محتوى الـ EXE مقابل المصدر**: `scripts\verify_build_artifact.py` — يثبت أن الأرشيف المدمج هو هذا الكود بالضبط (حمية ضد بناء قديم بالخطأ)، ويمنع تسريب قواعد بيانات أو ملفات seed.
6. **فحص اكتمال مجلد التوزيع**: `scripts\verify_build.py` — EXE، كل ملفات الواجهة والخطوط والمكتبات المدمجة، DLL بايثون، عدم وجود أي ملف `.sqlite3`، خلو `index.html` من أي روابط خارجية — ويكتب التقرير `build_verification_report.json`.
7. اختبار دخان EXE (`scripts\smoke_exe.ps1`) في مجلد بيانات مؤقت معزول.
8. إنشاء اختصار سطح المكتب (إنجليزي + عربي).
9. بناء المثبت بـ Inno Setup إن وُجد (`installer\Output\YawmJadidAcademy-Setup-1.3.0.exe`).

## 3. التحقق اليدوي بعد أول بناء (لا تتجاوزيه)

1. شغّل `dist\YawmJadidAcademy\YawmJadidAcademy.exe` على جهاز **بدون Python مثبت** (أو حساب نظيف).
2. تأكد: نافذة سطح مكتب تفتح خلال ~10 ثوان، واجهة عربية RTL بخط Cairo (لا خط نظام بديل)، لا نافذة كونسول سوداء.
3. أضف طالبة تجريبية → أغلق البرنامج → افتح `%LOCALAPPDATA%\AlAhdAcademySuite` واتأكد من وجود `alahd_erp_v1.sqlite3` + نسخة `_exit` في `backups\`.
4. افتح البرنامج مرتين: النافذة القائمة تُحضر للواجهة ولا تُنشأ نسخة ثانية.
5. جرّب تصدير HTML بالوضع العام وتأكد من غياب أي مبالغ، ثم الوضع المالي وتأكد من ظهور التحذير.
6. شغّل `python scripts\verify_build_artifact.py dist\YawmJadidAcademy\YawmJadidAcademy.exe --strict` بعد أي تعديل للمصدر.

## 4. الملفات المسؤولة عن البناء

| الملف | الدور |
|---|---|
| `YawmJadidAcademy.spec` | مواصفات PyInstaller: وحدة مخفية (uvicorn/webview/clr_loader + وحدات البنية التحتية الجديدة)، أصول الواجهة، أيقونة، بيانات الإصدار |
| `requirements-core.txt` / `requirements-build.txt` | اعتماديات التشغيل/البناء (pywebview اختياري: فشل تثبيته = فتح في المتصفح بدل فشل التسطيب) |
| `installer\YawmJadidAcademy.iss` | مثبت لكل مستخدم (بدون صلاحيات مدير)، AppId ثابت للترقية في مكانها، لا يمس مجلد البيانات عند إزالة التثبيت، يحذّر إن غاب WebView2 |
| `installer\version_info.txt` | خصائص الملف في Explorer (يجب أن تتطابق مع `alahd_erp.__version__` — يفحصها `tests/test_branding.py`) |
| `scripts\verify_build.py` | فحص اكتمال مجلد التوزيع + تقرير JSON |
| `scripts\verify_build_artifact.py` | فحص أرشيف PyInstaller ضد شجرة المصدر |
| `scripts\smoke_exe.ps1` | تشغيل EXE في مجلد بيانات معزول والتحقق من `/api/health` |
| `scripts\make_lock.bat` | تثبيت الإصدارات الدقيقة بعد أول بناء ناجح (`requirements.lock`) — نفّذيها فور أول بناء ناجح وأرسلي الملف |

## 5. أين توجد البيانات والنسخ في التوزيع؟

- EXE/المثبت: ملفات البرنامج فقط. **قاعدة البيانات والنسخ والسجلات في `%LOCALAPPDATA%\AlAhdAcademySuite` دائمًا** — نقل البرنامج أو تحديثه أو إعادة تثبيته لا يلمسها، ولا يُستبدل ملف قاعدة بيانات قائم أبدًا أثناء التحديث.
- مجلد Portable: انسخي `dist\YawmJadidAcademy\` كاملًا لأي جهاز؛ البيانات تبقى في المجلد الشخصي للمستخدم على كل جهاز.

## 6. أخطاء أول بناء متوقعة وحلولها

| الخطأ | الحل |
|---|---|
| `pywebview` فشل تثبيته (pythonnet) | البناء يكمل بلا نافذة أصلية — يفتح التطبيق في المتصفح. حدّثي .NET ثم أعيدي التثبيت |
| فشل اختبار واحد على ويندوز | أرسلي رسالة الفشل — الافتراضي متابعة البناء؛ `/strict` لإيقافه |
| `iscc not recognized` | Inno Setup غير مثبت — مجلد Portable يخرج عاديًا والمثبت اختياري |
| EXE لا يفتح | شغّليه من cmd واقرئي الخطأ، أو أرسلي `%LOCALAPPDATA%\AlAhdAcademySuite\logs\alahd_erp.log` |
| بناء بطيء جدًا أول مرة | طبيعي (تحليل المكتبات)؛ البناءات التالية أسرع، والأسرع `build_and_run.bat /fast` |

## 7. بناء إعادة الإنتاج (Reproducible)

بعد أول بناء ناجح: `scripts\make_lock.bat` ثم أرسلي `requirements.lock` — كل البناءات التالية ستثبت نفس الإصدارات المضبوطة بالضبط.
