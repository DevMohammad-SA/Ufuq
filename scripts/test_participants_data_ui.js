/* Test-only Playwright: NODE_PATH=/tmp/rahhal-mobile-ui-qa/node_modules node scripts/test_participants_data_ui.js */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const python = String.raw`
import os, re, json
from pathlib import Path
from types import SimpleNamespace as NS
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
os.environ['SECRET_KEY'] = 'synthetic-local-tests'
import django
django.setup()
from django.template import Template, Context
from participants.roster_ordering import order_roster_for_export, ROSTER_SORT_KEYS
records = [
 (1, '', 'بيئة ب', 'الثاني المتوسط', 900, 30, 2, 2, 1, True),
 (2, '\ufeffآدم', 'بيئة أ', 'الأول المتوسط', 100, 10, 20, 1, 3, False),
 (3, 'بدر', 'بيئة ب', 'الثالث المتوسط', 300, 10, 20, 3, 2, True),
 (4, 'سالم', 'بيئة أ', 'الأول المتوسط', 700, 5, 5, 1, 1, True),
]
rows = []
for pk, name, group, stage, miles, points, purchase, circle, meeting, active in records:
    rows.append(NS(pk=pk, user=NS(full_name=name, national_id=f'99000000{pk:02d}', is_active=active),
        group=NS(name=group), get_academic_stage_display=lambda stage=stage: stage,
        miles=miles, points=points, purchase_points=purchase,
        circle_days_attended=circle, meetings_attended=meeting, phone='', guardian_phone=''))
source = Path('participants/templates/participants/participants_data.html').read_text()
def block(name):
    return re.search(r'{% block '+name+r' %}(.*?){% endblock %}', source, re.S).group(1)
html = Template('{% load static %}<!doctype html><html dir="rtl"><meta charset="utf-8">'
    '<style>:root{--font-ui:Arial;--line:#ddd;--sand:#faf8f1;--gold:#c29b3b;--gold-ink:#826212;--asphalt:#25282b;--slate:#666;--s2:12px}body{margin:8px}'
    +block('extra_style')+'</style>'+block('content')+block('scripts')+'</html>').render(Context({
    'participants': rows, 'group_names': ['بيئة أ','بيئة ب'], 'participant_status':'all',
    'can_prepare_reports':True, 'can_manage_accounts':True, 'is_scoped_to_group':False,
}))
expected = {key: {direction: [row.pk for row in order_roster_for_export(rows, {'sort_by':key,'sort_dir':direction})]
    for direction in ('asc','desc')} for key in ROSTER_SORT_KEYS}
print(json.dumps({'html':html, 'expected':expected}, ensure_ascii=False))
`;
const fixture = JSON.parse(execFileSync(path.join(root, ".venv/bin/python"), ["-c", python], { cwd: root, encoding: "utf8" }));
let checks = 0;

(async function () {
  const browser = await chromium.launch({ executablePath: "/usr/bin/chromium", headless: true, args: ["--no-sandbox"] });
  try {
    for (const width of [360, 390, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(String(error)));
      let exported;
      await page.route("**/*", async route => {
        const url = new URL(route.request().url());
        if (url.pathname === "/participants/data/") {
          return route.fulfill({ contentType: "text/html", body: fixture.html });
        }
        if (url.pathname === "/static/js/participants-data.js") {
          return route.fulfill({ contentType: "application/javascript", body: fs.readFileSync(path.join(root, "static/js/participants-data.js"), "utf8") });
        }
        if (url.pathname === "/participants/data/export-pdf/") {
          exported = url;
          return route.fulfill({ contentType: "application/pdf", headers: { "Content-Disposition": 'attachment; filename="synthetic.pdf"' }, body: "%PDF-1.7 synthetic" });
        }
        return route.abort();
      });
      await page.goto("https://roster.test/participants/data/");
      async function ids() {
        return page.locator("#roster-body tr").evaluateAll(rows => rows.map(row => Number(row.querySelector("a.roster-student-link").getAttribute("href").match(/(\d+)\/?$/)[1])));
      }
      async function download() {
        await Promise.all([page.waitForEvent("download"), page.locator("#export-pdf-btn").click()]);
        assert.equal(exported.searchParams.get("status"), "all");
        assert.deepEqual(exported.searchParams.getAll("group"), ["بيئة أ", "بيئة ب"]);
      }
      await download();
      assert.equal(exported.searchParams.has("sort_by"), false);
      checks++;
      for (const key of Object.keys(fixture.expected)) {
        const header = page.locator('th[data-sort-key="' + key + '"]');
        for (const direction of ["asc", "desc"]) {
          await header.click();
          assert.deepEqual(await ids(), fixture.expected[key][direction], key + " " + direction + " at " + width);
          assert.equal(await header.getAttribute("aria-sort"), direction === "asc" ? "ascending" : "descending");
          await download();
          assert.equal(exported.searchParams.get("sort_by"), key);
          assert.equal(exported.searchParams.get("sort_dir"), direction);
          checks++;
        }
      }
      // Switching columns must not use the previous sorted order as a tie-breaker.
      await page.locator('th[data-sort-key="purchase_points"]').click();
      await page.locator('th[data-sort-key="points"]').click();
      assert.deepEqual(await ids(), [4, 2, 3, 1]);
      checks++;
      const name = page.locator('th[data-sort-key="full_name"]');
      await name.focus();
      await page.keyboard.press("Space");
      assert.deepEqual(await ids(), fixture.expected.full_name.asc);
      await page.keyboard.press("Enter");
      assert.deepEqual(await ids(), fixture.expected.full_name.desc);
      checks++;
      await page.locator("#roster-search").fill("بدر");
      const visible = await page.locator("#roster-body tr").evaluateAll(rows => rows.filter(row => !row.hidden).map(row => row.querySelector(".row-number").textContent));
      assert.deepEqual(visible, ["1"]);
      await download();
      assert.equal(exported.searchParams.has("q"), false);
      assert.equal(exported.searchParams.get("sort_by"), "full_name");
      assert.deepEqual(errors, []);
      checks++;
      await context.close();
    }
    console.log("Participants data: " + checks + " Chromium sort/export checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
