"""Gera ~100 membros FICTÍCIOS (com histórico e aniversários espalhados pelo ano) para visualizar Membros e Aniversariantes.
Uso:  python gerar_membros_exemplo.py            (cria 100)
      python gerar_membros_exemplo.py 60         (cria 60)
      python gerar_membros_exemplo.py --limpar   (remove só os membros de exemplo e o histórico deles)
Os membros de exemplo são identificados pelo e-mail terminado em @exemplo.teste."""
import random, sys
from datetime import date, timedelta
import app as A

MARCA = "@exemplo.teste"
PM = "Josué José João Pedro Lucas Gabriel Mateus Rafael Daniel Samuel Elias Davi Tiago Paulo Marcos Carlos Antônio Francisco Wesley Márcio Sérgio Ricardo Jorge".split()
PF = "Maria Ana Fernanda Juliana Camila Aline Patrícia Priscila Raquel Rebeca Sara Débora Larissa Letícia Beatriz Luana Simone Cláudia Adriana Eliane Márcia Vanessa Thaís Isabela Rute Noemi Lídia Marta".split()
SOB = "Silva Santos Oliveira Souza Pereira Ferreira Costa Rodrigues Almeida Nascimento Lima Araújo Ribeiro Carvalho Gomes Martins Barbosa Miranda Rocha Dias Moreira Cardoso Teixeira Monteiro Vieira Freitas Mendes Barros Nunes Azevedo".split()
ENDS = [("28680-000", "Rua XV de Novembro", "Centro", "Cachoeiras de Macacu", "RJ"), ("24020-005", "Avenida Amaral Peixoto", "Centro", "Niterói", "RJ"),
        ("28610-000", "Rua Visconde de Itaboraí", "Centro", "Nova Friburgo", "RJ"), ("24230-000", "Rua Mariz e Barros", "Icaraí", "Niterói", "RJ"),
        ("28690-000", "Estrada do Sertão", "Boa Vista", "Cachoeiras de Macacu", "RJ"), ("20040-020", "Rua da Assembleia", "Centro", "Rio de Janeiro", "RJ")]
CONGS = [("Matriz", "001", 5), ("Centro", "002", 4), ("Jardim", "003", 3), ("Vila Nova", "004", 3), ("Boa Vista", "005", 2), ("São José", "006", 2), ("Santa Rita", "007", 2), ("Bela Vista", "008", 1)]
CARGO_CADEIA = {"Obreiro": ["Obreiro"], "Diácono": ["Obreiro", "Diácono"], "Presbítero": ["Obreiro", "Diácono", "Presbítero"],
                "Evangelista": ["Obreiro", "Evangelista"], "Pastor": ["Obreiro", "Evangelista", "Pastor"]}
TEXTO_CARGO = {"Obreiro": "Apresentado como obreiro", "Diácono": "Consagrado a diácono", "Presbítero": "Consagrado a presbítero", "Evangelista": "Consagrado a evangelista", "Pastor": "Consagrado a pastor"}
MOTIVOS = ["Falta de testemunho no ambiente de trabalho", "Ausência prolongada e conduta em desacordo com a doutrina", "Desavença não resolvida com irmãos da congregação", "Conforme ata do conselho"]

def ascii_(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" ", "")

def cpf_novo(usados):
    while True:
        d = [random.randint(0, 9) for _ in range(9)]
        if len(set(d)) == 1: continue
        for n in (9, 10): d.append((sum(d[i] * (n + 1 - i) for i in range(n)) * 10 % 11) % 10)
        c = "".join(map(str, d))
        if c not in usados: usados.add(c); return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"

def aleatoria(a, b): return a + timedelta(days=random.randint(0, (b - a).days))

def gerar(n, hoje, congs):
    """congs: lista de (id, nome). Retorna lista de (dados_da_ficha, [(data, descricao), ...])."""
    usados, nomes, out = set(), set(), []
    # aniversários: 12 no mês atual (alguns no mesmo dia, para mostrar a ordenação), o resto espalhado pelo ano
    mes_atual = [(hoje.month, d) for d in (3, 3, 3, 9, 9, 14, 17, 21, 21, 25, 28, 30 if hoje.month not in (2, 4, 6, 9, 11) else 28)]
    for i in range(n):
        g = random.choice("MF")
        for _ in range(50):
            nm = " ".join(random.sample(PM if g == "M" else PF, random.choice([1, 1, 2]))) + " " + " ".join(random.sample(SOB, random.choice([1, 2, 2])))
            if nm not in nomes: nomes.add(nm); break
        crianca = random.random() < .08
        ano = random.randint(2010, 2016) if crianca else random.randint(1940, 2008)
        if i < len(mes_atual) and not crianca:
            m, d = mes_atual[i]; nasc = date(ano, m, d)
        else:
            nasc = aleatoria(date(ano, 1, 1), date(ano, 12, 28))
        idade = (hoje - nasc).days // 365
        r = random.random()
        status = "Ativo" if r < .72 else "Inativo" if r < .80 else "Disciplinado" if r < .87 else "Desligado" if r < .92 else "Faleceu" if r < .95 else "Congregado"
        if idade < 14 and status in ("Faleceu",): status = "Ativo"
        membro = not crianca and status != "Congregado" and random.random() < .88
        cargo = None
        if membro and g == "M" and idade >= 25 and status in ("Ativo", "Disciplinado") and random.random() < .4:
            cargo = random.choices(list(CARGO_CADEIA), [8, 5, 3, 2, 2])[0]
        cong_id, cong_nome = random.choices(congs, [dict((a, w) for a, _, w in CONGS).get(nm_, 1) for _, nm_ in congs])[0]
        e = random.choice(ENDS)
        ficha = dict(nome=nm, data_nascimento=nasc.isoformat(), sexo="Masculino" if g == "M" else "Feminino",
                     estado_civil=random.choice(["Solteiro(a)", "Casado(a)", "Casado(a)", "Viúvo(a)", "Divorciado(a)", "União estável"]) if idade >= 18 else "Solteiro(a)",
                     cpf=cpf_novo(usados) if idade >= 16 or random.random() < .5 else "", telefone=f"(21) 9{random.randint(6000, 9999)}-{random.randint(1000, 9999)}" if random.random() < .9 else "",
                     email=f"{ascii_(nm)[:26]}{random.randint(1, 99)}{MARCA}", cep=e[0], logradouro=e[1], numero=str(random.randint(1, 900)),
                     complemento=random.choice(["", "", "Apto 102", "Fundos", "Casa 2"]), bairro=e[2], cidade=e[3], uf=e[4], congregacao_id=cong_id,
                     status=status, status_membresia="Membro" if membro else "Congregado", cargo=cargo or "")
        hist = []
        if membro:
            conv = aleatoria(nasc + timedelta(days=365 * 10), hoje - timedelta(days=400)) if idade >= 12 else None
            if conv:
                bat = min(hoje - timedelta(days=200), conv + timedelta(days=random.randint(60, 900)))
                rec = bat if random.random() < .8 else min(hoje - timedelta(days=100), bat + timedelta(days=random.randint(30, 700)))
                ficha.update(data_conversao=conv.isoformat(), data_batismo=bat.isoformat(), data_recebimento=rec.isoformat(),
                             forma_recebimento="Batismo" if rec == bat else random.choice(["Aclamação", "Carta de mudança", "Reconciliação"]))
                hist.append((rec, f"Recebido(a) como membro ({ficha['forma_recebimento'].lower()}) na congregação {cong_nome}"))
                ficha["data_chegada"] = (rec if random.random() < .25 else aleatoria(conv, rec)).isoformat()  # veio para a igreja
                ult = rec
                if cargo:
                    for c in CARGO_CADEIA[cargo]:
                        ult = ult + timedelta(days=random.randint(500, 2200))
                        if ult >= hoje - timedelta(days=30): break
                        hist.append((ult, TEXTO_CARGO[c]))
                if random.random() < .15:
                    outra = random.choice([x for x in congs if x[0] != cong_id])[1]
                    hist.append((aleatoria(rec, hoje - timedelta(days=30)) if rec < hoje - timedelta(days=30) else rec, f"Transferido(a) da congregação {outra} para {cong_nome}"))
        else:
            d0 = aleatoria(max(nasc + timedelta(days=365 * 6), hoje - timedelta(days=1500)), hoje - timedelta(days=60))
            hist.append((d0, f"Passou a frequentar a congregação {cong_nome}"))
            ficha["data_chegada"] = d0.isoformat()
        fim = hoje - timedelta(days=random.randint(10, 400))
        base = max([h[0] for h in hist], default=hoje - timedelta(days=900))
        fim = max(fim, base + timedelta(days=1)) if base + timedelta(days=1) <= hoje else hoje
        if status == "Disciplinado": hist.append((fim, f"Disciplinado(a): {random.choice(MOTIVOS)}"))
        elif status == "Inativo": hist.append((fim, "Afastado(a) das atividades por motivo pessoal"))
        elif status == "Desligado":
            mot, forma = random.choice([("Desligado(a) a pedido", "Pedido do membro"), ("Desligado(a) por mudança de cidade", "Carta de mudança"), ("Desligado(a) conforme ata", "Desligamento")])
            hist.append((fim, mot)); ficha.update(data_saida=fim.isoformat(), forma_saida=forma)
        elif status == "Faleceu":
            hist.append((fim, "Falecimento registrado pela secretaria")); ficha.update(data_saida=fim.isoformat(), forma_saida="Falecimento")
        hist = [(d, t) for d, t in hist if nasc < d <= hoje]
        out.append((ficha, sorted(hist)))
    return out

if __name__ == "__main__":
    with A.conn() as c:
        if "--limpar" in sys.argv:
            ids = "SELECT id FROM membros WHERE email LIKE %s"
            # o histórico é imutável no banco; a limpeza dos dados de exemplo desliga a trava só dentro desta transação
            c.execute("ALTER TABLE historico_membro DISABLE TRIGGER tg_historico_membro_imutavel")
            c.execute(f"DELETE FROM historico_membro WHERE membro_id IN ({ids})", ("%" + MARCA,))
            n = c.execute("DELETE FROM membros WHERE email LIKE %s", ("%" + MARCA,)).rowcount
            c.execute("ALTER TABLE historico_membro ENABLE TRIGGER tg_historico_membro_imutavel"); c.commit()
            sys.exit(print(f"Removidos {n} membros de exemplo (e o histórico deles)."))
        if c.execute("SELECT 1 FROM membros WHERE email LIKE %s", ("%" + MARCA,)).fetchone(): sys.exit("Já existem membros de exemplo. Rode com --limpar antes.")
        u = c.execute("SELECT id,nome FROM usuarios WHERE perfil='admin' ORDER BY id LIMIT 1").fetchone()
        if not u or not c.execute("SELECT 1 FROM information_schema.tables WHERE table_name='membros'").fetchone(): sys.exit("Rode antes: python app.py migrate")
        for nm, cod, _ in CONGS:
            c.execute("INSERT INTO congregacoes(nome,codigo,ativo) VALUES(%s,%s,%s) ON CONFLICT(nome) DO NOTHING", (nm, cod, nm != "Jardim"))
        congs = [(r["id"], r["nome"]) for r in c.execute("SELECT id,nome FROM congregacoes WHERE ativo ORDER BY id")]
        random.seed(7); n = int(next((a for a in sys.argv[1:] if a.isdigit()), 100)); hoje = date.today(); nh = 0
        for ficha, hist in gerar(n, hoje, congs):
            v, erros = A.validar_membro(ficha)
            assert not erros, (ficha["nome"], erros)
            mid = c.execute("INSERT INTO membros(" + ",".join(A.COLS_MEMBRO) + ",nome_busca) VALUES(" + ",".join(["%s"] * len(A.COLS_MEMBRO)) + ",f_unaccent(%s)) RETURNING id",
                            [v[k] for k in A.COLS_MEMBRO] + [v["nome"]]).fetchone()["id"]
            for d, t in hist:
                c.execute("INSERT INTO historico_membro(membro_id,data,secretario_id,secretario_nome,descricao) VALUES(%s,%s,%s,%s,%s)", (mid, d, u["id"], u["nome"], t)); nh += 1
        c.commit()
        print(f"Base de exemplo criada: {n} membros e {nh} registros de histórico (secretário: {u['nome']}). Para remover: python gerar_membros_exemplo.py --limpar")
