# TermoTrânsito (IDTE_V2)

Monitor de conforto térmico no trânsito, em tempo quase real, para Campina Grande (12 pontos) e Recife (14 pontos). Grupo TROPOCLIMA, UFCG.

## Como funciona

GitHub Actions roda a cada 15 min (05h–19h, horário de Brasília) e executa, em ordem:

1. `publicar_ftp.py --baixar-estado`: traz do HostGator os arquivos `percentis_*.json` (janela móvel de exposição de cada ponto).
2. `coletor.py`: consulta Open-Meteo (temperatura, umidade relativa e vento a 10 m) e TomTom (velocidade atual e de fluxo livre) e calcula o IDTE_V2.
3. `publicar_ftp.py`: envia `conforto_latest.json`, `conforto_recife_latest.json`, os `percentis_*.json` e acrescenta as linhas do dia em `hist2_*.csv` (todos em `public_html/dados/`).

A página `conforto.html` lê os JSON; nenhuma chave de API fica no navegador. Segredos e variáveis do GitHub são os mesmos de antes (`TOMTOM_KEY`, `FTP_HOST`, `FTP_USER`, `FTP_PASS`; opcionais `OWM_KEY`, `TEMP_FONTE`, `FTP_DIR`, `FTP_TLS`). Nada novo a configurar.

## Cálculo do IDTE_V2

0. **Temperatura ajustada:** `T_aj = T + K_LST · dt_lst`. `dt_lst` é o contraste mediano de temperatura de superfície do ponto (Landsat 8/9, 2014–2025) em relação à referência (Embrapa em Campina Grande; Parque da Jaqueira em Recife), gravado em `pontos*.json`. `K_LST` = 0,25 (variável de ambiente `K_LST`, ou `vars.K_LST` no workflow se você quiser expô-la): converte contraste de superfície em contraste do ar e **não foi calibrado**. Pontos sem contraste e a referência têm `dt_lst` = 0. Nos passos seguintes, `T` é `T_aj`.
1. Pressão de vapor: `e = (UR/100) · 6,105 · exp(17,27·T / (237,7 + T))` (hPa).
2. Temperatura aparente (Steadman, sem radiação): `TA = T + 0,33·e − 0,70·w − 4,00` (w = vento a 10 m, m/s).
3. Carga térmica: `CT = max(0, TA − 26)` (°C).
4. Tempo por quilômetro: `t = 60 / max(v_atual, 5)` (min/km).
5. Exposição: `E = CT · t` (°C·min/km).
6. **IDTE_V2** = percentil de `E` entre as últimas 1000 coletas do próprio ponto (0 a 100). Aparece só com pelo menos 100 coletas ("em formação" antes). Classes: baixa (< P50), moderada (P50 a < P90), alta (≥ P90 **e** carga térmica > 0).
7. **Velocidade suavizada:** no passo 4 usa-se a mediana das últimas 3 leituras do ponto (`N_SUAVE`), para reduzir o ruído do tráfego. A velocidade bruta continua gravada.
8. **Classe de conforto (independente do histórico do ponto):** (a) classe de calor: a temperatura aparente do ponto (com o ajuste do Landsat) é comparada com os cortes P25, P50, P75 e P95 da climatologia local **do mesmo mês e da mesma hora** (`climatologia.json`, temperatura aparente horária de 2015 a 2025 do Open-Meteo na coordenada de referência): confortável, leve, moderado, alto, extremo; (b) trânsito: razão `v_suave / v_livre` ≥ 0,8 livre (+0), de 0,5 a 0,8 lento (+1 classe), < 0,5 congestionado (+2), limitado a extremo (`R_LENTO`, `R_CONGEST`).

Parâmetros no topo de `coletor.py`: `T_REF`, `V_MIN`, `N_JANELA`, `N_MIN`, `P_MEDIANA`, `P_ALTO`, `N_SUAVE`, `R_LENTO`, `R_CONGEST`. São convenções, ainda sem validação de campo. O percentil é relativo ao histórico de cada ponto e não permite comparar pontos entre si; para isso use a exposição.

## Calibrar as faixas de conforto (fazer uma vez)

Depois de subir os arquivos, vá em **Actions → "TermoTrânsito - calibrar faixas de conforto (climatologia)" → Run workflow**. O fluxo baixa 11 anos de dados do Open-Meteo, calcula os cortes por mês e hora e grava `climatologia.json` no repositório (cerca de 1 a 3 minutos). Enquanto esse arquivo não existir, os pontos aparecem como "faixas em calibração". Os cortes calculados ficam no log do fluxo. Para testar sem rede: `python calibrar_climatologia.py --teste`.

## Atualização de esquema

O `hist2_*.csv` ganhou colunas (`ajuste_lst`, `temp_ajustada`, `classe_calor`, `transito`, `classe_conforto`, `vel_suave`). Se o arquivo do dia no servidor tiver o cabeçalho antigo, `publicar_ftp.py` o guarda como `*.esquema-anterior` e começa um novo. Os `percentis_*.json` de versão anterior são descartados automaticamente (a exposição passou a usar a temperatura ajustada) e a janela recomeça, com nova fase "em formação" de cerca de 2 dias.

## Partida a frio

O histórico antigo (`hist_*.csv`) não tem umidade nem vento e não é usado. Os percentis começam do zero no primeiro dia; com 60 coletas por dia, cada ponto passa a exibir o índice em cerca de 2 dias. A janela é guardada em `percentis_*.json` no servidor; se esse arquivo for apagado, o processo recomeça.

## Arquivos

- `pontos.json` (Campina Grande) e `pontos_recife.json` (Recife): id, nome, categoria, coordenadas, `dt_lst` e `dt_lst_n` (contraste de superfície e número de datas). Para atualizar o contraste, rode `resumir_lst.py` e copie os valores.
- `coletor.py`: `--cidade campina|recife`, `--simular` (testa sem APIs), `--verificar` (mostra o trecho de via que a TomTom associou a cada ponto).
- `calibrar_climatologia.py`: gera `climatologia.json` (cortes da climatologia local).
- `.github/workflows/coletar.yml` (coleta), `calibrar.yml` (calibração, manual) e `verificar.yml` (manual).

## Teste local

```
python coletor.py --simular    # repita 101 vezes para ver o percentil aparecer
```

Apague a pasta `saida/` depois do teste para não publicar o estado simulado.

## Contraste de superfície (Landsat)

Informação complementar, fixa, embutida em `conforto.html` (constante `LST`, mostrada no popup). Não entra no índice. Para atualizar, rode `resumir_lst.py` sobre os CSV exportados do Google Earth Engine (`termotransito_lst_landsat_gee.js`) e cole o novo objeto em `LST`.
