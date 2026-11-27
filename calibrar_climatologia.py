#!/usr/bin/env python3
"""Calibra as faixas de conforto do TermoTrânsito com a climatologia local (Open-Meteo, reanálise ERA5-Land/ERA5).

Para cada cidade, baixa a série horária de temperatura a 2 m, umidade relativa e vento a 10 m de uma coordenada
de referência (Embrapa, em Campina Grande; UFRPE, no Recife) entre ANO_INI e ANO_FIM, mantém só as horas da
janela de coleta do monitor (05h-19h, horário local), calcula a temperatura aparente (a mesma fórmula do coletor)
e grava em climatologia.json os cortes P25, P50, P75 e P95 dessa distribuição para cada combinação de mês e
hora (cortes_mh: mês 1-12 -> hora 5-19), além dos cortes gerais (todas as horas e meses), usados só como reserva.

O coletor classifica cada ponto comparando a sua temperatura aparente (já com o ajuste do Landsat) com os cortes
do mês e da hora da coleta:
  confortável (< P25), desconforto leve (P25 a < P50), moderado (P50 a < P75), alto (P75 a < P95), extremo (>= P95).

Uso:  python calibrar_climatologia.py            (precisa de internet; só biblioteca padrão)
      python calibrar_climatologia.py --teste    (série fictícia, para testar sem rede)
"""
import json
import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
ANO_INI, ANO_FIM = 2015, 2025
HORA_INI, HORA_FIM = 5, 19          # janela de coleta do monitor, inclusive
PERCENTIS = (25, 50, 75, 95)
TENTATIVAS = 6     # por ano, com espera crescente (15, 30, 60, 120, 240 s)
PAUSA = 3          # segundos entre requisições
CIDADES = {
    "campina": {"referencia": "Embrapa (Centenário)", "lat": -7.22559, "lon": -35.9053859},
    "recife": {"referencia": "UFRPE (Dois Irmãos)", "lat": -8.0175579, "lon": -34.9492827},
}


IDT_LIMITES = [21.0, 24.0, 27.0, 29.0, 32.0]   # Giles et al. (1990)
IDT_CLASSES = ["sem_desconforto", "menos_metade", "mais_metade", "maioria", "estresse_forte", "emergencia"]


def idt_thom(t, ur):
    """Índice de Desconforto de Thom (1959), °C."""
    return t - (0.55 - 0.0055 * ur) * (t - 14.5)


def classe_idt(v):
    for lim, nome in zip(IDT_LIMITES, IDT_CLASSES):
        if v < lim:
            return nome
    return IDT_CLASSES[-1]


def freq_idt(valores):
    n = len(valores)
    cont = {c: 0 for c in IDT_CLASSES}
    for v in valores:
        cont[classe_idt(v)] += 1
    return {c: round(100.0 * k / n, 1) for c, k in cont.items()}


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
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                dados = json.loads(r.read().decode("utf-8"))["hourly"]
            time.sleep(PAUSA)  # respeita o limite de requisições da API gratuita
            return dados
        except Exception as e:  # noqa: BLE001  (timeout, 429, erro de rede)
            if tentativa == TENTATIVAS:
                raise
            espera = 15 * 2 ** (tentativa - 1)
            print(f"  {ano}: falhou ({e}); nova tentativa em {espera} s", flush=True)
            time.sleep(espera)


def serie_ta(h):
    """Lista de (mês, hora, temperatura aparente) das horas dentro da janela de coleta."""
    out = []
    for hora, t, ur, w in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"], h["wind_speed_10m"]):
        if None in (t, ur, w):
            continue
        hh = int(hora[11:13])
        if HORA_INI <= hh <= HORA_FIM:
            out.append((int(hora[5:7]), hh, temperatura_aparente(t, ur, w), idt_thom(t, ur)))
    return out


def serie_teste():
    out = []
    for _ in range(11 * 365):
        m = random.randint(1, 12)
        for hh in range(HORA_INI, HORA_FIM + 1):
            base = 24 + 6 * math.sin(math.pi * (hh - 5) / 14)
            t, ur = random.gauss(base, 1.5), random.uniform(45, 90)
            out.append((m, hh, temperatura_aparente(t, ur, random.uniform(0.5, 5)), idt_thom(t, ur)))
    return out


def cortes_de(valores):
    v = sorted(valores)
    return {f"p{p}": round(percentil(v, p), 2) for p in PERCENTIS}


def main():
    teste = "--teste" in sys.argv
    doc = {"versao": 3, "periodo": f"{ANO_INI}-{ANO_FIM}", "horas": f"{HORA_INI:02d}-{HORA_FIM:02d}",
           "variavel": "temperatura aparente (Steadman, sem radiação)", "cidades": {}}
    for chave, c in CIDADES.items():
        if teste:
            ta = serie_teste()
        else:
            ta = []
            for ano in range(ANO_INI, ANO_FIM + 1):
                ta += serie_ta(baixar_ano(c["lat"], c["lon"], ano))
                print(f"{chave} {ano}: {len(ta)} horas acumuladas", flush=True)
        grupos = {}
        for m, hh, v, _ in ta:
            grupos.setdefault(m, {}).setdefault(hh, []).append(v)
        cortes_mh = {str(m): {str(hh): cortes_de(vs) for hh, vs in sorted(hs.items())} for m, hs in sorted(grupos.items())}
        n_min = min(len(vs) for hs in grupos.values() for vs in hs.values())
        cortes = cortes_de([x[2] for x in ta])
        idt_geral = freq_idt([x[3] for x in ta])
        idt_mes = {str(m): freq_idt([x[3] for x in ta if x[0] == m]) for m in range(1, 13)}
        idt_hora = {str(h): freq_idt([x[3] for x in ta if x[1] == h]) for h in range(HORA_INI, HORA_FIM + 1)}
        idt_med15 = {str(m): round(percentil(sorted(x[3] for x in ta if x[0] == m and x[1] == 15), 50), 1) for m in range(1, 13)}
        doc["cidades"][chave] = {"referencia": c["referencia"], "lat": c["lat"], "lon": c["lon"],
                                 "n_horas": len(ta), "n_min_por_celula": n_min, "cortes": cortes, "cortes_mh": cortes_mh,
                                 "idt_freq_geral": idt_geral, "idt_freq_mes": idt_mes, "idt_freq_hora": idt_hora,
                                 "idt_mediana_15h": idt_med15}
        print(f"{chave}: {len(ta)} horas; mínimo de {n_min} horas por combinação mês-hora; cortes gerais: {cortes}")
        print(f"{chave}: cortes às 9h e às 15h, por mês (P25 / P50 / P75 / P95):")
        for m in sorted(grupos):
            c9, c15 = cortes_mh[str(m)].get("9"), cortes_mh[str(m)].get("15")
            f = lambda c: " / ".join(f"{c[k]:.1f}" for k in ("p25", "p50", "p75", "p95")) if c else "-"
            print(f"  mês {m:2d}   9h: {f(c9)}   15h: {f(c15)}")
        cab = "  <21 / 21-24 / 24-27 / 27-29 / 29-32 / >=32"
        g = lambda d: " / ".join(f"{d[c]:5.1f}" for c in IDT_CLASSES)
        print(f"{chave}: IDT de Thom, % de horas por classe de Giles (5h-19h, 2015-2025)\n{cab}")
        print(f"  geral   {g(idt_geral)}")
        for m in range(1, 13):
            print(f"  mês {m:2d}  {g(idt_mes[str(m)])}   mediana às 15h: {idt_med15[str(m)]}")
        for h in (6, 9, 12, 15, 18):
            print(f"  {h:2d}h     {g(idt_hora[str(h)])}")
    if teste:
        print("modo teste: nada gravado")
        return
    with open(os.path.join(AQUI, "climatologia.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("climatologia.json gravado")


if __name__ == "__main__":
    main()
