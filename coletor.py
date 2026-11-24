#!/usr/bin/env python3
"""Coletor do Monitor de Conforto Térmico do Climando.

Para cada ponto de pontos.json consulta:
  - temperatura do ar (Open-Meteo por padrão; OpenWeatherMap se TEMP_FONTE=owm)
  - velocidade atual e de fluxo livre do trecho mais próximo (TomTom Flow Segment Data)
e calcula o Índice de Desconforto Térmico Estimado (IDTE), conforme o TCC de Davi Lopes
da Costa (UFCG, 2026), com uma diferença: o resultado é truncado entre 0 e 100.

Saídas (pasta saida/), uma por cidade (ver CIDADES):
  conforto_latest.json         estado atual, lido pela página conforto.html
  hist_AAAA-MM-DD.csv          linhas desta coleta (o publicar_ftp.py junta com as anteriores)

Variáveis de ambiente: TOMTOM_KEY (obrigatória, exceto com --simular),
OWM_KEY (só se TEMP_FONTE=owm), TEMP_FONTE (openmeteo | owm).
Só usa a biblioteca padrão do Python.
"""
import argparse
import csv
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FUSO = timezone(timedelta(hours=-3))  # Paraíba: UTC-3, sem horário de verão
AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "saida")

# Cidades monitoradas: arquivo de pontos, arquivo de saída e prefixo do histórico.
CIDADES = {
    "campina": {"pontos": "pontos.json", "latest": "conforto_latest.json", "hist": "hist"},
    "recife": {"pontos": "pontos_recife.json", "latest": "conforto_recife_latest.json", "hist": "hist_recife"},
}

T_REFERENCIA = 25.0   # °C, neutralidade (TCC, Eq. 2)
PESO = 8.0            # fator de ponderação empírico (TCC, Eq. 2)
FT_MIN, FT_MAX = 1.0, 1.7  # intervalo do Fator de Trânsito (TCC, item 3.3)
LIMIAR_INICIAL, LIMIAR_ELEVADO = 45.0, 75.0  # TCC, Quadro 1


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "climando-conforto/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def temperatura_openmeteo(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m", "timezone": "America/Fortaleza",
    })
    d = get_json("https://api.open-meteo.com/v1/forecast?" + q)
    return float(d["current"]["temperature_2m"])


def temperatura_owm(lat, lon, chave):
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "units": "metric", "appid": chave})
    d = get_json("https://api.openweathermap.org/data/2.5/weather?" + q)
    return float(d["main"]["temp"])


def trafego_tomtom(lat, lon, chave):
    q = urllib.parse.urlencode({"key": chave, "point": f"{lat},{lon}", "unit": "KMPH"})
    d = get_json("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?" + q)
    f = d["flowSegmentData"]
    return {
        "vel_atual": float(f["currentSpeed"]),
        "vel_livre": float(f["freeFlowSpeed"]),
        "confianca": f.get("confidence"),
        "via_fechada": bool(f.get("roadClosure", False)),
    }


def fator_transito(vel_atual, vel_livre):
    """Razão vel_livre/vel_atual limitada a [1,0 ; 1,7].

    ATENÇÃO: o TCC informa o intervalo, mas não a função que leva a velocidade ao fator.
    Esta razão é uma suposição; confira com o código do app original e ajuste aqui.
    """
    if not vel_atual or vel_atual <= 0 or not vel_livre:
        return None
    return round(min(FT_MAX, max(FT_MIN, vel_livre / vel_atual)), 2)


def calcular_idte(temp_ar, efeito_ilha, ft):
    temp_ajustada = temp_ar + efeito_ilha          # Eq. 1 do TCC
    bruto = (temp_ajustada - T_REFERENCIA) * PESO * ft  # Eq. 2 do TCC
    return round(temp_ajustada, 1), round(bruto, 1), round(min(100.0, max(0.0, bruto)), 1)


def classificar(valor):
    if valor is None:
        return "sem_indice"
    if valor < LIMIAR_INICIAL:
        return "conforto"
    if valor < LIMIAR_ELEVADO:
        return "desconforto_inicial"
    return "desconforto_elevado"


def coletar_ponto(p, args, chave_tt, chave_owm):
    out = {
        "id": p["id"], "nome": p["nome"], "categoria": p.get("categoria"),
        "lat": p.get("lat"), "lon": p.get("lon"), "efeito_ilha": p.get("efeito_ilha"),
        "temp_ar": None, "temp_ajustada": None, "vel_atual": None, "vel_livre": None,
        "fator_transito": None, "confianca": None, "idte_bruto": None, "idte": None,
        "classe": "sem_indice", "status": "ok", "erros": [],
    }
    if args.simular:
        out["lat"] = out["lat"] or -7.23
        out["lon"] = out["lon"] or -35.88
        t = round(random.uniform(24, 33), 1)
        livre = random.choice([40, 50, 60])
        atual = round(livre / random.uniform(1.0, 1.9), 0)
        tt = {"vel_atual": atual, "vel_livre": float(livre), "confianca": 0.9, "via_fechada": False}
    else:
        if out["lat"] is None or out["lon"] is None:
            out["status"] = "sem_coordenadas"
            out["erros"].append("lat/lon ausentes em pontos.json")
            return out
        t, tt = None, None
        try:
            fonte = os.environ.get("TEMP_FONTE", "openmeteo").lower()
            t = (temperatura_owm(out["lat"], out["lon"], chave_owm) if fonte == "owm"
                 else temperatura_openmeteo(out["lat"], out["lon"]))
        except Exception as e:  # noqa: BLE001 - registra e segue para o próximo ponto
            out["erros"].append(f"temperatura: {e}")
        try:
            tt = trafego_tomtom(out["lat"], out["lon"], chave_tt)
        except Exception as e:  # noqa: BLE001
            out["erros"].append(f"trafego: {e}")

    out["temp_ar"] = t
    if tt:
        out["vel_atual"], out["vel_livre"] = tt["vel_atual"], tt["vel_livre"]
        out["confianca"] = tt["confianca"]
        out["fator_transito"] = fator_transito(tt["vel_atual"], tt["vel_livre"])

    ef, ft = out["efeito_ilha"], out["fator_transito"]
    if t is not None and ef is not None and ft is not None:
        out["temp_ajustada"], out["idte_bruto"], out["idte"] = calcular_idte(t, ef, ft)
    out["classe"] = classificar(out["idte"])
    if out["erros"]:
        out["status"] = "parcial" if (t is not None or tt) else "falha"
    return out


def distancia_m(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt
    a = (sin(radians(lat2 - lat1) / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * asin(sqrt(a))


def verificar(pontos, chave):
    """Mostra, para cada ponto, o trecho de via que a TomTom associou (uma consulta por ponto)."""
    print("ponto | distância ao trecho | classe da via | confiança | vel. atual/livre")
    for p in pontos:
        if p.get("lat") is None:
            print(f"{p['id']}: sem coordenadas")
            continue
        q = urllib.parse.urlencode({"key": chave, "point": f"{p['lat']},{p['lon']}", "unit": "KMPH"})
        try:
            f = get_json("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?" + q)["flowSegmentData"]
            vs = f.get("coordinates", {}).get("coordinate", [])
            dmin = min((distancia_m(p["lat"], p["lon"], v["latitude"], v["longitude"]) for v in vs), default=None)
            print(f"{p['id']}: {'?' if dmin is None else round(dmin)} m | {f.get('frc')} | {f.get('confidence')} | "
                  f"{f.get('currentSpeed')}/{f.get('freeFlowSpeed')} km/h")
        except Exception as e:  # noqa: BLE001
            print(f"{p['id']}: erro {e}")


def processar_cidade(chave, cfg, args, chave_tt, chave_owm, agora):
    """Coleta uma cidade, grava saida/<latest> e o CSV do dia. Devolve (pontos ok, total)."""
    with open(os.path.join(AQUI, cfg["pontos"]), encoding="utf-8") as f:
        pontos = json.load(f)["pontos"]
    resultados = [coletar_ponto(p, args, chave_tt, chave_owm) for p in pontos]
    ok = sum(1 for r in resultados if r["status"] == "ok")
    if ok == 0 and not args.simular:
        print(f"{chave}: nenhum ponto válido; arquivo não atualizado")
        for r in resultados[:3]:
            print(f"  - {r['id']}: {'; '.join(r['erros'])}")
        return 0, len(resultados)

    os.makedirs(SAIDA, exist_ok=True)
    doc = {
        "versao": 1,
        "cidade": chave,
        "gerado_em": agora.isoformat(timespec="seconds"),
        "simulado": bool(args.simular),
        "pontos": resultados,
    }
    with open(os.path.join(SAIDA, cfg["latest"]), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    caminho_csv = os.path.join(SAIDA, f"{cfg['hist']}_{agora:%Y-%m-%d}.csv")
    with open(caminho_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["data_hora", "id", "temp_ar", "vel_atual", "vel_livre",
                    "fator_transito", "idte_bruto", "idte", "classe", "status"])
        for r in resultados:
            w.writerow([agora.isoformat(timespec="seconds"), r["id"], r["temp_ar"], r["vel_atual"],
                        r["vel_livre"], r["fator_transito"], r["idte_bruto"], r["idte"],
                        r["classe"], r["status"]])

    print(f"{agora:%H:%M} {chave}: {ok}/{len(resultados)} pontos ok")
    for r in resultados:
        if r["erros"]:
            print(f"  - {r['id']}: {'; '.join(r['erros'])}")
    return ok, len(resultados)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cidade", default="todas", choices=["todas"] + list(CIDADES),
                    help="cidade a coletar (padrão: todas)")
    ap.add_argument("--verificar", action="store_true", help="mostra o trecho de via associado a cada ponto e sai")
    ap.add_argument("--simular", action="store_true", help="não chama APIs; gera valores fictícios para teste")
    args = ap.parse_args()

    chave_tt = os.environ.get("TOMTOM_KEY", "")
    chave_owm = os.environ.get("OWM_KEY", "")
    if not args.simular and not chave_tt:
        sys.exit("Defina TOMTOM_KEY (ou use --simular).")
    if os.environ.get("TEMP_FONTE", "openmeteo").lower() == "owm" and not chave_owm and not args.simular:
        sys.exit("TEMP_FONTE=owm exige OWM_KEY.")

    escolhidas = list(CIDADES) if args.cidade == "todas" else [args.cidade]

    if args.verificar:
        for c in escolhidas:
            print(f"== {c} ==")
            with open(os.path.join(AQUI, CIDADES[c]["pontos"]), encoding="utf-8") as f:
                verificar(json.load(f)["pontos"], chave_tt)
        return

    agora = datetime.now(FUSO)
    total_ok = 0
    for c in escolhidas:
        ok, _ = processar_cidade(c, CIDADES[c], args, chave_tt, chave_owm, agora)
        total_ok += ok
    if total_ok == 0 and not args.simular:
        sys.exit(1)  # nada válido em nenhuma cidade: não publica


if __name__ == "__main__":
    main()
