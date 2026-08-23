import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const destination = "https://unikorn.hkust-gz.edu.cn/";
const defaultNoticeUrl = new URL("./nginx/old-site-notice.html", import.meta.url);
const noticePath = process.env.LEGACY_NOTICE_HTML_PATH || fileURLToPath(defaultNoticeUrl);

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

const portText = argumentValue("--port") || process.env.PORT || "3000";
const port = Number.parseInt(portText, 10);
const host = process.env.HOST || "0.0.0.0";

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error(`Invalid notice server port: ${portText}`);
}

const notice = await readFile(noticePath);

const server = createServer((request, response) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, {
      Allow: "GET, HEAD",
      "Cache-Control": "no-store",
      "Content-Length": "0",
    });
    response.end();
    return;
  }

  response.writeHead(200, {
    "Cache-Control": "no-store, max-age=0",
    "Content-Length": String(notice.length),
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Content-Type": "text/html; charset=utf-8",
    "Referrer-Policy": "no-referrer",
    Refresh: `10; url=${destination}`,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  });

  response.end(request.method === "HEAD" ? undefined : notice);
});

server.listen(port, host, () => {
  console.log(`Legacy migration notice listening on ${host}:${port}`);
});
