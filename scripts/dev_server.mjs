// 零依赖预览服务器：兼容 --host、--host 0.0.0.0、--host=0.0.0.0 及端口参数。
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'demo');
const args = process.argv.slice(2);
let host = '127.0.0.1';
let port = 7100;

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--host') {
    const next = args[i + 1];
    if (next && !next.startsWith('--')) {
      host = next;
      i += 1;
    } else {
      host = '0.0.0.0';
    }
  } else if (arg.startsWith('--host=')) {
    host = arg.slice('--host='.length) || '0.0.0.0';
  } else if (arg === '--port' && args[i + 1]) {
    port = Number(args[i + 1]);
    i += 1;
  } else if (arg.startsWith('--port=')) {
    port = Number(arg.slice('--port='.length));
  }
}

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error(`无效端口：${port}`);
  process.exit(1);
}

const contentTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp'
};

const server = http.createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    let filePath = path.resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`);
    if (!filePath.startsWith(root + path.sep)) {
      response.writeHead(403);
      response.end('Forbidden');
      return;
    }
    if ((await stat(filePath)).isDirectory()) filePath = path.join(filePath, 'index.html');
    const body = await readFile(filePath);
    response.writeHead(200, {
      'Content-Type': contentTypes[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-store'
    });
    response.end(body);
  } catch (error) {
    response.writeHead(error.code === 'ENOENT' ? 404 : 500, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end(error.code === 'ENOENT' ? 'Not Found' : 'Internal Server Error');
  }
});

server.on('error', error => {
  console.error(`预览启动失败：${error.message}`);
  process.exit(1);
});
server.listen(port, host, () => {
  const shownHost = host === '0.0.0.0' ? 'localhost' : host;
  console.log(`Local: http://${shownHost}:${port}/`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
