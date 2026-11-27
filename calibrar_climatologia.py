#!/usr/bin/env python3
"""Calibra as faixas de conforto do TermoTrânsito com a climatologia local (Open-Meteo, reanálise ERA5-Land/ERA5).

Para cada cidade, baixa a série horária de temperatura a 2 m, umidade relativa e vento a 10 m de uma coordenada
de referência (Embrapa, em Campina Grande; UFRPE, no Recife) entre ANO_INI e ANO_FIM, mantém só as horas da
janela de coleta do monitor (05h-19h, horário local), calcula a temperatura aparente (a mesma fórmula do coletor)
e grava em climatologia.json os cortes P25, P50, P75 e P95 dessa distribuição.

O coletor classifica cada ponto comparando a sua temperatura aparente (já com o ajuste do Landsat) com esses cortes:
  confortável (< P25), desconforto leve (P25 a < P50), moderado (P50 a < P75), alto (P75 a < P95), extremo (>= P95).

Uso:  python calibrar_climatologia.py            (precisa de internet; só biblioteca padrão)
      python calibrar_climatologia.py --teste    (série fictícia, para testar sem rede)
"""
import json
import math
import os
import random
import sys
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
ANO_INI, ANO_FIM = 2015, 2025
HORA_INI, HORA_FIM = 5, 19          # janela de coleta do monitor, inclusive
PERCENTIS = (25, 50, 75, 95)
CIDADES = {
    "campina": {"referencia": "Embrapa (Centenário)", "lat": -7.22559, "lon": -35.9053859},
    "recife": {"referencia": "UFRPE (Dois Irmãos)", "lat": -8.0175579, "lon": -34.9492827},
}


def temperatura_aparente(t, ur, vento):
    e = (ur / 100.0) * 6.105 * math.exp(17.27 * t / (237.7 + t))
    return t + 0.33 * e - 0.70 * vento - 4.00


def percentil(ordenado, p):
    k = (len(ordenado) - 1) * p / 100.0
    i = int(math.floor(k))
    j = min(i + 1, len(ordenado) - 1)
    return ordenado[i] + (ordenado[j] - ordenado[i]) * (k - i)


def baixar_ano(lat, lon, ano):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": f"{ano}-01-01", "end_date": f"{ano}-12-31",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m", "wind_speed_unit": "ms",
        "timezone": "America/Fortaleza"})
    req = urllib.request.Request("https://archive-api.open-meteo.com/v1/archive?" + q,
                                 headers={"User-Agent": "termotransito/2.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))["hourly"]


def serie_ta(h):
    """Temperatura aparente das horas dentro da janela de coleta."""
    out = []
    for hora, t, ur, w in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"], h["wind_speed_10m"]):
        if None in (t, ur, w):
            continue
        if HORA_INI <= int(hora[11:13]) <= HORA_FIM:
            out.append(temperatura_aparente(t, ur, w))
    return out


def serie_teste():
    return [temperatura_aparente(random.gauss(28, 2.5), random.uniform(50, 90), random.uniform(0.5, 5))
            for _ in range(11 * 365 * 15)]


def main():
    teste = "--teste" in sys.argv
    doc = {"versao": 1, "periodo": f"{ANO_INI}-{ANO_FIM}", "horas": f"{HORA_INI:02d}-{HORA_FIM:02d}",
           "variavel": "temperatura aparente (Steadman, sem radiação)", "cidades": {}}
    for chave, c in CIDADES.items():
        if teste:
            ta = serie_teste()
        else:
            ta = []
            for ano in range(ANO_INI, ANO_FIM + 1):
                ta += serie_ta(baixar_ano(c["lat"], c["lon"], ano))
                print(f"{chave} {ano}: {len(ta)} horas acumuladas", flush=True)
        ta.sort()
        cortes = {f"p{p}": round(percentil(ta, p), 2) for p in PERCENTIS}
        doc["cidades"][chave] = {"referencia": c["referencia"], "lat": c["lat"], "lon": c["lon"],
                                 "n_horas": len(ta), "cortes": cortes}
        print(chave, len(ta), "horas; cortes:", cortes)
    if teste:
        print("modo teste: nada gravado")
        return
    with open(os.path.join(AQUI, "climatologia.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("climatologia.json gravado")


if __name__ == "__main__":
    main()
