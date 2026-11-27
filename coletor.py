#!/usr/bin/env python3
"""TermoTrânsito: coletor do monitor de conforto térmico no trânsito (IDTE_V2).

Para cada ponto de pontos*.json consulta
  - temperatura, umidade relativa e vento a 10 m (Open-Meteo; OpenWeatherMap se TEMP_FONTE=owm)
  - velocidade atual e de fluxo livre do trecho mais próximo (TomTom Flow Segment Data)
e calcula o IDTE_V2 (Índice de Desconforto Térmico Estimado, versão 2):

  0. Temperatura ajustada: T_aj = T + K_LST * dt_lst, em que dt_lst é o contraste mediano de temperatura de
     superfície do ponto (Landsat, pontos*.json) em relação à referência (Embrapa; Parque da Jaqueira) e K_LST
     (padrão 0,25) é o fator que converte contraste de superfície em contraste do ar. K_LST NÃO foi calibrado.
  0. Temperatura ajustada: T_aj = T + K_LST * dt_lst, em que dt_lst é o contraste mediano de temperatura de
     superfície do ponto (Landsat, em pontos*.json) em relação à referência (Embrapa; Parque da Jaqueira) e
     K_LST (padrão 0,25; variável de ambiente K_LST) converte contraste de superfície em contraste do ar.
     K_LST NÃO foi calibrado. Sem contraste válido (ou na própria referência), dt_lst = 0.
  1. Temperatura aparente (Steadman, 1994, sem radiação):
        e  = (UR/100) * 6,105 * exp(17,27 T / (237,7 + T))         [hPa]
        TA = T + 0,33 e - 0,70 w - 4,00                             [°C; w = vento a 10 m, m/s]
  2. Carga térmica:      CT = max(0, TA - T_REF)                      [°C]   (T_REF = 26 °C)
  3. Tempo no trecho:    t  = 60 / max(v_atual, V_MIN)                [min/km]
  4. Exposição térmica:  E  = CT * t                                  [°C·min/km]
  5. IDTE_V2 = percentil de E na janela móvel das últimas N_JANELA coletas do próprio ponto (0-100).
     Só é exibido quando o ponto já tem N_MIN coletas na janela ("em formação" antes disso).

A velocidade usada no passo 3 é a mediana das últimas N_SUAVE leituras do ponto (reduz o ruído do tráfego).

Classe relativa (histórico do ponto): baixa (< P50), moderada (P50 a < P90), alta (>= P90).
Trava: "alta" exige também carga térmica > 0; sem ela, o ponto fica em "moderada".

Classe absoluta (independente do histórico): índice de calor do NWS (Rothfusz, 1990) aplicado a T e UR,
nas faixas do NWS: ameno (< 26,7 °C), cautela, cautela extrema, perigo, perigo extremo.
O índice de calor não considera vento nem radiação e supõe sombra.

Saídas (pasta saida/), por cidade:
  conforto_latest.json / conforto_recife_latest.json   estado atual, lido pela página
  hist2_AAAA-MM-DD.csv / hist2_recife_AAAA-MM-DD.csv   linhas desta coleta (publicar_ftp.py junta)
  percentis_<cidade>.json                              janela móvel de E por ponto (estado do percentil)

O estado dos percentis é baixado do servidor antes da coleta (publicar_ftp.py --baixar-estado)
e enviado de volta depois. Só usa a biblioteca padrão do Python.
Variáveis de ambiente: TOMTOM_KEY (exceto com --simular), OWM_KEY (se TEMP_FONTE=owm), TEMP_FONTE.
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FUSO = timezone(timedelta(hours=-3))  # UTC-3, sem horário de verão
AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "saida")

CIDADES = {
    "campina": {"pontos": "pontos.json", "latest": "conforto_latest.json", "hist": "hist2",
                "estado": "percentis_campina.json"},
    "recife": {"pontos": "pontos_recife.json", "latest": "conforto_recife_latest.json", "hist": "hist2_recife",
               "estado": "percentis_recife.json"},
}

K_LST = float(os.environ.get("K_LST", "0.25"))  # fator superfície -> ar (não calibrado)
T_REF = 26.0      # °C: limite superior da faixa "sem estresse térmico" do UTCI (BRÖDE et al., 2012); referência convencional
V_MIN = 5.0       # km/h: piso da velocidade, evita tempo de percurso explosivo em trânsito parado
N_JANELA = 1000   # coletas guardadas por ponto (~17 dias a 60 coletas/dia)
N_MIN = 100       # coletas mínimas para exibir o percentil (~2 dias)
P_MEDIANA, P_ALTO = 50.0, 90.0
N_SUAVE = 3       # leituras de velocidade na mediana móvel
FAIXAS_IC = [(26.7, "ameno"), (32.2, "cautela"), (39.4, "cautela_extrema"), (51.7, "perigo")]  # °C; acima: perigo_extremo


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "termotransito/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def clima_openmeteo(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "America/Fortaleza", "wind_speed_unit": "ms",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
    })
    c = get_json("https://api.open-meteo.com/v1/forecast?" + q)["current"]
    return float(c["temperature_2m"]), float(c["relative_humidity_2m"]), float(c["wind_speed_10m"])


def clima_owm(lat, lon, chave):
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "units": "metric", "appid": chave})
    d = get_json("https://api.openweathermap.org/data/2.5/weather?" + q)
    return float(d["main"]["temp"]), float(d["main"]["humidity"]), float(d["wind"]["speed"])


def trafego_tomtom(lat, lon, chave):
    q = urllib.parse.urlencode({"key": chave, "point": f"{lat},{lon}", "unit": "KMPH"})
    d = get_json("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?" + q)
    f = d["flowSegmentData"]
    return {"vel_atual": float(f["currentSpeed"]), "vel_livre": float(f["freeFlowSpeed"]),
            "confianca": f.get("confidence")}


# ---------- cálculo do IDTE_V2 ----------
def temperatura_aparente(t, ur, vento):
    e = (ur / 100.0) * 6.105 * math.exp(17.27 * t / (237.7 + t))
    return t + 0.33 * e - 0.70 * vento - 4.00


def exposicao(ta, vel_atual, vel_livre):
    """Devolve (carga térmica °C, tempo min/km, atraso min/km, exposição °C·min/km)."""
    ct = max(0.0, ta - T_REF)
    t_km = 60.0 / max(vel_atual, V_MIN)
    atraso = max(0.0, t_km - 60.0 / max(vel_livre, V_MIN)) if vel_livre else None
    return ct, t_km, atraso, ct * t_km


def percentil(valor, janela):
    """Posição percentual (0-100) de valor entre os da janela; empates contam metade."""
    n = len(janela)
    abaixo = sum(1 for x in janela if x < valor)
    iguais = sum(1 for x in janela if x == valor)
    return 100.0 * (abaixo + 0.5 * iguais) / n


def classificar(p, carga):
    if p is None:
        return "em_formacao"
    if p < P_MEDIANA:
        return "baixa"
    return "alta" if (p >= P_ALTO and carga > 0) else "moderada"


def indice_calor(t, ur):
    """Índice de calor do NWS (Rothfusz, 1990), em °C. Devolve (valor, classe absoluta)."""
    tf = t * 9 / 5 + 32
    ic = 0.5 * (tf + 61.0 + (tf - 68.0) * 1.2 + ur * 0.094)
    if (ic + tf) / 2 >= 80:
        ic = (-42.379 + 2.04901523 * tf + 10.14333127 * ur - 0.22475541 * tf * ur - 0.00683783 * tf ** 2
              - 0.05481717 * ur ** 2 + 0.00122874 * tf ** 2 * ur + 0.00085282 * tf * ur ** 2
              - 0.00000199 * tf ** 2 * ur ** 2)
        if ur < 13 and 80 <= tf <= 112:
            ic -= ((13 - ur) / 4) * math.sqrt((17 - abs(tf - 95)) / 17)
        elif ur > 85 and 80 <= tf <= 87:
            ic += ((ur - 85) / 10) * ((87 - tf) / 5)
    ic = (ic - 32) * 5 / 9
    for limite, nome in FAIXAS_IC:
        if ic < limite:
            return ic, nome
    return ic, "perigo_extremo"


def mediana(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


# ---------- estado dos percentis ----------
def carregar_estado(cfg):
    caminho = os.path.join(SAIDA, cfg["estado"])
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                d = json.load(f)
                if d.get("versao") != 3:  # exposição passou a usar a temperatura ajustada: janela antiga não é comparável
                    print(f"aviso: {cfg['estado']} é de versão anterior; recomeçando a janela de percentis")
                    return {}, {}
                return d.get("pontos", {}), d.get("velocidades", {})
        except (OSError, ValueError):
            print(f"aviso: {cfg['estado']} ilegível; recomeçando o histórico de percentis")
    return {}, {}


def salvar_estado(cfg, estado, vels, agora):
    os.makedirs(SAIDA, exist_ok=True)
    doc = {"versao": 3, "atualizado_em": agora.isoformat(timespec="seconds"),
           "n_janela": N_JANELA, "pontos": estado, "velocidades": vels}
    with open(os.path.join(SAIDA, cfg["estado"]), "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))


def coletar_ponto(p, args, chave_tt, chave_owm, estado, vels):
    out = {
        "id": p["id"], "nome": p["nome"], "categoria": p.get("categoria"),
        "lat": p.get("lat"), "lon": p.get("lon"),
        "temp_ar": None, "temp_ajustada": None, "dt_lst": p.get("dt_lst", 0.0), "dt_lst_n": p.get("dt_lst_n", 0), "ajuste_lst": None, "umidade": None, "vento": None, "temp_aparente": None,
        "indice_calor": None, "classe_abs": None, "vel_suave": None,
        "vel_atual": None, "vel_livre": None, "atraso_min_km": None, "carga_termica": None,
        "exposicao": None, "idte_v2": None, "classe": "em_formacao", "n_amostras": 0,
        "confianca": None, "status": "ok", "erros": [],
    }
    clima = tt = None
    if args.simular:
        out["lat"] = out["lat"] or -7.23
        out["lon"] = out["lon"] or -35.88
        clima = (round(random.uniform(24, 33), 1), round(random.uniform(50, 90)), round(random.uniform(0.5, 5), 1))
        livre = random.choice([40, 50, 60])
        tt = {"vel_atual": float(round(livre / random.uniform(1.0, 2.5))), "vel_livre": float(livre), "confianca": 0.9}
    else:
        if out["lat"] is None or out["lon"] is None:
            out["status"] = "sem_coordenadas"
            out["erros"].append("lat/lon ausentes")
            return out
        try:
            fonte = os.environ.get("TEMP_FONTE", "openmeteo").lower()
            clima = (clima_owm(out["lat"], out["lon"], chave_owm) if fonte == "owm"
                     else clima_openmeteo(out["lat"], out["lon"]))
        except Exception as e:  # noqa: BLE001
            out["erros"].append(f"clima: {e}")
        try:
            tt = trafego_tomtom(out["lat"], out["lon"], chave_tt)
        except Exception as e:  # noqa: BLE001
            out["erros"].append(f"trafego: {e}")

    if clima:
        out["temp_ar"], out["umidade"], out["vento"] = clima
        out["ajuste_lst"] = round(K_LST * (p.get("dt_lst") or 0.0), 2)
        t_aj = clima[0] + out["ajuste_lst"]
        out["temp_ajustada"] = round(t_aj, 1)
        out["temp_aparente"] = round(temperatura_aparente(t_aj, clima[1], clima[2]), 1)
        ic, out["classe_abs"] = indice_calor(t_aj, clima[1])
        out["indice_calor"] = round(ic, 1)
    if tt:
        out["vel_atual"], out["vel_livre"], out["confianca"] = tt["vel_atual"], tt["vel_livre"], tt["confianca"]

    janela = estado.setdefault(p["id"], [])
    out["n_amostras"] = len(janela)
    if clima and tt:
        recentes = vels.setdefault(p["id"], [])
        recentes.append(tt["vel_atual"])
        del recentes[:-N_SUAVE]
        out["vel_suave"] = round(mediana(recentes), 1)
        ct, _, atraso, e = exposicao(out["temp_aparente"], out["vel_suave"], tt["vel_livre"])
        out["carga_termica"] = round(ct, 1)
        out["atraso_min_km"] = None if atraso is None else round(atraso, 2)
        out["exposicao"] = round(e, 1)
        if len(janela) >= N_MIN:  # compara com o passado antes de acrescentar o valor atual
            out["idte_v2"] = round(percentil(out["exposicao"], janela), 1)
        out["classe"] = classificar(out["idte_v2"], ct)
        janela.append(out["exposicao"])
        del janela[:-N_JANELA]
    elif not out["erros"]:
        out["erros"].append("dados incompletos")
    if out["erros"]:
        out["status"] = "parcial" if (clima or tt) else "falha"
    return out


def distancia_m(lat1, lon1, lat2, lon2):
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


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
    with open(os.path.join(AQUI, cfg["pontos"]), encoding="utf-8") as f:
        pontos = json.load(f)["pontos"]
    estado, vels = carregar_estado(cfg)
    resultados = [coletar_ponto(p, args, chave_tt, chave_owm, estado, vels) for p in pontos]
    ok = sum(1 for r in resultados if r["status"] == "ok")
    if ok == 0 and not args.simular:
        print(f"{chave}: nenhum ponto válido; arquivos não atualizados")
        for r in resultados[:3]:
            print(f"  - {r['id']}: {'; '.join(r['erros'])}")
        return 0, len(resultados)

    os.makedirs(SAIDA, exist_ok=True)
    doc = {"versao": 3, "k_lst": K_LST, "cidade": chave, "gerado_em": agora.isoformat(timespec="seconds"),
           "simulado": bool(args.simular), "n_min": N_MIN, "n_janela": N_JANELA, "pontos": resultados}
    with open(os.path.join(SAIDA, cfg["latest"]), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    salvar_estado(cfg, estado, vels, agora)

    with open(os.path.join(SAIDA, f"{cfg['hist']}_{agora:%Y-%m-%d}.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["data_hora", "id", "temp_ar", "ajuste_lst", "temp_ajustada", "umidade", "vento_ms", "temp_aparente", "indice_calor", "classe_abs", "vel_atual", "vel_suave", "vel_livre",
                    "atraso_min_km", "carga_termica", "exposicao", "idte_v2", "classe", "n_amostras", "status"])
        for r in resultados:
            w.writerow([agora.isoformat(timespec="seconds"), r["id"], r["temp_ar"], r["ajuste_lst"], r["temp_ajustada"], r["umidade"], r["vento"],
                        r["temp_aparente"], r["indice_calor"], r["classe_abs"], r["vel_atual"], r["vel_suave"], r["vel_livre"], r["atraso_min_km"], r["carga_termica"],
                        r["exposicao"], r["idte_v2"], r["classe"], r["n_amostras"], r["status"]])

    print(f"{agora:%H:%M} {chave}: {ok}/{len(resultados)} pontos ok")
    for r in resultados:
        if r["erros"]:
            print(f"  - {r['id']}: {'; '.join(r['erros'])}")
    return ok, len(resultados)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cidade", default="todas", choices=["todas"] + list(CIDADES))
    ap.add_argument("--verificar", action="store_true", help="mostra o trecho de via associado a cada ponto e sai")
    ap.add_argument("--simular", action="store_true", help="não chama APIs; gera valores fictícios para teste")
    args = ap.parse_args()

    chave_tt, chave_owm = os.environ.get("TOMTOM_KEY", ""), os.environ.get("OWM_KEY", "")
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
    total_ok = sum(processar_cidade(c, CIDADES[c], args, chave_tt, chave_owm, agora)[0] for c in escolhidas)
    if total_ok == 0 and not args.simular:
        sys.exit(1)


if __name__ == "__main__":
    main()
