"""Arma el archivo de recorridos de colectivos que usa la ventana "Colectivos".

Baja de OpenStreetMap (Overpass) las líneas de colectivo de La Matanza y alrededores,
simplifica los trazados y guarda un JSON compacto con líneas, paradas y tarifas.
Lo corre GitHub Actions una vez por semana (.github/workflows/colectivos.yml).

Uso:  python armar_colectivos.py test/colectivos.json
"""
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CAJA = (-34.92, -58.78, -34.60, -58.42)        # sur, oeste, norte, este
UA = {"User-Agent": "OfertasABC-bot/1.0 (ventana de colectivos; github.com/MatrixGuays)"}
TOLERANCIA_M = 12                              # simplificación del trazado
PARADAS_JUNTAS_KM = 0.06                       # dos marcas de parada a menos de 60 m son la misma
URL_TARIFAS = "https://www.argentina.gob.ar/node/52326"
TARIFA_RESPALDO = [[3, 1063.98], [6, 1196.97], [12, 1329.97], [27, 1595.97], [9999, 1876.33]]
SERVIDORES = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
              "https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]


def metros(a, b):
    la = math.radians((a[0] + b[0]) / 2)
    return math.hypot((b[1] - a[1]) * 111320 * math.cos(la), (b[0] - a[0]) * 110540)


def bajar():
    s, o, n, e = CAJA
    q = (f'[out:json][timeout:240];relation["route"="bus"]({s},{o},{n},{e})->.r;.r out geom;'
         f'node(r.r)["name"]->.n;.n out tags;')
    for host in SERVIDORES:
        try:
            t = time.time()
            req = urllib.request.Request(host, data=urllib.parse.urlencode({"data": q}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                crudo = r.read()
            print(f"Overpass ({host.split('/')[2]}): {len(crudo) / 1e6:.1f} MB en {time.time() - t:.0f} s", flush=True)
            return json.loads(crudo.decode("utf-8"))["elements"]
        except Exception as err:
            print(f"Overpass {host}: falló ({err})", flush=True)
            time.sleep(15)
    raise SystemExit("No pude bajar los recorridos")


def encadenar(rel):
    puntos, tramos = [], 0
    for m in rel.get("members", []):
        if m.get("type") != "way" or m.get("role", "") not in ("", "forward", "backward") or not m.get("geometry"):
            continue
        g = [(p["lat"], p["lon"]) for p in m["geometry"] if p]
        if len(g) < 2:
            continue
        if puntos:
            if tramos == 1 and min(metros(puntos[0], g[0]), metros(puntos[0], g[-1])) < \
                    min(metros(puntos[-1], g[0]), metros(puntos[-1], g[-1])):
                puntos.reverse()
            if metros(puntos[-1], g[-1]) < metros(puntos[-1], g[0]):
                g.reverse()
        puntos += g if not puntos or metros(puntos[-1], g[0]) > 1 else g[1:]
        tramos += 1
    return puntos


def simplificar(pts, forzar=(), tol=TOLERANCIA_M):
    """Douglas-Peucker sin recursión; devuelve los índices que se conservan (siempre los de "forzar")."""
    if len(pts) < 3:
        return list(range(len(pts)))
    lat0 = math.radians(pts[0][0])
    xy = [(p[1] * 111320 * math.cos(lat0), p[0] * 110540) for p in pts]
    keep = [False] * len(pts)
    fijos = sorted({0, len(pts) - 1, *forzar})
    for i in fijos:
        keep[i] = True
    pila = list(zip(fijos, fijos[1:]))
    while pila:
        a, b = pila.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        mejor, mi = -1, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if L == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > mejor:
                mejor, mi = d, i
        if mejor > tol:
            keep[mi] = True
            pila += [(a, mi), (mi, b)]
    return [i for i, k in enumerate(keep) if k]


def linea(rel, nombres, tabla):
    t = rel.get("tags", {})
    pts = encadenar(rel)
    if len(pts) < 2:
        return None
    ref = str(t.get("ref") or "").strip()
    m = re.match(r"\s*(\d+(?:/\d+)?)", ref) or re.search(r"[Ll][íi]nea\s+(\d+(?:/\d+)?)", t.get("name", ""))
    if not m:
        return None                                  # sin número de línea no sirve para la ventana
    ramal = ""
    mv = re.search(r"→\s*(x\s*[^→]+?)\s*→", t.get("name", ""))
    if mv:
        ramal = mv.group(1)
    elif t.get("via"):
        ramal = "x " + str(t["via"])
    ramal = re.sub(r"\s*;\s*", " · ", ramal).strip()

    # paradas: primero las ubico sobre el trazado completo, así su punto no se pierde al simplificar
    crudas = []
    for mb in rel.get("members", []):
        if mb.get("type") == "node" and str(mb.get("role", "")).startswith(("stop", "platform")) and "lat" in mb:
            a, b = mb["lat"], mb["lon"]
            bi = min(range(len(pts)), key=lambda i: (pts[i][0] - a) ** 2 + ((pts[i][1] - b) * 0.82) ** 2)
            if metros((a, b), pts[bi]) < 120:
                crudas.append((bi, nombres.get(mb["ref"], "")))
    idx = simplificar(pts, [i for i, _ in crudas])
    nuevo = {v: k for k, v in enumerate(idx)}
    sp = [pts[i] for i in idx]
    acum = [0.0]
    for i in range(1, len(sp)):
        acum.append(acum[-1] + metros(sp[i - 1], sp[i]) / 1000)
    paradas = [(nuevo[i], n) for i, n in crudas]
    paradas.sort(key=lambda x: x[0])
    unicas = []
    for i, n in paradas:
        if unicas and acum[i] - acum[unicas[-1][0]] < PARADAS_JUNTAS_KM:
            if n and not unicas[-1][1]:
                unicas[-1] = (unicas[-1][0], n)
            continue
        unicas.append((i, n))

    def id_nombre(n):
        n = n.strip()
        if n not in tabla:
            tabla[n] = len(tabla)
        return tabla[n]

    # coordenadas en enteros (x 1e5) y diferencias, para que el archivo pese poco
    p, pla, plo = [], 0, 0
    for la, lo in sp:
        a, b = round(la * 1e5), round(lo * 1e5)
        p += [a - pla, b - plo]
        pla, plo = a, b
    s, prev = [], 0
    for i, n in unicas:
        s += [i - prev, id_nombre(n)]
        prev = i
    return {"r": m.group(1), "m": ramal, "h": str(t.get("to", "")).strip(), "p": p, "s": s}


def num(x):
    return float(x.replace(".", "").replace(",", "."))


def tarifas():
    try:
        req = urllib.request.Request(URL_TARIFAS, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        i = txt.lower().find("provincia de buenos aires")
        bloque = txt[i:i + 1500] if i >= 0 else ""
        vals = re.findall(r"(0-3|3-6|6-12|12-27|m[aá]s de 27) km \$ ?([\d.]+,\d{2})", bloque)
        if len(vals) >= 4:
            lim = {"0-3": 3, "3-6": 6, "6-12": 12, "12-27": 27}
            t = [[lim.get(a, 9999), num(p)] for a, p in vals]
            print(f"Tarifas oficiales: {t}", flush=True)
            return t, True
    except Exception as e:
        print(f"Tarifas: no pude leer el cuadro oficial ({e})", flush=True)
    return TARIFA_RESPALDO, False


def main():
    salida = sys.argv[1] if len(sys.argv) > 1 else "colectivos.json"
    previo = None
    try:
        with open(salida, encoding="utf-8") as f:
            previo = json.load(f)
    except Exception:
        pass
    els = bajar()
    nombres = {e["id"]: e.get("tags", {}).get("name", "") for e in els if e.get("type") == "node"}
    tabla = {"": 0}
    lineas = [L for L in (linea(r, nombres, tabla) for r in els if r.get("type") == "relation") if L]
    if len(lineas) < 300:
        raise SystemExit(f"Solo {len(lineas)} recorridos: algo salió mal, no piso el archivo anterior")
    tar, oficial = tarifas()
    if not oficial and previo and previo.get("t"):
        tar = previo["t"]                            # me quedo con la última tarifa oficial leída
    datos = {
        "v": 1,
        "f": datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y"),
        "t": tar,
        "n": [n for n, _ in sorted(tabla.items(), key=lambda x: x[1])],
        "l": sorted(lineas, key=lambda L: (len(L["r"]), L["r"], L["m"])),
    }
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    refs = sorted({L["r"] for L in lineas}, key=lambda r: (len(r), r))
    import os
    print(f"Listo: {len(lineas)} recorridos, {len(refs)} líneas, {len(tabla)} nombres de parada, "
          f"{os.path.getsize(salida) / 1e6:.2f} MB → {salida}", flush=True)


if __name__ == "__main__":
    main()
