"""Testes de integração do módulo Relatórios (comparativo entre períodos).
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
mz, ct, vn, bv = cong("Matriz"), cong("Centro"), cong("Vila Nova"), cong("Boa Vista")
from datetime import date
def membro(**k):
    d = dict(nome="Fulano", status="Ativo", status_membresia="Membro"); d.update(k); s, r = call("/api/membros", d); return s, r
def lote(tipo, data, cg, n, **extra):
    return call("/api/lotes", dict(tipo=tipo, data=data, congregacao_id=cg, comuns=extra.get("comuns", {}), linhas=[{"nome": f"Pessoa {i}"} for i in range(n)]))
def rel(desc, de, ate): return call(f"/api/relatorios?desc={desc}&de={de}&ate={ate}")
def lin(r, nome): return next(l for l in r["linhas"] if l["nome"] == nome)

# 1) período anterior
P = A.periodo_anterior
t("mês fechado inteiro -> mês anterior inteiro", P(date(2026, 11, 1), date(2026, 11, 30)) == (date(2026, 10, 1), date(2026, 10, 31)))
t("mês fechado: março -> fevereiro (28 dias)", P(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28)))
t("mês fechado: janeiro -> dezembro do ano anterior", P(date(2027, 1, 1), date(2027, 1, 31)) == (date(2026, 12, 1), date(2026, 12, 31)))
t("mês fechado: fevereiro bissexto", P(date(2028, 2, 1), date(2028, 2, 29)) == (date(2028, 1, 1), date(2028, 1, 31)))
t("período livre: mesmo nº de dias, termina na véspera", P(date(2026, 11, 10), date(2026, 11, 20)) == (date(2026, 10, 30), date(2026, 11, 9)))
t("mês incompleto não é tratado como mês fechado", P(date(2026, 11, 1), date(2026, 11, 29)) == (date(2026, 10, 3), date(2026, 10, 31)))
t("um dia só", P(date(2026, 11, 5), date(2026, 11, 5)) == (date(2026, 11, 4), date(2026, 11, 4)))
V = A.variacao
t("variação ▲ +1 (+3%)", V(30, 31) == dict(sinal="▲", dif=1, pct=3))
t("variação ▼ −3 (−30%)", V(10, 7) == dict(sinal="▼", dif=-3, pct=-30))
t("variação = ", V(8, 8) == dict(sinal="=", dif=0, pct=0))
t("variação ▲ +4 (+33%)", V(12, 16) == dict(sinal="▲", dif=4, pct=33))
t("anterior zero: sem percentual", V(0, 5) == dict(sinal="▲", dif=5, pct=None))
t("arredonda para cima a partir de 0,5", V(2, 3)["pct"] == 50 and V(3, 2)["pct"] == -33 and V(200, 201)["pct"] == 1)

# 2) ocorrências: o exemplo do pedido (Batismo nas águas, 01/11 a 30/11)
for cg_, a, b in ((mz, 12, 16), (vn, 8, 8), (bv, 10, 7)):
    lote("batismo_aguas", "2026-10-15", cg_, a); lote("batismo_aguas", "2026-11-20", cg_, b)
lote("batismo_aguas", "2026-09-30", mz, 5); lote("batismo_aguas", "2026-12-01", mz, 5)  # fora dos dois períodos
lote("aclamacao", "2026-11-10", ct, 3)
s, r = rel("batismo_aguas", "2026-11-01", "2026-11-30")
t("exemplo do pedido: status 200", s == 200 and r["tipo"] == "ocorrencias" and r["nome"] == "Batismo nas águas")
t("períodos calculados (01/10–31/10)", r["ant_de"] == "2026-10-01" and r["ant_ate"] == "2026-10-31" and r["de"] == "2026-11-01")
t("Matriz 12 -> 16 ▲ +4 (+33%)", (lin(r, "Matriz")["ant"], lin(r, "Matriz")["atu"], lin(r, "Matriz")["sinal"], lin(r, "Matriz")["dif"], lin(r, "Matriz")["pct"]) == (12, 16, "▲", 4, 33))
t("Vila Nova 8 -> 8 =", (lin(r, "Vila Nova")["sinal"], lin(r, "Vila Nova")["dif"]) == ("=", 0))
t("Boa Vista 10 -> 7 ▼ −3 (−30%)", (lin(r, "Boa Vista")["sinal"], lin(r, "Boa Vista")["dif"], lin(r, "Boa Vista")["pct"]) == ("▼", -3, -30))
t("Total 30 -> 31 ▲ +1 (+3%)", r["total"] == dict(ant=30, atu=31, sinal="▲", dif=1, pct=3))
t("congregação sem movimento aparece com = (Centro)", lin(r, "Centro")["ant"] == 0 and lin(r, "Centro")["sinal"] == "=")
s, r = rel("todas", "2026-11-01", "2026-11-30")
t("Todas as ocorrências soma os tipos (atual 31 + 3)", r["total"]["atu"] == 34 and lin(r, "Centro")["atu"] == 3)
s, r2 = rel("aclamacao", "2026-11-01", "2026-11-30"); t("filtro por um tipo", r2["total"]["atu"] == 3 and r2["total"]["ant"] == 0)
s, r2 = call("/api/lotes?tipo=batismo_aguas&de=2026-11-20&ate=2026-11-20&cong=%d" % mz); lid = r2[0]["id"]
call(f"/api/lotes/{lid}/cancelar", {})
s, r = rel("batismo_aguas", "2026-11-01", "2026-11-30")
t("lote cancelado não conta", lin(r, "Matriz")["atu"] == 0 and r["total"]["atu"] == 15)
s, r = rel("batismo_aguas", "2026-11-10", "2026-11-20")
t("período livre (11 dias): anterior 30/10 a 09/11", r["ant_de"] == "2026-10-30" and r["ant_ate"] == "2026-11-09")

# 3) validações e acesso
t("data final antes da inicial: 400", rel("todas", "2026-11-30", "2026-11-01")[0] == 400)
t("sem datas: 400", call("/api/relatorios?desc=todas")[0] == 400)
t("data inválida: 400", rel("todas", "2026-13-01", "2026-11-30")[0] == 400)
t("descrição inválida: 400", rel("qualquer", "2026-11-01", "2026-11-30")[0] == 400)
t("cargo inexistente: 400", rel("cargo:Rei", "2026-11-01", "2026-11-30")[0] == 400)
t("período absurdo (>10 anos): 400", rel("todas", "1990-01-01", "2026-11-30")[0] == 400)
t("sem login: 401", A.app.test_client().get("/api/relatorios?desc=todas&de=2026-11-01&ate=2026-11-30").status_code == 401)
t("impressão sem login redireciona", A.app.test_client().get("/relatorios/imprimir?desc=todas&de=2026-11-01&ate=2026-11-30").status_code == 302)
m = call("/api/meta")[1]; ops = m["relatorio"]
t("filtro Descrição: 'Todas as ocorrências' no topo, 11 tipos, Membros, Congregados e 5 cargos", len(ops) == 19 and ops[0] == dict(v="todas", n="Todas as ocorrências") and [o["n"] for o in ops[12:]] == ["Membros", "Congregados", "Obreiro", "Diácono", "Presbítero", "Evangelista", "Pastor"])
t("meta traz os motivos de saída", m["membros"]["motivos_saida"] == ["Carta de mudança", "Falecimento"])

# 4) ficha: novas datas e validações
t("saída sem motivo: 400", membro(nome="X", data_saida="2026-05-01")[0] == 400)
t("motivo sem data: 400", membro(nome="X", motivo_saida="Falecimento")[0] == 400)
t("motivo inválido: 400", membro(nome="X", data_saida="2026-05-01", motivo_saida="Viagem")[0] == 400)
t("saída antes da chegada: 400", membro(nome="X", data_chegada="2026-05-01", data_saida="2026-04-01", motivo_saida="Falecimento")[0] == 400)
t("registro como membro antes da chegada: 400", membro(nome="X", data_chegada="2026-05-01", data_membresia="2026-04-01")[0] == 400)
t("data de chegada inválida: 400", membro(nome="X", data_chegada="2026-02-31")[0] == 400)
s, r = membro(nome="Sem Cargo", data_cargo="2026-01-01"); x = call(f"/api/membros/{r['id']}")[1]
t("sem cargo, a data do cargo é descartada", s == 201 and x["data_cargo"] is None)
t("banco recusa saída sem motivo", bloqueado("UPDATE membros SET data_saida='2026-01-01' WHERE id=%s", (r["id"],)))

# 5) Membros, Congregados e cargos: total ao fim de cada período (01/11 a 30/11 -> fotos em 31/10 e 30/11)
membro(nome="A Antigo", congregacao_id=mz, data_chegada="2026-01-10", data_membresia="2026-02-01")
membro(nome="B Passou a membro", congregacao_id=mz, data_chegada="2026-10-20", data_membresia="2026-11-10")
membro(nome="C Saiu com carta", congregacao_id=ct, status="Desligado", data_membresia="2026-03-01", data_saida="2026-11-15", motivo_saida="Carta de mudança")
membro(nome="D Congregado novo", congregacao_id=ct, status_membresia="Congregado", data_chegada="2026-11-05")
membro(nome="E Diácono", congregacao_id=mz, data_membresia="2026-01-01", cargo="Diácono", data_cargo="2026-11-02")
membro(nome="F Inativo", congregacao_id=mz, status="Inativo", data_membresia="2026-01-01")
membro(nome="G Falecido", congregacao_id=vn, status="Faleceu", data_membresia="2026-01-01", data_saida="2026-10-10", motivo_saida="Falecimento")
membro(nome="H Sem congregação", data_membresia="2026-01-01")
s, r = rel("membros", "2026-11-01", "2026-11-30")
t("Membros: tipo pessoas e total ao fim de cada período", s == 200 and r["tipo"] == "pessoas")
t("Membros Matriz: A e E em 31/10; A, B e E em 30/11 (▲ +1)", (lin(r, "Matriz")["ant"], lin(r, "Matriz")["atu"], lin(r, "Matriz")["sinal"]) == (2, 3, "▲"))
t("Membros Centro: C conta em 31/10 e sai em 15/11 (▼ −1)", (lin(r, "Centro")["ant"], lin(r, "Centro")["atu"], lin(r, "Centro")["sinal"], lin(r, "Centro")["pct"]) == (1, 0, "▼", -100))
t("Quem saiu antes do período (G) e o inativo (F) não contam", lin(r, "Vila Nova")["ant"] == 0 and lin(r, "Vila Nova")["atu"] == 0)
t("Membros sem congregação aparecem em 'Sem congregação' (H e o 'Sem Cargo' cadastrado hoje)", (lin(r, "Sem congregação")["ant"], lin(r, "Sem congregação")["atu"]) == (1, 2))
t("Membros total: A,E,C,H (4) -> A,B,E,H,Sem Cargo (5) ▲ +1 (+25%)", (r["total"]["ant"], r["total"]["atu"], r["total"]["sinal"], r["total"]["pct"]) == (4, 5, "▲", 25))
s, g1 = rel("membros", "2026-10-01", "2026-10-31")
t("G conta como membro até 10/10: em 30/09 sim, em 31/10 não", lin(g1, "Vila Nova")["ant"] == 1 and lin(g1, "Vila Nova")["atu"] == 0)
s, r = rel("congregados", "2026-11-01", "2026-11-30")
t("Congregados: B era congregado em 31/10; D chega em 05/11", (lin(r, "Matriz")["ant"], lin(r, "Matriz")["atu"], lin(r, "Centro")["ant"], lin(r, "Centro")["atu"]) == (1, 0, 0, 1))
t("Congregados Centro: anterior zero -> sem percentual", lin(r, "Centro")["pct"] is None and lin(r, "Matriz")["pct"] == -100)
s, r = rel("cargo:Diácono", "2026-11-01", "2026-11-30")
t("Cargo Diácono: 0 em 31/10, 1 em 30/11 (data do cargo)", (r["total"]["ant"], r["total"]["atu"]) == (0, 1) and lin(r, "Matriz")["atu"] == 1)
s, r = rel("cargo:Pastor", "2026-11-01", "2026-11-30"); t("cargo sem ninguém: tudo =", r["total"]["atu"] == 0 and r["total"]["sinal"] == "=")
membro(nome="I Presbítero sem data do cargo", congregacao_id=ct, data_membresia="2026-06-01", cargo="Presbítero")
s, r = rel("cargo:Presbítero", "2026-11-01", "2026-11-30")
t("cargo sem Data do cargo usa a data de registro como membro e avisa", r["total"]["ant"] == 1 and any("sem “Data do cargo”" in n for n in r["notas"]))
s, r = rel("membros", "2026-11-01", "2026-11-30")
t("relatório de pessoas traz observações (estimativa)", len(r["notas"]) >= 2)
membro(nome="J Sem nenhuma data", congregacao_id=mz)
s, r = rel("membros", "2026-11-01", "2026-11-30")
t("cadastro sem datas usa a data de cadastro e é avisado", any("data de cadastro" in n for n in r["notas"]) and lin(r, "Matriz")["ant"] == 2 and lin(r, "Matriz")["atu"] == 4)
s, r = rel("membros", date.today().isoformat(), date.today().isoformat())
t("hoje: o cadastro novo já conta no período atual", s == 200 and lin(r, "Matriz")["atu"] == 4)

# 5b) quem conta como ativo: Ativo e Disciplinado; ficam de fora Inativo, Desligado, Faleceu e quem saiu com carta
sr = cong("Santa Rita")
antes_painel = call("/api/painel/membros")[1]["ativos"]
membro(nome="K Disciplinado", congregacao_id=sr, status="Disciplinado", data_membresia="2026-01-01", cargo="Obreiro", data_cargo="2026-02-01")
membro(nome="L Inativo", congregacao_id=sr, status="Inativo", data_membresia="2026-01-01")
membro(nome="M Desligado", congregacao_id=sr, status="Desligado", data_membresia="2026-01-01")
membro(nome="N Faleceu", congregacao_id=sr, status="Faleceu", data_membresia="2026-01-01")
membro(nome="O Carta", congregacao_id=sr, status="Desligado", data_membresia="2026-01-01", data_saida="2026-10-05", motivo_saida="Carta de mudança")
s, r = rel("membros", "2026-11-01", "2026-11-30")
t("Disciplinado conta como membro ativo; Inativo, Desligado, Faleceu e saída com carta ficam de fora", (lin(r, "Santa Rita")["ant"], lin(r, "Santa Rita")["atu"]) == (1, 1))
s, r = rel("cargo:Obreiro", "2026-11-01", "2026-11-30")
t("cargo de membro Disciplinado entra na contagem do cargo", lin(r, "Santa Rita")["atu"] == 1)
s, r = rel("membros", "2026-09-01", "2026-09-30")
t("quem saiu com carta em 05/10 ainda contava em 30/09", lin(r, "Santa Rita")["ant"] == 2 and lin(r, "Santa Rita")["atu"] == 2)
t("resumo do Início passa a incluir Disciplinado (e só ele)", call("/api/painel/membros")[1]["ativos"] == antes_painel + 1)

# 6) impressão
h = cl.get("/relatorios/imprimir?desc=batismo_aguas&de=2026-11-01&ate=2026-11-30")
tx = h.get_data(as_text=True)
t("impressão: 200, título, períodos, congregações e total", h.status_code == 200 and "BATISMO NAS ÁGUAS" in tx and "01/10/2026 a 31/10/2026" in tx and "01/11/2026 a 30/11/2026" in tx and "Boa Vista" in tx and "Total" in tx)
t("impressão: variação com sinais (lote da Matriz foi cancelado)", "▼ −3 (−30%)" in tx and "▼ −15 (−50%)" in tx and "=" in tx)
h = cl.get("/relatorios/imprimir?desc=membros&de=2026-11-01&ate=2026-11-30").get_data(as_text=True)
t("impressão de pessoas traz as observações", "Observações" in h)
t("impressão com período inválido: 400", cl.get("/relatorios/imprimir?desc=todas&de=2026-11-30&ate=2026-11-01").status_code == 400)

# 7) interface
h = cl.get("/").get_data(as_text=True)
t("menu Relatórios e tela com os filtros", 'data-v="rl"' in h and 'id="rdesc"' in h and 'id="rde"' in h and 'id="rate"' in h and 'id="rbp"' in h and 'id="rkpi"' in h and 'id="rbars"' in h)
t("ficha traz as novas datas", all(k in h for k in ("data_chegada", "data_membresia", "data_saida", "motivo_saida", "data_cargo")))

# 8) migrations preservam dados
antes = db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM lotes_ocorrencias)")[0]; A.init_db()
t("migrate repetido preserva dados", db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM lotes_ocorrencias)")[0] == antes)
t("migration 005 registrada", db("SELECT 1 FROM schema_migrations WHERE arquivo='005_membros_datas_relatorios.sql'") != [])
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
