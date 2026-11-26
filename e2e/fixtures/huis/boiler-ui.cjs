// Dit browserproces en de huisapp staan in hetzelfde interne Compose-project.
const { chromium } = require("playwright");
const fs = require("node:fs");

async function run() {
  const { cookies, apparaat } = JSON.parse(fs.readFileSync(0, "utf8"));
  const origin = "http://my-house:8080";
  const browser = await chromium.launch({ args: ["--disable-background-networking"] });
  try {
    const context = await browser.newContext({ serviceWorkers: "block" });
    await context.route("**/*", route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      return route.abort();
    });
    await context.addCookies(Object.entries(cookies).map(([name, value]) => ({ name, value, url: origin })));
    const page = await context.newPage();
    await page.goto(origin + "/apparaten");
    const kaart = page.getByTestId("device-" + apparaat);
    await kaart.getByText(/schakelen geblokkeerd/i).waitFor();
    const schakelaars = kaart.locator('[role="switch"], input[type="checkbox"]');
    const knoppen = kaart.getByRole("button", { name: /schakel|relais|^(aan|uit|zet aan|zet uit)$/i });
    const aantal = await schakelaars.count() + await knoppen.count();
    process.stdout.write(JSON.stringify({ beleidsslot_zichtbaar: true, schakelbedieningen: aantal }));
  } finally {
    await browser.close();
  }
}
run().catch(() => { process.exitCode = 1; });
