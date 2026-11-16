"""Static, Self-Contained Read-Only HTML Export & Printable Receipt Service (Phase 7).

Security & Engineering Guarantees:
- Produces ONE self-contained `.html` document with inline CSS, zero external URLs/CDN/fonts,
  zero `<form>` elements, zero `fetch`/`XMLHttpRequest`/`localStorage` calls.
- Enforces strict Content-Security-Policy:
  `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;`
- Escapes every dynamic value via `html.escape(..., quote=True)` so XSS payloads render as inert text.
- Fully readable with JavaScript disabled (`<details open>` sections and `#anchor` table of contents).
- Supports privacy mode (`hide_personal_data=True` by default) masking phone numbers for safe sharing.
- Supports all 8 scopes: `full`, `statistics`, `month_finance`, `collections`, `payroll`, `teacher`,
  `student`, `alerts`, plus printable individual receipt (`generate_printable_receipt_html`).
"""
from __future__ import annotations

import datetime
import html
import re
from typing import Any

from .. import __version__
from ..domain.alerts_engine import compute_alerts
from ..domain.attendance_engine import format_month_label
from ..domain.finance_engine import (
    compute_accrual_month_report,
    compute_collection_report,
    compute_month_stats,
    derive_month_payment_status,
    effective_month_collected,
    effective_month_net_subscription,
    effective_month_wage,
    index_installments_by_student_month,
    resolve_actual_start_date,
    resolve_end_date,
    resolve_payment_date,
)
from ..domain.statistics_engine import compute_general_statistics

SUPPORTED_HTML_SCOPES = (
    "full",
    "statistics",
    "month_finance",
    "collections",
    "payroll",
    "teacher",
    "student",
    "alerts",
)

# Public-HTML export sections (mirrors domain.settings_registry.HTML_EXPORT_SECTIONS ids).
HTML_SECTION_IDS = ("statistics", "month_finance", "students", "collections", "payroll", "alerts")
SECTIONS_REQUIRING_PAYMENTS = frozenset({"month_finance", "collections", "payroll"})

# Alert categories that reveal payment/financial information: they are removed completely from
# the public (no-payment) export, not merely hidden.
FINANCIAL_ALERT_CATEGORIES = frozenset({
    "overdue_payments",
    "unpaid_or_partial",
    "unpaid_teacher_wages",
    "paid_in_advance_not_started",
})


def resolve_html_sections(
    scope: str = "full",
    sections: list[str] | None = None,
    include_payments: bool = True,
) -> tuple[list[str], list[str]]:
    """Resolves the effective section list + the sections disabled by the privacy mode.

    Legacy scopes map to their section equivalents; an explicit `sections` list wins when given.
    Financial sections are dropped (never rendered) when `include_payments=False`.
    Returns `(effective_sections, sections_disabled_by_privacy_mode)`.
    """
    sc = scope if scope in SUPPORTED_HTML_SCOPES else "full"
    legacy_map = {
        "full": ["statistics", "month_finance", "collections", "payroll", "alerts"],
        "statistics": ["statistics"],
        "month_finance": ["month_finance"],
        "collections": ["collections"],
        "payroll": ["payroll"],
        "alerts": ["alerts"],
        "teacher": ["month_finance", "payroll"],
        "student": ["students"],
    }
    if sections:
        requested = [s for s in sections if s in HTML_SECTION_IDS]
    elif sc == "full" and not include_payments:
        # New-style public report: the privacy-safe default selection (roster instead of the
        # financial month table; financial sections are not applicable without payment data).
        requested = ["statistics", "students", "alerts"]
    else:
        requested = list(legacy_map.get(sc, legacy_map["full"]))
    if sc == "student" and "students" not in requested:
        requested.insert(0, "students")
    if not include_payments:
        disabled = [s for s in requested if s in SECTIONS_REQUIRING_PAYMENTS]
        requested = [s for s in requested if s not in SECTIONS_REQUIRING_PAYMENTS]
    else:
        disabled = []
    # de-duplicate preserving order
    seen: set[str] = set()
    requested = [s for s in requested if not (s in seen or seen.add(s))]
    return requested, disabled


def _esc(val: Any) -> str:
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def mask_phone(phone: Any, hide: bool = True) -> str:
    s = str(phone or "").strip()
    if not s:
        return "—"
    if not hide:
        return _esc(s)
    if len(s) <= 5:
        return "•••••"
    return _esc(f"{s[:3]}•••••{s[-3:]}")


def sanitize_export_filename(raw_name: str, default: str = "YawmJadid_Report") -> str:
    cleaned = re.sub(r"[^\w\u0600-\u06FF\-_]+", "_", str(raw_name or "").strip()).strip("_")
    return (cleaned or default)[:80]


def _status_ar(status: str) -> str:
    return {
        "paid": "مسدد بالكامل",
        "partial": "سداد جزئي",
        "unpaid": "غير مسدد",
        "over_discounted": "إعفاء كامل",
        "unsubscribed": "غير مشترك",
    }.get(status, status)


def generate_readonly_html(
    state: dict[str, Any],
    scope: str = "full",
    month: str | None = None,
    teacher: str | None = None,
    student_id: str | None = None,
    hide_personal_data: bool = True,
    period: str = "all_time",
    start_date: str | None = None,
    end_date: str | None = None,
    reference_date: str | None = None,
    backup_health: dict[str, Any] | None = None,
    include_payments: bool | None = None,
    sections: list[str] | None = None,
) -> str:
    """Generates a self-contained, read-only Arabic RTL HTML report.

    Financial privacy modes (requirement 5.x):
    - Mode A (`include_payments=False`, the safe default for public sharing): every payment-related
      value is REMOVED from the generated markup itself — amounts, receipts, payment dates/methods,
      statuses, wages, balances, financial summaries and financial alerts never enter the document.
    - Mode B (`include_payments=True`, explicit opt-in): financial sections render normally; the
      document banner clearly warns that the file contains sensitive financial information.
    - `sections` selects exactly which sections appear (see HTML_SECTION_IDS); financial sections
      that conflict with Mode A are silently dropped and reported in a head meta comment.
    - Legacy scope-only calls keep their previous behavior (full report incl. financial data,
      phone masking driven by `hide_personal_data`).
    """
    sc = scope if scope in SUPPORTED_HTML_SCOPES else "full"
    if include_payments is None:
        # Legacy callers (scope + hide_personal_data) keep the historical behavior: financial
        # content included, phones masked. New callers pass the mode explicitly.
        include_payments = True
    eff_sections, disabled_sections = resolve_html_sections(scope, sections, include_payments)

    settings = state.get("settings") or {}
    acad_name = str(settings.get("academyName") or "أكاديمية يوم جديد")
    currency = str(settings.get("currency") or settings.get("currencyLabel") or "ج.م")
    logo_data_url = str(settings.get("logoDataUri") or settings.get("logoDataUrl") or "")
    if logo_data_url and not logo_data_url.startswith("data:image/"):
        logo_data_url = ""

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ref_dt = reference_date or datetime.date.today().isoformat()
    cur_mk = month or ref_dt[:7]
    wage_basis = str(settings.get("wageBasis") or "net")
    wage_accrual_mode = str(settings.get("wageAccrualMode") or "accrued")

    students = [s for s in (state.get("students") or []) if isinstance(s, dict)]
    if teacher:
        students = [s for s in students if s.get("teacher") == teacher]

    installments = state.get("installments") or []
    inst_map = index_installments_by_student_month(installments)
    teacher_meta = state.get("teacherMeta") or {}

    if eff_sections == ["statistics"]:
        report_title = "تقرير الإحصائيات العامة والمؤشرات التنفيذية"
    elif eff_sections == ["month_finance"]:
        report_title = f"التقرير المالي الشهري — {format_month_label(cur_mk)}"
    elif eff_sections == ["collections"]:
        report_title = f"تقرير التحصيل النقدي والاستحقاق — {format_month_label(cur_mk)}"
    elif eff_sections == ["payroll"]:
        report_title = f"كشف أجور ومرتبات المعلمات — {format_month_label(cur_mk)}"
    elif eff_sections == ["students"]:
        report_title = "التقرير الشامل للطالبة 360°" if student_id else "سجل طالبات الأكاديمية"
    elif eff_sections == ["alerts"]:
        report_title = "ملخص التنبيهات والملاحظات الرقابية"
    elif sc == "teacher":
        report_title = f"تقرير المعلمة: {teacher or 'الكل'}"
    else:
        report_title = "التقرير الشامل للأكاديمية"

    sections_html: list[str] = []
    nav_links: list[tuple[str, str]] = []

    # ---- 1. Statistics section (non-financial KPIs only in the public mode) ----
    if "statistics" in eff_sections:
        stats_data = compute_general_statistics(
            state,
            period=period,
            start_date=start_date,
            end_date=end_date,
            reference_date=ref_dt,
        )
        kpis = stats_data["kpis"]
        nav_links.append(("sec-stats", "الإحصائيات العامة"))
        if include_payments:
            kpi_boxes = f"""
                <div class="kpi-box"><span>الطالبات النشطات</span><strong>{kpis.get('activeStudents', 0)}</strong></div>
                <div class="kpi-box"><span>صافي الاشتراكات</span><strong>{kpis.get('totalSubscriptionsNet', kpis.get('totalSubscriptions', 0))} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>المحصّل الفعلي</span><strong>{kpis.get('totalCollected', 0)} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>المتبقي غير المحصّل</span><strong>{kpis.get('totalOutstanding', 0)} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>نسبة التحصيل</span><strong>{kpis.get('collectionRatePct', 0)}%</strong></div>
                <div class="kpi-box"><span>أجور المعلمات المستحقة</span><strong>{kpis.get('teacherWagesAccrued', kpis.get('totalWagesAccrued', 0))} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>صافي الربح التشغيلي</span><strong>{kpis.get('netOperatingProfit', 0)} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>معدل استبقاء الطالبات</span><strong>{kpis.get('retentionRatePct', 0)}%</strong></div>
            """
            trend_head = """
                    <tr>
                      <th>الشهر</th><th>الطالبات النشطات</th><th>صافي الاشتراكات</th>
                      <th>المحصّل</th><th>المتبقي</th><th>أجور المعلمات</th><th>صافي الربح</th>
                    </tr>"""
            trend_rows = "".join(
                f"""
            <tr>
              <td>{_esc(t.get('label', ''))}</td>
              <td>{t.get('activeStudents', 0)}</td>
              <td>{t.get('revenue', t.get('subscriptions', 0))} {_esc(currency)}</td>
              <td>{t.get('collected', 0)} {_esc(currency)}</td>
              <td>{t.get('uncollected', t.get('outstanding', 0))} {_esc(currency)}</td>
              <td>{t.get('wages', 0)} {_esc(currency)}</td>
              <td>{t.get('profit', t.get('netProfit', 0))} {_esc(currency)}</td>
            </tr>
            """
                for t in stats_data.get("monthlyTrends") or []
            ) or '<tr><td colspan="7" class="empty">لا توجد بيانات شهرية في هذه الفترة</td></tr>'
            fin_note = ""
        else:
            # Public mode: no amounts, no collection rates, no wages, no profits.
            kpi_boxes = f"""
                <div class="kpi-box"><span>الطالبات النشطات</span><strong>{kpis.get('activeStudents', 0)}</strong></div>
                <div class="kpi-box"><span>معدل استبقاء الطالبات</span><strong>{kpis.get('retentionRatePct', 0)}%</strong></div>
                <div class="kpi-box"><span>عدد أقسام الحفظ النشطة</span><strong>{len(state.get('groups') or [])}</strong></div>
                <div class="kpi-box"><span>عدد المعلمات</span><strong>{len([t for t in (state.get('teachers') or []) if teacher_meta.get(t, {}).get('active', True)])}</strong></div>
            """
            trend_head = "<tr><th>الشهر</th><th>الطالبات النشطات</th></tr>"
            trend_rows = "".join(
                f"""
            <tr>
              <td>{_esc(t.get('label', ''))}</td>
              <td>{t.get('activeStudents', 0)}</td>
            </tr>
            """
                for t in stats_data.get("monthlyTrends") or []
            ) or '<tr><td colspan="2" class="empty">لا توجد بيانات شهرية في هذه الفترة</td></tr>'
            fin_note = '<p class="rule-note">وضع الخصوصية: هذا القسم يعرض المؤشرات التعليمية فقط دون أي بيانات مالية.</p>'
        sections_html.append(
            f"""
            <details open id="sec-stats" class="report-section">
              <summary class="section-summary">📊 الإحصائيات العامة ({_esc(stats_data.get('periodLabel', stats_data.get('period', '')))})</summary>
              {fin_note}
              <div class="kpi-grid">
                {kpi_boxes}
              </div>
              <h4>الاتجاه الشهري</h4>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    {trend_head}
                  </thead>
                  <tbody>{trend_rows}</tbody>
                </table>
              </div>
            </details>
            """
        )

    # ---- 2. Month Finance & Students section (financial: Mode B only) ----
    if "month_finance" in eff_sections:
        m_stats = compute_month_stats(
            students,
            cur_mk,
            installments=installments,
            teacher_meta=teacher_meta,
            wage_basis=wage_basis,
            wage_accrual_mode=wage_accrual_mode,
            reference_date=ref_dt,
        )
        m_total = m_stats.get("total") or {}
        nav_links.append(("sec-month-finance", f"سجل الشهر ({format_month_label(cur_mk)})"))
        stu_rows = []
        for idx, s in enumerate(students, start=1):
            m = (s.get("months") or {}).get(cur_mk) or {}
            net_v = effective_month_net_subscription(m)
            disc = max(0, int(m.get("discount") or 0))
            sm_inst = inst_map.get((s.get("id"), cur_mk), [])  # type: ignore[arg-type]
            coll = effective_month_collected(m, sm_inst, reference_date=ref_dt)
            w = effective_month_wage(
                s,
                m,
                teacher_meta=teacher_meta,
                wage_basis=wage_basis,
                wage_accrual_mode=wage_accrual_mode,
                collected_amount=coll,
            )
            st_code = derive_month_payment_status(m, sm_inst, reference_date=ref_dt)
            pay_dt = resolve_payment_date(m)
            start_dt = resolve_actual_start_date(m)
            end_dt = resolve_end_date(m)
            stu_rows.append(
                f"""
                <tr>
                  <td>{idx}</td>
                  <td>{_esc(s.get('name'))}</td>
                  <td dir="ltr">{mask_phone(s.get('phone'), hide_personal_data)}</td>
                  <td>{_esc(s.get('teacher') or 'بدون معلمة')}</td>
                  <td>{_esc(s.get('type') or '—')}</td>
                  <td>{net_v} {_esc(currency)}{f' (خصم {disc})' if disc > 0 else ''}</td>
                  <td>{coll} {_esc(currency)}</td>
                  <td><span class="badge badge-{_esc(st_code)}">{_esc(_status_ar(st_code))}</span></td>
                  <td>{_esc(pay_dt or '—')}</td>
                  <td>{_esc(start_dt or '—')}</td>
                  <td>{_esc(end_dt or '—')}</td>
                  <td>{w} {_esc(currency)}</td>
                </tr>
                """
            )
        stu_tbody = "".join(stu_rows) or '<tr><td colspan="12" class="empty">لا توجد سجلات طالبات</td></tr>'
        sections_html.append(
            f"""
            <details open id="sec-month-finance" class="report-section">
              <summary class="section-summary">👩‍🎓 سجل الطالبات والاشتراكات — {_esc(format_month_label(cur_mk))}</summary>
              <p class="rule-note">ملاحظة محاسبية: «تاريخ الدفع» (مالي) مستقل تمامًا عن «تاريخ البداية الفعلي» (دراسي).</p>
              <div class="kpi-grid">
                <div class="kpi-box"><span>المشتركات</span><strong>{m_total.get('n', m_stats.get('count', 0))}</strong></div>
                <div class="kpi-box"><span>صافي الاشتراكات</span><strong>{m_total.get('subs', m_stats.get('value', 0))} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>المحصّل</span><strong>{m_total.get('collected', m_stats.get('collected', 0))} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>المتبقي</span><strong>{m_total.get('uncollected', m_stats.get('uncollected', 0))} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>أجور المعلمات</span><strong>{m_total.get('wages', m_stats.get('wages', 0))} {_esc(currency)}</strong></div>
              </div>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>م</th>
                      <th>اسم الطالبة</th>
                      <th>رقم التواصل</th>
                      <th>المعلمة</th>
                      <th>الباقة</th>
                      <th>صافي الاشتراك</th>
                      <th>المحصّل</th>
                      <th>الحالة</th>
                      <th>تاريخ الدفع</th>
                      <th>تاريخ البداية الفعلي</th>
                      <th>تاريخ النهاية</th>
                      <th>أجر المعلمة</th>
                    </tr>
                  </thead>
                  <tbody>{stu_tbody}</tbody>
                </table>
              </div>
            </details>
            """
        )

    # ---- 3. Collections section (financial: Mode B only) ----
    if "collections" in eff_sections:
        cash_rep = compute_collection_report(
            students,
            month_key=cur_mk,
            installments=installments,
            start_payment_date=start_date,
            end_payment_date=end_date,
            reference_date=ref_dt,
        )
        accrual_rep = compute_accrual_month_report(
            students,
            month_key=cur_mk,
            installments=installments,
            teacher_meta=teacher_meta,
            wage_basis=wage_basis,
            wage_accrual_mode=wage_accrual_mode,
            reference_date=ref_dt,
        )
        nav_links.append(("sec-collections", "تقرير التحصيل (نقدي واستحقاق)"))
        receipt_items = cash_rep.get("cashReceipts") or []
        if not receipt_items:
            receipt_items = [
                {
                    "receiptNo": r.get("receiptNo", "—"),
                    "paymentDate": r.get("paymentDate", "—"),
                    "studentName": r.get("studentName", "—"),
                    "teacher": r.get("teacher") or "—",
                    "month": r.get("month", ""),
                    "amount": r.get("collected", 0),
                    "method": r.get("method") or "—",
                    "actualStartDate": r.get("actualStartDate") or "—",
                }
                for r in (cash_rep.get("records") or [])
                if r.get("collected", 0) > 0 or r.get("paymentDate")
            ]

        rcp_rows = "".join(
            f"""
            <tr>
              <td>{_esc(str(r.get('receiptNo') or '—'))}</td>
              <td>{_esc(str(r.get('paymentDate') or '—'))}</td>
              <td>{_esc(str(r.get('studentName') or '—'))}</td>
              <td>{_esc(str(r.get('teacher') or '—'))}</td>
              <td>{_esc(format_month_label(r.get('subscriptionMonth') or r.get('month') or ''))}</td>
              <td>{r.get('amount', r.get('collected', 0))} {_esc(currency)}</td>
              <td>{_esc(str(r.get('method') or '—'))}</td>
              <td>{_esc(str(r.get('actualStartDate') or '—'))}</td>
            </tr>
            """
            for r in receipt_items
        ) or '<tr><td colspan="8" class="empty">لا توجد إيصالات مسجلة في هذه الفترة</td></tr>'

        cash_count = len(receipt_items)
        cash_total = cash_rep.get("cashCollectedInPeriod", cash_rep.get("totalCollected", 0))
        accrual_net = accrual_rep.get("totalNetValue", accrual_rep.get("totalNetSubscriptions", 0))
        accrual_coll = accrual_rep.get("totalCollected", 0)
        accrual_uncoll = accrual_rep.get("totalUncollected", accrual_rep.get("totalRemaining", 0))

        sections_html.append(
            f"""
            <details open id="sec-collections" class="report-section">
              <summary class="section-summary">💳 تقرير التحصيل (الأساس النقدي وأساس الاستحقاق) — {_esc(format_month_label(cur_mk))}</summary>
              <div class="kpi-grid">
                <div class="kpi-box"><span>إجمالي الإيصالات المحصّلة (أساس نقدي)</span><strong>{cash_total} {_esc(currency)} ({cash_count} إيصال)</strong></div>
                <div class="kpi-box"><span>صافي اشتراكات الشهر (أساس استحقاق)</span><strong>{accrual_net} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>المحصّل لاشتراكات الشهر</span><strong>{accrual_coll} {_esc(currency)}</strong></div>
                <div class="kpi-box"><span>متأخرات الشهر غير المحصّلة</span><strong>{accrual_uncoll} {_esc(currency)}</strong></div>
              </div>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>رقم الإيصال</th>
                      <th>تاريخ الدفع</th>
                      <th>الطالبة</th>
                      <th>المعلمة</th>
                      <th>شهر الاشتراك</th>
                      <th>المبلغ</th>
                      <th>طريقة الدفع</th>
                      <th>تاريخ البداية الفعلي</th>
                    </tr>
                  </thead>
                  <tbody>{rcp_rows}</tbody>
                </table>
              </div>
            </details>
            """
        )

    # ---- 4. Payroll section (financial: Mode B only) ----
    if "payroll" in eff_sections:
        m_stats = compute_month_stats(
            students,
            cur_mk,
            installments=installments,
            teacher_meta=teacher_meta,
            wage_basis=wage_basis,
            wage_accrual_mode=wage_accrual_mode,
            reference_date=ref_dt,
        )
        payouts = [p for p in (state.get("payouts") or []) if isinstance(p, dict) and p.get("month") == cur_mk]
        nav_links.append(("sec-payroll", "مرتبات وأجور المعلمات"))
        t_rows = []
        for tinfo in m_stats.get("teachers") or []:
            tname = str(tinfo.get("teacher") or "")
            paid_t = sum(int(p.get("amount") or 0) for p in payouts if p.get("teacher") == tname)
            rem_t = max(0, int(tinfo.get("wages") or 0) - paid_t)
            t_rows.append(
                f"""
                <tr>
                  <td>{_esc(tname or 'بدون معلمة')}</td>
                  <td>{tinfo.get('n', 0)}</td>
                  <td>{tinfo.get('subs', 0)} {_esc(currency)}</td>
                  <td>{tinfo.get('collected', 0)} {_esc(currency)}</td>
                  <td>{tinfo.get('wages', 0)} {_esc(currency)}</td>
                  <td>{paid_t} {_esc(currency)}</td>
                  <td>{rem_t} {_esc(currency)}</td>
                </tr>
                """
            )
        t_tbody = "".join(t_rows) or '<tr><td colspan="7" class="empty">لا توجد بيانات أجور لهذا الشهر</td></tr>'
        sections_html.append(
            f"""
            <details open id="sec-payroll" class="report-section">
              <summary class="section-summary">💼 كشف مرتبات المعلمات — {_esc(format_month_label(cur_mk))}</summary>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>المعلمة</th>
                      <th>عدد الطالبات</th>
                      <th>صافي الاشتراكات</th>
                      <th>المحصّل</th>
                      <th>الأجر المستحق</th>
                      <th>المصروف فعليًا</th>
                      <th>المتبقي للمعلمة</th>
                    </tr>
                  </thead>
                  <tbody>{t_tbody}</tbody>
                </table>
              </div>
            </details>
            """
        )

    # ---- 5. Students section: a 360° card for a chosen student, or a privacy-safe roster ----
    if "students" in eff_sections:
        target_stu = next(
            (s for s in (state.get("students") or []) if isinstance(s, dict) and s.get("id") == student_id),
            None,
        )
        if target_stu is not None:
            sid = target_stu.get("id", "")
            s_months = target_stu.get("months") or {}
            m_rows = []
            for mk in sorted(s_months.keys()):
                m = s_months[mk] or {}
                if include_payments:
                    sm_inst = inst_map.get((sid, mk), [])
                    net_v = effective_month_net_subscription(m)
                    coll = effective_month_collected(m, sm_inst, reference_date=ref_dt)
                    st_code = derive_month_payment_status(m, sm_inst, reference_date=ref_dt)
                    m_rows.append(
                        f"""
                    <tr>
                      <td>{_esc(format_month_label(mk))}</td>
                      <td>{net_v} {_esc(currency)}</td>
                      <td>{coll} {_esc(currency)}</td>
                      <td>{_esc(_status_ar(st_code))}</td>
                      <td>{_esc(resolve_payment_date(m) or '—')}</td>
                      <td>{_esc(resolve_actual_start_date(m) or '—')}</td>
                      <td>{_esc(resolve_end_date(m) or '—')}</td>
                    </tr>
                    """
                    )
                else:
                    # Public mode: study dates only — no subscription values, no payment data, no status.
                    m_rows.append(
                        f"""
                    <tr>
                      <td>{_esc(format_month_label(mk))}</td>
                      <td>{_esc(resolve_actual_start_date(m) or '—')}</td>
                      <td>{_esc(resolve_end_date(m) or '—')}</td>
                    </tr>
                    """
                    )
            if include_payments:
                months_head = "<tr><th>الشهر</th><th>صافي الاشتراك</th><th>المحصّل</th><th>الحالة</th><th>تاريخ الدفع</th><th>تاريخ البداية الفعلي</th><th>تاريخ النهاية</th></tr>"
                months_empty = '<tr><td colspan="7" class="empty">لا توجد شهور مسجلة</td></tr>'
            else:
                months_head = "<tr><th>الشهر</th><th>تاريخ البداية الفعلي</th><th>تاريخ النهاية</th></tr>"
                months_empty = '<tr><td colspan="3" class="empty">لا توجد شهور مسجلة</td></tr>'

            receipts_block = ""
            if include_payments:
                s_rcps = [i for i in installments if isinstance(i, dict) and i.get("studentId") == sid]
                rcp_html = "".join(
                    f"<tr><td>{_esc(i.get('receiptNo'))}</td><td>{_esc(i.get('paymentDate') or i.get('date'))}</td><td>{_esc(format_month_label(i.get('month') or ''))}</td><td>{i.get('amount')} {_esc(currency)}</td><td>{_esc(i.get('method') or '—')}</td></tr>"
                    for i in s_rcps
                ) or '<tr><td colspan="5" class="empty">لا توجد إيصالات</td></tr>'
                receipts_block = f"""
                  <h4>إيصالات الدفع</h4>
                  <div class="table-wrap">
                    <table class="data-table">
                      <thead><tr><th>رقم الإيصال</th><th>تاريخ الدفع</th><th>الشهر</th><th>المبلغ</th><th>طريقة الدفع</th></tr></thead>
                      <tbody>{rcp_html}</tbody>
                    </table>
                  </div>
                """

            s_sess = [x for x in (state.get("sessions") or []) if isinstance(x, dict) and x.get("studentId") == sid]
            sess_html = "".join(
                f"<tr><td>{_esc(x.get('date'))}</td><td>{_esc(x.get('time'))}</td><td>{_esc(x.get('teacher') or '')}</td><td>{_esc(x.get('status'))}</td><td>{_esc(x.get('notes') or '')}</td></tr>"
                for x in s_sess
            ) or '<tr><td colspan="5" class="empty">لا توجد حصص مسجلة</td></tr>'

            s_mem = [x for x in (state.get("memorization") or []) if isinstance(x, dict) and x.get("studentId") == sid]
            mem_html = "".join(
                f"<tr><td>{_esc(x.get('date'))}</td><td>{_esc(x.get('track'))}</td><td>{_esc(x.get('fromSurah'))} ({x.get('fromAyah')}) → {_esc(x.get('toSurah'))} ({x.get('toAyah')})</td><td>{x.get('pages')}</td><td>{_esc(x.get('grade'))} ({x.get('score')})</td></tr>"
                for x in s_mem
            ) or '<tr><td colspan="5" class="empty">لا توجد سجلات تسميع</td></tr>'

            privacy_note = (
                "" if include_payments
                else '<p class="rule-note">وضع الخصوصية العام: هذا الملف لا يتضمن أي مبالغ أو إيصالات أو حالات سداد.</p>'
            )
            sections_html.append(
                f"""
                <details open id="sec-student" class="report-section">
                  <summary class="section-summary">🪪 ملف الطالبة الشامل: {_esc(target_stu.get('name'))}</summary>
                  {privacy_note}
                  <div class="kpi-grid">
                    <div class="kpi-box"><span>الاسم</span><strong>{_esc(target_stu.get('name'))}</strong></div>
                    <div class="kpi-box"><span>الكود</span><strong>{_esc(target_stu.get('code') or sid)}</strong></div>
                    <div class="kpi-box"><span>رقم التواصل</span><strong dir="ltr">{mask_phone(target_stu.get('phone'), hide_personal_data)}</strong></div>
                    <div class="kpi-box"><span>المعلمة</span><strong>{_esc(target_stu.get('teacher') or 'بدون معلمة')}</strong></div>
                    <div class="kpi-box"><span>الباقة</span><strong>{_esc(target_stu.get('type') or '—')}</strong></div>
                    <div class="kpi-box"><span>المواعيد</span><strong>{_esc(target_stu.get('schedule') or '—')}</strong></div>
                  </div>
                  <h4>سجل الشهور</h4>
                  <div class="table-wrap">
                    <table class="data-table">
                      <thead>{months_head}</thead>
                      <tbody>{"".join(m_rows) or months_empty}</tbody>
                    </table>
                  </div>
                  {receipts_block}
                  <h4>سجل الحضور والحصص</h4>
                  <div class="table-wrap">
                    <table class="data-table">
                      <thead><tr><th>التاريخ</th><th>الوقت</th><th>المعلمة</th><th>الحالة</th><th>ملاحظات</th></tr></thead>
                      <tbody>{sess_html}</tbody>
                    </table>
                  </div>
                  <h4>سجل الحفظ والمراجعة</h4>
                  <div class="table-wrap">
                    <table class="data-table">
                      <thead><tr><th>التاريخ</th><th>المسار</th><th>النطاق</th><th>الأوجه</th><th>التقدير</th></tr></thead>
                      <tbody>{mem_html}</tbody>
                    </table>
                  </div>
                </details>
                """
            )
        else:
            # Roster (no specific student chosen): identity/study columns only — never amounts.
            nav_links.append(("sec-students", f"سجل الطالبات ({len(students)})"))
            roster_rows = []
            for idx, s in enumerate(students, start=1):
                months_count = len(s.get("months") or {})
                roster_rows.append(
                    f"""
                <tr>
                  <td>{idx}</td>
                  <td>{_esc(s.get('code') or '—')}</td>
                  <td>{_esc(s.get('name'))}</td>
                  <td dir="ltr">{mask_phone(s.get('phone'), hide_personal_data)}</td>
                  <td>{_esc(s.get('teacher') or 'بدون معلمة')}</td>
                  <td>{_esc(s.get('type') or '—')}</td>
                  <td>{months_count}</td>
                </tr>
                """
                )
            roster_tbody = "".join(roster_rows) or '<tr><td colspan="7" class="empty">لا توجد سجلات طالبات</td></tr>'
            sections_html.append(
                f"""
                <details open id="sec-students" class="report-section">
                  <summary class="section-summary">👩‍🎓 سجل طالبات الأكاديمية ({len(students)})</summary>
                  <div class="table-wrap">
                    <table class="data-table">
                      <thead>
                        <tr>
                          <th>م</th><th>الكود</th><th>الاسم</th><th>رقم التواصل</th><th>المعلمة</th><th>الباقة</th><th>عدد الأشهر المسجلة</th>
                        </tr>
                      </thead>
                      <tbody>{roster_tbody}</tbody>
                    </table>
                  </div>
                </details>
                """
            )

    # ---- 6. Alerts section (financial alert categories removed entirely in the public mode) ----
    if "alerts" in eff_sections:
        alerts_data = compute_alerts(state, month=cur_mk, reference_date=ref_dt, backup_health=backup_health)
        if not include_payments:
            alerts_data = dict(alerts_data)
            alerts_data["alerts"] = [
                cat for cat in (alerts_data.get("alerts") or [])
                if isinstance(cat, dict) and str(cat.get("key") or cat.get("category") or "") not in FINANCIAL_ALERT_CATEGORIES
            ]
        tot_alerts = alerts_data.get("totalActive", alerts_data.get("totalItems", len(alerts_data.get("alerts") or [])))
        tot_alerts = sum(len(cat.get("items") or []) for cat in alerts_data.get("alerts") or []) or tot_alerts
        nav_links.append(("sec-alerts", f"التنبيهات ({tot_alerts})"))
        alert_rows = []
        for cat in alerts_data.get("alerts") or []:
            sev = cat.get("severity", "info")
            sev_ar = {"critical": "حرج", "warning": "تنبيه", "info": "معلومة"}.get(sev, sev)
            items = cat.get("items") or []
            if not items:
                alert_rows.append(
                    f"""
                    <tr>
                      <td><span class="badge badge-{_esc(sev)}">{_esc(sev_ar)}</span></td>
                      <td>{_esc(cat.get('title', '—'))}</td>
                      <td>عام</td>
                      <td>—</td>
                      <td>{_esc(cat.get('description', '—'))}</td>
                    </tr>
                    """
                )
            else:
                for a in items:
                    detail = a.get("reason") or a.get("detail") or cat.get("description") or "—"
                    m_lbl = format_month_label(a.get("month") or "") if a.get("month") else (a.get("monthLabel") or "—")
                    alert_rows.append(
                        f"""
                        <tr>
                          <td><span class="badge badge-{_esc(sev)}">{_esc(sev_ar)}</span></td>
                          <td>{_esc(cat.get('title', '—'))}</td>
                          <td>{_esc(a.get('studentName') or a.get('teacher') or 'عام')}</td>
                          <td>{_esc(m_lbl)}</td>
                          <td>{_esc(detail)}</td>
                        </tr>
                        """
                    )
        a_tbody = "".join(alert_rows) or '<tr><td colspan="5" class="empty">لا توجد تنبيهات نشطة حاليًا</td></tr>'
        sections_html.append(
            f"""
            <details open id="sec-alerts" class="report-section">
              <summary class="section-summary">🔔 ملخص التنبيهات الرقابية ({tot_alerts})</summary>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>الأهمية</th>
                      <th>التنبيه</th>
                      <th>الطالبة / المعلمة</th>
                      <th>الشهر</th>
                      <th>التفاصيل</th>
                    </tr>
                  </thead>
                  <tbody>{a_tbody}</tbody>
                </table>
              </div>
            </details>
            """
        )

    logo_img_html = (
        f'<img src="{_esc(logo_data_url)}" alt="شعار الأكاديمية" class="brand-logo">'
        if logo_data_url
        else '<div class="brand-logo-placeholder">ع</div>'
    )
    toc_html = "".join(
        f'<a href="#{_esc(sec_id)}" class="toc-link">{_esc(sec_title)}</a>'
        for sec_id, sec_title in nav_links
    )

    # Privacy-mode banner: the two modes must be clearly distinguishable to the recipient.
    privacy_mode_id = "public" if not include_payments else "financial"
    if not include_payments:
        banner_note = "🔒 نسخة عامة للقراءة فقط — لا تحتوي على أي بيانات مالية (آمنة للمشاركة)"
    else:
        banner_note = "⚠️ تنبيه: يحتوي هذا التقرير على بيانات مالية حساسة — لا تُشارك علنًا دون تصريح من إدارة الأكاديمية"
    disabled_note = f" — الأقسام المالية غير متاحة في وضع الخصوصية العام: {', '.join(disabled_sections)}" if disabled_sections else ""

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
  <meta name="export-privacy-mode" content="{privacy_mode_id}">
  <meta name="export-sections" content="{','.join(eff_sections)}">
  <meta name="generator" content="YawmJadidAcademy">
  <!-- privacy-mode: {privacy_mode_id} | sections: {','.join(eff_sections)} | disabled-by-privacy: {','.join(disabled_sections) if disabled_sections else 'none'}{disabled_note} -->
  <title>{_esc(acad_name)} — {_esc(report_title)} (نسخة للقراءة فقط)</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --surface: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #cbd5e1;
      --primary: #556a4f;
      --primary-dark: #44563f;
      --accent: #b45309;
      --danger: #b91c1c;
      --success: #15803d;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0f172a;
        --surface: #1e293b;
        --text: #f8fafc;
        --muted: #94a3b8;
        --border: #334155;
        --primary: #8fa886;
        --primary-dark: #6c7c64;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 20px;
      font-family: Tahoma, "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
    }}
    .watermark-banner {{
      background: #fef3c7;
      color: #92400e;
      border: 1px solid #f59e0b;
      padding: 8px 16px;
      border-radius: 8px;
      text-align: center;
      font-weight: bold;
      font-size: 13px;
      margin-bottom: 16px;
    }}
    .report-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-right: 6px solid var(--primary);
      padding: 16px 20px;
      border-radius: 12px;
      margin-bottom: 16px;
    }}
    .brand-wrap {{ display: flex; align-items: center; gap: 14px; }}
    .brand-logo {{ width: 52px; height: 52px; object-fit: contain; border-radius: 10px; }}
    .brand-logo-placeholder {{
      width: 48px; height: 48px; border-radius: 10px;
      background: var(--primary-dark); color: #fff;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; font-weight: bold;
    }}
    .report-title h1 {{ margin: 0; font-size: 20px; color: var(--primary); }}
    .report-title p {{ margin: 4px 0 0; font-size: 13px; color: var(--muted); }}
    .toolbar {{
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 10px; margin-bottom: 16px;
    }}
    .toc {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .toc-link {{
      text-decoration: none;
      background: var(--surface);
      color: var(--primary);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: bold;
    }}
    .search-input {{
      padding: 7px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      min-width: 220px;
    }}
    .print-btn {{
      padding: 7px 14px;
      background: var(--primary);
      color: #fff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-weight: bold;
    }}
    .report-section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 20px;
    }}
    .section-summary {{
      font-size: 16px;
      font-weight: bold;
      cursor: pointer;
      color: var(--primary);
      margin-bottom: 12px;
    }}
    .rule-note {{
      font-size: 12.5px;
      color: var(--muted);
      margin: 4px 0 12px;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}
    .kpi-box {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--bg);
    }}
    .kpi-box span {{ display: block; font-size: 12px; color: var(--muted); }}
    .kpi-box strong {{ font-size: 16px; color: var(--text); }}
    .table-wrap {{ overflow-x: auto; }}
    table.data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    table.data-table th, table.data-table td {{
      border: 1px solid var(--border);
      padding: 8px 10px;
      text-align: right;
    }}
    table.data-table th {{
      background: var(--primary-dark);
      color: #ffffff;
      font-weight: bold;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: bold;
    }}
    .badge-paid {{ background: #dcfce7; color: #166534; }}
    .badge-partial, .badge-warning {{ background: #fef3c7; color: #92400e; }}
    .badge-unpaid, .badge-critical {{ background: #fee2e2; color: #991b1b; }}
    .badge-info {{ background: #dbeafe; color: #1e40af; }}
    .empty {{ text-align: center !important; color: var(--muted); padding: 16px !important; }}
    @media print {{
      body {{ background: #ffffff; color: #000000; padding: 0; }}
      .no-print {{ display: none !important; }}
      .report-section {{ page-break-inside: avoid; break-inside: avoid; border: 1px solid #94a3b8; }}
      thead {{ display: table-header-group; }}
      tr {{ page-break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <div class="watermark-banner">
    🔒 نسخة للقراءة فقط — صادرة بتاريخ {_esc(now_str)} — الإصدار v{_esc(__version__)}
    <br>{banner_note}
    {(" — (بيانات التواصل محجوبة للخصوصية)" if hide_personal_data else "")}
  </div>

  <header class="report-header">
    <div class="brand-wrap">
      {logo_img_html}
      <div class="report-title">
        <h1>{_esc(acad_name)}</h1>
        <p>{_esc(report_title)}</p>
      </div>
    </div>
    <div class="meta-info">
      <div>تاريخ الإصدار: <strong>{_esc(now_str)}</strong></div>
      <div>تاريخ المرجع: <strong>{_esc(ref_dt)}</strong></div>
    </div>
  </header>

  <div class="toolbar no-print">
    <nav class="toc" aria-label="فهرس الأقسام">{toc_html}</nav>
    <div>
      <input type="search" id="tblSearch" class="search-input" placeholder="بحث سريع في الجداول..." aria-label="بحث سريع في الجداول">
      <button type="button" class="print-btn" id="btnPrintReport">🖨️ طباعة التقرير</button>
    </div>
  </div>

  <main>
    {"".join(sections_html)}
  </main>

  <script>
    (function() {{
      var searchInput = document.getElementById('tblSearch');
      if (searchInput) {{
        searchInput.addEventListener('input', function() {{
          var q = (searchInput.value || '').trim().toLowerCase();
          var rows = document.querySelectorAll('table.data-table tbody tr');
          for (var i = 0; i < rows.length; i++) {{
            var txt = (rows[i].textContent || '').toLowerCase();
            rows[i].style.display = (!q || txt.indexOf(q) !== -1) ? '' : 'none';
          }}
        }});
      }}
      var btnPrint = document.getElementById('btnPrintReport');
      if (btnPrint) {{
        btnPrint.addEventListener('click', function() {{ window.print(); }});
      }}
    }})();
  </script>
</body>
</html>"""


def generate_printable_receipt_html(state: dict[str, Any], receipt_id: str) -> str:
    """Generates a self-contained printable Arabic payment receipt (`إيصال سداد`) for a given receipt ID or number."""
    settings = state.get("settings") or {}
    acad_name = str(settings.get("academyName") or "أكاديمية يوم جديد")
    currency = str(settings.get("currencyLabel") or "ج.م")
    installments = state.get("installments") or []
    rec = next(
        (
            i for i in installments
            if isinstance(i, dict) and (i.get("id") == receipt_id or i.get("receiptNo") == receipt_id)
        ),
        None,
    )
    if not rec:
        raise KeyError(f"receipt not found: {receipt_id}")

    stu = next(
        (s for s in (state.get("students") or []) if isinstance(s, dict) and s.get("id") == rec.get("studentId")),
        {},
    )
    mk = str(rec.get("month") or "")
    m_entry = (stu.get("months") or {}).get(mk) or {}
    net_sub = effective_month_net_subscription(m_entry)
    actual_start = resolve_actual_start_date(m_entry)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
  <title>إيصال سداد رقم {_esc(rec.get('receiptNo'))} — {_esc(acad_name)}</title>
  <style>
    body {{ font-family: Tahoma, Arial, sans-serif; background: #f8fafc; color: #0f172a; padding: 24px; }}
    .receipt-box {{
      max-width: 680px; margin: 0 auto; background: #ffffff;
      border: 2px solid #556a4f; border-radius: 14px; padding: 24px;
    }}
    .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px dashed #cbd5e1; padding-bottom: 14px; margin-bottom: 16px; }}
    .header h1 {{ margin: 0; font-size: 20px; color: #556a4f; }}
    .rcp-no {{ background: #fef3c7; color: #92400e; padding: 6px 12px; border-radius: 8px; font-weight: bold; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
    .field {{ background: #f1f5f9; padding: 10px 14px; border-radius: 8px; }}
    .field span {{ display: block; font-size: 12px; color: #475569; }}
    .field strong {{ font-size: 15px; }}
    .amount-banner {{ background: #dcfce7; color: #166534; padding: 14px; border-radius: 10px; text-align: center; font-size: 18px; font-weight: bold; margin-bottom: 16px; }}
    .footer {{ font-size: 12px; color: #64748b; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 12px; }}
    @media print {{ .no-print {{ display: none !important; }} body {{ background: #fff; padding: 0; }} }}
  </style>
</head>
<body>
  <div class="receipt-box">
    <div class="header">
      <div>
        <h1>{_esc(acad_name)}</h1>
        <div>سند قبض / إيصال استلام اشتراك (إيصال سداد)</div>
      </div>
      <div class="rcp-no">رقم الإيصال: {_esc(rec.get('receiptNo'))}</div>
    </div>
    <div class="amount-banner">
      المبلغ المسدد: {int(rec.get('amount') or 0)} {_esc(currency)}
    </div>
    <div class="grid">
      <div class="field"><span>اسم الطالبة</span><strong>{_esc(stu.get('name') or rec.get('studentId'))}</strong></div>
      <div class="field"><span>المعلمة</span><strong>{_esc(stu.get('teacher') or '—')}</strong></div>
      <div class="field"><span>شهر الاشتراك</span><strong>{_esc(format_month_label(mk))}</strong></div>
      <div class="field"><span>طريقة الدفع</span><strong>{_esc(rec.get('method') or 'نقدي / محول')}</strong></div>
      <div class="field"><span>تاريخ الدفع الفعلي (مالي)</span><strong>{_esc(rec.get('paymentDate') or rec.get('date'))}</strong></div>
      <div class="field"><span>تاريخ البداية الفعلي للدراسة</span><strong>{_esc(actual_start or 'مستقل / غير محدد')}</strong></div>
      <div class="field"><span>صافي اشتراك الشهر</span><strong>{net_sub} {_esc(currency)}</strong></div>
      <div class="field"><span>ملاحظات</span><strong>{_esc(rec.get('note') or '—')}</strong></div>
    </div>
    <div class="footer">
      نسخة رسمية صادرة من النظام المحلي بتاريخ {_esc(now_str)} — الإصدار v{_esc(__version__)}
      <div class="no-print" style="margin-top:12px;">
        <button type="button" onclick="window.print()" style="padding:8px 18px;background:#556a4f;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:bold;">🖨️ طباعة الإيصال</button>
      </div>
    </div>
  </div>
</body>
</html>"""
