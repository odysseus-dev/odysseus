# Knowledge Base — Instructions / إرشادات قاعدة المعارف

Purpose / الغرض
- Provide clear, repeatable instructions for contributing, organizing, and maintaining the `knowledge_base` content. / توضيح إرشادات واضحة ومتكررة للمساهمة في محتوى `knowledge_base` وتنظيمه وصيانته.

Scope / النطاق
- Applies to all files and folders under `knowledge_base/` (01–22 categories). / ينطبق على جميع الملفات والمجلدات تحت `knowledge_base/` (الفئات 01–22).

Core rules (enforced) / قواعد أساسية (ملزمة)
- Use the todo-list tool when starting multi-step tasks: update `manage_todo_list` with clear items and status. / استخدم أداة قائمة المهام عند بدء مهام متعددة الخطوات: حدّث `manage_todo_list` بعناصر وحالة واضحة.
- Reference workspace files with backticks when mentioned in messages (e.g., `knowledge_base/01_Administrative_Affairs/README.md`). / اذكر مسارات الملفات بين علامات اقتباس معكوفة عند الإشارة إليها.
- Always post a short preamble (1–2 sentences) before making file-editing tool calls explaining what will be done. / أضف إحاطة قصيرة (1–2 جملة) قبل استخدام أدوات التعديل تشرح العمل المقصود.
- Do not reveal the agent/model name unless explicitly requested. / لا تفصح عن اسم الوكيل/النموذج إلا عند الطلب الصريح.
- Use absolute paths for edits (workspace-rooted), e.g. `/workspaces/odysseus/...`. / استخدم مسارات مطلقة للتعديلات، مثال `/workspaces/odysseus/...`.

Preferences (recommended) / تفضيلات (موصى بها)
- Major deliverables (readmes, templates, policies) should be bilingual (Arabic + English). / المخرجات الرئيسية (README، القوالب، السياسات) يُفضّل أن تكون ثنائية اللغة.
- Keep messages concise and action-oriented. / اجعل الرسائل موجزة وموجهة للعمل.
- After 3–5 tool calls or >3 file edits, provide a concise progress update and next steps. / بعد 3–5 استدعاءات أدوات أو أكثر من 3 تعديلات ملفات، قدم تحديث تقدم مختصر وخطوات لاحقة.

Knowledge Base structure & naming / بنية قاعدة المعارف والتسمية
- Categories: 01_Administrative_Affairs ... 22_Archive (see folder list). / الفئات: 01_Administrative_Affairs ... 22_Archive (راجع قائمة المجلدات).
- File naming: `YYYY-MM-DD_title_lang` (e.g., `2026-06-04_onboarding_en`); include extension when applicable. / تسمية الملفات: `YYYY-MM-DD_title_lang` (مثال: `2026-06-04_onboarding_ar`) وارفق الامتداد عند الاقتضاء.
- For binaries (PBIX, XLSX, STL): include `_v1`, `_v2` version suffix and maintain a small manifest entry in the folder README. / للملفات الثنائية مثل PBIX, XLSX, STL: أرفق لاحقة إصدار `_v1` واحتفظ بمدخل في ملف README الخاص بالمجلد يوضح الإصدارات.

Governance / الحوكمة
- Each category must list an owner and contact in its README (Owner / Contacts sections). / يجب أن يحتوي README لكل فئة على مالك وجهة اتصال.
- Review cadence: major docs reviewed annually; policies reviewed bi-annually unless marked otherwise. / وتيرة المراجعة: المستندات الرئيسية سنوياً؛ السياسات نصف سنوية ما لم يُذكر خلاف ذلك.
- Permission & edits: propose edits via PR with descriptive title and link to todo item. / التعديلات عبر طلب سحب مع عنوان وصفي ورابط لمهمة القائمة.

Templates & examples / القوالب والأمثلة
- Place master templates in `21_Templates/` and reference them from category READMEs. / ضع القوالب الرئيسية في `21_Templates/` وادرج إشارات لها في README لكل فئة.
- Include one example deliverable per category (onboarding, budget, board deck, SOP). / ضع مثالاً واحداً لكل فئة كمخرَج نموذجي.

Ambiguities to confirm / استيضاحات مطلوبة
1. Bilingual requirement: apply to every message or only major deliverables? / هل الثنائية مطلوبة لكل رسالة أم فقط للمخرجات الرئيسية؟
2. File retention policy for large binaries (PBIX, STL): archive policy and storage location? / سياسة الاحتفاظ للملفات الكبيرة: أين الأرشيف والمعايير؟

Examples of expected workflow / مثال سير العمل المتوقع
1. Update `manage_todo_list` with task "Create HR onboarding README".  
2. Preamble: "I'll create `knowledge_base/02_Human_Resources/onboarding.md` (EN+AR) and update the todo list."  
3. Use `apply_patch` to add the file under `/workspaces/odysseus/knowledge_base/02_Human_Resources/`.  
4. Post progress update after the patch and mark todo item completed. / مثال خطوات العمل المفصلة أعلاه.

Next customizations (suggested) / اقتراحات تخصيص لاحقة
- Add `AGENTS.md` describing agent roles and edit permissions. / أضف `AGENTS.md` يصف أدوار الوكلاء وصلاحيات التحرير.
- Add template PR descriptions and commit message guide. / أضف قوالب وصف طلب السحب ودليل رسائل الالتزام.

Saved: 2026-06-04
