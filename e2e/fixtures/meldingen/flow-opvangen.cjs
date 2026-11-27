// De testontvanger deelt loopback met Node-RED. Alleen het verzonden voorstel verlaat dit proces.
const http = require("node:http");

async function ontvang(inject) {
    const { promise, resolve, reject } = Promise.withResolvers();
    const server = http.createServer(async (req, res) => {
        if (req.method !== "POST" || req.url !== "/aanvragen") {
            res.writeHead(404).end();
            return;
        }
        try {
            const delen = [];
            for await (const deel of req) delen.push(deel);
            const voorstel = JSON.parse(Buffer.concat(delen));
            res.writeHead(202, { Connection: "close" }).end();
            resolve(voorstel);
        } catch {
            res.writeHead(400).end();
            reject(new Error("Ongeldig flowvoorstel"));
        }
    });
    await new Promise((klaar, fout) => server.once("error", fout).listen(31819, "localhost", klaar));
    const klok = setTimeout(() => reject(new Error("Geen flowvoorstel ontvangen")), 8000);
    try {
        const antwoord = await fetch("http://localhost:1880/inject/" + inject, { method: "POST" });
        if (antwoord.status !== 200) throw new Error("Inject geweigerd");
        return await promise;
    } finally {
        clearTimeout(klok);
        server.close();
    }
}

ontvang(process.argv[1]).then(r => console.log(JSON.stringify(r))).catch(() => { process.exitCode = 1; });
