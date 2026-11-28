// Motor de recorridos compartido (copiado de colectivos.html, 23/11). Recorridos de OpenStreetMap.

const CERCA_M = 500, TRANSBORDO_M = 300, MIN_TRAMO_KM = 1.0, A_PIE_M = 1200, CELDA = 0.0025;

function metros(a, b) {
  const la = (a[0] + b[0]) / 2 * Math.PI / 180;
  return Math.hypot((b[1] - a[1]) * 111320 * Math.cos(la), (b[0] - a[0]) * 110540);
}
function plata(p) { return "$" + Math.round(p).toLocaleString("es-AR"); }
function fmtKm(km) { return km < 1 ? Math.round(km * 10) * 100 + " m" : km.toFixed(1).replace(".", ",") + " km"; }
function aPie(m) { return Math.max(1, Math.round(m * 1.3 / 80)); }   // minutos caminando (1,3 por las esquinas)

let LINEAS = [], GRILLA = new Map(), TARIFAS = [], FECHA = "";

function cargarDatos(d) {
  TARIFAS = d.t; FECHA = d.f;
  LINEAS = d.l.map(L => {
    const crudos = []; let a = 0, b = 0;
    for (let i = 0; i < L.p.length; i += 2) { a += L.p[i]; b += L.p[i + 1]; crudos.push([a / 1e5, b / 1e5]); }
    // el archivo trae el trazado simplificado: lo relleno con un punto cada ~50 m para medir bien las distancias
    const pts = [crudos[0]], nuevo = [0], km = [0];
    for (let i = 1; i < crudos.length; i++) {
      const p0 = crudos[i - 1], p1 = crudos[i], dm = metros(p0, p1), n = Math.max(1, Math.ceil(dm / 50));
      for (let k = 1; k <= n; k++) {
        pts.push([p0[0] + (p1[0] - p0[0]) * k / n, p0[1] + (p1[1] - p0[1]) * k / n]);
        km.push(km[km.length - 1] + dm / n / 1000);
      }
      nuevo.push(pts.length - 1);
    }
    const paradas = []; let i0 = 0;
    for (let k = 0; k < L.s.length; k += 2) { i0 += L.s[k]; paradas.push({ i: nuevo[i0], n: d.n[L.s[k + 1]] || "" }); }
    return { ref: L.r, ramal: L.m, hasta: L.h, pts, km, paradas };
  });
  LINEAS.forEach((L, li) => L.pts.forEach((p, i) => {
    const k = Math.floor(p[0] / CELDA) + "," + Math.floor(p[1] / CELDA);
    let v = GRILLA.get(k); if (!v) GRILLA.set(k, v = []); v.push(li, i);
  }));
}

function celdasCerca(P) {     // la celda del punto y sus 8 vecinas
  const cx = Math.floor(P[0] / CELDA), cy = Math.floor(P[1] / CELDA), res = [];
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) { const v = GRILLA.get((cx + dx) + "," + (cy + dy)); if (v) res.push(v); }
  return res;
}

function cercanas(P, radio = CERCA_M) {
  const cx = Math.floor(P[0] / CELDA), cy = Math.floor(P[1] / CELDA), r = Math.floor(radio / 230) + 1;
  const res = new Map();
  for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) {
    const v = GRILLA.get((cx + dx) + "," + (cy + dy)); if (!v) continue;
    for (let k = 0; k < v.length; k += 2) {
      const li = v[k], i = v[k + 1], d = metros(P, LINEAS[li].pts[i]);
      if (d <= radio && (!res.has(li) || d < res.get(li)[1])) res.set(li, [i, d]);
    }
  }
  return res;
}

function precio(km) {
  for (const [hasta, p] of TARIFAS) if (km <= hasta) return p;
  return TARIFAS[TARIFAS.length - 1][1];
}

function minutos(o) { return Math.round(o.km / 15 * 60 + aPie(o.pie) + (o.tramos.length > 1 ? 8 : 0)); }
function costo(o) { return o.tramos.reduce((s, t, n) => s + precio(t.km) * (n ? 0.5 : 1), 0); }

function opciones(A, B) {
  const na = cercanas(A), nb = cercanas(B);
  let ops = [];
  for (const [li, [ia, da]] of na) {
    if (!nb.has(li)) continue;
    const [ib, db] = nb.get(li), L = LINEAS[li], km = L.km[ib] - L.km[ia];
    if (ib > ia && km >= MIN_TRAMO_KM) ops.push({ tramos: [{ li, ia, ib, km }], pie: da + db, pies: [da, db], km });
  }
  if (new Set(ops.map(o => LINEAS[o.tramos[0].li].ref)).size < 3) {
    for (const [l1, [ia, da]] of na) {
      const L1 = LINEAS[l1], mejor = new Map();
      for (let i = ia + 1; i < L1.pts.length; i++) {
        const k1 = L1.km[i] - L1.km[ia];
        if (k1 < MIN_TRAMO_KM) continue;
        const P = L1.pts[i];
        for (const v of celdasCerca(P)) for (let k = 0; k < v.length; k += 2) {
          const l2 = v[k], j = v[k + 1];
          if (!nb.has(l2) || l2 === l1 || LINEAS[l2].ref === L1.ref) continue;
          const ib = nb.get(l2)[0]; if (j >= ib) continue;
          const L2 = LINEAS[l2], k2 = L2.km[ib] - L2.km[j];
          if (k2 < MIN_TRAMO_KM) continue;
          const dt = metros(P, L2.pts[j]);
          if (dt <= TRANSBORDO_M && (!mejor.has(l2) || k1 + k2 + dt / 500 < mejor.get(l2).total + mejor.get(l2).dt / 500)) mejor.set(l2, { total: k1 + k2, i, j, k1, k2, dt });
        }
      }
      for (const [l2, m] of mejor) {
        const db = nb.get(l2)[1];
        ops.push({ tramos: [{ li: l1, ia, ib: m.i, km: m.k1 }, { li: l2, ia: m.j, ib: nb.get(l2)[0], km: m.k2 }],
                   pie: da + db + m.dt, pies: [da, db], combina: m.dt, km: m.total });
      }
    }
  }
  const puntaje = o => o.km + o.pie / 1000;
  const directos = ops.filter(o => o.tramos.length === 1).map(puntaje);
  if (directos.length) { const tope = Math.min(...directos) * 0.8; ops = ops.filter(o => o.tramos.length === 1 || puntaje(o) + 2 < tope); }
  ops.sort((a, b) => a.tramos.length - b.tramos.length || puntaje(a) - puntaje(b));
  // una opción por combinación de números de línea
  const vistos = new Set(), unicas = [];
  for (const o of ops) {
    const clave = o.tramos.map(t => LINEAS[t.li].ref).join(">");
    if (!vistos.has(clave)) { vistos.add(clave); unicas.push(o); }
  }
  // las que hacen el mismo viaje con otra línea al principio se juntan
  const final = [];
  for (const o of unicas) {
    o.alt = [];
    const f = final.find(f => f.tramos.length === o.tramos.length && Math.abs(f.km - o.km) <= Math.max(0.8, 0.12 * f.km)
      && metros(LINEAS[f.tramos[0].li].pts[f.tramos[0].ia], LINEAS[o.tramos[0].li].pts[o.tramos[0].ia]) < 300
      && metros(LINEAS[f.tramos[0].li].pts[f.tramos[0].ib], LINEAS[o.tramos[0].li].pts[o.tramos[0].ib]) < 300
      && (o.tramos.length === 1 || LINEAS[f.tramos[1].li].ref === LINEAS[o.tramos[1].li].ref));
    if (f) { if (!f.alt.includes(LINEAS[o.tramos[0].li].ref)) f.alt.push(LINEAS[o.tramos[0].li].ref); }
    else final.push(o);
  }
  final.sort((a, b) => minutos(a) - minutos(b));
  const mejor = final.length ? minutos(final[0]) : 0;
  return final.filter(o => minutos(o) <= mejor * 1.6 + 15).slice(0, 3);
}

function paradaCerca(L, i) {
  let m = null;
  for (const s of L.paradas) if (!m || Math.abs(s.i - i) < Math.abs(m.i - i)) m = s;
  return m;
}
function paradasEntre(L, ia, ib) { return L.paradas.filter(s => s.i > ia && s.i <= ib).length; }

function linkParada(L, s, extra) {
  const p = L.pts[s.i];
  const url = "https://www.google.com/maps/search/?api=1&query=" + p[0].toFixed(6) + "," + p[1].toFixed(6);
  return `<a data-url="${url}">${esc(s.n || extra)}</a>`;
}

function viajeDe(A, B) {
  const d = metros(A, B);
  if (d < A_PIE_M) return { pie: d, ops: [] };
  return { pie: null, ops: opciones(A, B) };
}
