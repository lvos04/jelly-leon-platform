// Live ontwerpreview van de statuspagina: de ECHTE dienst draait als eigen processen (`python -m lifecycle api` en
// `worker`; in plaats van de ingebouwde PostgreSQL een eigen database op de lokale/CI-PostgreSQL) naast de Hub-simulator.
// Een echte Chromium doorloopt elke toestand zoals een gebruiker dat doet: formulier invullen en versturen (met de
// echte CSRF-cookie en Origin-controle), redirect volgen, opnieuw laden. Van elke toestand bewaart dit script het
// onbewerkte antwoord van de dienst (HTML + koppen) in het formaat van tests/ui/controle.mjs, dat daarna de
// Peil-controles en schermafdrukken doet.
//
//   node live/live.mjs --map <uitvoermap> [--python <pad>] [--pg postgresql+psycopg://postgres@127.0.0.1:5432]
//
// Alleen voor ontwikkeling en CI: gebruikt LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB=1 (de Hub-simulator luistert op
// 127.0.0.1). Testdata: adressen en vingerafdrukken van de simulator, geen NAS-gegevens.
//
// Negatieve gevallen (contract 1.3 §3.7 punt 6), elk door Chromium zelf verstuurd, nooit met nagemaakte headers:
// een formulier van een andere app op hetzelfde adres (andere poort: Origin anders, Sec-Fetch-Site same-site), van een
// andere site (localhost: cross-site), van een pagina zonder oorsprong (Origin: null), via een niet-toegestane naam
// (Chromium met --host-resolver-rules: Origin = Host, zoals bij DNS-rebinding) en zonder cookie. Elk geval moet 403
// geven met de juiste code, en de Hub-simulator mag in die tijd geen enkel enroll-verzoek krijgen.
import { spawn, execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const HIER = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HIER, '..', '..', '..');
const KOPPEN = ['Content-Security-Policy', 'Content-Type', 'X-Frame-Options', 'X-Content-Type-Options',
  'Referrer-Policy', 'Cache-Control'];
const VREEMDE_NAAM = 'jlp.voorbeeld.test'; // geen IP-adres, geen naam zonder punt, geen lokale naam (§3.7 punt 4)

// Pagina van "een andere app" met een formulier naar de statuspagina (de aanvaller kent het formuliertoken niet).
const aanvalsPagina = (doel) => `<!doctype html><html lang="nl"><title>andere app</title><form method="post" action="${doel}/koppelen">
<input name="hub_adres" value="http://127.0.0.1:9"><input name="koppelcode" value="HJKM-NPQR-STVW">
<input name="hub_vingerafdruk" value="AAAA-BBBB-CCCC-DDDD"><input name="csrf" value="geraden-token-0000000000">
<button type="submit">versturen</button></form></html>`;

function args(argv) {
  const out = { python: path.join(REPO, '.venv', 'bin', 'python'), pg: 'postgresql+psycopg://postgres@127.0.0.1:5432' };
  for (let i = 2; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  if (!out.map) throw new Error('gebruik: node live/live.mjs --map <uitvoermap> [--python <pad>] [--pg <url zonder database>]');
  return out;
}

const vrijePoort = () => new Promise((resolve) => {
  const s = net.createServer();
  s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
});
const wacht = (ms) => new Promise((r) => setTimeout(r, ms));

async function wachtOp(url, test = (r) => r.ok, ms = 30000) {
  const einde = Date.now() + ms;
  for (;;) {
    try { const r = await fetch(url); if (await test(r)) return r; } catch { /* nog niet bereikbaar */ }
    if (Date.now() > einde) throw new Error(`niet bereikbaar binnen ${ms} ms: ${url}`);
    await wacht(250);
  }
}

class Processen {
  constructor() { this.lijst = []; }
  start(naam, cmd, argv, env = {}) {
    const p = spawn(cmd, argv, { cwd: REPO, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    p.uitvoer = '';
    p.stdout.on('data', (d) => { p.uitvoer += d; });
    p.stderr.on('data', (d) => { p.uitvoer += d; });
    p.naam = naam;
    this.lijst.push(p);
    return p;
  }
  async stop(p) {
    if (p.exitCode !== null) return;
    p.kill('SIGTERM');
    for (let i = 0; i < 40 && p.exitCode === null; i += 1) await wacht(100);
    if (p.exitCode === null) p.kill('SIGKILL');
  }
  async stopAlles() { for (const p of this.lijst) await this.stop(p); }
}

// Database aanmaken/opruimen via psycopg uit de venv van de dienst (geen psql nodig, geen keuze tussen clientversies).
const SQL_PY = 'import sys, psycopg\nwith psycopg.connect(sys.argv[1], autocommit=True) as c:\n    c.execute(sys.argv[2])\n';
function sql(python, pgUrl, opdracht) {
  execFileSync(python, ['-c', SQL_PY, `${pgUrl.replace('postgresql+psycopg', 'postgresql')}/postgres`, opdracht],
    { stdio: ['ignore', 'ignore', 'inherit'] });
}

async function main() {
  const opt = args(process.argv);
  fs.rmSync(opt.map, { recursive: true, force: true });
  fs.mkdirSync(opt.map, { recursive: true });
  const proc = new Processen();
  const werk = fs.mkdtempSync(path.join(os.tmpdir(), 'jlp-live-'));
  const databases = [];
  const verslag = { toestanden: {}, hub_vingerafdruk: null };
  // Een naam die naar 127.0.0.1 wijst maar geen lokale naam is: Origin = Host, zoals bij DNS-rebinding.
  const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP ${VREEMDE_NAAM} 127.0.0.1`] });

  // ---------------------------------------------------------------- Hub-simulator
  const hubPoort = await vrijePoort();
  const hubUrl = `http://127.0.0.1:${hubPoort}`;
  proc.start('hub', opt.python, [path.join(HIER, 'hub_review.py'), '--port', String(hubPoort)]);
  await wachtOp(`${hubUrl}/api/v1/auth/jwks`);
  const hubPost = async (pad, body = {}, headers = {}) => {
    const r = await fetch(`${hubUrl}${pad}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`Hub-simulator ${pad}: ${r.status}`);
    return r.json();
  };
  const koppelcode = async () => hubPost('/_sim/grants', { kind: 'koppelcode', app_id: 'jelly-leon-platform' });

  // ---------------------------------------------------------------- dienst starten
  let volgnummer = 0;
  async function dienst({ worker = true, database = true } = {}) {
    volgnummer += 1;
    const dataDir = path.join(werk, `dienst-${volgnummer}`);
    const db = `jlp_ontwerpreview_${process.pid}_${volgnummer}`;
    let dbUrl = `${opt.pg}/${db}`;
    if (database) { sql(opt.python, opt.pg, `CREATE DATABASE ${db}`); databases.push(db); } else {
      dbUrl = `postgresql+psycopg://postgres@127.0.0.1:${await vrijePoort()}/onbereikbaar`; // niemand luistert daar
    }
    const poort = await vrijePoort();
    const env = {
      LIFECYCLE_DATA_DIR: dataDir, LIFECYCLE_DATABASE_URL: dbUrl, LIFECYCLE_HOST: '127.0.0.1',
      LIFECYCLE_PORT: String(poort), LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB: '1', LIFECYCLE_WORKER_INTERVAL_SECONDS: '1',
    };
    execFileSync(opt.python, ['-m', 'lifecycle', 'init'], { cwd: REPO, env: { ...process.env, ...env }, stdio: 'ignore' });
    if (database) execFileSync(opt.python, ['-m', 'lifecycle', 'migrate'], { cwd: REPO, env: { ...process.env, ...env }, stdio: 'ignore' });
    const api = proc.start(`api-${volgnummer}`, opt.python, ['-m', 'lifecycle', 'api'], env);
    const basis = `http://127.0.0.1:${poort}`;
    await wachtOp(`${basis}/healthz`, (r) => r.status === 200 || r.status === 503);
    let wrk = null;
    if (worker) {
      wrk = proc.start(`worker-${volgnummer}`, opt.python, ['-m', 'lifecycle', 'worker'], env);
      // de hartslag van de worker staat alleen op de statuspagina (niet in /readyz): wachten tot hij gezond is
      await wachtOp(`${basis}/`, async (r) => /Achtergrondproces(<span class="sr">:<\/span>)?<\/span><span class="pil pil-gezond">/.test(await r.text()));
    }
    return { basis, dataDir, api, wrk };
  }

  // ---------------------------------------------------------------- toestand bewaren
  async function bewaar(naam, response, extra = {}) {
    const headers = response.headers();
    const koppen = {};
    for (const k of KOPPEN) if (headers[k.toLowerCase()] !== undefined) koppen[k] = headers[k.toLowerCase()];
    // schermafdrukken komen in de repo: de tijdelijke datamap wordt het pad in de image (/appdata)
    const html = (await response.body()).toString('utf8').replace(/\/[^\s<"]*\/jlp-live-[^/<]+\/dienst-\d+\//g, '/appdata/');
    fs.writeFileSync(path.join(opt.map, `${naam}.html`), html);
    fs.writeFileSync(path.join(opt.map, `${naam}.headers.json`), `${JSON.stringify(koppen, null, 1)}\n`);
    const via = response.request().redirectedFrom();
    const pad = (url) => url.replace(/^https?:\/\/[^/]+/, '');
    const eind = `${response.request().method()} ${pad(response.url())}`;
    verslag.toestanden[naam] = { http: response.status(), actie: via ? `${via.method()} ${pad(via.url())} → 303 → ${eind}` : eind, ...extra };
    console.log(`bewaard ${naam.padEnd(30)} HTTP ${response.status()}${via ? ` (na ${via.method()} → 303)` : ''}`);
  }

  async function verstuur(page, velden) {
    await page.fill('#hub', velden.hub);
    await page.fill('#koppelcode', velden.code);
    await page.fill('#vingerafdruk', velden.vinger);
    const [response] = await Promise.all([page.waitForNavigation({ waitUntil: 'load' }), page.click('button[type=submit]')]);
    return response;
  }

  try {
    // ============ dienst 1: nooit gekoppeld → invoer ongeldig → code geweigerd → vingerafdruk onjuist → gekoppeld → ontkoppeld
    const d1 = await dienst();
    const ctx1 = await browser.newContext();
    const p1 = await ctx1.newPage();
    await bewaar('niet-gekoppeld', await p1.goto(`${d1.basis}/`));

    await bewaar('koppelen-invoer-ongeldig', await verstuur(p1, { hub: 'ftp://nas', code: 'ABC', vinger: '1234' }));

    const { keyset_fingerprint: vinger } = await koppelcode();
    verslag.hub_vingerafdruk = vinger;
    await p1.goto(`${d1.basis}/`);
    await bewaar('koppelen-code-geweigerd', await verstuur(p1, { hub: hubUrl, code: 'HJKM-NPQR-STVW', vinger }));

    const code2 = (await koppelcode()).secret;
    await p1.goto(`${d1.basis}/`);
    await bewaar('koppelen-vingerafdruk-onjuist', await verstuur(p1, { hub: hubUrl, code: code2, vinger: 'AAAA-BBBB-CCCC-DDDD' }));

    await bewaar('laatste-poging-mislukt', await p1.goto(`${d1.basis}/`));

    const code3 = (await koppelcode()).secret;
    await bewaar('gekoppeld-bevestigd', await verstuur(p1, { hub: hubUrl, code: code3, vinger }));
    await bewaar('gekoppeld', await p1.goto(`${d1.basis}/`));

    const { token } = await hubPost('/_sim/jwt', { token_use: 'hub_service', aud: 'jelly-leon-platform' });
    const unlink = await fetch(`${d1.basis}/connector/v1/hub-link/unlink`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': randomUUID() },
    });
    if (unlink.status !== 200) throw new Error(`ontkoppelen via de connector-API: HTTP ${unlink.status}`);
    await bewaar('ontkoppeld', await p1.goto(`${d1.basis}/`));
    await ctx1.close();

    // ============ dienst 2: formulier verlopen (CSRF) → te veel pogingen → koppelbestand onleesbaar
    const d2 = await dienst();
    const ctx2 = await browser.newContext();
    const p2 = await ctx2.newPage();
    await p2.goto(`${d2.basis}/`);
    await ctx2.clearCookies(); // zoals een verlopen of gewiste cookie
    await bewaar('formulier-verlopen', await verstuur(p2, { hub: hubUrl, code: 'HJKM-NPQR-STVW', vinger }));
    let laatste = null;
    for (let i = 0; i < 6; i += 1) {
      await p2.goto(`${d2.basis}/`);
      laatste = await verstuur(p2, { hub: 'ftp://nas', code: 'ABC', vinger: '1234' });
    }
    if (laatste.status() !== 429) throw new Error(`zesde poging gaf HTTP ${laatste.status()}, verwacht 429`);
    await bewaar('te-veel-pogingen', laatste);
    fs.writeFileSync(path.join(d2.dataDir, 'secrets', 'hub-link.json'), '{"state": "gekoppeld", kapot');
    await bewaar('koppelbestand-onleesbaar', await p2.goto(`${d2.basis}/`));
    await ctx2.close();

    // ============ dienst 5: negatieve herkomstgevallen (§3.7 punt 6) — niets mag de Hub bereiken
    const d5 = await dienst();
    const enrolls = async () => (await (await fetch(`${hubUrl}/_sim/enroll-requests`)).json()).length;
    const voor = await enrolls();
    const helper = http.createServer((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Referrer-Policy': 'strict-origin-when-cross-origin' });
      res.end(aanvalsPagina(d5.basis));
    });
    await new Promise((r) => helper.listen(0, '127.0.0.1', r));
    const hulpPoort = helper.address().port;
    const ctx5 = await browser.newContext();
    const p5 = await ctx5.newPage();
    await p5.goto(`${d5.basis}/`); // deze browser heeft de echte cookie (SameSite=Strict: niet bij een andere site)
    verslag.negatief = {};
    const doel = (r) => r.url().endsWith('/koppelen') && r.request().method() === 'POST';
    async function verwacht(naam, response, code, bewaarAls = null) {
      const tekst = await response.text();
      const kop = await response.request().allHeaders();
      verslag.negatief[naam] = { http: response.status(), code, origin: kop.origin ?? null, sec_fetch_site: kop['sec-fetch-site'] ?? null, host: kop.host ?? null };
      if (response.status() !== 403 || !tekst.includes(`Code <code>${code}</code>`)) {
        throw new Error(`${naam}: verwacht 403 ${code}, kreeg HTTP ${response.status()}`);
      }
      if (bewaarAls) await bewaar(bewaarAls, response);
      console.log(`negatief ${naam.padEnd(26)} HTTP ${response.status()} ${code} (Origin ${kop.origin ?? '-'}, Sec-Fetch-Site ${kop['sec-fetch-site'] ?? '-'})`);
    }
    // andere app op hetzelfde NAS-adres (andere poort): same-site, Origin ≠ Host
    await p5.goto(`http://127.0.0.1:${hulpPoort}/`);
    await verwacht('andere-app-zelfde-adres', (await Promise.all([p5.waitForResponse(doel), p5.click('button')]))[0], 'herkomst_ongeldig', 'herkomst-geweigerd');
    // andere site (localhost ≠ 127.0.0.1): cross-site
    await p5.goto(`http://localhost:${hulpPoort}/`);
    await verwacht('andere-site', (await Promise.all([p5.waitForResponse(doel), p5.click('button')]))[0], 'herkomst_ongeldig');
    // pagina zonder oorsprong (about:blank): Origin null
    await p5.goto('about:blank');
    await p5.setContent(aanvalsPagina(d5.basis));
    await verwacht('origin-null', (await Promise.all([p5.waitForResponse(doel), p5.click('button')]))[0], 'herkomst_ongeldig');
    // niet-toegestane naam (DNS-rebinding): de echte pagina en het echte formulier, Origin = Host
    const vreemd = `http://${VREEMDE_NAAM}:${new URL(d5.basis).port}`;
    await bewaar('adres-niet-toegestaan-live', await p5.goto(`${vreemd}/`));
    const { keyset_fingerprint: vinger5, secret: code5 } = await koppelcode();
    await verwacht('host-niet-toegestaan', await verstuur(p5, { hub: hubUrl, code: code5, vinger: vinger5 }), 'host_niet_toegestaan', 'host-niet-toegestaan');
    // geen cookie (verlopen of gewist): het echte formulier, zonder cookie
    await p5.goto(`${d5.basis}/`);
    await ctx5.clearCookies();
    await verwacht('geen-cookie', await verstuur(p5, { hub: hubUrl, code: code5, vinger: vinger5 }), 'csrf_ongeldig');
    if ((await enrolls()) !== voor) throw new Error('een geweigerd verzoek bereikte de Hub (enroll)');
    verslag.negatief_hub_enroll_verzoeken = 0;
    // daarna werkt koppelen via het IP-adres gewoon (dezelfde code: de geweigerde verzoeken verbruikten niets)
    await p5.goto(`${d5.basis}/`);
    const ok = await verstuur(p5, { hub: hubUrl, code: code5, vinger: vinger5 });
    if (!/Gekoppeld met de Hub/.test(await ok.text())) throw new Error('koppelen na de negatieve gevallen mislukte');
    helper.close();
    await ctx5.close();

    // ============ dienst 3: de Hub trekt de koppeling direct na het koppelen in
    await hubPost('/_review/intrekken-na-koppelen');
    const d3 = await dienst();
    const ctx3 = await browser.newContext();
    const p3 = await ctx3.newPage();
    await p3.goto(`${d3.basis}/`);
    const code4 = (await koppelcode()).secret;
    await bewaar('koppelen-ingetrokken', await verstuur(p3, { hub: hubUrl, code: code4, vinger }));
    await bewaar('ingetrokken', await p3.goto(`${d3.basis}/`));
    await ctx3.close();

    // ============ dienst 4: eigen database onbereikbaar (geen worker)
    const d4 = await dienst({ worker: false, database: false });
    const ctx4 = await browser.newContext();
    const p4 = await ctx4.newPage();
    await bewaar('niet-gereed', await p4.goto(`${d4.basis}/`));
    await ctx4.close();
  } catch (e) {
    for (const p of proc.lijst) if (p.uitvoer) console.error(`--- ${p.naam}\n${p.uitvoer.slice(-3000)}`);
    throw e;
  } finally {
    await browser.close();
    await proc.stopAlles();
    for (const db of databases) { try { sql(opt.python, opt.pg, `DROP DATABASE IF EXISTS ${db} WITH (FORCE)`); } catch { /* opruimen */ } }
    fs.rmSync(werk, { recursive: true, force: true });
    fs.writeFileSync(path.join(opt.map, 'live.json'), `${JSON.stringify(verslag, null, 1)}\n`);
  }
  console.log(`Klaar: ${Object.keys(verslag.toestanden).length} toestanden van de live dienst in ${opt.map}.`);
}

main().catch((e) => { console.error(e); process.exit(1); });
