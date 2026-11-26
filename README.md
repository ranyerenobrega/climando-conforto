# TermoTrânsito (IDTE_V2)

Monitor de conforto térmico no trânsito, em tempo quase real, para Campina Grande (12 pontos) e Recife (14 pontos). Grupo TROPOCLIMA, UFCG.

## Como funciona

GitHub Actions roda a cada 15 min (05h–19h, horário de Brasília) e executa, em ordem:

1. `publicar_ftp.py --baixar-estado`: traz do HostGator os arquivos `percentis_*.json` (janela móvel de exposição de cada ponto).
2. `coletor.py`: consulta Open-Meteo (temperatura, umidade relativa e vento a 10 m) e TomTom (velocidade atual e de fluxo livre) e calcula o IDTE_V2.
3. `publicar_ftp.py`: envia `conforto_latest.json`, `conforto_recife_latest.json`, os `percentis_*.json` e acrescenta as linhas do dia em `hist2_*.csv` (todos em `public_html/dados/`).

A página `conforto.html` lê os JSON; nenhuma chave de API fica no navegador. Segredos e variáveis do GitHub são os mesmos de antes (`TOMTOM_KEY`, `FTP_HOST`, `FTP_USER`, `FTP_PASS`; opcionais `OWM_KEY`, `TEMP_FONTE`, `FTP_DIR`, `FTP_TLS`). Nada novo a configurar.

## Cálculo do IDTE_V2

1. Pressão de vapor: `e = (UR/100) · 6,105 · exp(17,27·T / (237,7 + T))` (hPa).
2. Temperatura aparente (Steadman, sem radiação): `TA = T + 0,33·e − 0,70·w − 4,00` (w = vento a 10 m, m/s).
3. Carga térmica: `CT = max(0, TA − 26)` (°C).
4. Tempo por quilômetro: `t = 60 / max(v_atual, 5)` (min/km).
5. Exposição: `E = CT · t` (°C·min/km).
6. **IDTE_V2** = percentil de `E` entre as últimas 1000 coletas do próprio ponto (0 a 100). Aparece só com pelo menos 100 coletas ("em formação" antes). Classes: baixa (< P50), moderada (P50 a < P90), alta (≥ P90).

Parâmetros no topo de `coletor.py`: `T_REF`, `V_MIN`, `N_JANELA`, `N_MIN`, `P_MEDIANA`, `P_ALTO`. São convenções, ainda sem validação de campo. O percentil é relativo ao histórico de cada ponto e não permite comparar pontos entre si; para isso use a exposição.

## Partida a frio

O histórico antigo (`hist_*.csv`) não tem umidade nem vento e não é usado. Os percentis começam do zero no primeiro dia; com 60 coletas por dia, cada ponto passa a exibir o índice em cerca de 2 dias. A janela é guardada em `percentis_*.json` no servidor; se esse arquivo for apagado, o processo recomeça.

## Arquivos

- `pontos.json` (Campina Grande) e `pontos_recife.json` (Recife): id, nome, categoria, coordenadas. Os campos `efeito_ilha` não são mais usados.
- `coletor.py`: `--cidade campina|recife`, `--simular` (testa sem APIs), `--verificar` (mostra o trecho de via que a TomTom associou a cada ponto).
- `.github/workflows/coletar.yml` (coleta) e `verificar.yml` (manual).

## Teste local

```
python coletor.py --simular    # repita 101 vezes para ver o percentil aparecer
```

Apague a pasta `saida/` depois do teste para não publicar o estado simulado.

## Contraste de superfície (Landsat)

Informação complementar, fixa, embutida em `conforto.html` (constante `LST`, mostrada no popup). Não entra no índice. Para atualizar, rode `resumir_lst.py` sobre os CSV exportados do Google Earth Engine (`termotransito_lst_landsat_gee.js`) e cole o novo objeto em `LST`.
