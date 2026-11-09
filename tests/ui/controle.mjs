// Browsercontrole van de Peil-statuspagina van jelly-leon-platform (ONTWERPTAAL §4, §11, §12 en §13.2).
//
//   node controle.mjs --map <momentopnamen> [--shots <map>] [--shot-toestanden standaard|alle|a,b]
//                     [--shot-breedtes 320,360,390,768,1440] [--rapport <bestand.json>]
//
// <momentopnamen> komt uit apps/api/tests/integration/test_statuspage_peil.py met STATUSPAGINA_SNAPSHOTS=<map>, of uit
// live/live.mjs (de echte dienst als eigen processen, doorlopen in Chromium): per toestand de HTML zoals de dienst hem
// serveert plus de antwoordkoppen. Een kleine lokale server levert ze met precies die koppen (dus ook de CSP); de
// browser laadt verder niets.
//
// Per toestand × breedte (320, 360, 390, 768, 1024, 1440) × thema (licht, donker):
//   - axe-core (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa) = 0 overtredingen
//   - geen horizontale overloop (scrollWidth ≤ breedte; geen element buiten beeld)
//   - aanraakdoelen ≥ 44 px (knoppen, velden, losse links; links midden in een zin tellen niet, zoals in Peil)
//   - geen tekst < 14 px, één h1
//   - gemeten contrast in de gerenderde pagina: tekst ≥ 4,5:1; UI-grenzen ≥ 3:1 (veldrand, knop, betekenisdragende
//     pilrand, meldingsrand, iconen, trace-ID-kader)
//   - statusregels: elke pil heeft icoon + woord; icoon en pilvorm horen bij elkaar; groen alleen bij Gezond; totaal
//     nooit Gezond als de dienst niet gereed is of een controle niet gezond is; één woord = één uiterlijk per pagina
//   - microcopy: geen woorden die de Peil-woordenlijst vervangt (§11.1), geen uitroeptekens
//   - CSP: geen enkele overtreding en geen enkel extra verzoek naast de pagina zelf
// Op 390 en 1440 (licht en donker) ook een Tab-doorloop: elk element krijgt een zichtbare focusring ≥ 3 px met
// ≥ 3:1 contrast tegen de achtergrond, en de focus wordt niet bedekt.
// Op 320 px per thema ook: tekstafstand volgens WCAG 1.4.12 (geen overloop, niets afgekapt) en forced colors
// (stippelrand van Onbekend blijft, focusring blijft).
// Met --shots: volledige schermafdrukken (standaard de vijf hoofdtoestanden op 320/360/390/768/1440 × licht/donker).
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
const HOOFDTOESTANDEN = ['niet-gekoppeld', 'koppelen-mislukt', 'gekoppeld', 'ingetrokken', 'niet-gereed'];
const SHOT_BREEDTES = [320, 360, 390, 768, 1440];
// Peil §11.1 "Niet meer" (en §11 toon): deze woorden komen op een statuspagina niet voor.
const VERBODEN = [/\bin orde\b/i, /\bActueel\b/, /\bVerminderd\b/, /\bVerstoord\b/, /\bLet op\b/, /\bOffline\b/,
  /\bProbleem\b/, /\bniet gezond\b/i, /Laden…/, /\bKlaar\b/, /\bGelukt\b/, /\bOK\b/, /\bDoorgaan\b/,
  /Weet je het zeker/i, /\bsnooze/i, /\bsafe mode\b/i, /\bblast radius\b/i, /\brunbook\b/i, /\bhealthcheck\b/i,
  /\ballowlist\b/i, /!/];

function args(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  if (!out.map) throw new Error('gebruik: node controle.mjs --map <momentopnamen> [--shots <map>] [--shot-toestanden standaard|alle|a,b] [--shot-breedtes 320,360] [--rapport <bestand>]');
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

// Draait in de pagina (via DevTools, dus niet onder de CSP van de pagina). Geeft fouten en gemeten minima terug.
function meet(verboden) {
  const vw = document.documentElement.clientWidth;
  const fouten = [];
  const minima = { tekst: 99, ui: 99 };
  if (document.documentElement.scrollWidth > vw) fouten.push(`overloop: scrollWidth ${document.documentElement.scrollWidth} > ${vw}`);
  const zichtbaar = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const verborgen = (el) => el.closest('.sr, .spring, svg[style]');
  // --- kleur: samengestelde achtergrond (alle lagen, wit canvas onderaan) en WCAG-contrast
  const cvs = document.createElement('canvas'); cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d', { willReadFrequently: true });
  const rgba = (s) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); return [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / (a || 1)).concat([a]); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const achtergrond = (el) => { const lagen = []; for (let e = el; e && e.nodeType === 1; e = e.parentElement) lagen.push(rgba(getComputedStyle(e).backgroundColor)); let c = [255, 255, 255, 1]; for (const l of lagen.reverse()) c = over(l, c); return c; };
  const naam = (el) => `<${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? ` class="${el.className}"` : ''}> "${(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 30)}"`;
  const ui = (omschrijving, voorgrond, tegen, eis = 3) => {
    const r = ratio(over(voorgrond, tegen), tegen);
    minima.ui = Math.min(minima.ui, r);
    if (r < eis - 0.005) fouten.push(`UI-contrast ${r.toFixed(2)}:1 < ${eis}:1: ${omschrijving}`);
  };

  for (const el of document.querySelectorAll('body *')) {
    if (!zichtbaar(el) || verborgen(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 0.5 || r.left < -0.5) fouten.push(`buiten beeld: ${naam(el)} ${Math.round(r.left)}–${Math.round(r.right)}`);
  }
  for (const el of document.querySelectorAll('a, button, input:not([type=hidden])')) {
    if (!zichtbaar(el) || el.classList.contains('spring')) continue;
    if (el.tagName === 'A' && getComputedStyle(el).display === 'inline') continue; // link midden in een zin
    const r = el.getBoundingClientRect();
    if (r.height < 44 - 0.5) fouten.push(`doel < 44 px: <${el.tagName.toLowerCase()}> "${(el.textContent || el.name || '').trim().slice(0, 40)}" ${r.height.toFixed(1)} px`);
  }
  // --- tekst: grootte en contrast
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!n.textContent.trim() || !el || !zichtbaar(el) || verborgen(el)) continue;
    const cs = getComputedStyle(el);
    const px = parseFloat(cs.fontSize);
    if (px < 14 - 0.01) fouten.push(`tekst < 14 px (${px}): "${n.textContent.trim().slice(0, 40)}"`);
    const bg = achtergrond(el);
    const c = ratio(over(rgba(cs.color), bg), bg);
    minima.tekst = Math.min(minima.tekst, c);
    if (c < 4.5 - 0.005) fouten.push(`tekstcontrast ${c.toFixed(2)}:1 < 4,5:1: "${n.textContent.trim().slice(0, 40)}"`);
  }
  // --- UI-grenzen en iconen (WCAG 1.4.11)
  for (const el of document.querySelectorAll('.veld input')) {
    if (!zichtbaar(el)) continue;
    const rand = rgba(getComputedStyle(el).borderTopColor);
    ui(`veldrand ${el.id} t.o.v. het vlak eromheen`, rand, achtergrond(el.parentElement));
    ui(`veldrand ${el.id} t.o.v. het veld`, rand, achtergrond(el));
  }
  for (const el of document.querySelectorAll('.knop')) {
    if (!zichtbaar(el)) continue;
    const cs = getComputedStyle(el);
    const buiten = achtergrond(el.parentElement);
    const vlak = rgba(cs.backgroundColor);
    // de grens van de knop is het vlak (primair) of de rand (secundair): één van beide moet ≥ 3:1 halen
    const r = Math.max(ratio(over(vlak, buiten), buiten), ratio(over(rgba(cs.borderTopColor), buiten), buiten));
    if (r < 3 - 0.005) fouten.push(`UI-contrast ${r.toFixed(2)}:1 < 3:1: knopgrens ${naam(el)}`);
    minima.ui = Math.min(minima.ui, r);
  }
  for (const el of document.querySelectorAll('.pil, .melding, .selecteer, .veldfout')) {
    if (!zichtbaar(el)) continue;
    const cs = getComputedStyle(el);
    const eigen = achtergrond(el);
    if (cs.borderTopStyle !== 'none' && parseFloat(cs.borderTopWidth) > 0 && rgba(cs.borderTopColor)[3] > 0) {
      ui(`rand ${naam(el)} t.o.v. het vlak eromheen`, rgba(cs.borderTopColor), achtergrond(el.parentElement));
    }
    for (const svg of el.querySelectorAll(':scope > svg')) ui(`icoon in ${naam(el)}`, rgba(getComputedStyle(svg).color), eigen);
  }
  // --- statusregels (ONTWERPTAAL §4.1–§4.3)
  const ICOON = { gezond: ['gezond'], beperkt: ['beperkt'], storing: ['storing'], onbekend: ['onbekend', 'niet-gekoppeld'],
    gate: ['gate'], neutraal: ['gekoppeld'] };
  const root = getComputedStyle(document.documentElement);
  const groen = ['--healthy', '--healthy-ink', '--healthy-bg'].map((v) => rgba(root.getPropertyValue(v).trim()).slice(0, 3).join(','));
  const uiterlijk = new Map();
  for (const pil of document.querySelectorAll('.pil')) {
    const soort = [...pil.classList].find((c) => c.startsWith('pil-'))?.slice(4);
    const use = pil.querySelector('svg use');
    const icoon = use ? (use.getAttribute('href') || '').replace('#i-', '') : null;
    const woord = pil.textContent.trim();
    if (!use || !woord) fouten.push(`status alleen kleur: pil zonder icoon of woord (${soort})`);
    if (soort && icoon && !(ICOON[soort] || []).includes(icoon)) fouten.push(`icoon ${icoon} hoort niet bij pil-${soort} ("${woord}")`);
    const vorm = `${soort}/${icoon}`;
    if (!uiterlijk.has(woord)) uiterlijk.set(woord, new Set());
    uiterlijk.get(woord).add(vorm);
  }
  for (const [woord, vormen] of uiterlijk) if (vormen.size > 1) fouten.push(`één woord, verschillend uiterlijk: "${woord}" als ${[...vormen].join(' en ')}`);
  for (const el of document.querySelectorAll('body *')) {
    if (!zichtbaar(el) || el.closest('.pil-gezond')) continue;
    const cs = getComputedStyle(el);
    for (const [eig, waarde] of [['tekst', cs.color], ['vlak', cs.backgroundColor], ['rand', cs.borderTopStyle !== 'none' ? cs.borderTopColor : 'transparent']]) {
      const c = rgba(waarde);
      if (c[3] > 0 && groen.includes(c.slice(0, 3).join(','))) fouten.push(`groen buiten Gezond (${eig}): ${naam(el)}`);
    }
  }
  const totaal = document.querySelector('.totaal .pil');
  const gereed = [...document.querySelectorAll('.feiten dt')].find((dt) => dt.textContent.trim() === 'Gereed')?.nextElementSibling?.textContent.trim() || '';
  if (!totaal) fouten.push('geen totaalstatus');
  else {
    if (totaal.classList.contains('pil-gezond') && !gereed.startsWith('Ja')) fouten.push(`totaal groen terwijl Gereed = "${gereed}"`);
    const nietGezond = [...document.querySelectorAll('#h-status ~ .checks .pil')].filter((p) => !p.classList.contains('pil-gezond'));
    if (totaal.classList.contains('pil-gezond') && nietGezond.length) fouten.push('totaal groen terwijl een controle niet gezond is');
  }
  // --- microcopy
  const tekst = document.body.innerText;
  for (const re of verboden.map((s) => new RegExp(s.bron, s.vlag))) {
    const m = tekst.match(re);
    if (m) fouten.push(`microcopy: "${m[0]}" (Peil-woordenlijst §11.1)`);
  }
  // skiplink: zonder focus helemaal buiten beeld (ook als de tekst over twee regels loopt)
  for (const el of document.querySelectorAll('.spring')) {
    if (el !== document.activeElement && el.getBoundingClientRect().bottom > 0.5) fouten.push(`skiplink zonder focus deels in beeld: "${el.textContent.trim()}"`);
  }
  const h1 = document.querySelectorAll('h1').length;
  if (h1 !== 1) fouten.push(`aantal h1: ${h1}`);
  return { fouten, minima };
}

async function tabDoorloop(page) {
  const fouten = [];
  const gezien = new Set();
  let ringMin = 99;
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press('Tab');
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const r = el.getClientRects()[0] || el.getBoundingClientRect(); // eerste regel: een link kan over regels lopen
      const midden = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1),
        Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
      const cvs = document.createElement('canvas'); cvs.width = cvs.height = 1;
      const ctx = cvs.getContext('2d', { willReadFrequently: true });
      const rgba = (s) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
      const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); return [0, 1, 2].map((k) => (t[k] * t[3] + b[k] * b[3] * (1 - t[3])) / (a || 1)).concat([a]); };
      const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
      const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
      const lagen = []; for (let e = el.parentElement; e && e.nodeType === 1; e = e.parentElement) lagen.push(rgba(getComputedStyle(e).backgroundColor));
      let bg = [255, 255, 255, 1]; for (const l of lagen.reverse()) bg = over(l, bg);
      return {
        sleutel: `${el.tagName}#${el.id}|${el.getAttribute('href') || el.getAttribute('name') || ''}|${el.className}|${el.textContent.trim().slice(0, 40)}`,
        stijl: cs.outlineStyle, breedte: parseFloat(cs.outlineWidth), ring: ratio(over(rgba(cs.outlineColor), bg), bg),
        bedekt: !(midden && (midden === el || el.contains(midden))),
      };
    });
    if (!info || gezien.has(info.sleutel)) break;
    gezien.add(info.sleutel);
    if (info.stijl === 'none' || info.breedte < 3) fouten.push(`focusring ontbreekt of < 3 px: ${info.sleutel}`);
    else {
      ringMin = Math.min(ringMin, info.ring);
      if (info.ring < 3 - 0.005) fouten.push(`focusring ${info.ring.toFixed(2)}:1 < 3:1: ${info.sleutel}`);
    }
    if (info.bedekt) fouten.push(`focus bedekt: ${info.sleutel}`);
  }
  if (gezien.size === 0) fouten.push('Tab bereikt geen enkel element');
  return { fouten, aantal: gezien.size, ringMin };
}

// WCAG 1.4.12 tekstafstand: met deze waarden mag niets overlopen of afgekapt worden.
const TEKSTAFSTAND = '*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}p{margin-bottom:2em!important}';
function afgekapt() {
  const fouten = [];
  const vw = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > vw) fouten.push(`tekstafstand: overloop ${document.documentElement.scrollWidth} > ${vw}`);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || el.closest('.sr, .spring, svg[style]')) continue;
    const knipt = ['hidden', 'clip'].includes(cs.overflowX) || ['hidden', 'clip'].includes(cs.overflowY);
    if (knipt && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) fouten.push(`tekstafstand: afgekapt <${el.tagName.toLowerCase()} class="${el.className}">`);
  }
  return fouten;
}
function forcedColors() {
  const fouten = [];
  for (const pil of document.querySelectorAll('.pil-onbekend')) {
    const cs = getComputedStyle(pil);
    if (cs.borderTopStyle !== 'dashed' || parseFloat(cs.borderTopWidth) < 2) fouten.push(`forced colors: stippelrand van Onbekend weg ("${pil.textContent.trim()}")`);
  }
  for (const pil of document.querySelectorAll('.pil-beperkt, .pil-storing, .pil-neutraal, .pil-gate, .melding, .veld input')) {
    const cs = getComputedStyle(pil);
    if (cs.borderTopStyle === 'none' || parseFloat(cs.borderTopWidth) < 1) fouten.push(`forced colors: rand weg (${pil.className})`);
  }
  return fouten;
}

async function main() {
  const opt = args(process.argv);
  const toestanden = fs.readdirSync(opt.map).filter((f) => f.endsWith('.html')).map((f) => f.slice(0, -5)).sort();
  const keuze = opt['shot-toestanden'] || 'standaard';
  const shotToestanden = keuze === 'alle' ? toestanden : keuze === 'standaard' ? HOOFDTOESTANDEN : keuze.split(',');
  const shotBreedtes = opt['shot-breedtes'] ? opt['shot-breedtes'].split(',').map(Number) : SHOT_BREEDTES;
  if (opt.shots) for (const t of shotToestanden) if (!toestanden.includes(t)) throw new Error(`momentopname ontbreekt: ${t}`);
  const verboden = VERBODEN.map((re) => ({ bron: re.source, vlag: re.flags }));
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
      const r = (rapport.toestanden[toestand] = { axe: 0, overloop_320: null, min_tekstcontrast: {}, min_ui_contrast: {}, min_focusring: {}, fouten: [], tab: {} });
      for (const [thema, schema] of Object.entries(THEMAS)) {
        const context = await browser.newContext({ colorScheme: schema, reducedMotion: 'reduce' });
        const page = await context.newPage();
        const verzoeken = [];
        const console_ = [];
        page.on('request', (req) => verzoeken.push(req.url()));
        page.on('console', (m) => { if (m.type() === 'error' || /Content Security Policy/i.test(m.text())) console_.push(m.text()); });
        page.on('pageerror', (e) => console_.push(String(e)));
        let tekstMin = 99, uiMin = 99, ringMin = 99;
        for (const breedte of BREEDTES) {
          await page.setViewportSize({ width: breedte, height: 900 });
          verzoeken.length = 0;
          await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
          const extra = verzoeken.filter((u) => u !== `${basis}/${toestand}` && !u.endsWith('/favicon.ico'));
          if (extra.length) r.fouten.push(`${thema} ${breedte}: extra verzoeken ${extra.join(', ')}`);
          const m = await page.evaluate(meet, verboden);
          for (const f of m.fouten) r.fouten.push(`${thema} ${breedte}: ${f}`);
          tekstMin = Math.min(tekstMin, m.minima.tekst);
          uiMin = Math.min(uiMin, m.minima.ui);
          if (breedte === 320) r.overloop_320 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          await page.evaluate(AXE);
          const axe = await page.evaluate((tags) => window.axe.run(document, { runOnly: { type: 'tag', values: tags } }), AXE_TAGS);
          r.axe += axe.violations.length;
          for (const v of axe.violations) r.fouten.push(`${thema} ${breedte}: axe ${v.id} (${v.nodes.length}×): ${v.help}`);
          if (TAB_BREEDTES.includes(breedte)) {
            const tab = await tabDoorloop(page);
            r.tab[`${thema}-${breedte}`] = tab.aantal;
            ringMin = Math.min(ringMin, tab.ringMin);
            for (const f of tab.fouten) r.fouten.push(`${thema} ${breedte}: ${f}`);
          }
          if (breedte === 320) {
            await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
            await page.addStyleTag({ content: TEKSTAFSTAND });
            for (const f of await page.evaluate(afgekapt)) r.fouten.push(`${thema} ${breedte}: ${f}`);
            await page.emulateMedia({ forcedColors: 'active' });
            await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
            for (const f of await page.evaluate(forcedColors)) r.fouten.push(`${thema} ${breedte}: ${f}`);
            await page.emulateMedia({ forcedColors: 'none' });
          }
          if (opt.shots && shotToestanden.includes(toestand) && shotBreedtes.includes(breedte)) {
            await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
            await page.screenshot({ path: path.join(opt.shots, `${toestand}-${thema}-${breedte}.png`), fullPage: true });
          }
        }
        if (opt.shots && shotToestanden.includes(toestand)) {
          for (const breedte of shotBreedtes.filter((b) => !BREEDTES.includes(b))) {
            await page.setViewportSize({ width: breedte, height: 900 });
            await page.goto(`${basis}/${toestand}`, { waitUntil: 'load' });
            await page.screenshot({ path: path.join(opt.shots, `${toestand}-${thema}-${breedte}.png`), fullPage: true });
          }
        }
        r.min_tekstcontrast[thema] = +tekstMin.toFixed(2);
        r.min_ui_contrast[thema] = +uiMin.toFixed(2);
        r.min_focusring[thema] = ringMin === 99 ? null : +ringMin.toFixed(2);
        for (const m of console_) r.fouten.push(`${thema}: console/CSP: ${m}`);
        await context.close();
      }
      rapport.fouten += r.fouten.length;
      const status = r.fouten.length ? 'FOUT' : 'ok';
      console.log(`${status.padEnd(4)} ${toestand.padEnd(30)} axe ${r.axe} · overloop@320 ${r.overloop_320} px · tekst ≥ ${JSON.stringify(r.min_tekstcontrast)} · UI ≥ ${JSON.stringify(r.min_ui_contrast)} · ring ≥ ${JSON.stringify(r.min_focusring)} · Tab ${JSON.stringify(r.tab)}`);
      // samengevat: dezelfde bevinding op meerdere breedtes/thema's één keer, met waar ze optreedt
      const groep = new Map();
      for (const f of r.fouten) {
        const m = f.match(/^(licht|donker)(?: (\d+))?: (.*)$/);
        const [waar, tekst] = m ? [`${m[1]}${m[2] ? ` ${m[2]}` : ''}`, m[3]] : ['', f];
        if (!groep.has(tekst)) groep.set(tekst, []);
        groep.get(tekst).push(waar);
      }
      for (const [tekst, waar] of groep) console.log(`     - ${tekst} [${waar.length}×: ${waar.slice(0, 4).join(', ')}${waar.length > 4 ? ', …' : ''}]`);
    }
  } finally {
    await browser.close();
    server.close();
  }
  if (opt.rapport) fs.writeFileSync(opt.rapport, `${JSON.stringify(rapport, null, 1)}\n`);
  console.log(rapport.fouten === 0
    ? `Geslaagd: ${toestanden.length} toestanden × ${BREEDTES.length} breedtes × 2 thema's, axe 0 overtredingen, contrast tekst ≥ 4,5:1 en UI/focus ≥ 3:1, geen overloop.`
    : `MISLUKT: ${rapport.fouten} bevinding(en).`);
  process.exit(rapport.fouten === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(2); });
