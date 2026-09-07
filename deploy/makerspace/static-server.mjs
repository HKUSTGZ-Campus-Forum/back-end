// Runs inside the sandbox, serving a bounded static tree with SPA fallback.
import { createServer } from 'node:http'
import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { resolve, extname, sep } from 'node:path'
const root = await realpath(resolve(process.argv[2] || 'dist'))
const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }
createServer(async (request, response) => {
  try {
    if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405).end(); return }
    const pathname = decodeURIComponent(new URL(request.url, 'http://app').pathname)
    if (pathname.split('/').some(segment => segment.startsWith('.'))) { response.writeHead(404).end(); return }
    let path = resolve(root, '.' + pathname)
    if (!path.startsWith(root + sep) && path !== root) { response.writeHead(404).end(); return }
    try { if ((await stat(path)).isDirectory()) path = resolve(path, 'index.html'); path = await realpath(path) }
    catch { if (extname(pathname)) throw new Error('missing asset'); path = await realpath(resolve(root, 'index.html')) }
    if (!path.startsWith(root + sep)) throw new Error('outside root')
    const info = await stat(path)
    if (!info.isFile() || info.size > 8 * 1024 * 1024) throw new Error('invalid file')
    response.writeHead(200, { 'Content-Type': types[extname(path)] || 'application/octet-stream', 'Content-Length': info.size, 'Cache-Control': 'no-store' })
    if (request.method === 'HEAD') response.end(); else createReadStream(path).pipe(response)
  } catch { response.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found') }
}).listen(8080, '0.0.0.0')
