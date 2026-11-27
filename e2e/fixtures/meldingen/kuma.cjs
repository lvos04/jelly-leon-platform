// Gebruik de Socket.IO-API uit de gepinde Kuma-image. Alleen booleans en het monitor-ID gaan naar de proef.
const fs = require("node:fs");
const { io } = require("socket.io-client");
const invoer = JSON.parse(fs.readFileSync(0, "utf8"));
const socket = io("http://localhost:3001", { reconnection: false, transports: ["websocket"] });

function vraag(naam, ...args) {
    return socket.timeout(15000).emitWithAck(naam, ...args);
}

async function vereist(naam, ...args) {
    const antwoord = await vraag(naam, ...args);
    if (!antwoord.ok) throw new Error(naam);
    return antwoord;
}

function kanaal(naam, url, token) {
    return { type: "gotify", name: naam, gotifyserverurl: url, gotifyapplicationToken: token, isDefault: false };
}

async function proef() {
    await new Promise((klaar, fout) => socket.once("connect", klaar).once("connect_error", fout));
    if (invoer.inrichten) await vereist("setup", "proefbeheer", invoer.wachtwoord);
    await vereist("login", { username: "proefbeheer", password: invoer.wachtwoord });
    const direct = kanaal("Rechtstreeks", "http://gotify-sim:30215", invoer.direct);
    const via = kanaal("Via de proef-Hub", "http://hub:31822", invoer.via);
    const a = await vraag("testNotification", direct);
    const b = await vraag("testNotification", via);
    const uit = { direct: a.ok === true, via_hub: b.ok === true,
        hub_onbereikbaar: !b.ok && /ENOTFOUND|ECONNREFUSED|EAI_AGAIN|EHOSTUNREACH|ETIMEDOUT/.test(b.msg || "") };
    if (invoer.inrichten) {
        const eerste = await vereist("addNotification", direct, null);
        const tweede = await vereist("addNotification", via, null);
        const monitor = await vereist("add", {
            type: "http", name: "Proefdienst", url: "http://lifecycle:30440/healthz", method: "GET",
            interval: 20, retryInterval: 20, timeout: 5, maxretries: 0, accepted_statuscodes: ["200"],
            notificationIDList: { [eerste.id]: true, [tweede.id]: true }
        });
        uit.monitor = monitor.monitorID;
    }
    return uit;
}

const klok = setTimeout(() => process.exit(2), 55000);
proef().then(r => console.log(JSON.stringify(r))).catch(e => {
    // Een leveranciersfout kan de kanaalconfiguratie citeren. Alleen de naam van de mislukte API-stap is bewijs.
    const namen = ["setup", "login", "addNotification", "add"];
    console.error(namen.includes(e.message) ? e.message : "Kuma-aanroep mislukt");
    process.exitCode = 1;
}).finally(() => { clearTimeout(klok); socket.close(); });
