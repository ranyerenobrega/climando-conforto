#!/usr/bin/env python3
"""Envia saida/conforto_latest.json e o histórico do dia ao HostGator por FTP/FTPS.

O histórico diário é montado sem guardar estado no GitHub: baixa o CSV do dia que já está
no servidor, acrescenta as linhas desta coleta e sobe de volta.

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


def main():
    latest = os.path.join(SAIDA, "conforto_latest.json")
    if not os.path.exists(latest):
        sys.exit("saida/conforto_latest.json não encontrado; rode coletor.py antes.")

    ftp = conectar()
    try:
        entrar_na_pasta(ftp, os.environ.get("FTP_DIR", "public_html/dados"))
        with open(latest, "rb") as f:
            enviar_atomico(ftp, "conforto_latest.json", f.read())

        for csv_local in sorted(glob.glob(os.path.join(SAIDA, "hist_*.csv"))):
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
