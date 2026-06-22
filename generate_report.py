from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import os

SCR_BASE = r"C:\Users\amit ginzberg\Pictures\Screenshots"
SCR_DIR  = r"C:\Users\amit ginzberg\gadash_bot"

# New screenshots taken from live site
SCR_LOGIN       = SCR_DIR  + r"\scr_login.png"
SCR_MAIN_FULL   = SCR_DIR  + r"\scr_main.png"
SCR_MAIN_TABLE  = SCR_DIR  + r"\scr_main_table.png"
SCR_ADD         = SCR_DIR  + r"\scr_add.png"
SCR_SUMMARY     = SCR_DIR  + r"\scr_summary.png"
SCR_AUDIT       = SCR_DIR  + r"\scr_audit.png"
SCR_PRINT       = SCR_DIR  + r"\scr_print.png"
SCR_IMPORT      = SCR_DIR  + r"\scr_import.png"
SCR_EDIT        = SCR_BASE + r"\Screenshot 2026-06-21 171017.png"
SCR_DELETE      = SCR_DIR  + r"\screen_delete.png"

IMG_BASE = r"C:\Users\amit ginzberg\Documents\Amit_PC\Summaries\Computer_Science_Tel_Hai\Year_B\נושאים מתקדמים בפיתוח תוכנה\פרוייקט"
IMG_USECASE  = IMG_BASE + r"\UseCasedDiagram.png"
IMG_CLASS    = IMG_BASE + r"\ClassDiagram.png"
IMG_ACTIVITY = IMG_BASE + r"\ActivityDiagram.png"
IMG_STATE    = IMG_BASE + r"\StateMachineDiagram.png"

doc = Document()

# ── Page setup ────────────────────────────────────────────────────────────────
section = doc.sections[0]
section.page_width  = Inches(8.27)
section.page_height = Inches(11.69)
section.left_margin   = Cm(2.5)
section.right_margin  = Cm(2.5)
section.top_margin    = Cm(2.5)
section.bottom_margin = Cm(2.5)

# ── RTL helpers ───────────────────────────────────────────────────────────────
def set_rtl(para):
    pPr = para._p.get_or_add_pPr()
    bidi = OxmlElement('w:bidi')
    pPr.append(bidi)
    jc = OxmlElement('w:jc')
    jc.set(qn('w:val'), 'right')
    pPr.append(jc)

def set_rtl_run(run):
    rPr = run._r.get_or_add_rPr()
    rtl = OxmlElement('w:rtl')
    rPr.append(rtl)

def add_rtl_para(doc, text, style='Normal', bold=False, size=None, color=None, align=None):
    p = doc.add_paragraph(style=style)
    set_rtl(p)
    p.alignment = align if align else WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run(text)
    run.bold = bold
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor(*color)
    set_rtl_run(run)
    return p

def add_heading_rtl(doc, text, level=1):
    p = doc.add_heading('', level=level)
    set_rtl(p)
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run(text)
    set_rtl_run(run)
    return p

def make_rtl_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    hdr = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ''
        p = cell.paragraphs[0]
        set_rtl(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = p.add_run(h)
        run.bold = True
        run.font.size = Pt(10)
        set_rtl_run(run)
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), '1E3A5F')
        tcPr.append(shd)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for ri, row in enumerate(rows):
        tr = table.rows[ri + 1]
        for ci, val in enumerate(row):
            cell = tr.cells[ci]
            cell.text = ''
            p = cell.paragraphs[0]
            set_rtl(p)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = p.add_run(str(val))
            run.font.size = Pt(9)
            set_rtl_run(run)
    if col_widths:
        for i, w in enumerate(col_widths):
            for row in table.rows:
                row.cells[i].width = Cm(w)
    return table

def add_picture_safe(doc, path, width=Inches(5.5)):
    if os.path.exists(path):
        doc.add_picture(path, width=width)
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        add_rtl_para(doc, f'[תמונה לא נמצאה: {os.path.basename(path)}]',
                     size=9, color=(150, 150, 150), align=WD_ALIGN_PARAGRAPH.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# COVER PAGE
# ══════════════════════════════════════════════════════════════════════════════
add_rtl_para(doc, 'הפקולטה למדעים — החוג למדעי המחשב', bold=False, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
add_rtl_para(doc, 'נושאים מתקדמים בפיתוח תוכנה | סמסטר ב׳ תשפ״ה', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
add_rtl_para(doc, 'מרצה: ד״ר איאד סולימאן', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()
doc.add_paragraph()

p_title = doc.add_paragraph()
p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_rtl(p_title)
r = p_title.add_run('מערכת ניהול עבודות גד״ש — אתר ובוט טלגרם')
r.bold = True
r.font.size = Pt(22)
r.font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)
set_rtl_run(r)

add_rtl_para(doc, 'מסמך סופי', bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()

add_rtl_para(doc, 'כתובת המערכת:', bold=True, size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
add_rtl_para(doc, 'https://gadash-bot.fly.dev/', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()

add_rtl_para(doc, 'שותפים:', bold=True, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
add_rtl_para(doc, 'עמית גינזברג  |  ת״ז: 313393027  |  amitginz@gmail.com  |  054-3192907', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
add_rtl_para(doc, 'הדר קלר  |  ת״ז: 209825512  |  050-7551810', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()
add_rtl_para(doc, 'תאריך הגשה: 15/08/2025', size=11, align=WD_ALIGN_PARAGRAPH.CENTER)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — חלוקת עבודה
# ══════════════════════════════════════════════════════════════════════════════
add_heading_rtl(doc, 'סעיף 1 — חלוקת עבודה בין השותפים', 1)

make_rtl_table(doc,
    headers=['תחום', 'עמית גינזברג', 'הדר קלר'],
    rows=[
        ['ארכיטקטורת המערכת ו-WorkEntry Dataclass', '✓', ''],
        ['בוט טלגרם — ConversationHandler, מצבי שיחה, /undo, סטטיסטיקות', '✓', ''],
        ['שילוב Google Sheets (gspread, google-auth, cache)', '✓', ''],
        ['Flask Web App — נתיבים, לוגיקה עסקית, REST API', '✓', ''],
        ['אבטחה — CSRF, rate limiting, session timeout, סיסמה', '✓', ''],
        ['לוג שינויים (audit trail), דוח חודשי, דוח שבועי אוטומטי', '✓', ''],
        ['ממשק משתמש Web — HTML, CSS, Bootstrap RTL', '', '✓'],
        ['עריכה מוטבעת (inline edit), מחיקה מרובה, שכפול רשומה', '', '✓'],
        ['מסך נייד (mobile cards), הדפסה/PDF', '', '✓'],
        ['טפסי הוספה/עריכה עם השלמה אוטומטית (autocomplete)', '', '✓'],
        ['ייצוא/ייבוא Excel וCSV, ייבוא חכם (dedup)', '', '✓'],
        ['פריסה לענן (Fly.io, Dockerfile, fly.toml)', '✓', ''],
        ['תיעוד, README ו-UML', '✓', '✓'],
    ],
    col_widths=[9.5, 2.5, 2.5]
)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — תיאור מילולי
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 2 — תיאור מילולי של המערכת', 1)

add_heading_rtl(doc, 'רקע ומטרה', 2)
add_rtl_para(doc,
    'מערכת "ניהול עבודות גד״ש" פותחה עבור גוף חקלאי המנהל עבודות שדה מגוונות כגון חריש, '
    'ריסוס, קציר ודיסוק. הצורך שעמד בבסיס הפרויקט נבע מכך שעובדי שדה שאינם רגילים לממשקים '
    'ממוחשבים נדרשים לדווח על עבודות בזמן אמת, מהשדה, ללא גישה למחשב. במקביל, מנהל החווה '
    'נדרש לצפות בנתונים מרוכזים, לסנן לפי לקוח ותאריך, לערוך רשומות ולייצא דוחות לאקסל.',
    size=11)

add_heading_rtl(doc, 'הפתרון', 2)
add_rtl_para(doc, 'המערכת מורכבת משני ממשקים המשלימים זה את זה:', bold=True, size=11)

add_rtl_para(doc,
    'בוט טלגרם (ממשק ניידי לעובדי השדה): עובד השדה פותח את אפליקציית טלגרם ומתחיל שיחה עם '
    'הבוט. הבוט מנחה אותו בשפה העברית דרך שאלות פשוטות ומדורגות: שם לקוח, תאריך, סוג העבודה, '
    'שם החלקה, כמות, כלי עבודה, שם המפעיל והערות. בסוף מוצג סיכום לאישור, ורק לאחר אישור '
    'המשתמש הנתונים נשמרים. הבוט תומך גם בפקודות נוספות: /undo לביטול הרשומה האחרונה, '
    'צפייה ב-5 עבודות אחרונות, חיפוש לפי לקוח, סטטיסטיקות, עריכת הרשומה האחרונה, '
    'ודוחות שבועיים ויומיים אוטומטיים.',
    size=11)

add_rtl_para(doc,
    'ממשק Web לניהול (למנהל): מנהל החווה ניגש לממשק Web שהוא לוח בקרה מלא עם אימות סיסמה. '
    'הממשק כולל: צפייה בכל הרשומות ממוינות לפי תאריך עם עימוד (50 רשומות לדף), '
    'סינון לפי שם לקוח, תאריך, שדה ומפעיל (עם שמירה בזיכרון דפדפן), '
    'הוספה ידנית עם השלמה אוטומטית משדות קיימים, '
    'עריכה ידנית בטופס נפרד, עריכה מוטבעת (double-click על תא), '
    'מחיקה בודדת ומחיקה מרובה, שכפול רשומה, '
    'ייצוא לאקסל וCSV, ייבוא אקסל עם ניקוי כפילויות, '
    'דוח חודשי מפורט, לוג שינויים מלא, הדפסה/PDF, ושינוי סיסמה.',
    size=11)

add_heading_rtl(doc, 'אחסון הנתונים', 2)
add_rtl_para(doc,
    'כל הנתונים מאוחסנים ב-Google Sheets בגיליון בשם "Gadash Data". בחירה זו מאפשרת גם '
    'למנהל לפתוח את הגיליון ישירות לצפייה ועריכה ידנית. הגיליון מהווה מקור האמת היחיד של '
    'המערכת, ושני הממשקים קוראים וכותבים לאותו גיליון. הנתונים נטענים עם cache בזיכרון '
    '(TTL 30 שניות) ו-threading.Lock למניעת race conditions.',
    size=11)

add_heading_rtl(doc, 'אבטחה', 2)
add_rtl_para(doc,
    'המערכת כוללת מספר שכבות הגנה: אימות סיסמה לכל הממשק ה-Web, '
    'הגבלת קצב כניסות (5 ניסיונות בדקה) למניעת brute force, '
    'CSRF tokens לכל הטפסים ובקשות AJAX, '
    'session timeout של 8 שעות, '
    'ולוג שינויים (audit trail) המתעד כל פעולת כתיבה עם תאריך, משתמש ופרטים.',
    size=11)

add_heading_rtl(doc, 'פריסה', 2)
add_rtl_para(doc,
    'המערכת מותקנת על שרת ענן Fly.io ורצה ברציפות עם שני מכונות (2×) לזמינות גבוהה. '
    'תהליך Python יחיד מריץ במקביל את שרת ה-Web (gunicorn) ואת הבוט (ב-thread נפרד '
    'עם asyncio event loop). כתובת המערכת: https://gadash-bot.fly.dev/',
    size=11)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — טכנולוגיות
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 3 — טכנולוגיות האינטרנט', 1)

add_heading_rtl(doc, '3.1 ארכיטקטורה', 2)
add_rtl_para(doc,
    'המערכת בנויה על ארכיטקטורה Monolithic בשכבות, הרצה בתהליך Python יחיד עם שני רכיבים '
    'במקביל: שרת Flask (gunicorn) לממשק ה-Web ובוט טלגרם ב-thread נפרד עם asyncio event loop. '
    'שני הרכיבים ניגשים לאותו מקור נתונים — Google Sheets — דרך ספריית gspread, '
    'עם מנגנון cache משותף ו-threading.Lock. '
    'נתוני הקלט עוברים אימות דרך WorkEntry dataclass לפני כל שמירה.',
    size=11)

add_heading_rtl(doc, '3.2 צד לקוח — Front-End', 2)
make_rtl_table(doc,
    headers=['טכנולוגיה', 'שימוש'],
    rows=[
        ['HTML5', 'מבנה כל דפי ה-Web'],
        ['CSS3', 'עיצוב מותאם, media queries למסך נייד'],
        ['Bootstrap 5.3 RTL', 'רספונסיביות, רכיבי UI, תמיכה בעברית ימין-לשמאל'],
        ['Bootstrap Icons', 'אייקונים (bi-*)'],
        ['JavaScript (Vanilla)', 'עריכה מוטבעת (double-click → PATCH), מחיקה מרובה, '
                                 'CSRF auto-inject, localStorage לשמירת סינון, '
                                 'השלמה אוטומטית (datalist)'],
        ['Chart.js', 'גרפי סטטיסטיקה בלוח הבקרה'],
        ['Fetch API', 'בקשות AJAX ל-REST API (PATCH /api/entries/<id>)'],
        ['Jinja2', 'תבניות HTML דינמיות (מנוע ה-templating של Flask)'],
        ['Telegram App', 'ממשק הנייד לעובדי השדה'],
    ],
    col_widths=[4.5, 10]
)

doc.add_paragraph()
add_heading_rtl(doc, '3.3 צד שרת — Back-End', 2)
make_rtl_table(doc,
    headers=['טכנולוגיה', 'גרסה', 'שימוש'],
    rows=[
        ['Python', '3.11', 'שפת התכנות הראשית'],
        ['Flask', '2.3.3', 'מסגרת Web — ניתוב HTTP, תבניות, session'],
        ['python-telegram-bot', '20.7', 'ספריית הבוט — ConversationHandler, async/await'],
        ['asyncio + threading', '—', 'הרצה מקבילה של הבוט ושרת ה-Web'],
        ['pandas', '2.2.2', 'עיבוד נתונים טבלאיים, קריאה/כתיבה Excel'],
        ['openpyxl', '3.1.2', 'מנוע ייצוא/ייבוא קבצי Excel'],
        ['gspread', '6.1.2', 'ממשק ל-Google Sheets API'],
        ['google-auth', '2.29.0', 'אימות מול Google (Service Account)'],
        ['gunicorn', '21.2.0', 'שרת WSGI לפרודקשן'],
        ['WorkEntry (dataclass)', '—', 'אימות ומבנה נתוני כל רשומת עבודה'],
    ],
    col_widths=[5, 2.5, 7]
)

doc.add_paragraph()
add_rtl_para(doc, 'נתיבי ה-Web:', bold=True, size=11)
make_rtl_table(doc,
    headers=['נתיב', 'Method', 'תיאור'],
    rows=[
        ['/', 'GET', 'דף ראשי — רשימת עבודות עם סינון ועימוד'],
        ['/login', 'GET, POST', 'כניסה עם סיסמה + rate limiting'],
        ['/logout', 'GET', 'יציאה'],
        ['/change-password', 'GET, POST', 'שינוי סיסמה'],
        ['/add', 'GET, POST', 'הוספת רשומה חדשה (autocomplete)'],
        ['/duplicate/<id>', 'GET', 'שכפול רשומה קיימת (prefill)'],
        ['/edit/<id>', 'GET, POST', 'עריכת רשומה קיימת'],
        ['/delete/<id>', 'POST', 'מחיקת רשומה בודדת'],
        ['/bulk-delete', 'POST', 'מחיקה מרובה'],
        ['/summary', 'GET', 'דוח חודשי — pivot + לקוחות מובילים'],
        ['/audit', 'GET', 'לוג שינויים (200 פעולות אחרונות)'],
        ['/print', 'GET', 'דף הדפסה/PDF'],
        ['/export', 'GET', 'הורדת קובץ Excel'],
        ['/export/csv', 'GET', 'הורדת קובץ CSV'],
        ['/import', 'GET, POST', 'העלאת קובץ Excel עם dedup'],
        ['/api/entries', 'GET', 'REST API — רשומות בפורמט JSON'],
        ['/api/entries/<id>', 'PATCH', 'REST API — עריכה מוטבעת'],
    ],
    col_widths=[4, 2.5, 8]
)

add_heading_rtl(doc, '3.4 בסיס נתונים', 2)
add_rtl_para(doc,
    'Google Sheets משמש כבסיס הנתונים של המערכת. הגיליון "Gadash Data" מכיל את העמודות הבאות:',
    size=11)
make_rtl_table(doc,
    headers=['שדה', 'תיאור'],
    rows=[
        ['שם לקוח', 'שם הלקוח / בית העסק (שדה חובה)'],
        ['תאריך', 'תאריך ביצוע (YYYY-MM-DD, שדה חובה)'],
        ['עבודה', 'סוג: חריש / ריסוס / קציר / דיסוק / אחר (שדה חובה)'],
        ['שם חלקה', 'שם או מזהה החלקה החקלאית'],
        ['כמות', 'כמות (למשל: "30 דונם")'],
        ['כלי', 'כלי העבודה (למשל: "טרקטור")'],
        ['מפעיל', 'שם מפעיל הציוד'],
        ['הערות', 'הערות חופשיות'],
        ['מזין', 'מי הזין — שם מטלגרם, "Web", או "import"'],
    ],
    col_widths=[4, 11]
)
doc.add_paragraph()
add_rtl_para(doc,
    'הסבר הבחירה ב-Google Sheets: נגיש ישירות לבעל העסק ללא ממשק נוסף, גיבוי אוטומטי '
    'ב-Google Drive, ניתן לשיתוף עם גורמים נוספים, ללא עלות, ו-API מובנה.',
    size=11)

add_heading_rtl(doc, '3.5 פלטפורמה', 2)
add_rtl_para(doc, 'המערכת מותאמת לשלוש פלטפורמות:', size=11)
add_rtl_para(doc, '• סמארטפון — ממשק הטלגרם, מותאם לשימוש בשדה', size=11)
add_rtl_para(doc, '• Web (דסקטופ/טאבלט) — ממשק הניהול עם טבלה, עריכה מוטבעת וגרפים', size=11)
add_rtl_para(doc, '• Web (נייד) — כרטיסיות mobile cards במקום טבלה ב-Bootstrap RTL', size=11)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — תדפיסי מסכים
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 4 — תדפיסי מסכים', 1)

add_rtl_para(doc, 'מסך 1 — דף כניסה עם אימות סיסמה', bold=True, size=11)
add_picture_safe(doc, SCR_LOGIN)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 2 — לוח בקרה ראשי: כרטיסיות סטטיסטיקה, גרף ותגיות צבעוניות', bold=True, size=11)
add_picture_safe(doc, SCR_MAIN_FULL)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 3 — טבלת הרשומות: עריכה מוטבעת, מחיקה מרובה, שכפול ועימוד', bold=True, size=11)
add_picture_safe(doc, SCR_MAIN_TABLE)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 4 — טופס הוספת רשומה חדשה עם השלמה אוטומטית', bold=True, size=11)
add_picture_safe(doc, SCR_ADD)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 5 — דוח חודשי: pivot לפי חודש וסוג עבודה + לקוחות מובילים', bold=True, size=11)
add_picture_safe(doc, SCR_SUMMARY)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 6 — לוג שינויים: רישום כל פעולות הכתיבה עם תאריך ומשתמש', bold=True, size=11)
add_picture_safe(doc, SCR_AUDIT)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 7 — דף הדפסה/PDF', bold=True, size=11)
add_picture_safe(doc, SCR_PRINT)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 8 — ייבוא נתונים מקובץ Excel עם ניקוי כפילויות', bold=True, size=11)
add_picture_safe(doc, SCR_IMPORT)
doc.add_paragraph()

add_rtl_para(doc, 'מסך 9 — אישור מחיקת רשומה', bold=True, size=11)
add_picture_safe(doc, SCR_DELETE)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — UML
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 5 — מודלי UML', 1)
add_rtl_para(doc,
    'הדיאגרמות נוצרו ב-PlantUML ומצורפות כקבצים נפרדים (.puml). '
    'להלן תיאור של כל דיאגרמה.',
    size=11)

# 5.1 Use Case
add_heading_rtl(doc, '5.1 Use Case Diagram', 2)
add_picture_safe(doc, IMG_USECASE)
add_rtl_para(doc, 'שחקנים (Actors):', bold=True, size=11)
add_rtl_para(doc, '• עובד שדה — משתמש בבוט טלגרם להזנת עבודות וצפייה', size=11)
add_rtl_para(doc, '• מנהל — משתמש בממשק ה-Web לניהול מלא', size=11)
add_rtl_para(doc, '• Google Sheets — מערכת חיצונית לאחסון', size=11)
doc.add_paragraph()
add_rtl_para(doc, 'תרחישי שימוש (Use Cases):', bold=True, size=11)
make_rtl_table(doc,
    headers=['Use Case', 'שחקן'],
    rows=[
        ['הזנת עבודה חדשה דרך הבוט', 'עובד שדה'],
        ['צפייה ב-5 עבודות אחרונות', 'עובד שדה'],
        ['חיפוש לפי לקוח', 'עובד שדה'],
        ['קבלת סטטיסטיקות', 'עובד שדה'],
        ['ביטול הרשומה האחרונה (/undo)', 'עובד שדה'],
        ['קבלת דוח יומי/שבועי אוטומטי', 'עובד שדה'],
        ['כניסה עם סיסמה', 'מנהל'],
        ['צפייה בכל העבודות עם עימוד', 'מנהל'],
        ['סינון לפי לקוח / תאריך / שדה / מפעיל', 'מנהל'],
        ['הוספת עבודה דרך ה-Web', 'מנהל'],
        ['עריכה ידנית / עריכה מוטבעת (inline)', 'מנהל'],
        ['שכפול רשומה', 'מנהל'],
        ['מחיקה בודדת / מחיקה מרובה', 'מנהל'],
        ['ייצוא ל-Excel / CSV', 'מנהל'],
        ['ייבוא מ-Excel עם dedup', 'מנהל'],
        ['צפייה בדוח חודשי', 'מנהל'],
        ['צפייה בלוג שינויים', 'מנהל'],
        ['הדפסה / PDF', 'מנהל'],
        ['שינוי סיסמה', 'מנהל'],
    ],
    col_widths=[9, 5.5]
)

# 5.2 Class
doc.add_paragraph()
add_heading_rtl(doc, '5.2 Class Diagram', 2)
add_picture_safe(doc, IMG_CLASS)
add_rtl_para(doc, 'המערכת כוללת את המחלקות הבאות:', size=11)
make_rtl_table(doc,
    headers=['מחלקה', 'תכונות עיקריות', 'פעולות עיקריות'],
    rows=[
        ['WorkEntry (dataclass)',
         'client, date, task, field_name\nquantity, tool, operator\nnotes, entered_by',
         '__post_init__() — validations\nto_sheet_row(), to_dict()\nfrom_form(), from_bot()'],
        ['FlaskApp',
         'app: Flask\nCOLUMNS: list\nPAGE_SIZE: int\n_current_password: str',
         'index(), login(), add(), edit(), delete()\nbulk_delete(), duplicate(), summary()\naudit(), print(), export(), import_data()\napi_entries(), api_entry_patch()'],
        ['GoogleSheetsConnector',
         '_gs_client: gspread.Client\n_cache_data, _cache_time\n_CACHE_TTL=30\n_gs_lock: Lock',
         '_get_sheet()\nload_data_from_gsheet()\nappend_row_to_gsheet(entry: WorkEntry)\nsave_data_to_gsheet(df)\n_invalidate_cache()'],
        ['TelegramBot',
         'token: str\nsubscribers: set',
         'start(), menu_choice()\nbot_recent(), bot_stats(), bot_edit_last()\nbot_search_results(), bot_undo()\nconfirm(), cancel()\n_scheduled_reports()'],
        ['AuditLog',
         'AUDIT_LOG_FILE: str\n_audit_lock: Lock',
         '_log_audit(action, user, detail)\n_read_audit_log(limit)'],
    ],
    col_widths=[3.5, 5.5, 5.5]
)

# 5.3 Activity
doc.add_paragraph()
add_heading_rtl(doc, '5.3 Activity Diagram — זרימת הזנת עבודה דרך הבוט', 2)
add_picture_safe(doc, IMG_ACTIVITY)
add_rtl_para(doc, 'תיאור הזרימה:', bold=True, size=11)
for i, s in enumerate([
    'משתמש שולח /start',
    'הבוט מציג הודעת ברוך הבא ושואל אם להתחיל',
    'תפריט MENU — המשתמש בוחר: הזן עבודה / 5 אחרונות / חפש / סטטיסטיקות / ערוך אחרונה / סיים',
    'אם "הזן עבודה": הבוט מבקש שם לקוח (CLIENT)',
    'המשתמש מזין תאריך (YYYY-MM-DD או "היום") — אם שגוי, שואל שוב',
    'בחירת סוג עבודה מלוח מקשים (חריש / ריסוס / קציר / דיסוק / אחר)',
    'הזנת שם חלקה, כמות, כלי, מפעיל, הערות (ניתן לדלג על הערות)',
    'WorkEntry.__post_init__() מאמת את כל השדות',
    'הצגת סיכום — המשתמש מאשר ("כן") או מבטל ("לא")',
    'אם אושר: append_row_to_gsheet() + _log_audit() ← חזרה לתפריט',
    'אם /undo: מחיקת הרשומה האחרונה של המשתמש + _log_audit("undo")',
    'אם "סיים": סיום השיחה',
], 1):
    add_rtl_para(doc, f'{i}. {s}', size=11)

# 5.4 State Machine
doc.add_paragraph()
add_heading_rtl(doc, '5.4 State Machine Diagram — מצבי שיחת הבוט', 2)
add_picture_safe(doc, IMG_STATE)
add_rtl_para(doc, 'מצבי המערכת (States):', bold=True, size=11)
make_rtl_table(doc,
    headers=['מצב', 'תיאור', 'מעברים אפשריים'],
    rows=[
        ['MENU', 'תפריט ראשי', 'CLIENT / SEARCH / [צפייה/סטטיסטיקות/עריכה] / END'],
        ['CLIENT', 'המתנה לשם לקוח', 'DATE'],
        ['DATE', 'המתנה לתאריך', 'TASK (תקין) / DATE (שגוי)'],
        ['TASK', 'בחירת סוג עבודה', 'FIELD'],
        ['FIELD', 'המתנה לשם חלקה', 'AMOUNT'],
        ['AMOUNT', 'המתנה לכמות', 'TOOL'],
        ['TOOL', 'המתנה לכלי', 'OPERATOR'],
        ['OPERATOR', 'המתנה למפעיל', 'NOTE'],
        ['NOTE', 'המתנה להערות (אפשר לדלג)', 'CONFIRM'],
        ['CONFIRM', 'אישור הנתונים', 'MENU (שמירה/ביטול)'],
        ['SEARCH', 'המתנה לשם לקוח לחיפוש', 'MENU (לאחר הצגת תוצאות)'],
        ['END', 'סיום השיחה', '—'],
    ],
    col_widths=[2.5, 4.5, 7.5]
)
doc.add_paragraph()
add_rtl_para(doc,
    'הערה: מכל מצב ניתן לשלוח /cancel (מעבר ל-END) או /start (מעבר ל-MENU). '
    '/undo זמין כ-fallback מכל מצב.',
    size=10)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — GitHub
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 6 — ניהול גרסאות ב-GitHub (בונוס)', 1)
add_rtl_para(doc,
    'הפרויקט מנוהל ב-Git עם מאגר ב-GitHub תחת חשבון amitginz. '
    'כתובת המאגר: https://github.com/amitginz/gadash_bot',
    size=11)
doc.add_paragraph()
make_rtl_table(doc,
    headers=['Commit Hash', 'תיאור'],
    rows=[
        ['1caf955', 'Add improvements #1-#12: CSRF, rate limit, session timeout, inline edit, mobile cards, CSV export, audit log, REST API'],
        ['67b8f9f', 'Refactor: add WorkEntry dataclass for validated data management'],
        ['410648f', 'Add improvements #1-#10: print PDF, duplicate row, bulk delete, monthly summary, change password, filter memory, smart import'],
        ['6fe52c8', 'Add improvements #6-#11 and #13: auth, pagination, autocomplete, bot search'],
        ['2984acd', 'Add flash messages and date range filter'],
        ['122b60b', 'Add GSheets cache, Chart.js dashboard charts, and POST delete'],
        ['595896a', 'Add final submission documents, screenshots, and UML source files'],
        ['1725595', 'Keep one machine always running for Telegram polling'],
        ['a879511', 'Fix bot thread: replace run_polling with manual async loop'],
        ['0150cb1', 'Switch to Fly.io v2 config with Dockerfile'],
    ],
    col_widths=[3.5, 11]
)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — ספריות צד שלישי
# ══════════════════════════════════════════════════════════════════════════════
doc.add_paragraph()
add_heading_rtl(doc, 'סעיף 7 — ספריות צד שלישי', 1)
make_rtl_table(doc,
    headers=['ספרייה', 'גרסה', 'שימוש'],
    rows=[
        ['Flask', '2.3.3', 'מסגרת Web, ניתוב, תבניות Jinja2, session management'],
        ['python-telegram-bot', '20.7', 'בוט טלגרם — async, ConversationHandler, JobQueue'],
        ['pandas', '2.2.2', 'עיבוד נתונים טבלאיים, Excel, pivot tables'],
        ['numpy', '1.26.4', 'תלות של pandas'],
        ['openpyxl', '3.1.2', 'קריאה/כתיבה קבצי Excel (.xlsx)'],
        ['gspread', '6.1.2', 'גישה ל-Google Sheets API'],
        ['google-auth', '2.29.0', 'אימות מול Google Service Account'],
        ['gunicorn', '21.2.0', 'שרת WSGI לפרודקשן'],
        ['Bootstrap', '5.3.0 RTL', 'עיצוב UI, רספונסיביות, תמיכה עברית'],
        ['Bootstrap Icons', '1.11.0', 'אייקוני SVG (bi-*)'],
        ['Chart.js', '4.x (CDN)', 'גרפי סטטיסטיקה בדפדפן'],
    ],
    col_widths=[4.5, 3, 7]
)

# ══════════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════════
out = 'gadash_report.docx'
doc.save(out)
print(f'Saved: {out}')
