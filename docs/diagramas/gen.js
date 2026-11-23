// Genera 5 páginas HTML con diagramas SVG (logos incluidos inline).
const fs = require('fs');
const path = require('path');
const DIR = process.argv[2] || __dirname; // carpeta de salida de los .html
const ICONS = path.join(__dirname, 'icons');

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ---------- iconos ----------
const BRAND = {
  cloudflare: '#F38020', nginx: '#22B24C', flask: '#E6EDF3',
  postgresql: '#6E91F0', redis: '#FF5A4E', docker: '#2496ED', python: '#FFD43B',
  socketdotio: '#E6EDF3', react: '#61DAFB', googlechrome: '#4C8BF5', jsonwebtokens: '#D63AFF',
  sqlalchemy: '#E8452C', pwa: '#9B7BFF',
};
// Solo logos oficiales (simple-icons); sin iconos genéricos.
function iconInner(name) {
  const s = fs.readFileSync(path.join(ICONS, name + '.svg'), 'utf8');
  const d = s.match(/<path d="([^"]+)"/)[1];
  return { body: `<path d="${d}"/>` };
}
function icon(name, x, y, size, color) {
  const ic = iconInner(name);
  return `<svg x="${x}" y="${y}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="${color}">${ic.body}</svg>`;
}
// Mosaico: cuadro redondeado con tinte del color + logo
function tile(name, x, y, s, color) {
  color = color || BRAND[name] || '#9FB3D1';
  const pad = Math.round(s * 0.22);
  return `<rect x="${x}" y="${y}" width="${s}" height="${s}" rx="${s * 0.26}" fill="${color}" fill-opacity="0.13" stroke="${color}" stroke-opacity="0.35"/>` +
    icon(name, x + pad, y + pad, s - pad * 2, color);
}

// Etiqueta de proceso: cuadro plano con el id en monoespaciada (W1, W2…)
function tagTile(text, x, y, s) {
  return `<rect x="${x}" y="${y}" width="${s}" height="${s}" rx="${s * 0.26}" fill="#1A2C4D" stroke="#3D6BC2"/>` +
    `<text x="${x + s / 2}" y="${y + s / 2 + 5}" text-anchor="middle" class="tg">${esc(text)}</text>`;
}

// ---------- tarjetas ----------
const STYLE = {
  normal: { fill: '#152034', stroke: '#2B3A55', title: '#F1F5FB', sub: '#93A4BF' },
  hl: { fill: '#16284A', stroke: '#3D6BC2', title: '#F1F5FB', sub: '#A9BEE3' },
  danger: { fill: '#2A1420', stroke: '#8A2D45', title: '#FF8FA3', sub: '#E0788C' },
  ghost: { fill: 'none', stroke: '#3B4C68', title: '#C5D2E6', sub: '#8597B3', dash: '5 5' },
};
// Tarjeta vertical: logo arriba, título y subtítulo centrados
function vcard({ x, y, w, h = 112, title, sub, icons = [], kind = 'normal', colors = [], badge, tag }) {
  const st = STYLE[kind];
  const s = 40, gap = 8;
  const total = icons.length * s + (icons.length - 1) * gap;
  let out = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${st.fill}" stroke="${st.stroke}" stroke-width="1.2" ${st.dash ? `stroke-dasharray="${st.dash}"` : ''}/>`;
  if (tag) out += tagTile(tag, x + w / 2 - s / 2, y + 16, s);
  icons.forEach((n, i) => { out += tile(n, x + w / 2 - total / 2 + i * (s + gap), y + 16, s, colors[i]); });
  const ty = icons.length || tag ? y + 77 : y + h / 2 + (sub ? -4 : 5);
  out += `<text x="${x + w / 2}" y="${ty}" text-anchor="middle" class="t" fill="${st.title}">${esc(title)}</text>`;
  if (sub) out += `<text x="${x + w / 2}" y="${ty + 19}" text-anchor="middle" class="s" fill="${st.sub}">${esc(sub)}</text>`;
  if (badge) out += `<circle cx="${x + 16}" cy="${y + 16}" r="11" fill="#2F5BA8"/><text x="${x + 16}" y="${y + 20}" text-anchor="middle" class="b">${badge}</text>`;
  return out;
}
// Tarjeta horizontal: logo a la izquierda, texto a la derecha
function hcard({ x, y, w, h = 76, title, sub, icon: ic, kind = 'normal', color, mono, tag }) {
  const st = STYLE[kind];
  const s = 40;
  let out = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${st.fill}" stroke="${st.stroke}" stroke-width="1.2" ${st.dash ? `stroke-dasharray="${st.dash}"` : ''}/>`;
  const cy = y + h / 2;
  let tx = x + 18;
  if (ic) { out += tile(ic, x + 16, cy - s / 2, s, color); tx = x + 16 + s + 14; }
  else if (tag) { out += tagTile(tag, x + 16, cy - s / 2, s); tx = x + 16 + s + 14; }
  if (sub) {
    out += `<text x="${tx}" y="${cy - 3}" class="${mono ? 'tm' : 't'}" fill="${st.title}">${esc(title)}</text>`;
    out += `<text x="${tx}" y="${cy + 16}" class="s" fill="${st.sub}">${esc(sub)}</text>`;
  } else out += `<text x="${tx}" y="${cy + 5}" class="${mono ? 'tm' : 't'}" fill="${st.title}">${esc(title)}</text>`;
  return out;
}

// ---------- flechas ----------
const C = { gray: '#6F819E', green: '#2BD38A', red: '#FF5370', blue: '#5B8DEF', amber: '#F6A945' };
function defs() {
  return '<defs>' + Object.entries(C).map(([k, c]) =>
    `<marker id="m-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="${c}"/></marker>`
  ).join('') + '</defs>';
}
function line(d, color = 'gray', { dash, end = true, start = false, width = 1.6 } = {}) {
  return `<path d="${d}" fill="none" stroke="${C[color]}" stroke-width="${width}" ${dash ? `stroke-dasharray="${dash}"` : ''} ${end ? `marker-end="url(#m-${color})"` : ''} ${start ? `marker-start="url(#m-${color})"` : ''}/>`;
}
const curve = (x1, y1, x2, y2, color, o) => {
  const mx = (x1 + x2) / 2;
  return line(`M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`, color, o);
};
const vcurve = (x1, y1, x2, y2, color, o) => {
  const my = (y1 + y2) / 2;
  return line(`M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`, color, o);
};
const lbl = (x, y, t, color = '#7F90AC', anchor = 'middle', cls = 'l') =>
  `<text x="${x}" y="${y}" text-anchor="${anchor}" class="${cls}" fill="${color}">${esc(t)}</text>`;
function group(x, y, w, h, title, ic) {
  let out = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14" fill="#0F1829" fill-opacity="0.55" stroke="#34445F" stroke-dasharray="6 6"/>`;
  let tx = x + 16;
  if (ic) { out += icon(ic, x + 14, y + 11, 16, BRAND[ic] || '#9FB3D1'); tx = x + 38; }
  out += lbl(tx, y + 24, title, '#8A9BB8', 'start');
  return out;
}
function chip(x, y, t, color) {
  const w = t.length * 7.7 + 30;
  return { w, svg: `<rect x="${x}" y="${y}" width="${w}" height="28" rx="14" fill="${color}" fill-opacity="0.1" stroke="${color}" stroke-opacity="0.45"/><circle cx="${x + 14}" cy="${y + 14}" r="3.5" fill="${color}"/>` + `<text x="${x + 24}" y="${y + 18.5}" class="c" fill="#D5DEEC">${esc(t)}</text>` };
}
function chips(items, cx, y) {
  const ws = items.map(([t]) => t.length * 7.7 + 30);
  const total = ws.reduce((a, b) => a + b, 0) + (items.length - 1) * 12;
  let x = cx - total / 2, out = '';
  items.forEach(([t, c], i) => { out += chip(x, y, t, c).svg; x += ws[i] + 12; });
  return out;
}

const W = 1088;
const svg = (h, body) => `<svg width="${W}" height="${h}" viewBox="0 0 ${W} ${h}" xmlns="http://www.w3.org/2000/svg">${defs()}${body}</svg>`;

// ---------- diagramas ----------
const D = {};

D.peticion = () => {
  const w = 150, step = (W - w) / 5, y = 40, cy = y + 56;
  const nodes = [
    { title: 'Usuario', sub: 'navegador · PWA', icons: ['googlechrome', 'pwa'] },
    { title: 'Cloudflare', sub: 'TLS · CDN · WAF', icons: ['cloudflare'] },
    { title: 'Túnel CF', sub: 'conexión saliente', icons: ['cloudflare'] },
    { title: 'Nginx', sub: 'proxy · rate-limit', icons: ['nginx'] },
    { title: 'Gunicorn', sub: 'Flask · 4 workers', icons: ['python', 'flask'] },
    { title: 'Datos', sub: 'PostgreSQL · Redis', icons: ['postgresql', 'redis'] },
  ];
  const protos = ['HTTPS', 'TÚNEL', 'HTTP', 'WSGI', 'SQL'];
  let b = '';
  nodes.forEach((n, i) => { b += vcard({ x: i * step, y, w, ...n, kind: i === 4 ? 'hl' : 'normal' }); });
  for (let i = 0; i < 5; i++) {
    const x1 = i * step + w + 4, x2 = (i + 1) * step - 4;
    b += line(`M${x1} ${cy - 9} L${x2} ${cy - 9}`, 'gray');
    b += line(`M${x2} ${cy + 9} L${x1} ${cy + 9}`, 'green');
    b += lbl((x1 + x2) / 2, cy - 22, protos[i]);
  }
  b += `<rect x="${W / 2 - 300}" y="182" width="600" height="34" rx="17" fill="#5B8DEF" fill-opacity="0.08" stroke="#5B8DEF" stroke-opacity="0.35"/>`;
  b += `<text x="${W / 2}" y="203.5" text-anchor="middle" class="s2" fill="#BFD0EE">6 roles · el permiso se valida en el frontend y se exige otra vez en cada endpoint</text>`;
  return svg(230, b);
};

D.compose = () => {
  let b = group(0, 0, 690, 330, 'DOCKER COMPOSE', 'docker');
  b += vcard({ x: 30, y: 105, w: 220, h: 130, title: 'api', sub: 'python:3.12-slim · gevent', icons: ['docker', 'python', 'flask'], kind: 'hl' });
  b += `<text x="140" y="256" text-anchor="middle" class="l" fill="#7F90AC">4 WORKERS · SIN ROOT</text>`;
  const svc = [
    { title: 'db', sub: 'postgres:18-alpine', icon: 'postgresql', vol: 'datos_postgres' },
    { title: 'redis', sub: 'redis:7-alpine', icon: 'redis', vol: 'datos_redis' },
    { title: 'clamav', sub: 'clamav/clamav:stable', vol: 'firmas_clamav' },
  ];
  svc.forEach((s, i) => {
    const y = 50 + i * 92, cy = y + 38;
    b += hcard({ x: 350, y, w: 300, title: s.title, sub: s.sub, icon: s.icon, color: s.color, mono: true });
    b += curve(252, 170, 346, cy, 'gray');
    b += line(`M652 ${cy} L${776} ${cy}`, 'gray', { dash: '4 4' });
    b += hcard({ x: 780, y: y + 6, w: 308, h: 64, title: s.vol, kind: 'ghost', color: '#8EA3C4', mono: true });
  });
  b += lbl(780, 34, 'VOLÚMENES PERSISTENTES', '#8A9BB8', 'start');
  return svg(336, b);
};

D.workers = () => {
  let b = group(0, 0, W, 124, 'GUNICORN · 4 WORKERS GEVENT');
  const w = 245, gap = (W - 48 - 4 * w) / 3;
  for (let i = 0; i < 4; i++) {
    const x = 24 + i * (w + gap);
    b += hcard({ x, y: 38, w, h: 70, title: `Worker ${i + 1}`, sub: 'hasta 1000 conexiones', tag: `W${i + 1}` });
    b += vcurve(x + w / 2, 110, W / 2 - 60 + i * 40, 186, 'blue');
  }
  b += hcard({ x: W / 2 - 170, y: 190, w: 340, h: 80, title: 'Redis', sub: 'estado compartido entre procesos', icon: 'redis', kind: 'hl' });
  b += chips([['rate-limit', '#F6A945'], ['lockout escalado', '#FF5370'], ['anti-replay 2FA · 90 s', '#D63AFF'], ['eventos Socket.IO', '#2BD38A']], W / 2, 296);
  return svg(336, b);
};

D.tunel = () => {
  let b = '';
  b += hcard({ x: 0, y: 40, w: 270, h: 84, title: 'Usuarios', sub: 'internet' });
  b += hcard({ x: 409, y: 40, w: 270, h: 84, title: 'Cloudflare', sub: 'borde global · WAF', icon: 'cloudflare' });
  b += hcard({ x: 818, y: 40, w: 270, h: 84, title: 'Origen', sub: '0 puertos expuestos', kind: 'hl', color: '#7FA6F5' });
  b += line('M274 82 L405 82', 'gray'); b += lbl(340, 70, 'HTTPS');
  b += line('M814 82 L683 82', 'green', { width: 2 }); b += lbl(748, 70, 'TÚNEL SALIENTE', '#2BD38A');
  b += line('M135 128 L135 190 L520 190', 'red', { dash: '6 5', end: false });
  b += line('M568 190 L953 190 L953 132', 'red', { dash: '6 5' });
  b += `<circle cx="544" cy="190" r="17" fill="#2A1420" stroke="#FF5370"/><path d="M537 183 L551 197 M551 183 L537 197" stroke="#FF5370" stroke-width="2.6" stroke-linecap="round"/>`;
  b += lbl(544, 230, 'conexión entrante directa · el firewall la descarta', '#FF7A90');
  return svg(246, b);
};

D.imagenes = () => {
  const w = 152, step = (W - w) / 5, y = 24;
  const n = [
    { title: 'URL externa', sub: 'llega del cliente' },
    { title: 'Anti-SSRF', sub: 'host válido · https' },
    { title: 'Magic bytes', sub: 'máx. 50 MP' },
    { title: 'Re-encode', sub: 'Pillow → WebP' },
    { title: 'SHA-256', sub: 'llave del objeto' },
    { title: 'R2 + CDN', sub: 'caché 1 año · aviso WS', icons: ['cloudflare'] },
  ];
  let b = '';
  n.forEach((o, i) => { b += vcard({ x: i * step, y, w, ...o, badge: i + 1, kind: i === 5 ? 'hl' : 'normal' }); });
  for (let i = 0; i < 5; i++) b += line(`M${i * step + w + 4} ${y + 56} L${(i + 1) * step - 4} ${y + 56}`, 'gray');
  b += chips([['−70 % de peso', '#2BD38A'], ['nada se guarda tal como llega', '#FF5370'], ['misma imagen = misma llave', '#D63AFF']], W / 2, 160);
  return svg(200, b);
};

D.lock = () => {
  let b = '';
  b += hcard({ x: 0, y: 20, w: 270, title: 'Transacción A', sub: 'quiere escribir' });
  b += hcard({ x: 0, y: 156, w: 270, title: 'Transacción B', sub: 'quiere escribir' });
  b += hcard({ x: 409, y: 84, w: 290, h: 84, title: 'Bloqueo de fila', sub: 'SELECT … FOR UPDATE NOWAIT', kind: 'hl', color: '#7FA6F5' });
  b += hcard({ x: 838, y: 88, w: 250, title: 'PostgreSQL', sub: 'stock ya escrito', icon: 'postgresql' });
  b += curve(274, 58, 405, 116, 'green', { width: 2 }); b += lbl(318, 52, 'toma el bloqueo', '#2BD38A', 'start', 'lm');
  b += curve(274, 194, 405, 138, 'red', { dash: '5 5' }); b += lbl(318, 214, 'espera o se rechaza', '#FF5370', 'start', 'lm');
  b += line('M703 126 L834 126', 'gray'); b += lbl(768, 114, 'COMMIT');
  return svg(240, b);
};

D.login = () => {
  const w = 172, step = (W - w) / 4, y = 34, cy = y + 56;
  const n = [
    { title: 'Usuario', sub: 'contraseña' },
    { title: '/auth/login', sub: 'verifica el hash', icons: ['flask'] },
    { title: 'stepToken', sub: 'jti · un solo uso', kind: 'hl' },
    { title: '/verify-2fa', sub: 'TOTP de 6 dígitos' },
    { title: 'Sesión', sub: 'access + refresh', icons: ['jsonwebtokens'] },
  ];
  const lab = ['POST', 'TOKEN', 'TOTP', 'OK'];
  let b = '';
  n.forEach((o, i) => { b += vcard({ x: i * step, y, w, ...o }); });
  for (let i = 0; i < 4; i++) {
    const x1 = i * step + w + 4, x2 = (i + 1) * step - 4;
    b += line(`M${x1} ${cy} L${x2} ${cy}`, i === 3 ? 'green' : 'gray');
    b += lbl((x1 + x2) / 2, cy - 12, lab[i]);
  }
  [[1, '5 fallos en 15 min', 'lockout de 10 min a 24 h'], [3, 'anti-replay', 'código quemado 90 s']].forEach(([i, t, s]) => {
    const cx = i * step + w / 2;
    b += line(`M${cx} ${y + 114} L${cx} 196`, 'red', { dash: '4 4' });
    b += hcard({ x: cx - 120, y: 200, w: 240, h: 64, title: t, sub: s, kind: 'danger', color: '#FF5370' });
  });
  return svg(272, b);
};

D.metricas = () => {
  let b = '';
  const cy = 150;
  b += vcard({ x: 0, y: cy - 56, w: 150, title: 'Petición', sub: 'termina' });
  b += vcard({ x: 200, y: cy - 56, w: 200, title: 'after_request', sub: 'nunca frena la respuesta', icons: ['flask'] });
  b += vcard({ x: 466, y: 6, w: 220, title: 'Contadores', sub: 'siempre, sin muestreo', kind: 'hl' });
  b += vcard({ x: 466, y: 182, w: 220, title: 'Detalle', sub: 'error · ≥500 ms · 1 de 20' });
  b += vcard({ x: 752, y: cy - 56, w: 150, title: 'Redis', sub: 'agregados del día', icons: ['redis'] });
  b += vcard({ x: 942, y: cy - 56, w: 146, title: 'Panel', sub: 'p50 · p95 · p99', icons: ['react'] });
  b += line(`M154 ${cy} L196 ${cy}`, 'gray');
  b += curve(404, cy, 462, 62, 'gray'); b += curve(404, cy, 462, 238, 'gray');
  b += curve(690, 62, 748, cy, 'gray'); b += curve(690, 238, 748, cy, 'gray');
  b += line(`M906 ${cy} L938 ${cy}`, 'gray');
  b += lbl(W / 2 + 32, 326, 'tramos del histograma:  ≤50 · ≤100 · ≤250 · ≤500 · ≤1000 · ≤3000 ms', '#8A9BB8', 'middle', 's2');
  return svg(340, b);
};

D.realtime = () => {
  let b = '';
  const y = 64, cy = y + 56;
  b += vcard({ x: 0, y, w: 160, title: 'Worker 2', sub: 'hace el cambio', tag: 'W2' });
  b += vcard({ x: 206, y, w: 170, title: 'Redis', sub: 'message_queue', icons: ['redis'], kind: 'hl' });
  b += vcard({ x: 422, y, w: 186, title: 'Workers 1 · 3 · 4', sub: 'reenvían a sus sockets', icons: ['socketdotio'] });
  b += hcard({ x: 660, y: 14, w: 220, h: 68, title: 'user:{id}', sub: 'a esa persona', mono: true });
  b += hcard({ x: 660, y: 158, w: 220, h: 68, title: 'role:{rol}', sub: 'a todo un rol', mono: true });
  b += vcard({ x: 928, y, w: 160, title: 'Navegador', sub: 'recibe el aviso', icons: ['googlechrome', 'react'] });
  b += line(`M164 ${cy} L202 ${cy}`, 'gray'); b += line(`M380 ${cy} L418 ${cy}`, 'gray');
  b += curve(612, cy, 656, 48, 'gray'); b += curve(612, cy, 656, 192, 'gray');
  b += curve(884, 48, 924, cy, 'green'); b += curve(884, 192, 924, cy, 'green');
  b += lbl(770, 8, 'SALAS DE SOCKET.IO', '#8A9BB8');
  b += line(`M1008 ${y + 116} L1008 268 L80 268 L80 ${y + 118}`, 'blue', { dash: '6 5' });
  b += `<rect x="${W / 2 - 190}" y="254" width="380" height="28" rx="14" fill="#0E1626" stroke="#5B8DEF" stroke-opacity="0.5"/>`;
  b += lbl(W / 2, 272.5, 'vuelve a pedir el dato por REST · permiso revalidado', '#8FB2F7', 'middle', 'lm');
  return svg(292, b);
};

D.sesion = () => {
  let b = '';
  const lanes = [
    { label: 'PROACTIVO', c: 'gray', cards: [['Faltan 60 s', 'para que expire', null], ['/auth/refresh', 'cookie httpOnly', 'flask'], ['Token nuevo', 'sin cortes', 'jsonwebtokens']] },
    { label: 'REACTIVO', c: 'green', cards: [['Llega un 401', 'el token ya venció', null], ['Un solo refresh', 'aunque fallen 5 a la vez', null], ['Reintenta', 'la petición original', null]] },
  ];
  const x0 = 140, w = 276, step = (W - x0 - w) / 2;
  lanes.forEach((ln, li) => {
    const y = 14 + li * 116, cy = y + 38;
    b += `<rect x="0" y="${y + 20}" width="112" height="36" rx="18" fill="${C[ln.c]}" fill-opacity="0.12" stroke="${C[ln.c]}" stroke-opacity="0.5"/>`;
    b += lbl(56, y + 42.5, ln.label, li ? '#2BD38A' : '#A9B8CF');
    ln.cards.forEach(([t, s, ic, col], i) => { b += hcard({ x: x0 + i * step, y, w, title: t, sub: s, icon: ic, color: col }); });
    for (let i = 0; i < 2; i++) b += line(`M${x0 + i * step + w + 4} ${cy} L${x0 + (i + 1) * step - 4} ${cy}`, ln.c);
  });
  return svg(222, b);
};

// ---------- páginas ----------
const PAGES = [
  ['01-peticion-y-contenedores', [
    ['01', 'El camino de una petición', D.peticion, '<b class="g">▶</b> petición &nbsp; <b class="gr">◀</b> respuesta y eventos en vivo por WebSocket'],
    ['02', 'Cuatro contenedores, un solo comando', D.compose, 'docker compose up · imágenes fijadas · volúmenes persistentes · healthcheck en cada servicio'],
  ]],
  ['02-workers-y-tunel', [
    ['03', 'Cuatro procesos, una sola memoria', D.workers, 'un worker publica, todos reciben · lo que debe ser común vive en Redis'],
    ['04', 'Cero puertos abiertos', D.tunel, 'el origen no publica nada · la única conexión es el túnel que él mismo abre hacia Cloudflare'],
  ]],
  ['03-archivos-y-concurrencia', [
    ['05', 'No confiamos en el archivo: lo reconstruimos', D.imagenes, 'la llave del objeto es el SHA-256 de su contenido, así que se cachea un año sin riesgo'],
    ['06', 'Dos transacciones, la misma fila', D.lock, 'la segunda nunca lee un stock obsoleto: espera o falla y reintenta'],
  ]],
  ['04-login-2fa-y-metricas', [
    ['07', 'Entrar: dos factores y un vale de un solo uso', D.login, 'el stepToken acredita «ya pasé la contraseña» y se quema al usarlo · sin él no se puede pedir el código'],
    ['08', 'Cada petición se mide', D.metricas, 'los totales son exactos; solo se muestrea el detalle · el preflight de CORS no se cuenta'],
  ]],
  ['05-realtime-y-sesion', [
    ['09', 'Un cambio, todas las pantallas', D.realtime, 'el evento solo lleva {id, action} · el dato real viaja por REST'],
    ['10', 'La sesión se renueva sola', D.sesion, 'varias peticiones con 401 en vuelo comparten un único refresh'],
  ]],
];

const CSS = `
*{box-sizing:border-box;margin:0;padding:0}
html{background:#070C17}
body{width:1200px;padding:28px 32px 32px;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#E6EDF3;
 background:#070C17}
section+section{margin-top:30px}
h2{display:flex;align-items:center;gap:12px;font-size:21px;font-weight:650;letter-spacing:-.01em;margin:0 0 14px 2px}
h2 .n{font:600 12px "JetBrains Mono",Consolas,monospace;color:#8FB2F7;background:#16284A;border:1px solid #3D6BC2;border-radius:7px;padding:3px 7px}
.panel{background:#0F1828;border:1px solid #233149;border-radius:18px;padding:24px}
.panel svg{display:block}
.foot{margin-top:20px;padding-top:14px;border-top:1px solid #223047;font:12.5px "JetBrains Mono",Consolas,monospace;color:#8A9BB8}
.foot .g{color:#8FA0BB}.foot .gr{color:#2BD38A}
svg .t{font:650 14.5px Inter,"Segoe UI",sans-serif}
svg .tm{font:600 14px "JetBrains Mono",Consolas,monospace}
svg .s{font:400 12px Inter,"Segoe UI",sans-serif}
svg .s2{font:400 12.5px Inter,"Segoe UI",sans-serif}
svg .l{font:600 10.5px "JetBrains Mono",Consolas,monospace;letter-spacing:.08em}
svg .lm{font:500 12px "JetBrains Mono",Consolas,monospace}
svg .c{font:500 12px "JetBrains Mono",Consolas,monospace}
svg .tg{font:700 14px "JetBrains Mono",Consolas,monospace;fill:#8FB2F7}
svg .b{font:700 11px Inter,"Segoe UI",sans-serif;fill:#fff}
`;
for (const [file, secs] of PAGES) {
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${file}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=block" rel="stylesheet">
<style>${CSS}</style></head><body>
${secs.map(([n, t, fn, foot]) => `<section><h2><span class="n">${n}</span>${esc(t)}</h2><div class="panel">${fn()}<div class="foot">${foot.includes('<b') ? foot : esc(foot)}</div></div></section>`).join('\n')}
<script>document.fonts.ready.then(()=>{document.body.dataset.h=Math.ceil(document.body.getBoundingClientRect().height)})</script>
</body></html>`;
  fs.writeFileSync(path.join(DIR, file + '.html'), html);
}
console.log('ok');
