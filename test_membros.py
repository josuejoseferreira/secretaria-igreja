"""Testes de integração do módulo Membros / Histórico / Aniversariantes.
Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução)."""
import os, sys
from dotenv import load_dotenv
load_dotenv()
url = os.environ.get("TEST_DATABASE_URL", "")
if not url or not url.split("?")[0].rstrip("/").endswith("_test"):
    sys.exit("Defina TEST_DATABASE_URL apontando para um banco separado cujo nome termine em _test (ex.: secretaria_test).")
os.environ["DATABASE_URL"] = url; os.environ.pop("DATABASE_URL_DIRECT", None)
os.environ.update(SECRET_KEY="chave-de-teste", ADMIN_PASSWORD="senha-de-teste-123", ADMIN_LOGIN="admin", DEV_SEM_LOGIN="0")
import psycopg
import app as A
with psycopg.connect(url) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
A.init_db()
cl = A.app.test_client()
def tok(client=cl):
    with client.session_transaction() as s: return s.get("csrf", "")
def call(path, body=None, m=None, client=cl):
    r = client.open(path, method=m or ("POST" if body is not None else "GET"), json=body, headers={"X-CSRF-Token": tok(client)})
    return r.status_code, (r.get_json(silent=True) or {})
def entrar(client, login, senha):
    client.get("/login"); client.post("/login", data={"login": login, "senha": senha, "csrf": tok(client)}); client.get("/")
entrar(cl, "admin", "senha-de-teste-123")
res = []
def t(nome, cond): res.append(bool(cond)); print(("OK   " if cond else "FALHA"), nome)
def cong(n): call("/api/congregacoes", {"nome": n}); return [x for x in call("/api/congregacoes")[1] if x["nome"] == n][0]["id"]
def db(sql, p=()):
    with psycopg.connect(url) as c: return c.execute(sql, p).fetchall()
def bloqueado(sql, p=()):
    try:
        with psycopg.connect(url) as c: c.execute(sql, p)
        return False
    except psycopg.Error: return True
mz, ct = cong("Matriz"), cong("Centro")
BASE = dict(data_nascimento="1990-05-01", sexo="Masculino", estado_civil="Solteiro(a)", sem_cpf=True, cep="28680-000", logradouro="Rua A", numero="1",
            complemento="Casa", bairro="Centro", cidade="Cachoeiras de Macacu", uf="RJ", congregacao_id=mz)   # cadastro completo (campos obrigatórios, inclusive a congregação)
def F(**k):
    d = dict(BASE); d.update(k)
    if d.get("cpf"): d["sem_cpf"] = False
    return d
def novo(**k):
    d = dict(nome="Fulano", status="Ativo", status_membresia="Membro"); d.update(k); return call("/api/membros", F(**d))
t("congregação é obrigatória no cadastro de membro (400 sem ela)", novo(nome="Sem Congregacao", congregacao_id="")[0] == 400 and novo(nome="Sem Congregacao", congregacao_id=None)[0] == 400)
meta = call("/api/meta")[1]["membros"]
t("meta traz as opções pedidas", meta["status"] == ["Ativo", "Inativo", "Desligado", "Faleceu", "Disciplinado", "Congregado"] and meta["membresia"] == ["Membro", "Congregado"] and meta["cargos"] == ["Obreiro", "Diácono", "Presbítero", "Evangelista", "Pastor"])

# 1) cadastro e validações
s, r = novo(nome="José Álvares", data_nascimento="1980-05-10", sexo="Masculino", cpf="52998224725", cep="28680000", uf="rj", logradouro="Rua A", numero="10", congregacao_id=mz, cargo="Presbítero", telefone="(21) 99999-1234", email="Jose@Teste.com")
jose = r.get("id"); t("cadastro completo: 201", s == 201 and jose)
d = call(f"/api/membros/{jose}")[1]
t("dados normalizados (CPF, CEP, UF, e-mail)", d["cpf"] == "529.982.247-25" and d["cep"] == "28680-000" and d["uf"] == "RJ" and d["email"] == "jose@teste.com" and d["congregacao"] == "Matriz" and d["cargo"] == "Presbítero")
t("nome obrigatório", novo(nome="")[0] == 400)
t("CPF inválido recusado", novo(nome="X", cpf="111.111.111-11")[0] == 400)
t("CPF duplicado recusado", novo(nome="Outro", cpf="529.982.247-25")[0] == 400)
t("CEP inválido recusado", novo(nome="X", cep="123")[0] == 400)
t("status fora da lista recusado", novo(nome="X", status="Qualquer")[0] == 400)
s, r = novo(nome="Congregado Com Cargo", status_membresia="Congregado", cargo="Pastor"); cc = r.get("id")
t("Congregado nunca grava cargo", s == 201 and call(f"/api/membros/{cc}")[1]["cargo"] is None)
t("cargo inválido recusado", novo(nome="X", cargo="Rei")[0] == 400)
t("sem login: 401", A.app.test_client().get("/api/membros").status_code == 401)
t("POST sem CSRF recusado", cl.post("/api/membros", json={"nome": "X"}).status_code == 400)

# 2) grid e filtros
novo(nome="Ana Oliveira", status="Disciplinado", congregacao_id=ct, cargo="Diácono")
novo(nome="Bruno Lima", status="Inativo", status_membresia="Congregado", congregacao_id=ct)
lst = lambda q="": call("/api/membros" + q)[1]
t("lista traz as colunas do grid", set(lst()[0]) == {"id", "status", "congregacao", "nome", "status_membresia", "cargo"})
t("ordem alfabética", [x["nome"] for x in lst()] == ["Ana Oliveira", "Bruno Lima", "Congregado Com Cargo", "José Álvares"])
t("filtro Status", [x["nome"] for x in lst("?status=Disciplinado")] == ["Ana Oliveira"])
t("filtro Congregação", len(lst(f"?cong={ct}")) == 2)
t("filtro Nome sem acento", [x["nome"] for x in lst("?nome=jose+alvares")] == ["José Álvares"])
t("filtro Status da Membresia", len(lst("?membresia=Congregado")) == 2)
t("filtro Cargo", [x["nome"] for x in lst("?cargo=Diácono")] == ["Ana Oliveira"])
t("filtros combinados", len(lst(f"?cong={ct}&membresia=Membro")) == 1 and len(lst(f"?cong={ct}&status=Ativo")) == 0)
r = cl.get("/api/membros?por=2&pagina=2"); t("paginação com total", len(r.get_json()) == 2 and r.headers["X-Total-Count"] == "4")

# 3) histórico  (o PUT substitui a ficha inteira, como a tela faz: sempre enviamos todos os campos)
def put_jose(**k):
    d = dict(nome="José Álvares", status="Ativo", status_membresia="Membro", cargo="Presbítero", congregacao_id=mz, cpf="52998224725", data_nascimento="1980-05-10"); d.update(k)
    return d
s, _ = call(f"/api/membros/{jose}", put_jose(historico_novo=[
    {"data": "2022-03-10", "descricao": "Consagrado a presbítero", "secretario": "FALSO"}, {"data": "2015-01-05", "descricao": "Tornou-se obreiro"}]), "PUT")
h = call(f"/api/membros/{jose}")[1]["historico"]
t("histórico em ordem cronológica (antigo → recente)", s == 200 and [x["data"] for x in h] == ["2015-01-05", "2022-03-10"])
t("secretário = usuário logado (ignora o enviado)", all(x["secretario"] == "Administrador" for x in h))
with psycopg.connect(url) as c:
    uid_sec = c.execute("INSERT INTO usuarios(nome,login,senha_hash,perfil) VALUES('Secretária Teste','sec1',%s,'usuario') RETURNING id", (A.generate_password_hash("senha-sec-123"),)).fetchone()[0]
    for cid in (mz, ct): c.execute("INSERT INTO usuario_congregacoes(usuario_id,congregacao_id) VALUES(%s,%s)", (uid_sec, cid))
c3 = A.app.test_client(); entrar(c3, "sec1", "senha-sec-123")
call(f"/api/membros/{jose}", put_jose(historico_novo=[{"data": "2024-06-01", "descricao": "Transferido de congregação"}]), "PUT", c3)
h = call(f"/api/membros/{jose}")[1]["historico"]
t("outro usuário logado vira o secretário do novo registro; antigos preservados", len(h) == 3 and h[-1]["secretario"] == "Secretária Teste" and h[0]["secretario"] == "Administrador")
call(f"/api/membros/{jose}", put_jose(status="Inativo", status_membresia="Congregado", cargo="", congregacao_id=ct), "PUT")
d = call(f"/api/membros/{jose}")[1]
t("mudar status, membresia, cargo e congregação preserva o histórico", len(d["historico"]) == 3 and d["status"] == "Inativo" and d["cargo"] is None and d["congregacao"] == "Centro")
t("banco impede UPDATE no histórico", bloqueado("UPDATE historico_membro SET descricao='x'"))
t("banco impede DELETE no histórico", bloqueado("DELETE FROM historico_membro"))
s, _ = call(f"/api/membros/{jose}", put_jose(historico_novo=[{"data": "", "descricao": "sem data"}]), "PUT")
t("registro de histórico inválido: 400 e nada gravado", s == 400 and len(call(f"/api/membros/{jose}")[1]["historico"]) == 3)
s, _ = call(f"/api/membros/{jose}", put_jose(nome="Nome Que Não Deve Gravar", historico_novo=[{"data": "2020-01-01", "descricao": "ok"}, {"data": "2020-01-02", "descricao": ""}]), "PUT")
t("falha de validação não altera a ficha", call(f"/api/membros/{jose}")[1]["nome"] == "José Álvares")

# 4) exclusão (lógica) preserva o histórico
t("excluir: 200", call(f"/api/membros/{jose}", None, "DELETE")[0] == 200)
t("excluído some da lista e da consulta", all(x["id"] != jose for x in lst()) and call(f"/api/membros/{jose}")[0] == 404)
t("histórico permanece no banco após excluir o cadastro", db("SELECT count(*) FROM historico_membro WHERE membro_id=%s", (jose,))[0][0] == 3)
t("CPF do excluído pode ser reutilizado", novo(nome="Reaproveita CPF", cpf="529.982.247-25")[0] == 201)
t("excluir de novo: 404", call(f"/api/membros/{jose}", None, "DELETE")[0] == 404)

# 5) congregação inativa
call(f"/api/congregacoes/{ct}", {"nome": "Centro", "ativo": False}, "PUT")
t("congregação inativa recusada em novo membro", novo(nome="Y", congregacao_id=ct)[0] == 400)
ana = [x for x in lst() if x["nome"] == "Ana Oliveira"][0]["id"]
t("editar membro que já está em congregação inativa é permitido", call(f"/api/membros/{ana}", dict(nome="Ana Oliveira", status="Disciplinado", status_membresia="Membro", cargo="Diácono", congregacao_id=ct), "PUT")[0] == 200)
call(f"/api/congregacoes/{ct}", {"nome": "Centro", "ativo": True}, "PUT")

# 6) aniversariantes
for n, dn, cg, st in (("Aniv Carlos", "1975-03-03", ct, "Ativo"), ("Aniv Ana", "1990-03-03", mz, "Ativo"), ("Aniv Zélia", "1980-03-12", mz, "Ativo"), ("Aniv João", "1960-11-12", mz, "Ativo"),
                      ("Aniv Maria", "2001-11-25", ct, "Ativo"), ("Aniv Falecido", "1950-11-20", mz, "Faleceu"), ("Aniv Desligado", "1950-11-21", mz, "Desligado"), ("Aniv Natal", "1999-12-25", mz, "Ativo"), ("Aniv Janeiro", "2000-01-05", mz, "Ativo")):
    novo(nome=n, data_nascimento=dn, congregacao_id=cg, status=st)
an = lambda q: [(x["nome"], x["dia"]) for x in call("/api/aniversariantes?" + q)[1].get("rows", []) if x["nome"].startswith("Aniv")]
t("mês: ordem por dia e, no mesmo dia, alfabética", an("mes=3") == [("Aniv Ana", 3), ("Aniv Carlos", 3), ("Aniv Zélia", 12)])
t("falecido e desligado não aparecem", [n for n, _ in an("mes=11")] == ["Aniv João", "Aniv Maria"])
t("período considera dia/mês, não o ano", [n for n, _ in an("de=2030-11-01&ate=2030-11-30")] == ["Aniv João", "Aniv Maria"])
t("período que atravessa o ano (20/12 a 10/01)", [n for n, _ in an("de=2026-12-20&ate=2027-01-10")] == ["Aniv Natal", "Aniv Janeiro"])
t("somente período inicial / somente final", [n for n, _ in an("de=2026-11-20")] == ["Aniv Maria", "Aniv Natal"] and [n for n, _ in an("ate=2026-02-28")] == ["Aniv Janeiro"])
t("filtro Nome", [n for n, _ in an("nome=zelia")] == ["Aniv Zélia"])
t("filtro Congregação + mês", an(f"mes=3&cong={ct}") == [("Aniv Carlos", 3)])
t("filtros combinados mês + nome + congregação", an(f"mes=3&cong={mz}&nome=ana") == [("Aniv Ana", 3)])
t("sem filtros: todos os meses", len(an("")) == 7)
t("período invertido / mês inválido / data inválida: 400", call("/api/aniversariantes?de=2026-11-30&ate=2026-11-01")[0] == 400 and call("/api/aniversariantes?mes=13")[0] == 400 and call("/api/aniversariantes?de=abc")[0] == 400)
r = cl.get(f"/aniversariantes/imprimir?mes=11&cong={mz}"); h = r.get_data(as_text=True)
t("impressão respeita os filtros e traz o título", r.status_code == 200 and "ANIVERSARIANTES" in h and "Mês: Novembro" in h and "Aniv João" in h and "Aniv Maria" not in h and "Aniv Falecido" not in h and "Matriz" in h)
t("impressão limpa (sem menu do sistema)", "<nav" not in h)
t("impressão sem login redireciona", A.app.test_client().get("/aniversariantes/imprimir").status_code == 302)

# 7) faixas etárias e resumo do Início (compara antes/depois, então não depende dos dados anteriores)
from datetime import date, timedelta
def nasc(anos):  # data em que a pessoa fez exatamente `anos` anos ontem
    h = date.today(); return (date(h.year - anos, h.month, min(h.day, 28)) - timedelta(days=1)).isoformat()
fxs = lambda r: {x["slug"]: x["total"] for x in r["faixas"]}
t("config: padrão 11/17/29/59 e idoso sem limite", [x["ate"] for x in call("/api/config/faixas-etarias")[1]["faixas"]] == [11, 17, 29, 59, None])
r0 = call("/api/painel/membros")[1]
for nm, idade, sx, mem, stt in (("Fx Criança", 5, "Masculino", "Membro", "Ativo"), ("Fx Adolescente", 15, "Feminino", "Congregado", "Ativo"), ("Fx Idosa Inativa", 70, "Feminino", "Membro", "Inativo"), ("Fx Adulto", 40, "Masculino", "Membro", "Ativo")):
    novo(nome=nm, data_nascimento=nasc(idade), sexo=sx, status_membresia=mem, status=stt, congregacao_id=mz)
r1 = call("/api/painel/membros")[1]
t("resumo conta só Ativos (3 dos 4 criados)", r1["ativos"] - r0["ativos"] == 3)
t("resumo: membros e congregados", r1["membros"] - r0["membros"] == 2 and r1["congregados"] - r0["congregados"] == 1)
t("resumo: homens e mulheres", r1["homens"] - r0["homens"] == 2 and r1["mulheres"] - r0["mulheres"] == 1)
t("resumo: faixas pelo padrão (criança, adolescente, adulto; idosa inativa fora)", fxs(r1)["crianca"] - fxs(r0)["crianca"] == 1 and fxs(r1)["adolescente"] - fxs(r0)["adolescente"] == 1 and fxs(r1)["adulto"] - fxs(r0)["adulto"] == 1 and fxs(r1)["idoso"] == fxs(r0)["idoso"])
t("config inválida (adolescente <= criança): 400 com campo", call("/api/config/faixas-etarias", dict(crianca=11, adolescente=11, jovem=29, adulto=59), "PUT")[0] == 400)
t("config vazia/texto: 400", call("/api/config/faixas-etarias", dict(crianca="", adolescente="x", jovem=29, adulto=59), "PUT")[0] == 400)
t("config sem CSRF recusada", cl.put("/api/config/faixas-etarias", json=dict(crianca=4, adolescente=17, jovem=29, adulto=59)).status_code == 400)
s, _ = call("/api/config/faixas-etarias", dict(crianca=4, adolescente=17, jovem=29, adulto=59), "PUT")
r2 = call("/api/painel/membros")[1]
t("novo limite grava e reclassifica (criança de 5 anos vira adolescente)", s == 200 and [x["ate"] for x in call("/api/config/faixas-etarias")[1]["faixas"]] == [4, 17, 29, 59, None] and fxs(r2)["crianca"] == fxs(r1)["crianca"] - 1 and fxs(r2)["adolescente"] == fxs(r1)["adolescente"] + 1)
call("/api/config/faixas-etarias", dict(crianca=11, adolescente=17, jovem=29, adulto=59), "PUT")
t("restaurar o padrão volta ao resultado anterior", fxs(call("/api/painel/membros")[1]) == fxs(r1))
t("config sem login: 401", A.app.test_client().get("/api/config/faixas-etarias").status_code == 401 and A.app.test_client().get("/api/painel/membros").status_code == 401)

# 8) datas de chegada/saída e Relatório comparativo (congregação própria, para isolar os números)
from urllib.parse import quote
rel = cong("Relatório Teste")
def lote(data, n, tipo="batismo_aguas"):
    s, r = call("/api/lotes", {"tipo": tipo, "data": data, "congregacao_id": rel, "comuns": {"local": "Rio"}, "linhas": [{"nome": f"P{i}"} for i in range(n)]}); return s, r.get("id")
casos = [("R A Membro", dict(data_recebimento="2025-05-10", cargo="Diácono")),
         ("R B Membro", dict(data_recebimento="2025-10-20")),
         ("R C Membro", dict(data_recebimento="2025-11-10", cargo="Diácono")),
         ("R D Carta", dict(status="Desligado", data_recebimento="2024-01-01", data_saida="2025-10-15", forma_saida="Carta de mudança")),
         ("R E Faleceu", dict(status="Faleceu", data_recebimento="2020-01-01", data_saida="2025-11-12", forma_saida="Falecimento", cargo="Presbítero")),
         ("R F Congregado", dict(status_membresia="Congregado", data_chegada="2025-09-01")),
         ("R G Congregado", dict(status_membresia="Congregado", data_chegada="2025-11-05")),
         ("R H Virou membro", dict(data_chegada="2025-08-01", data_recebimento="2025-11-20"))]
t("ficha: cadastro com chegada, recebimento e saída (carta/falecimento)", all(novo(nome=n, congregacao_id=rel, **k)[0] == 201 for n, k in casos))
idD = [x for x in lst("?nome=R+D+Carta")][0]["id"]; dD = call(f"/api/membros/{idD}")[1]
t("ficha: datas e forma de saída gravadas", dD["data_saida"] == "2025-10-15" and dD["forma_saida"] == "Carta de mudança" and dD["data_chegada"] is None and dD["status"] == "Desligado")
t("ficha: chegada depois do recebimento é recusada", novo(nome="X", data_chegada="2021-01-01", data_recebimento="2020-01-01")[0] == 400)
t("ficha: saída com Status Ativo é recusada", novo(nome="X", data_saida="2025-10-15")[0] == 400)
t("ficha: saída antes do recebimento é recusada", novo(nome="X", status="Faleceu", data_recebimento="2025-05-01", data_saida="2025-04-01")[0] == 400)
t("ficha: saída futura é recusada", novo(nome="X", status="Faleceu", data_saida=(date.today() + timedelta(days=5)).isoformat())[0] == 400)
t("ficha: cadastro antigo Desligado sem data de saída continua aceito", novo(nome="R Sem Saída", status="Desligado")[0] == 201)
rq = lambda d, de="2025-11-01", ate="2025-11-30": call(f"/api/relatorios/comparativo?descricao={quote(d)}&de={de}&ate={ate}")
linha = lambda r, nome="Relatório Teste": [x for x in r["linhas"] if x["congregacao"] == nome][0]
s, r = rq("membros"); L = linha(r)
t("relatório de membros: posição no fim de out. (3) e de nov. (4)", s == 200 and (L["anterior"], L["atual"]) == (3, 4) and L["variacao"]["tipo"] == "subiu" and L["variacao"]["dif"] == 1 and L["variacao"]["pct"] == 33)
t("períodos: mês fechado volta para o mês anterior inteiro", r["periodo_anterior"] == dict(de="2025-10-01", ate="2025-10-31") and r["periodo_atual"] == dict(de="2025-11-01", ate="2025-11-30") and r["descricao"] == "Membros ativos")
s, r = rq("congregados"); L = linha(r); t("relatório de congregados: quem virou membro sai da contagem (2 -> 2)", (L["anterior"], L["atual"]) == (2, 2) and L["variacao"]["tipo"] == "igual")
s, r = rq("cargo:Diácono"); L = linha(r); t("relatório por cargo eclesiástico (1 -> 2)", (L["anterior"], L["atual"]) == (1, 2))
s, r = rq("cargo:Presbítero"); L = linha(r); t("cargo de quem faleceu em nov. conta em out. (1 -> 0)", (L["anterior"], L["atual"]) == (1, 0) and L["variacao"]["tipo"] == "caiu")
s, r = rq("geral"); L = linha(r); t("relatório geral (membros + congregados): 5 -> 6", s == 200 and (L["anterior"], L["atual"]) == (5, 6) and r["descricao"] == "Geral: membros + congregados ativos")
sm, rm = rq("membros"); sc, rc = rq("congregados"); t("geral = membros + congregados (por congregação e no total)", all(r["total"][k] == rm["total"][k] + rc["total"][k] for k in ("anterior", "atual")) and (L["anterior"], L["atual"]) == tuple(linha(rm)[k] + linha(rc)[k] for k in ("anterior", "atual")))
s, r = rq("cargo:todos"); L = linha(r); t("cargos: geral, todos os cargos juntos (2 -> 2)", s == 200 and (L["anterior"], L["atual"]) == (2, 2) and L["variacao"]["tipo"] == "igual" and "todos os cargos" in r["descricao"])
t("cargos: geral = soma dos cargos individuais", all(r["total"][k] == sum(rq(f"cargo:{c}")[1]["total"][k] for c in meta["cargos"]) for k in ("anterior", "atual")))
t("relatório de membros avisa cadastros sem data de saída", any("sem data de saída" in n for n in rq("membros")[1]["notas"]))
t("ocorrências: 8 lotes criados (datas dentro e fora dos períodos)", all(lote(d, n)[0] == 201 for d, n in (("2025-10-12", 2), ("2025-10-01", 1), ("2025-09-30", 1), ("2025-11-03", 3), ("2025-11-30", 1), ("2025-12-01", 1))))
s4, id4 = lote("2025-11-15", 4); t("lote criado e cancelado em seguida", s4 == 201 and call(f"/api/lotes/{id4}/cancelar", {})[0] == 200)
s, r = rq("oc:batismo_aguas"); L = linha(r); t("ocorrências: período inclusivo e cancelados fora (3 -> 4)", (L["anterior"], L["atual"]) == (3, 4) and r["descricao"] == "Batismo nas águas")
s, r = rq("oc:todas"); L = linha(r); t("todas as ocorrências", (L["anterior"], L["atual"]) == (3, 4) and r["descricao"] == "Todas as ocorrências")
s, r = rq("oc:casamento"); L = linha(r); t("filtro por tipo: outro tipo não conta", (L["anterior"], L["atual"]) == (0, 0))
s, r = rq("oc:batismo_aguas", "2025-11-10", "2025-11-20"); L = linha(r); t("período livre: anterior = mesmos dias até a véspera (30/10 a 09/11)", r["periodo_anterior"] == dict(de="2025-10-30", ate="2025-11-09") and (L["anterior"], L["atual"]) == (3, 0) and L["variacao"]["tipo"] == "caiu" and L["variacao"]["pct"] == -100)
s, r = rq("oc:todas", "2025-01-01", "2025-12-31"); t("ano fechado volta para o ano anterior", r["periodo_anterior"] == dict(de="2024-01-01", ate="2024-12-31"))
tt = r["total"]; t("total = soma das linhas", tt["atual"] == sum(x["atual"] for x in r["linhas"]) and tt["anterior"] == sum(x["anterior"] for x in r["linhas"]))
t("relatório: datas ausentes, invertidas ou descrição inválida: 400", call("/api/relatorios/comparativo?descricao=membros")[0] == 400 and rq("membros", "2025-11-30", "2025-11-01")[0] == 400 and rq("xx")[0] == 400)
t("relatório sem login: 401", A.app.test_client().get("/api/relatorios/comparativo?descricao=membros&de=2025-11-01&ate=2025-11-30").status_code == 401)
h = cl.get(f"/relatorios/comparativo/imprimir?descricao=oc:batismo_aguas&de=2025-11-01&ate=2025-11-30").get_data(as_text=True)
t("impressão do relatório: título, períodos e a congregação", "RELATÓRIO COMPARATIVO" in h and "01/10/2025 a 31/10/2025" in h and "Relatório Teste" in h and "<nav" not in h)

# 9) interface: login, arquivos estáticos e página inicial
r = A.app.test_client().get("/login"); h = r.get_data(as_text=True)
t("login estilizado mantém campos login/senha/csrf", r.status_code == 200 and 'name="login"' in h and 'name="senha"' in h and 'name="csrf"' in h and 'method="post"' in h)
hi = cl.get("/").get_data(as_text=True)
t("interface: relatório tem Geral (membros+congregados), Geral (cargos) e escolha do que mostrar", 'value="geral"' in hi and 'value="cargo:todos"' in hi and all(f'data-ver="{k}"' in hi for k in ("resumo", "grafico", "tabela", "notas")))
t("CSS é servido sem login", A.app.test_client().get("/static/css/app.css").status_code == 200)
h = cl.get("/").get_data(as_text=True)
t("página inicial traz CSS, menu e token CSRF", "/static/css/app.css" in h and 'data-v="mb"' in h and 'data-v="an"' in h and 'name="csrf"' in h)

# 9b) campos obrigatórios, "Não possui CPF" e "Sem número (S/N)"
def raw(**k): return call("/api/membros", dict(nome="Obrig", status="Ativo", status_membresia="Membro", **k))   # SEM os padrões de F()
s_, j_ = raw(); campos = {e.get("campo") for e in j_.get("erros", [])}
t("ficha vazia: 400 e aponta todos os obrigatórios", s_ == 400 and {"data_nascimento", "sexo", "estado_civil", "cpf", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"} <= campos)
t("cadastro completo com CPF válido: 201", novo(nome="Com CPF", cpf="529.982.247-25")[0] == 201)
s_, j_ = novo(nome="Sem CPF marcado", sem_cpf=True); mid = j_.get("id")
t("'Não possui CPF': salva sem CPF e grava a marca", s_ == 201 and db("SELECT cpf,sem_cpf FROM membros WHERE id=%s", (mid,))[0] == (None, True))
t("'Não possui CPF' junto de CPF preenchido: 400", novo(nome="Conflito", sem_cpf=True, cpf="529.982.247-25")[0] == 400)
t("sem a marca e sem CPF: 400 (CPF obrigatório)", novo(nome="Falta CPF", sem_cpf=False, cpf="")[0] == 400)
s_, j_ = novo(nome="Sem numero", numero="", sem_numero=True)
t("'Sem número (S/N)': salva e grava S/N", s_ == 201 and db("SELECT numero FROM membros WHERE id=%s", (j_.get("id"),))[0][0] == "S/N")
t("S/N digitado no campo também vale", db("SELECT numero FROM membros WHERE id=%s", (novo(nome="SN digitado", numero="s/n")[1].get("id"),))[0][0] == "S/N")
t("'Sem número' junto de número preenchido: 400", novo(nome="Conflito num", numero="12", sem_numero=True)[0] == 400)
t("número vazio sem a marca: 400", novo(nome="Falta num", numero="")[0] == 400)
for k in ("complemento", "bairro", "cidade", "uf", "cep", "logradouro", "data_nascimento", "sexo", "estado_civil"):
    t(f"{k} obrigatório", novo(nome="Falta " + k, **{k: ""})[0] == 400)
t("CPF inválido continua recusado", novo(nome="CPF ruim", cpf="111.111.111-11")[0] == 400)

# 10) migrations preservam dados
antes = db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM historico_membro)")[0]; A.init_db()
t("migrate repetido preserva membros e histórico", db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM historico_membro)")[0] == antes)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
