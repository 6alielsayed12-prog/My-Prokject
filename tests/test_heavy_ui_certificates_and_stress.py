"""Heavy Stress, Sidebar UX, Editable Certificate Studio & Live E2E Tests."""
from __future__ import annotations

import concurrent.futures
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from alahd_erp.api.server import create_app
from tests._helpers import ROOT

try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except Exception:
    HAS_TESTCLIENT = False


class HeavyUiAndCertificatesTests(unittest.TestCase):
    def test_sidebar_collapse_and_comfort_elements_present(self) -> None:
        html_txt = (ROOT / "alahd_erp/ui/index.html").read_text(encoding="utf-8")
        css_txt = (ROOT / "alahd_erp/ui/css/design-system.css").read_text(encoding="utf-8")
        app_txt = (ROOT / "alahd_erp/ui/js/app.js").read_text(encoding="utf-8")

        for dom_id in ("appSidebar", "btnToggleSidebar", "btnSidebarClose", "sidebarBackdrop", "btnScrollTop"):
            self.assertIn(f'id="{dom_id}"', html_txt)
        self.assertIn('data-sidebar-collapsed="true"', css_txt)
        self.assertIn("SIDEBAR_COLLAPSED_KEY", app_txt)
        self.assertIn("toLowerCase() === 'b'", app_txt)

    def test_all_six_certificate_templates_wired(self) -> None:
        html_txt = (ROOT / "alahd_erp/ui/index.html").read_text(encoding="utf-8")
        cert_js = (ROOT / "alahd_erp/ui/js/modules/certificates.js").read_text(encoding="utf-8")
        for tpl_id in (
            "academy_olive_gold",
            "botanical_sage",
            "islamic_arabesque",
            "marble_gold",
            "modern_teal_navy",
            "hifz_classic_brown",
        ):
            self.assertIn(f'data-tpl-id="{tpl_id}"', html_txt)
            self.assertIn(f"id: '{tpl_id}'", cert_js)

    def test_bat_scripts_ascii_crlf_and_valid_labels(self) -> None:
        for name in ("build_and_run.bat", "build_windows.bat"):
            raw = (ROOT / name).read_bytes()
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), f"{name} must use CRLF line endings")
            txt = raw.decode("ascii")
            self.assertIn(":unc_fail", txt)
            self.assertIn("\n:fail\r\n", txt.replace("\r\n", "\n").replace("\n", "\r\n"))

    @unittest.skipUnless(HAS_TESTCLIENT, "FastAPI TestClient unavailable")
    def test_certificate_crud_template_kv_and_heavy_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = "heavy-test-secret"
            app = create_app(Path(tmp), ui_dir=ROOT / "alahd_erp/ui", window_secret=secret, seed_on_empty=True)
            try:
                client = TestClient(app)
                hdrs = {"X-Window-Secret": secret}

                # Unauthorized without secret
                self.assertEqual(client.get("/api/sync").status_code, 401)

                state_res = client.get("/api/sync", headers=hdrs)
                self.assertEqual(state_res.status_code, 200)
                students = state_res.json()["students"]
                self.assertGreater(len(students), 0)
                sid = students[0]["id"]

                # 1. Save default certificateTemplate via KV
                tpl_payload = {
                    "templateId": "botanical_sage",
                    "academyName": "أكاديمية يوم جديد للقرآن الكريم",
                    "heading": "شهادة تكريم وإتقان",
                    "introLine": "تتشرف إدارة الأكاديمية بتكريم الطالبة:",
                    "blessingLine": "بارك الله في حفظها ورفعها بالقرآن.",
                    "rightRole": "معلمة الحلقة",
                    "leftRole": "مديرة الأكاديمية",
                    "leftName": "إدارة الأكاديمية",
                    "showBismillah": True,
                    "showSeal": True,
                    "showCertNo": True,
                }
                kv_res = client.put("/api/kv/certificateTemplate", json=tpl_payload, headers=hdrs)
                self.assertEqual(kv_res.status_code, 200)

                # 2. Issue custom certificate with full coords
                cert_res = client.post(
                    "/api/certificates",
                    json={
                        "studentId": sid,
                        "studentName": students[0]["name"],
                        "courseTitle": "إتمام حفظ سورة البقرة",
                        "grade": "ممتاز مع الإتقان",
                        "issueDate": "2026-11-16",
                        "teacherName": students[0].get("teacher", ""),
                        "supervisorName": "مشرفة الحلقات",
                        "coords": tpl_payload,
                    },
                    headers=hdrs,
                )
                self.assertEqual(cert_res.status_code, 200)
                cert_id = cert_res.json()["id"]

                # Verify persistence in /api/sync
                state2 = client.get("/api/sync", headers=hdrs).json()
                self.assertEqual(state2["certificateTemplate"]["templateId"], "botanical_sage")
                saved_cert = next((c for c in state2["certificates"] if c["id"] == cert_id), None)
                self.assertIsNotNone(saved_cert)
                self.assertEqual(saved_cert["coords"]["heading"], "شهادة تكريم وإتقان")

                # 3. Heavy concurrent read stress across endpoints (40 parallel requests)
                endpoints = ["/api/sync", "/api/alerts", "/api/health", "/api/system/diagnostics", "/api/system/logs"]
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futs = [pool.submit(client.get, endpoints[i % len(endpoints)], headers=hdrs) for i in range(40)]
                    for f in futs:
                        r = f.result(timeout=15)
                        self.assertEqual(r.status_code, 200)

                # 4. Cleanup certificate
                del_res = client.delete(f"/api/certificates/{cert_id}", headers=hdrs)
                self.assertEqual(del_res.status_code, 200)
            finally:
                if hasattr(app.state, "store"):
                    app.state.store.close()

    def test_live_node_e2e_certificates_module_and_http_roundtrip(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js unavailable")

        import uvicorn

        with tempfile.TemporaryDirectory() as tmp:
            secret = "e2e-cert-secret"
            app = create_app(Path(tmp), ui_dir=ROOT / "alahd_erp/ui", window_secret=secret, seed_on_empty=True)
            try:
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.close()

                config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
                server = uvicorn.Server(config)
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()

                deadline = time.time() + 10
                while time.time() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                            break
                    except OSError:
                        time.sleep(0.1)

                try:
                    js_dir = Path(tmp) / "js"
                    shutil.copytree(ROOT / "alahd_erp/ui/js", js_dir)
                    (js_dir / "package.json").write_text('{"type": "module"}', encoding="utf-8")

                    e2e_script = js_dir / "run_cert_e2e.mjs"
                    e2e_script.write_text(
                        f"""
const noop = () => {{}};
globalThis.window = {{ location: {{ hash: '#/certificates' }}, print: noop }};
globalThis.document = {{
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
}};

const {{ CERT_TEMPLATES, drawCertificateOnCanvas, setActiveCertTemplateId, getActiveCertTemplateId }} =
  await import('./modules/certificates.js');

if (CERT_TEMPLATES.length !== 6) throw new Error('Expected 6 certificate templates');

const calls = [];
const mockCtx = {{
  save: noop, restore: noop, translate: noop, scale: noop, rotate: noop,
  beginPath: noop, closePath: noop, moveTo: noop, lineTo: noop,
  quadraticCurveTo: noop, bezierCurveTo: noop, arc: noop, ellipse: noop,
  rect: noop, roundRect: noop, fill: noop, stroke: noop, setLineDash: noop,
  fillRect: noop, strokeRect: noop,
  measureText: (t) => ({{ width: String(t || '').length * 14 }}),
  fillText: (t) => calls.push(String(t || '')),
}};
const mockCanvas = {{ width: 1600, height: 1130, getContext: () => mockCtx }};

for (const tpl of CERT_TEMPLATES) {{
  setActiveCertTemplateId(tpl.id);
  if (getActiveCertTemplateId() !== tpl.id) throw new Error('Template switch failed for ' + tpl.id);
  calls.length = 0;
  drawCertificateOnCanvas(mockCanvas, {{
    templateId: tpl.id,
    academyName: 'أكاديمية يوم جديد للقرآن الكريم',
    heading: 'شهادة شكر وتقدير',
    introLine: 'تتشرف إدارة الأكاديمية بمنح هذه الشهادة للطالبة:',
    studentName: 'مريم أحمد',
    courseTitle: 'إتمام حفظ جزء عمّ',
    grade: 'ممتاز متقن',
    blessingLine: 'بارك الله في حفظها.',
    issueDate: '2026-11-16',
    hijriDate: '٥ جمادى الآخرة ١٤٤٨ هـ',
    rightRole: 'معلمة الحلقة',
    rightName: 'أ/ خديجة',
    leftRole: 'مديرة الأكاديمية',
    leftName: 'إدارة الأكاديمية',
    showBismillah: true,
    showSeal: true,
    showCertNo: true,
    certNo: 'CERT-1001',
  }});
  if (!calls.includes('مريم أحمد') || !calls.includes('شهادة شكر وتقدير')) {{
    throw new Error('Canvas draw missing expected text for template ' + tpl.id);
  }}
}}

// Real HTTP roundtrip against live Uvicorn server
const baseUrl = {json.dumps(f"http://127.0.0.1:{port}")};
const hdrs = {{ 'X-Window-Secret': {json.dumps(secret)}, 'Content-Type': 'application/json' }};
const stRes = await fetch(baseUrl + '/api/sync', {{ headers: hdrs }});
if (!stRes.ok) throw new Error('GET /api/sync failed: ' + stRes.status);
const st = await stRes.json();
const sid = st.students[0].id;

const postRes = await fetch(baseUrl + '/api/certificates', {{
  method: 'POST',
  headers: hdrs,
  body: JSON.stringify({{
    studentId: sid,
    studentName: st.students[0].name,
    courseTitle: 'حفظ سورة الكهف',
    grade: 'ممتاز',
    issueDate: '2026-11-16',
    coords: {{ templateId: 'modern_teal_navy', heading: 'شهادة تكريم وإتقان' }},
  }}),
}});
if (!postRes.ok) throw new Error('POST /api/certificates failed: ' + postRes.status);
console.log('E2E_CERTIFICATES_OK');
""",
                        encoding="utf-8",
                    )
                    res = subprocess.run([node, str(e2e_script)], cwd=str(js_dir), capture_output=True, text=True, timeout=20)
                    self.assertEqual(res.returncode, 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
                    self.assertIn("E2E_CERTIFICATES_OK", res.stdout)
                finally:
                    server.should_exit = True
                    thread.join(timeout=5)
            finally:
                if hasattr(app.state, "store"):
                    app.state.store.close()
