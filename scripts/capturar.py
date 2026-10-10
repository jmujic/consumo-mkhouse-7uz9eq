#!/usr/bin/env python3
"""Captura diaria de MK House: consumo (Emporia) y producción solar (Growatt).

Corre en GitHub Actions, sin navegador. Lee las credenciales de variables de
entorno (secretos del repositorio) y escribe el día anterior en data.json.

Variables:
  EMPORIA_USER, EMPORIA_PASS            cuenta de Emporia
  GROWATT_USER, GROWATT_PASS            cuenta de Growatt (servidor clásico)
  GROWATT_TOKEN                         opcional: token de la API abierta de Growatt (sustituye usuario/contraseña)
  FECHA                                 opcional: AAAA-MM-DD a capturar (por omisión, ayer en Cancún)

Nunca imprime ni guarda credenciales, números de serie, cuenta ni servicio.
"""
import datetime as dt
import json
import os
import sys
import traceback
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Cancun")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RAIZ, "data.json")
ESTADO = os.path.join(RAIZ, "estado.json")
DISPOSITIVOS_EMPORIA = ("lc inversus", "lci 1")
PLANTAS = {"v1": "CASA VECINA 1", "v2": "CASA VECINA 2"}


def r3(x):
    return None if x is None else round(float(x), 3)


# ---------------------------------------------------------------- Emporia
def leer_emporia(fecha: dt.date):
    from pyemvue import PyEmVue
    from pyemvue.enums import Scale, Unit

    vue = PyEmVue()
    if not vue.login(username=os.environ["EMPORIA_USER"], password=os.environ["EMPORIA_PASS"]):
        raise RuntimeError("Emporia rechazó el inicio de sesión")

    dispositivos = vue.get_devices()
    nombres = {}          # (gid, canal) -> nombre del circuito
    principal = None
    for d in dispositivos:
        for c in d.channels:
            if c.name:
                nombres[(d.device_gid, c.channel_num)] = c.name
        if (d.device_name or "").strip().lower() in DISPOSITIVOS_EMPORIA:
            principal = d.device_gid
    if principal is None:
        con_red = [d for d in dispositivos if any(c.channel_num == "1,2,3" for c in d.channels)]
        if not con_red:
            raise RuntimeError("No encontré el monitor principal en Emporia")
        principal = con_red[0].device_gid

    mediodia = dt.datetime.combine(fecha, dt.time(12, 0), TZ).astimezone(dt.timezone.utc)
    uso = vue.get_device_list_usage(deviceGids=[principal], instant=mediodia,
                                    scale=Scale.DAY.value, unit=Unit.KWH.value)
    dev = uso.get(principal)
    if dev is None or not dev.channels:
        raise RuntimeError("Emporia no devolvió datos del día")

    especiales = {}
    circuitos = {}

    def recorrer(canales):
        for num, ch in canales.items():
            if num in ("1,2,3", "MainsFromGrid", "MainsToGrid", "Balance", "TotalUsage"):
                especiales[num] = ch.usage
            else:
                nombre = ch.name or nombres.get((ch.device_gid, num)) or f"Canal {num}"
                circuitos[nombre] = r3(ch.usage or 0)
            for anidado in (ch.nested_devices or {}).values():
                recorrer(anidado.channels)

    recorrer(dev.channels)

    red = especiales.get("MainsFromGrid")
    exportado = especiales.get("MainsToGrid")
    neto = especiales.get("1,2,3")
    if red is None or exportado is None:
        raise RuntimeError("Emporia no devolvió Desde la red / Hacia la red")
    total = especiales.get("TotalUsage")
    if total is None:
        total = sum(v for v in circuitos.values() if v)
    if neto is None:
        neto = red - exportado
    saldo = especiales.get("Balance")
    if saldo is None:
        saldo = total - neto

    circuitos = dict(sorted(circuitos.items(), key=lambda kv: -(kv[1] or 0)))
    return {"red": r3(red), "exportado": r3(exportado), "total": r3(total),
            "saldo": r3(saldo), "circuitos": circuitos}


# ---------------------------------------------------------------- Growatt
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def leer_growatt(fecha: dt.date):
    """Devuelve ({clave: kWh del día}, {clave: total del mes}, {clave: inversores fuera de línea})."""
    token = os.environ.get("GROWATT_TOKEN")
    dia, mes, fuera = {}, {}, {}
    import growattServer

    if token:
        api = growattServer.OpenApiV1(token=token)
        plantas = api.plant_list().get("plants", [])
        for clave, nombre in PLANTAS.items():
            p = next((x for x in plantas if x.get("name", "").strip().upper() == nombre), None)
            if not p:
                raise RuntimeError(f"No encontré la planta {nombre} en Growatt")
            pid = p["plant_id"]
            h = api.plant_energy_history(pid, start_date=fecha, end_date=fecha, time_unit="day")
            regs = h.get("energys", [])
            dia[clave] = r3(_num(regs[0]["energy"])) if regs else None
            ini = fecha.replace(day=1)
            hm = api.plant_energy_history(pid, start_date=ini, end_date=fecha, time_unit="month")
            rm = [x for x in hm.get("energys", []) if str(x.get("date", "")).startswith(fecha.strftime("%Y-%m"))]
            mes[clave] = r3(_num(rm[0]["energy"])) if rm else None
            fuera[clave] = sum(1 for d in api.device_list(pid).get("devices", []) if d.get("lost"))
        return dia, mes, fuera

    api = growattServer.GrowattApi(add_random_user_id=True)
    login = api.login(os.environ["GROWATT_USER"], os.environ["GROWATT_PASS"])
    if not login.get("success"):
        raise RuntimeError("Growatt rechazó el inicio de sesión")
    plantas = api.plant_list(login["userId"])
    if isinstance(plantas, dict):
        plantas = plantas.get("data", [])
    for clave, nombre in PLANTAS.items():
        p = next((x for x in plantas if str(x.get("plantName", "")).strip().upper() == nombre), None)
        if not p:
            raise RuntimeError(f"No encontré la planta {nombre} en Growatt")
        pid = p.get("plantId") or p.get("id")
        det = api.plant_detail(pid, growattServer.Timespan.month, dt.datetime.combine(fecha, dt.time(12)))
        datos = det.get("data", {}) or {}
        val = None
        for k in (f"{fecha.day:02d}", str(fecha.day), fecha.isoformat()):
            if k in datos:
                val = _num(datos[k])
                break
        dia[clave] = r3(val)
        tot = (det.get("plantData") or {}).get("currentEnergy")
        if tot is None and datos:
            vals = [_num(v) for v in datos.values() if _num(v) is not None]
            tot = sum(vals) if vals else None
        mes[clave] = r3(_num(tot))
        fuera[clave] = sum(1 for d in api.device_list(pid)
                           if d.get("lost") in (True, "true") or str(d.get("deviceStatus", "")) in ("-1", "3"))
    return dia, mes, fuera


# ---------------------------------------------------------------- principal
def main():
    hoy = dt.datetime.now(TZ).date()
    fecha = dt.date.fromisoformat(os.environ["FECHA"]) if os.environ.get("FECHA") else hoy - dt.timedelta(days=1)
    clave = fecha.isoformat()
    mes_clave = fecha.strftime("%Y-%m")
    ahora = dt.datetime.now(TZ).isoformat(timespec="seconds")

    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)

    errores, emp, sol, sol_mes, fuera = [], None, None, None, None
    try:
        emp = leer_emporia(fecha)
    except Exception as e:
        errores.append(f"Emporia: {e}")
        traceback.print_exc()
    try:
        sol, sol_mes, fuera = leer_growatt(fecha)
    except Exception as e:
        errores.append(f"Growatt: {e}")
        traceback.print_exc()

    if emp is None and sol is None:
        print("No se pudo leer ninguna fuente:", "; ".join(errores))
        escribir_estado(clave, ahora, errores, fuera)
        sys.exit(1)

    previo = data.setdefault("dias", {}).get(clave, {})
    doc = {"fecha": clave}
    if emp:
        doc.update({"red": emp["red"], "exportado": emp["exportado"], "total": emp["total"],
                    "saldo": emp["saldo"], "parcial": False})
    else:
        for k in ("red", "exportado", "total", "saldo"):
            if k in previo:
                doc[k] = previo[k]
        doc["parcial"] = previo.get("parcial", True) if previo else True
    doc["capturado"] = ahora
    doc["solar"] = sol if sol else previo.get("solar", {})
    doc["circuitos"] = emp["circuitos"] if emp else previo.get("circuitos", {})
    data["dias"][clave] = doc

    if sol:
        sm = data.setdefault("solar_mes", {}).get(mes_clave, {})
        if sol_mes and all(v is not None for v in sol_mes.values()):
            v1, v2 = sol_mes["v1"], sol_mes["v2"]
        else:  # acumulado propio si el portal no da el total del mes
            ya = sm.get("hasta", "") >= clave
            v1 = sm.get("v1", 0) + (0 if ya else (sol.get("v1") or 0))
            v2 = sm.get("v2", 0) + (0 if ya else (sol.get("v2") or 0))
        ultimo = (fecha + dt.timedelta(days=1)).month != fecha.month
        nuevo = dict(sm)
        nuevo.update({"mes": mes_clave, "v1": r3(v1), "v2": r3(v2), "total": r3(v1 + v2), "hasta": clave})
        nuevo.pop("inversores", None)
        if ultimo and sol_mes and all(v is not None for v in sol_mes.values()):
            nuevo.pop("parcial", None)
        else:
            nuevo["parcial"] = True
        data["solar_mes"][mes_clave] = nuevo

    data["dias"] = dict(sorted(data["dias"].items()))
    data["solar_mes"] = dict(sorted(data.get("solar_mes", {}).items()))
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    escribir_estado(clave, ahora, errores, fuera)
    print(f"Registrado {clave}: red={doc.get('red')} exportado={doc.get('exportado')} solar={doc.get('solar')}")
    if errores:
        print("Avisos:", "; ".join(errores))
        sys.exit(2)


def escribir_estado(clave, ahora, errores, fuera):
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump({"fecha": clave, "ejecutado": ahora, "ok": not errores,
                   "errores": errores, "inversores_no_normales": fuera},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
