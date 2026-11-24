# Monitor de conforto térmico — Climando

Coleta temperatura do ar e fluxo de tráfego por ponto, calcula o IDTE (A base metodológica vem do TCC de Davi Lopes da Costa, UFCG, 2026)

## Como funciona
GitHub Actions roda `coletor.py` e `publicar_ftp.py` a cada 15 min (05h–19h). Os arquivos chegam em `public_html/dados/` (`conforto_latest.json` e `hist_AAAA-MM-DD.csv`). A página `conforto.html` lê o JSON; nenhuma chave de API fica no navegador.

## Antes de ligar
1. **Pontos.** `pontos.json` já traz 12 pontos de Campina Grande (coordenadas do Google Places, em 23/11/2026). Quatro bairros (Prata, Serrotão, Três Irmãs, Alto Branco) usam o centroide, que pode cair em rua sem dado de tráfego. Rode uma vez `TOMTOM_KEY=... python coletor.py --verificar`: ele mostra a que trecho de via cada ponto foi associado, a distância, a classe da via e a confiança. Troque as coordenadas dos pontos que ficarem longe (centenas de metros) ou com confiança baixa.
2. **Fator de Trânsito.** O TCC dá o intervalo (1,0–1,7), mas não a função. O coletor usa `vel_livre / vel_atual` limitado a esse intervalo (função `fator_transito`). Confira com o código do app e ajuste.
3. **Corredores** (Alpargatas, Trauma, UFCG, UEPB) não têm coeficiente de ilha no TCC; aparecem sem índice até você definir um valor em `pontos.json`.

## Publicar
1. Crie um repositório no GitHub com estes arquivos (mantenha `.github/workflows/coletar.yml`). Repositório público não gasta minutos de Actions; em privado, o plano gratuito tem cota mensal (confira) e 15 min só de dia costuma caber.
2. Em *Settings → Secrets and variables → Actions*, crie os segredos: `TOMTOM_KEY`, `FTP_HOST`, `FTP_USER`, `FTP_PASS`. Opcional: `OWM_KEY` e a variável `TEMP_FONTE=owm` para usar OpenWeatherMap no lugar do Open-Meteo.
3. Variável opcional `FTP_DIR` (padrão `public_html/dados`) e `FTP_TLS=0` se o servidor não aceitar FTPS.
4. Envie `conforto.html` para `public_html/` e troque o cabeçalho pelo menu das outras páginas do portal. O link no menu fica por sua conta.
5. Rode o workflow à mão (*Actions → Coletar conforto térmico → Run workflow*) e confira `https://SEU-DOMINIO/dados/conforto_latest.json`.

## Teste local sem chaves
`python coletor.py --simular` gera `saida/conforto_latest.json` com valores fictícios (a página mostra "dados de teste").

## Atenção
- Agendamentos do GitHub podem atrasar alguns minutos e, em repositório público, são desativados após 60 dias sem atividade no repositório.
- Confira os termos de uso e de atribuição da TomTom e do Open-Meteo para dados exibidos publicamente.
- A TomTom recomenda as APIs Orbis para novas integrações; o endpoint v4 usado aqui é a geração anterior.
