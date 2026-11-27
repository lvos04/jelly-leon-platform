// Gebruik de Socket.IO-API uit de gepinde Kuma-image. Alleen booleans en het monitor-ID gaan naar de proef.
const fs = require("node:fs");
const { io } = require("socket.io-client");
const invoer = JSON.parse(fs.readFileSync(0, "utf8"));
const socket = io("http://localhost:3001", { autoConnect: false, reconnection: false, transports: ["websocket"] });

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

const verbindingsfout = /ENOTFOUND|ECONNREFUSED|EAI_AGAIN|EHOSTUNREACH|ETIMEDOUT/;

async function routeOnbereikbaar(url) {
    try {
        await fetch(url, { signal: AbortSignal.timeout(3000) });
        return false;
    } catch (fout) {
        return fout.name === "TimeoutError" || verbindingsfout.test(fout.cause?.code || "");
    }
}

async function proef() {
    // Kuma meldt de aanmeldplicht pas nadat alle API-aanroepen op de server zijn geregistreerd.
    await new Promise((klaar, fout) => socket.once("loginRequired", klaar).once("connect_error", fout).connect());
    if (invoer.inrichten) await vereist("setup", "proefbeheer", invoer.wachtwoord);
    await vereist("login", { username: "proefbeheer", password: invoer.wachtwoord });
    const direct = kanaal("Rechtstreeks", "http://gotify-sim:30215", invoer.direct);
    const via = kanaal("Via de proef-Hub", "http://hub:31822", invoer.via);
    let b;
    try {
        b = await vraag("testNotification", via);
    } catch (fout) {
        if (fout.message !== "operation has timed out") throw fout;
    }
    // Een succesvolle verzending over dezelfde API-verbinding onderscheidt de meldroute van een stilgevallen Kuma.
    const a = await vraag("testNotification", direct);
    const uit = { direct: a.ok === true, via_hub: b?.ok === true, omweg_zonder_antwoord: b === undefined,
        hub_onbereikbaar: b ? !b.ok && verbindingsfout.test(b.msg || "") : await routeOnbereikbaar(via.gotifyserverurl) };
    if (invoer.inrichten) {
        const opgeslagen = invoer.omweg ? { ...direct, gotifyserverurl: via.gotifyserverurl } : direct;
        const eerste = await vereist("addNotification", opgeslagen, null);
        const tweede = await vereist("addNotification", via, null);
        const monitor = await vereist("add", {
            type: "http", name: "Proefdienst", url: "http://lifecycle:30440/healthz", method: "GET",
            interval: 20, retryInterval: 20, timeout: 5, maxretries: 0, accepted_statuscodes: ["200"], conditions: [],
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
