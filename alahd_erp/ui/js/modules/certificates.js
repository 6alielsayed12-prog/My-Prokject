/* ============================================================================
   modules/certificates.js — Academy-Branded Editable Certificate Studio
   6 offline high-res (1600x1130 A4 Landscape) templates tailored to Yawm Jadid Academy
   ============================================================================ */

import { $ } from './dom.js';
import { state, uiState } from './store.js';

export const CERT_TEMPLATES = [
  {
    id: 'academy_olive_gold',
    name: 'الأكاديمية الرسمي',
    swatch: 'linear-gradient(135deg, #2F3B2A 0%, #6B7F5A 48%, #C9A24B 100%)',
    bg: '#F6F3EA',
    bgInner: '#FCFBF7',
    primary: '#2F3B2A',
    secondary: '#6B7F5A',
    accent: '#C9A24B',
    textMain: '#1E261B',
    textMuted: '#5A6552',
    badgeBg: '#E6EDD9',
  },
  {
    id: 'botanical_sage',
    name: 'الزهور والريحان',
    swatch: 'linear-gradient(135deg, #F5EFE0 0%, #A8B89A 55%, #6B7F5A 100%)',
    bg: '#F5EFE0',
    bgInner: '#FCF9F2',
    primary: '#4A5D41',
    secondary: '#8A9A7B',
    accent: '#C9B27C',
    textMain: '#383A28',
    textMuted: '#6A715D',
    badgeBg: '#EAEFE3',
  },
  {
    id: 'islamic_arabesque',
    name: 'التراث المزخرف',
    swatch: 'linear-gradient(135deg, #1E3A2A 0%, #2B533C 55%, #D4AF37 100%)',
    bg: '#FAF7EF',
    bgInner: '#FFFFFF',
    primary: '#1E3A2A',
    secondary: '#2B533C',
    accent: '#C9A24B',
    textMain: '#17281E',
    textMuted: '#4D6154',
    badgeBg: '#EFE7CF',
  },
  {
    id: 'marble_gold',
    name: 'الرخام الذهبي',
    swatch: 'linear-gradient(135deg, #FFFFFF 0%, #EFECE6 52%, #C9A24B 100%)',
    bg: '#F9F8F5',
    bgInner: '#FFFFFF',
    primary: '#556A4F',
    secondary: '#7B8D6E',
    accent: '#C59B38',
    textMain: '#232B1E',
    textMuted: '#626B5C',
    badgeBg: '#F6ECD0',
  },
  {
    id: 'modern_teal_navy',
    name: 'العصري (كحلي وتيل)',
    swatch: 'linear-gradient(135deg, #0C2340 0%, #12766F 58%, #E6F4F3 100%)',
    bg: '#F4F9F9',
    bgInner: '#FFFFFF',
    primary: '#0C2340',
    secondary: '#12766F',
    accent: '#C9A24B',
    textMain: '#0C2340',
    textMuted: '#3E5B6B',
    badgeBg: '#DDF1EF',
  },
  {
    id: 'hifz_classic_brown',
    name: 'الإجازة الوقور',
    swatch: 'linear-gradient(135deg, #5C3D24 0%, #8C6239 55%, #F5EFE0 100%)',
    bg: '#F8F2E4',
    bgInner: '#FFFDF8',
    primary: '#5C3D24',
    secondary: '#8C6239',
    accent: '#C9A24B',
    textMain: '#382312',
    textMuted: '#6D543E',
    badgeBg: '#F2E5CE',
  },
];

let _activeTemplateId = 'academy_olive_gold';

export function getActiveCertTemplateId() {
  return _activeTemplateId;
}

export function setActiveCertTemplateId(tplId) {
  const found = CERT_TEMPLATES.find((t) => t.id === tplId);
  _activeTemplateId = found ? found.id : CERT_TEMPLATES[0].id;
  document.querySelectorAll('.cert-tpl-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('data-tpl-id') === _activeTemplateId);
  });
}

export function readCertFormConfig() {
  const stuSel = $('#certStudentSelect');
  const stu = state.students.find((s) => s.id === (stuSel && stuSel.value)) || state.students[0];
  const customRecipient = ($('#certRecipientOverride') && $('#certRecipientOverride').value.trim()) || '';
  const recipientName = customRecipient || (stu ? stu.name : 'اسم الطالبة');
  const acadDefault = (state.settings && state.settings.academyName) || 'أكاديمية يوم جديد للقرآن الكريم';
  const defaultRightName = (stu && stu.teacher) ? stu.teacher : 'معلمة الحلقة';

  return {
    templateId: _activeTemplateId,
    studentId: stu ? stu.id : '',
    studentName: recipientName,
    academyName: ($('#certAcademyInput') && $('#certAcademyInput').value.trim()) || acadDefault,
    heading: ($('#certHeadingInput') && $('#certHeadingInput').value.trim()) || 'شهادة شكر وتقدير',
    introLine: ($('#certIntroInput') && $('#certIntroInput').value.trim()) || 'تتشرف إدارة الأكاديمية بمنح هذه الشهادة للطالبة:',
    courseTitle: ($('#certTitleInput') && $('#certTitleInput').value.trim()) || 'إتمام حفظ ومراجعة جزء عمّ',
    grade: ($('#certGradeInput') && $('#certGradeInput').value.trim()) || 'ممتاز متقن',
    blessingLine: ($('#certBlessingInput') && $('#certBlessingInput').value.trim()) || 'سائلين الله أن يجعل القرآن الكريم ربيع قلبها ونور صدرها، وأن يبارك لها في حفظها ويثبته.',
    issueDate: ($('#certDateInput') && $('#certDateInput').value) || uiState.referenceDate || '',
    hijriDate: ($('#certHijriInput') && $('#certHijriInput').value.trim()) || '',
    rightRole: ($('#certRightRoleInput') && $('#certRightRoleInput').value.trim()) || 'معلمة الحلقة',
    rightName: ($('#certRightNameInput') && $('#certRightNameInput').value.trim()) || defaultRightName,
    leftRole: ($('#certLeftRoleInput') && $('#certLeftRoleInput').value.trim()) || 'مديرة الأكاديمية',
    leftName: ($('#certLeftNameInput') && $('#certLeftNameInput').value.trim()) || 'إدارة الأكاديمية',
    showBismillah: $('#certShowBismillah') ? Boolean($('#certShowBismillah').checked) : true,
    showSeal: $('#certShowSeal') ? Boolean($('#certShowSeal').checked) : true,
    showCertNo: $('#certShowCertNo') ? Boolean($('#certShowCertNo').checked) : true,
    certNo: ($('#certCurrentNo') && $('#certCurrentNo').value.trim()) || 'CERT-NEW',
  };
}

export function applyCertTemplateDefaults(savedTpl) {
  if (!savedTpl || typeof savedTpl !== 'object') return;
  if (savedTpl.templateId) setActiveCertTemplateId(savedTpl.templateId);
  if (savedTpl.academyName && $('#certAcademyInput')) $('#certAcademyInput').value = savedTpl.academyName;
  if (savedTpl.heading && $('#certHeadingInput')) $('#certHeadingInput').value = savedTpl.heading;
  if (savedTpl.introLine && $('#certIntroInput')) $('#certIntroInput').value = savedTpl.introLine;
  if (savedTpl.blessingLine && $('#certBlessingInput')) $('#certBlessingInput').value = savedTpl.blessingLine;
  if (savedTpl.rightRole && $('#certRightRoleInput')) $('#certRightRoleInput').value = savedTpl.rightRole;
  if (savedTpl.leftRole && $('#certLeftRoleInput')) $('#certLeftRoleInput').value = savedTpl.leftRole;
  if (savedTpl.leftName && $('#certLeftNameInput')) $('#certLeftNameInput').value = savedTpl.leftName;
  if (typeof savedTpl.showBismillah === 'boolean' && $('#certShowBismillah')) $('#certShowBismillah').checked = savedTpl.showBismillah;
  if (typeof savedTpl.showSeal === 'boolean' && $('#certShowSeal')) $('#certShowSeal').checked = savedTpl.showSeal;
  if (typeof savedTpl.showCertNo === 'boolean' && $('#certShowCertNo')) $('#certShowCertNo').checked = savedTpl.showCertNo;
}

function drawCornerOrnament(ctx, cx, cy, sx, sy, color, accent) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(sx, sy);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(0, 62);
  ctx.quadraticCurveTo(8, 8, 62, 0);
  ctx.stroke();

  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(10, 48);
  ctx.quadraticCurveTo(14, 14, 48, 10);
  ctx.stroke();

  ctx.fillStyle = accent;
  ctx.beginPath();
  ctx.arc(16, 16, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawAcademySeal(ctx, x, y, r, tpl, academyShort) {
  ctx.save();
  ctx.translate(x, y);

  // Outer scalloped ring
  ctx.fillStyle = tpl.bgInner;
  ctx.strokeStyle = tpl.accent;
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  ctx.strokeStyle = tpl.primary;
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  ctx.arc(0, 0, r - 9, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);

  // Crescent & olive leaf icon
  ctx.strokeStyle = tpl.secondary;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(0, -6, 18, 0.35 * Math.PI, 1.65 * Math.PI);
  ctx.stroke();

  ctx.fillStyle = tpl.primary;
  ctx.textAlign = 'center';
  ctx.font = 'bold 14px Cairo, sans-serif';
  ctx.fillText('ختم الأكاديمية', 0, 22);
  ctx.font = 'bold 12px Cairo, sans-serif';
  ctx.fillStyle = tpl.accent;
  ctx.fillText(academyShort.slice(0, 18), 0, 40);
  ctx.restore();
}

function drawBotanicalCorners(ctx, w, h, tpl) {
  const drawCluster = (ox, oy, sx, sy) => {
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(sx, sy);
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = tpl.secondary;
    ctx.beginPath();
    ctx.ellipse(75, 55, 68, 32, -0.35, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = tpl.accent;
    ctx.beginPath();
    ctx.ellipse(48, 105, 54, 24, 0.45, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = tpl.primary;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(14, 14);
    ctx.quadraticCurveTo(60, 50, 128, 74);
    ctx.stroke();
    const leaves = [[45, 38, -0.4], [78, 52, 0.3], [104, 66, -0.2], [58, 72, 0.6]];
    for (const [lx, ly, rot] of leaves) {
      ctx.save();
      ctx.translate(lx, ly);
      ctx.rotate(rot);
      ctx.fillStyle = tpl.secondary;
      ctx.beginPath();
      ctx.ellipse(0, 0, 16, 7, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.restore();
  };
  drawCluster(24, 24, 1, 1);
  drawCluster(w - 24, h - 24, -1, -1);
}

function drawModernGeometricCorners(ctx, w, h, tpl) {
  ctx.save();
  // Top-left navy/teal triangles
  ctx.fillStyle = tpl.primary;
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(240, 0);
  ctx.lineTo(0, 210);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = tpl.secondary;
  ctx.globalAlpha = 0.85;
  ctx.beginPath();
  ctx.moveTo(130, 0);
  ctx.lineTo(310, 0);
  ctx.lineTo(0, 280);
  ctx.lineTo(0, 130);
  ctx.closePath();
  ctx.fill();

  // Bottom-right accent triangles
  ctx.globalAlpha = 1;
  ctx.fillStyle = tpl.primary;
  ctx.beginPath();
  ctx.moveTo(w, h);
  ctx.lineTo(w - 240, h);
  ctx.lineTo(w, h - 210);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = tpl.accent;
  ctx.globalAlpha = 0.85;
  ctx.beginPath();
  ctx.moveTo(w - 220, h);
  ctx.lineTo(w - 270, h);
  ctx.lineTo(w, h - 245);
  ctx.lineTo(w, h - 200);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawMarbleTexture(ctx, w, h) {
  ctx.save();
  ctx.strokeStyle = 'rgba(180, 168, 140, 0.16)';
  ctx.lineWidth = 2;
  for (let i = 0; i < 14; i++) {
    ctx.beginPath();
    const y0 = 60 + i * 78;
    ctx.moveTo(40, y0);
    ctx.bezierCurveTo(w * 0.3, y0 - 45, w * 0.65, y0 + 55, w - 40, y0 - 15);
    ctx.stroke();
  }
  ctx.restore();
}

function drawWrappedText(ctx, text, x, startY, maxWidth, lineHeight) {
  const words = String(text || '').split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  for (const word of words) {
    const test = current ? `${current} ${word}` : word;
    if (ctx.measureText(test).width > maxWidth && current) {
      lines.push(current);
      current = word;
    } else {
      current = test;
    }
  }
  if (current) lines.push(current);
  lines.slice(0, 3).forEach((ln, idx) => {
    ctx.fillText(ln, x, startY + idx * lineHeight);
  });
  return lines.slice(0, 3).length;
}

export function drawCertificateOnCanvas(canvas, cfg) {
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  if (typeof ctx.save !== 'function' || typeof ctx.beginPath !== 'function') {
    if (typeof ctx.fillRect === 'function') {
      ctx.fillStyle = '#F6F3EA';
      ctx.fillRect(0, 0, canvas.width || 1600, canvas.height || 1130);
    }
    if (typeof ctx.fillText === 'function') {
      ctx.fillStyle = '#2F3B2A';
      ctx.fillText(cfg.academyName || '', (canvas.width || 1600) / 2, 120);
      ctx.fillText(cfg.heading || '', (canvas.width || 1600) / 2, 220);
      ctx.fillText(cfg.studentName || '', (canvas.width || 1600) / 2, 360);
      ctx.fillText(cfg.courseTitle || '', (canvas.width || 1600) / 2, 460);
    }
    return;
  }
  const w = canvas.width;
  const h = canvas.height;
  const tpl = CERT_TEMPLATES.find((t) => t.id === cfg.templateId) || CERT_TEMPLATES[0];

  // 1. Background
  ctx.fillStyle = tpl.bg;
  ctx.fillRect(0, 0, w, h);

  if (tpl.id === 'marble_gold') {
    drawMarbleTexture(ctx, w, h);
  }

  // Inner card surface
  ctx.fillStyle = tpl.bgInner;
  ctx.fillRect(42, 42, w - 84, h - 84);

  // Template-specific decorative motifs
  if (tpl.id === 'botanical_sage') {
    drawBotanicalCorners(ctx, w, h, tpl);
  } else if (tpl.id === 'modern_teal_navy') {
    drawModernGeometricCorners(ctx, w, h, tpl);
  } else if (tpl.id === 'islamic_arabesque') {
    // Top-right golden ribbon badge
    ctx.save();
    ctx.fillStyle = tpl.primary;
    ctx.fillRect(w - 230, 42, 130, 175);
    ctx.fillStyle = tpl.accent;
    ctx.beginPath();
    ctx.arc(w - 165, 165, 52, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = tpl.bgInner;
    ctx.font = 'bold 18px Cairo, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('أكاديمية', w - 165, 158);
    ctx.fillText('يوم جديد', w - 165, 184);
    ctx.restore();
  } else if (tpl.id === 'hifz_classic_brown') {
    // Subtle geometric diamond watermark
    ctx.save();
    ctx.strokeStyle = 'rgba(140, 98, 57, 0.08)';
    ctx.lineWidth = 2;
    ctx.translate(w / 2, h / 2);
    for (let k = 0; k < 4; k++) {
      ctx.rotate(Math.PI / 4);
      ctx.strokeRect(-220, -220, 440, 440);
    }
    ctx.restore();
  }

  // 2. Borders
  ctx.strokeStyle = tpl.primary;
  ctx.lineWidth = 10;
  ctx.strokeRect(36, 36, w - 72, h - 72);

  ctx.strokeStyle = tpl.accent;
  ctx.lineWidth = 3;
  ctx.strokeRect(54, 54, w - 108, h - 108);

  if (tpl.id !== 'modern_teal_navy') {
    drawCornerOrnament(ctx, 64, 64, 1, 1, tpl.primary, tpl.accent);
    drawCornerOrnament(ctx, w - 64, 64, -1, 1, tpl.primary, tpl.accent);
    drawCornerOrnament(ctx, 64, h - 64, 1, -1, tpl.primary, tpl.accent);
    drawCornerOrnament(ctx, w - 64, h - 64, -1, -1, tpl.primary, tpl.accent);
  }

  ctx.textAlign = 'center';
  ctx.direction = 'rtl';

  // 3. Header / Bismillah / Academy Name
  let curY = 128;
  if (cfg.showBismillah) {
    ctx.fillStyle = tpl.textMuted;
    ctx.font = '600 22px Cairo, sans-serif';
    ctx.fillText('بسم الله الرحمن الرحيم', w / 2, curY);
    curY += 46;
  }

  ctx.fillStyle = tpl.secondary;
  ctx.font = 'bold 30px Cairo, sans-serif';
  ctx.fillText(cfg.academyName, w / 2, curY);
  curY += 74;

  // 4. Main Certificate Heading + Gold Divider
  ctx.fillStyle = tpl.primary;
  ctx.font = '800 58px Cairo, sans-serif';
  ctx.fillText(cfg.heading, w / 2, curY);

  // Ornamental divider
  const divY = curY + 28;
  ctx.strokeStyle = tpl.accent;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(w / 2 - 250, divY);
  ctx.lineTo(w / 2 - 22, divY);
  ctx.moveTo(w / 2 + 22, divY);
  ctx.lineTo(w / 2 + 250, divY);
  ctx.stroke();
  ctx.fillStyle = tpl.accent;
  ctx.beginPath();
  ctx.arc(w / 2, divY, 6, 0, Math.PI * 2);
  ctx.fill();

  curY += 90;

  // 5. Intro Line
  ctx.fillStyle = tpl.textMuted;
  ctx.font = '600 26px Cairo, sans-serif';
  ctx.fillText(cfg.introLine, w / 2, curY);
  curY += 76;

  // 6. Recipient Name Highlight
  ctx.fillStyle = tpl.textMain;
  ctx.font = '800 54px Cairo, sans-serif';
  ctx.fillText(cfg.studentName, w / 2, curY);

  // Subtle underline under student name
  const nameW = Math.min(540, Math.max(260, ctx.measureText(cfg.studentName).width + 60));
  ctx.strokeStyle = tpl.accent;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(w / 2 - nameW / 2, curY + 18);
  ctx.lineTo(w / 2 + nameW / 2, curY + 18);
  ctx.stroke();
  curY += 78;

  // 7. Course / Quranic Achievement
  ctx.fillStyle = tpl.primary;
  ctx.font = 'bold 34px Cairo, sans-serif';
  ctx.fillText(cfg.courseTitle, w / 2, curY);
  curY += 54;

  // 8. Grade Pill
  const gradeLabel = `بتقدير: ${cfg.grade}`;
  ctx.font = 'bold 26px Cairo, sans-serif';
  const pillW = Math.min(480, Math.max(220, ctx.measureText(gradeLabel).width + 64));
  ctx.fillStyle = tpl.badgeBg;
  ctx.strokeStyle = tpl.accent;
  ctx.lineWidth = 2;
  ctx.beginPath();
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(w / 2 - pillW / 2, curY - 34, pillW, 48, 24);
  } else {
    ctx.rect(w / 2 - pillW / 2, curY - 34, pillW, 48);
  }
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = tpl.primary;
  ctx.fillText(gradeLabel, w / 2, curY);
  curY += 66;

  // 9. Blessing / Duaa text
  ctx.fillStyle = tpl.textMuted;
  ctx.font = '600 22px Cairo, sans-serif';
  drawWrappedText(ctx, cfg.blessingLine, w / 2, curY, w - 280, 34);

  // 10. Footer Signatories, Date & Official Seal
  const footY = h - 148;
  ctx.fillStyle = tpl.primary;
  ctx.font = 'bold 22px Cairo, sans-serif';
  ctx.fillText(cfg.rightRole, w - 280, footY);
  ctx.fillStyle = tpl.textMain;
  ctx.font = '600 21px Cairo, sans-serif';
  ctx.fillText(cfg.rightName, w - 280, footY + 38);

  ctx.fillStyle = tpl.primary;
  ctx.font = 'bold 22px Cairo, sans-serif';
  ctx.fillText(cfg.leftRole, 280, footY);
  ctx.fillStyle = tpl.textMain;
  ctx.font = '600 21px Cairo, sans-serif';
  ctx.fillText(cfg.leftName, 280, footY + 38);

  // Center Date + Cert No
  ctx.fillStyle = tpl.textMuted;
  ctx.font = '600 19px Cairo, sans-serif';
  const dateText = cfg.hijriDate
    ? `التاريخ: ${cfg.issueDate}  |  ${cfg.hijriDate}`
    : `تاريخ الإصدار: ${cfg.issueDate}`;
  ctx.fillText(dateText, w / 2, footY + 12);

  if (cfg.showCertNo && cfg.certNo) {
    ctx.font = '600 16px Cairo, sans-serif';
    ctx.fillText(`رقم الشهادة: ${cfg.certNo}`, w / 2, footY + 42);
  }

  // Seal
  if (cfg.showSeal) {
    drawAcademySeal(ctx, w / 2, footY - 56, 48, tpl, cfg.academyName);
  }
}

export function printCertificateCanvas(canvas, title = 'شهادة أكاديمية يوم جديد') {
  if (!canvas) return;
  const dataUrl = canvas.toDataURL('image/png');
  const win = window.open('', '_blank', 'width=1050,height=760');
  if (!win) {
    window.print();
    return;
  }
  const doc = win.document;
  doc.documentElement.setAttribute('lang', 'ar');
  doc.documentElement.setAttribute('dir', 'rtl');
  doc.title = String(title || 'شهادة أكاديمية يوم جديد');
  const styleEl = doc.createElement('style');
  styleEl.textContent = '@page{size:A4 landscape;margin:8mm;}body{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#fff;}img{max-width:100%;max-height:100vh;object-fit:contain;}';
  doc.head.appendChild(styleEl);
  const img = doc.createElement('img');
  img.src = dataUrl;
  img.alt = 'Certificate';
  doc.body.replaceChildren(img);
  win.focus();
  setTimeout(() => {
    win.print();
  }, 250);
}
