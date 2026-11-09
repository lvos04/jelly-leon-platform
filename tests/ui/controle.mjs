// Browsercontrole van de Peil-statuspagina van jelly-leon-platform (ONTWERPTAAL §12 en §13.2).
//
//   node controle.mjs --map <momentopnamen> [--shots <map>] [--rapport <bestand.json>]
//
// <momentopnamen> komt uit apps/api/tests/integration/test_statuspage_peil.py met STATUSPAGINA_SNAPSHOTS=<map>:
// per toestand de HTML zoals de dienst hem serveert plus de antwoordkoppen. Een kleine lokale server levert ze met
// precies die koppen (dus ook de CSP); de browser laadt verder niets.
//
// Per toestand × breedte (320, 360, 390, 768, 1024, 1440) × thema (licht, donker):
//   - axe-core (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa) = 0 overtredingen
//   - geen horizontale overloop (scrollWidth ≤ breedte; geen element buiten beeld)
//   - aanraakdoelen ≥ 44 px (knoppen, velden, losse links; links midden in een zin tellen niet, zoals in Peil)
//   - geen tekst < 14 px, één h1
//   - CSP: geen enkele overtreding en geen enkel extra verzoek naast de pagina zelf
// Op 390 en 1440 (licht en donker) ook een Tab-doorloop: elk element krijgt een zichtbare focusring ≥ 3 px.
// Met --shots: volledige schermafdrukken op 360/768/1440 × licht/donker van de vijf hoofdtoestanden.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { chromium } from 'playwright';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const BREEDTES = [320, 360, 390, 768, 1024, 1440];
const THEMAS = { licht: 'light', donker: 'dark' };
const TAB_BREEDTES = [390, 1440];
const SHOT_TOESTANDEN = ['niet-gekoppeld', 'koppelen-mislukt', 'gekoppeld', 'ingetrokken', 'niet-gereed'];
const SHOT_BREEDTES = [360, 768, 1440];

function args(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  if (!out.map) throw new Error('gebruik: node controle.mjs --map <momentopnamen> [--shots <map>] [--rapport <bestand>]');
  return out;
}

function serve(map) {
  const server = http.createServer((req, res) => {
    const naam = decodeURIComponent(req.url.split('?')[0].replace(/^\//, ''));
    const html = path.join(map, `${naam}.html`);
    if (!/^[a-z0-9-]+$/.test(naam) || !fs.existsSync(html)) {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('niet gevonden');
      return;
    }
    const headers = JSON.parse(fs.readFileSync(path.join(map, `${naam}.headers.json`), 'utf8'));
    res.writeHead(200, headers).end(fs.readFileSync(html));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

// Draait in de pagina (via DevTools, dus niet onder de CSP van de pagina).
function meet() {
  const vw = document.documentElement.clientWidth;
  const fouten = [];
  if (document.documentElement.scrollWidth > vw) fouten.push(`overloop: scrollWidth ${document.documentElement.scrollWidth} > ${vw}`);
  const zichtbaar = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (!zichtbaar(el) || el.closest('.sr, .spring, svg[style]')) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 0.5 || r.left < -0.5) fouten.push(`buiten beeld: <${el.tagName.toLowerCase()} class="${el.className.baseVal ?? el.className}"> ${Math.round(r.left)}–${Math.round(r.right)}`);
  }
  for (const el of document.querySelectorAll('a, button, input:not([type=hidden])')) {
    if (!zichtbaar(el) || el.classList.contains('spring')) continue;
    if (el.tagName === 'A' && getComputedStyle(el).display === 'inline') continue; // link midden in een zin
    const r = el.getBoundingClientRect();
    if (r.height < 44 - 0.5) fouten.push(`doel < 44 px: <${el.tagName.toLowerCase()}> "${(el.textContent || el.name || '').trim().slice(0, 40)}" ${r.height.toFixed(1)} px`);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!n.textContent.trim() || !el || !zichtbaar(el) || el.closest('.sr, .spring')) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 14 - 0.01) fouten.push(`tekst < 14 px (${px}): "${n.textContent.trim().slice(0, 40)}"`);
  }
  const h1 = document.querySelectorAll('h1').length;
  if (h1 !== 1) fouten.push(`aantal h1: ${h1}`);
  return fouten;
}

async function tabDoorloop(page) {
  const fouten = [];
  const gezien = new Set();
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press('Tab');
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const r = el.getClientRects()[0] || el.getBoundingClientRect(); // eerste regel: een link kan over regels lopen
      const midden = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1),
        Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
      return {
        sleutel: `${el.tagName}#${el.id}|${el.getAttribute('href') || el.getAttribute('name') || ''}|${el.className}|${el.textContent.trim().slice(0, 40)}`,
        stijl: cs.outlineStyle, breedte: parseFloat(cs.outlineWidth),
        bedekt: !(midden && (midden === el || el.contains(midden))),
      };
    });
    if (!info || gezien.has(info.sleutel)) break;
    gezien.add(info.sleutel);
    if (info.stijl === 'none' || info.breedte < 3) fouten.push(`focusring ontbreekt of < 3 px: ${info.sleutel}`);
    if (info.bedekt) fouten.push(`focus bedekt: ${info.sleutel}`);
  }
  if (gezien.size === 0) fouten.push('Tab bereikt geen enkel element');
  return { fouten, aantal: gezien.size };
}

async function main() {
  const opt = args(process.argv);
  const toestanden = fs.readdirSync(opt.map).filter((f) => f.endsWith('.html')).map((f) => f.slice(0, -5)).sort();
  for (const t of SHOT_TOESTANDEN) if (!toestanden.includes(t)) throw new Error(`momentopname ontbreekt: ${t}`);
  const server = await serve(opt.map);
  const basis = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const rapport = {
    chromium: browser.version(), playwright: require('playwright/package.json').version,
    axe_core: require('axe-core/package.json').version, axe_tags: AXE_TAGS, breedtes: BREEDTES,
    themas: Object.keys(THEMAS), toestanden: {}, fouten: 0,
  };
  if (opt.shots) fs.mkdirSync(opt.shots, { recursive: true });
  try {
    for (const toestand of toestanden) {
      const r = (rapport.toestanden[toestand] = { axe: 0, overloop_320: null, fouten: [], tab: {} });
      for (const [thema, schema] of Object.entries(THEMAS)) {
        const context = await browser.newContext({ colorScheme: schema, reducedMotion: 'reduce' });
        const page = await context.newPage();
        const verzoeken = [];
        const console_ = [];
        page.on('request', (req) => verzoeken.push(req.url()));
        page.on('console', (m) => { if (m.type() === 'error' || /Content Security Policy/i.test(m.text())) console_.push(m.text()); });
        page.on('pageerror', (e) => console_.push(String(e)));
        for (const breedte of BREEDTES) {
          await page.setViewportSize({ width: breedte, height: 900 });
          verzoeken.length = 0;
          await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
          const extra = verzoeken.filter((u) => u !== `${basis}/${toestand}` && !u.endsWith('/favicon.ico'));
          if (extra.length) r.fouten.push(`${thema} ${breedte}: extra verzoeken ${extra.join(', ')}`);
          for (const f of await page.evaluate(meet)) r.fouten.push(`${thema} ${breedte}: ${f}`);
          if (breedte === 320) r.overloop_320 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          await page.evaluate(AXE);
          const axe = await page.evaluate((tags) => window.axe.run(document, { runOnly: { type: 'tag', values: tags } }), AXE_TAGS);
          r.axe += axe.violations.length;
          for (const v of axe.violations) r.fouten.push(`${thema} ${breedte}: axe ${v.id} (${v.nodes.length}×): ${v.help}`);
          if (TAB_BREEDTES.includes(breedte)) {
            const tab = await tabDoorloop(page);
            r.tab[`${thema}-${breedte}`] = tab.aantal;
            for (const f of tab.fouten) r.fouten.push(`${thema} ${breedte}: ${f}`);
          }
          if (opt.shots && SHOT_TOESTANDEN.includes(toestand) && SHOT_BREEDTES.includes(breedte)) {
            await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
            await page.screenshot({ path: path.join(opt.shots, `${toestand}-${thema}-${breedte}.png`), fullPage: true });
          }
        }
        for (const m of console_) r.fouten.push(`${thema}: console/CSP: ${m}`);
        await context.close();
      }
      rapport.fouten += r.fouten.length;
      const status = r.fouten.length ? 'FOUT' : 'ok';
      console.log(`${status.padEnd(4)} ${toestand.padEnd(26)} axe ${r.axe} · overloop@320 ${r.overloop_320} px · Tab ${JSON.stringify(r.tab)}`);
      for (const f of r.fouten) console.log(`     - ${f}`);
    }
  } finally {
    await browser.close();
    server.close();
  }
  if (opt.rapport) fs.writeFileSync(opt.rapport, `${JSON.stringify(rapport, null, 1)}\n`);
  console.log(rapport.fouten === 0
    ? `Geslaagd: ${toestanden.length} toestanden × ${BREEDTES.length} breedtes × 2 thema's, axe 0 overtredingen, geen overloop.`
    : `MISLUKT: ${rapport.fouten} bevinding(en).`);
  process.exit(rapport.fouten === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(2); });
