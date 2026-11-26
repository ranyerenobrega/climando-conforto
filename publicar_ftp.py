#!/usr/bin/env python3
"""Sincroniza com o HostGator (FTP/FTPS) o que o monitor TermoTrânsito (IDTE_V2) precisa.

  python publicar_ftp.py --baixar-estado   antes da coleta: traz percentis_*.json do servidor
  python publicar_ftp.py                   depois da coleta: envia conforto*_latest.json, percentis_*.json
                                           e acrescenta as linhas de hist2_*.csv ao arquivo do dia

Nada é guardado no GitHub: o servidor é a memória do sistema (janela de percentis e histórico).
Se o servidor ainda não tem percentis_*.json, a coleta começa do zero (fase "em formação").

Variáveis de ambiente: FTP_HOST, FTP_USER, FTP_PASS, FTP_DIR (ex.: public_html/dados),
FTP_TLS (1 = FTPS explícito, padrão; 0 = FTP simples).
"""
import glob
import io
import os
import sys
from ftplib import FTP, FTP_TLS, error_perm

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "saida")


def conectar():
    host, user, senha = os.environ["FTP_HOST"], os.environ["FTP_USER"], os.environ["FTP_PASS"]
    if os.environ.get("FTP_TLS", "1") == "0":
        ftp = FTP(host, timeout=30)
        ftp.login(user, senha)
    else:
        ftp = FTP_TLS(host, timeout=30)
        ftp.login(user, senha)
        ftp.prot_p()
    return ftp


def entrar_na_pasta(ftp, caminho):
    for parte in [p for p in caminho.strip("/").split("/") if p]:
        try:
            ftp.cwd(parte)
        except error_perm:
            ftp.mkd(parte)
            ftp.cwd(parte)


def enviar_atomico(ftp, nome, dados: bytes):
    tmp = nome + ".tmp"
    ftp.storbinary(f"STOR {tmp}", io.BytesIO(dados))
    try:
        ftp.delete(nome)
    except error_perm:
        pass  # arquivo ainda não existia
    ftp.rename(tmp, nome)


def baixar(ftp, nome):
    buf = io.BytesIO()
    try:
        ftp.retrbinary(f"RETR {nome}", buf.write)
    except error_perm:
        return None
    return buf.getvalue()


def baixar_estado():
    ftp = conectar()
    try:
        entrar_na_pasta(ftp, os.environ.get("FTP_DIR", "public_html/dados"))
        os.makedirs(SAIDA, exist_ok=True)
        for nome in ("percentis_campina.json", "percentis_recife.json"):
            dados = baixar(ftp, nome)
            if dados:
                with open(os.path.join(SAIDA, nome), "wb") as f:
                    f.write(dados)
                print(f"estado baixado: {nome} ({len(dados)} bytes)")
            else:
                print(f"sem {nome} no servidor; histórico de percentis começa agora")
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()


def main():
    if "--baixar-estado" in sys.argv:
        return baixar_estado()
    latests = sorted(glob.glob(os.path.join(SAIDA, "conforto*_latest.json")))
    if not latests:
        sys.exit("nenhum saida/conforto*_latest.json encontrado; rode coletor.py antes.")

    ftp = conectar()
    try:
        entrar_na_pasta(ftp, os.environ.get("FTP_DIR", "public_html/dados"))
        for caminho in latests:
            with open(caminho, "rb") as f:
                enviar_atomico(ftp, os.path.basename(caminho), f.read())

        for estado in sorted(glob.glob(os.path.join(SAIDA, "percentis_*.json"))):
            with open(estado, "rb") as f:
                enviar_atomico(ftp, os.path.basename(estado), f.read())

        for csv_local in sorted(glob.glob(os.path.join(SAIDA, "hist2_*.csv"))):
            nome = os.path.basename(csv_local)
            with open(csv_local, "rb") as f:
                novo = f.read()
            antigo = baixar(ftp, nome)
            if antigo:
                corpo = novo.split(b"\n", 1)[1] if b"\n" in novo else b""
                if not antigo.endswith(b"\n"):
                    antigo += b"\n"
                novo = antigo + corpo
            enviar_atomico(ftp, nome, novo)
        print("publicado")
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()


if __name__ == "__main__":
    main()
