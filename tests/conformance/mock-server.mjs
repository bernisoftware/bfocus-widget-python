#!/usr/bin/env node
// Servidor SIMULADO para os testes de integração dos pacotes nativos. Sem dependências.
//
//   node widgets-native/conformance/mock-server.mjs [porta]      (padrão 8787; 0 = livre)
//
// Serve:
//   GET  /v1/embed.html, /v1/release-notes.html   → mock-embed.html (Host Protocol v1)
//   GET  /files/manual.pdf                         → PDF mínimo (teste de download)
//   POST /api/v1/widget/launcher-state             → fixture de scenarios.json
//        (escolha: ?scenario=, header X-Mock-Scenario ou POST /__scenario {name})
//   POST /api/v1/widget/push/devices[/unregister]  → 200 {data:{ok:true}}
//   GET  /__script?name=tickets                    → mockEmbedScript de scenarios.json
//   POST /__sent, /__received                      → o embed simulado registra mensagens
//   GET  /__log                                    → {requests, sent, received}
//   POST /__reset                                  → zera o log (e volta ao cenário default)
//
// Imprime "mock-server ouvindo em http://127.0.0.1:<porta>" ao subir (os testes esperam
// essa linha para descobrir a porta quando ela é 0).
import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const scenarios = JSON.parse(readFileSync(join(here, 'scenarios.json'), 'utf8'))
const embedHtml = readFileSync(join(here, 'mock-embed.html'))
const PDF = Buffer.from('%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n')

let current = 'default'
let log = { requests: [], sent: [], received: [] }

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers':
    'X-bFocus-Widget-Key, X-bFocus-Widget-User, X-bFocus-Parent-Origin, X-bFocus-Client, X-Mock-Scenario, Content-Type',
}

function send(res, status, body, type = 'application/json') {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': type, ...CORS })
  res.end(data)
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
  })
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const body = await readBody(req)
  if (req.method === 'OPTIONS') return send(res, 204, '')

  if (url.pathname.startsWith('/api/')) {
    log.requests.push({
      method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams),
      headers: req.headers, body,
    })
  }

  if (url.pathname === '/v1/embed.html' || url.pathname === '/v1/release-notes.html') {
    return send(res, 200, embedHtml, 'text/html; charset=utf-8')
  }
  if (url.pathname === '/files/manual.pdf') return send(res, 200, PDF, 'application/pdf')

  if (url.pathname === '/api/v1/widget/launcher-state' && req.method === 'POST') {
    const name = url.searchParams.get('scenario') || req.headers['x-mock-scenario'] || current
    if (name === 'identity_error') {
      return send(res, 401, { code: 'ERROR', data: null, message: 'WIDGET_USER_HASH_INVALID' })
    }
    const fixture = scenarios.launcherStateFixtures[name]
    if (!fixture) return send(res, 404, { code: 'ERROR', data: null, message: `cenário desconhecido: ${name}` })
    return send(res, 200, { code: 'OK', data: fixture, message: '' })
  }
  if (url.pathname.startsWith('/api/v1/widget/push/devices') && req.method === 'POST') {
    return send(res, 200, { code: 'OK', data: { ok: true }, message: '' })
  }

  if (url.pathname === '/__script') {
    return send(res, 200, scenarios.mockEmbedScript[url.searchParams.get('name')] || [])
  }
  if (url.pathname === '/__sent' || url.pathname === '/__received') {
    try { log[url.pathname.slice(3)].push(JSON.parse(body)) } catch { /* ignora */ }
    return send(res, 204, '')
  }
  if (url.pathname === '/__log') return send(res, 200, log)
  if (url.pathname === '/__reset') {
    log = { requests: [], sent: [], received: [] }
    current = 'default'
    return send(res, 204, '')
  }
  if (url.pathname === '/__scenario' && req.method === 'POST') {
    try { current = JSON.parse(body).name } catch { /* ignora */ }
    return send(res, 204, '')
  }
  return send(res, 404, { message: 'not found' })
})

const port = Number(process.argv[2] ?? process.env.PORT ?? 8787)
server.listen(port, '127.0.0.1', () => {
  console.log(`mock-server ouvindo em http://127.0.0.1:${server.address().port}`)
})
