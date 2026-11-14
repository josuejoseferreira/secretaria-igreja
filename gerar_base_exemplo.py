"""Gera 3 anos de dados FICTÍCIOS para visualizar o sistema.
Uso:  python gerar_base_exemplo.py            (cria)
      python gerar_base_exemplo.py --limpar   (remove só os dados de exemplo)"""
import random, sys, unicodedata
from datetime import date, datetime, time, timedelta
from psycopg.types.json import Jsonb
import app as A

PM = "Josué José João Pedro Lucas Gabriel Mateus Rafael Daniel Samuel Elias Davi Tiago Paulo Marcos Carlos".split()
PF = "Maria Ana Fernanda Juliana Camila Aline Patrícia Priscila Raquel Rebeca Sara Débora Larissa Letícia Beatriz Luana Simone Cláudia Adriana Eliane Márcia Vanessa Thaís Isabela".split()
SOB = "Silva Santos Oliveira Souza Pereira Ferreira Costa Rodrigues Almeida Nascimento Lima Araújo Ribeiro Carvalho Gomes Martins Barbosa Miranda Rocha Dias Moreira Cardoso Teixeira Monteiro Vieira Freitas Mendes Barros Nunes Azevedo".split()
ENDS = [("28680-000", "Rua XV de Novembro", "Centro", "Cachoeiras de Macacu", "RJ"), ("24020-005", "Avenida Amaral Peixoto", "Centro", "Niterói", "RJ"),
        ("28610-000", "Rua Visconde de Itaboraí", "Centro", "Nova Friburgo", "RJ"), ("24230-000", "Rua Mariz e Barros", "Icaraí", "Niterói", "RJ"),
        ("28690-000", "Estrada do Sertão", "Boa Vista", "Cachoeiras de Macacu", "RJ"), ("20040-020", "Rua da Assembleia", "Centro", "Rio de Janeiro", "RJ")]
CONGS = [("Matriz", "001", 5), ("Centro", "002", 4), ("Jardim", "003", 3), ("Vila Nova", "004", 3), ("Boa Vista", "005", 2), ("São José", "006", 2), ("Santa Rita", "007", 2), ("Bela Vista", "008", 1)]
PESOS = dict(novo_convertido=22, batismo_aguas=14, batismo_es=10, falecimento=5, carta_saida=8, carta_entrada=8, desligamento=4, aclamacao=6, cura_divina=8, crianca=9, casamento=6)
TAM = dict(batismo_aguas=(2, 15), batismo_es=(1, 8), aclamacao=(1, 6), novo_convertido=(1, 5), carta_saida=(1, 3), carta_entrada=(1, 3), crianca=(1, 6), casamento=(1, 3), falecimento=(1, 1), desligamento=(1, 3), cura_divina=(1, 3))
IGREJAS = ["Igreja Central de Niterói", "Congregação Vila Verde", "Igreja Sede de Friburgo", "Igreja do Centro de Itaboraí", "Congregação Monte Sião"]
CURAS = ["Cura de dores na coluna", "Restauração da visão", "Cura de enfermidade crônica", "Libertação de enxaqueca", "Recuperação após cirurgia"]
def nome(sob=None, g=None):
    lst = PM if (g or random.choice("MF")) == "M" else PF
    return " ".join(random.sample(lst, random.choice([1, 1, 2]))) + " " + (sob or " ".join(random.sample(SOB, random.choice([1, 2, 2, 3]))))
def ascii_(s): return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" ", "")
def fone(): return f"(21) 9{random.randint(6000,9999)}-{random.randint(1000,9999)}"
def linha(slug):
    if slug == "novo_convertido":
        n = nome(); e = random.choice(ENDS)
        return dict(nome=n, contato1=fone(), contato2=fone() if random.random() < .4 else "", email=f"{ascii_(n)[:28]}@{random.choice(['gmail.com','yahoo.com.br','hotmail.com'])}" if random.random() < .7 else "",
                    cep=e[0], logradouro=e[1], numero=str(random.randint(1, 900)), complemento=random.choice(["", "", "Apto 102", "Fundos", "Casa 2"]), bairro=e[2], cidade=e[3], uf=e[4])
    if slug == "falecimento": return dict(nome=nome(), motivo=random.choice(["Doença", "Idade avançada", "Acidente", "Causas naturais"]))
    if slug == "carta_saida": return dict(nome=nome(), destino=random.choice(IGREJAS))
    if slug == "carta_entrada": return dict(nome=nome(), origem=random.choice(IGREJAS))
    if slug in ("desligamento", "aclamacao"): return dict(nome=nome(), obs=random.choice(["", "", "A pedido", "Conforme ata", "Mudança de cidade"]))
    if slug == "cura_divina": return dict(nome=nome(), descricao=random.choice(CURAS))
    if slug == "crianca": s = " ".join(random.sample(SOB, 2)); return dict(crianca=random.choice(PM + PF) + " " + s, mae=nome(s, "F"), pai=nome(s, "M"))
    if slug == "casamento": return dict(noivo=nome(g="M"), noiva=nome(g="F"))
    return dict(nome=nome())

def limpar(c):
    ids = "SELECT id FROM lotes_ocorrencias WHERE dados_comuns->>'_exemplo' IS NOT NULL"
    c.execute(f"DELETE FROM historico_ocorrencias WHERE lote_id IN ({ids})"); c.execute(f"DELETE FROM ocorrencias WHERE lote_id IN ({ids})")
    n = c.execute(f"DELETE FROM lotes_ocorrencias WHERE id IN ({ids})").rowcount; print(f"Removidos {n} lotes de exemplo.")

random.seed(42)
with A.conn() as c:
    if "--limpar" in sys.argv:
        limpar(c); c.commit(); sys.exit()
    if c.execute("SELECT 1 FROM lotes_ocorrencias WHERE dados_comuns->>'_exemplo' IS NOT NULL").fetchone(): sys.exit("Já existem dados de exemplo. Rode com --limpar antes.")
    u = c.execute("SELECT id FROM usuarios WHERE perfil='admin' ORDER BY id LIMIT 1").fetchone()
    tp = {r["slug"]: r["id"] for r in c.execute("SELECT id,slug FROM tipos_ocorrencia")}
    if not u or not tp: sys.exit("Rode antes: python app.py migrate")
    hoje = date.today(); corte = hoje - timedelta(days=240); ini = hoje.replace(year=hoje.year - 3); ini -= timedelta(days=ini.weekday())
    cg = {}
    for n, cod, _ in CONGS:
        c.execute("INSERT INTO congregacoes(nome,codigo,ativo) VALUES(%s,%s,%s) ON CONFLICT(nome) DO NOTHING", (n, cod, n != "Jardim"))
        cg[n] = c.execute("SELECT id FROM congregacoes WHERE nome=%s", (n,)).fetchone()["id"]
    nl = nr = 0; sit = {"Lançada": 0, "Cancelada": 0}
    for sem in range((hoje - ini).days // 7 + 1):
        seg = ini + timedelta(weeks=sem)
        for _ in range(random.randint(6, 10)):
            slug = random.choices(list(PESOS), list(PESOS.values()))[0]
            d = seg + timedelta(days={"casamento": 5, "batismo_aguas": 6, "batismo_es": 6, "aclamacao": 6, "cura_divina": 6, "crianca": 6, "novo_convertido": 6}.get(slug, random.randint(0, 6)))
            if d > hoje: continue
            opc = [(n, w) for n, _, w in CONGS if n != "Jardim" or d < corte]
            cong = random.choices([o[0] for o in opc], [o[1] for o in opc])[0]
            regs = [linha(slug) for _ in range(random.randint(*TAM[slug]))]
            comuns = {"_exemplo": True}
            if slug == "batismo_aguas": comuns["local"] = random.choice(["Rio Macacu", "Batistério da Sede", "Sítio das Águas", "Rio Guapiaçu"])
            ts = datetime.combine(d, time(random.randint(9, 21), random.randint(0, 59)))
            l = c.execute("INSERT INTO lotes_ocorrencias(tipo_ocorrencia_id,congregacao_id,data_ocorrencia,dados_comuns,usuario_id) VALUES(%s,%s,%s,%s,%s) RETURNING id", (tp[slug], cg[cong], d, Jsonb(comuns), u["id"])).fetchone()["id"]
            c.execute("INSERT INTO historico_ocorrencias(lote_id,usuario_id,acao,dados_novos) VALUES(%s,%s,'lote criado',%s)", (l, u["id"], Jsonb({"linhas": len(regs)})))
            A.gravar_regs(c, l, slug, regs, u["id"], "criada")
            r = random.random(); recente = (hoje - d).days <= 30
            s = "Cancelada" if (not recente and .9 <= r < .95) else "Lançada"
            c.execute("UPDATE lotes_ocorrencias SET created_at=%s,updated_at=%s WHERE id=%s", (ts, ts, l))
            c.execute("UPDATE ocorrencias SET created_at=%s,updated_at=%s WHERE lote_id=%s", (ts, ts, l))
            c.execute("UPDATE historico_ocorrencias SET created_at=%s WHERE lote_id=%s", (ts, l))
            if s != "Lançada":
                t2 = ts + timedelta(days=random.randint(1, 3))
                c.execute("UPDATE lotes_ocorrencias SET situacao=%s,updated_at=%s,cancelado_em=%s WHERE id=%s", (s, t2, t2 if s == "Cancelada" else None, l))
                c.execute("INSERT INTO historico_ocorrencias(lote_id,usuario_id,acao,dados_anteriores,dados_novos,created_at) VALUES(%s,%s,%s,%s,%s,%s)", (l, u["id"], "lote " + s.lower(), Jsonb({"situacao": "Lançada"}), Jsonb({"situacao": s}), t2))
            nl += 1; nr += len(regs); sit[s] += 1
    print(f"Base de exemplo criada: {nl} lotes, {nr} registros, de {ini} a {hoje}. Situações: {sit}")
