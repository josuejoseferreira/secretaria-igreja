"""Testes de integração. Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução)."""
import json, os, re, sys, urllib.parse
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
def tok():
    with cl.session_transaction() as s: return s.get("csrf", "")
def call(path, body=None, m=None):
    r = cl.open(path, method=m or ("POST" if body is not None else "GET"), json=body, headers={"X-CSRF-Token": tok()})
    return r.status_code, (r.get_json(silent=True) or {})
cl.get("/login")
cl.post("/login", data={"login": "admin", "senha": "senha-de-teste-123", "csrf": tok()}); cl.get("/")
res = []
def t(nome, cond): res.append(cond); print(("OK   " if cond else "FALHA"), nome)
def cong(n, c=None): call("/api/congregacoes", {"nome": n, "codigo": c}); return [x for x in call("/api/congregacoes")[1] if x["nome"] == n][0]["id"]
mz, ct = cong("Matriz", "001"), cong("Centro", "002")
t("cadastro duplicado de congregação é recusado", call("/api/congregacoes", {"nome": "Matriz"})[0] == 400)
meta = call("/api/meta")[1]; t("11 tipos disponíveis", len(meta["tipos"]) == 11)
# 1) cada tipo, individual
for tp in meta["tipos"]:
    linha = {c["k"]: "Teste " + c["k"] for c in tp["campos"]}
    linha.update({k: v for k, v in (("cep", "28680-000"), ("uf", "RJ")) if k in linha})
    s, _ = call("/api/lotes", {"tipo": tp["slug"], "data": "2026-11-05", "congregacao_id": mz, "comuns": {}, "linhas": [linha]})
    t(f"cadastro individual: {tp['nome']}", s == 201)
# 2) lote de 15
nomes = [f"Pessoa {i:02d}" for i in range(1, 16)]; nomes[0] = "José Álvares"
s, r = call("/api/lotes", {"tipo": "batismo_aguas", "data": "2026-11-07", "congregacao_id": mz, "comuns": {"local": "Rio"}, "linhas": [{"nome": n} for n in nomes]})
lote = r["id"]; t("lote de 15 pessoas salvo", s == 201)
lst = call("/api/lotes?tipo=batismo_aguas&de=2026-11-07&ate=2026-11-07")[1]
t("exibe Várias (15) com os 15 nomes", len(lst) == 1 and lst[0]["n"] == 15 and len(lst[0]["nomes"]) == 15)
t("registro individual mostra 1 nome", call("/api/lotes?tipo=casamento")[1][0]["n"] == 1)
# 3) validação por linha + sem gravação parcial
antes = len(call("/api/lotes")[1])
s, r = call("/api/lotes", {"tipo": "casamento", "data": "2026-11-02", "congregacao_id": mz, "comuns": {}, "linhas": [{"noivo": "A", "noiva": "B"}, {"noivo": "C", "noiva": ""}]})
t("erro apontado na linha 2", s == 400 and r["erros"][0]["linha"] == 2)
t("nada gravado após erro", len(call("/api/lotes")[1]) == antes)
t("congregação obrigatória", call("/api/lotes", {"tipo": "falecimento", "data": "2026-11-01", "linhas": [{"nome": "X"}]})[0] == 400)
# 4) filtros
t("busca por nome sem acento dentro do lote", any(l["id"] == lote for l in call("/api/lotes?nome=jose+alvares")[1]))
t("busca por mãe", len(call("/api/lotes?nome=" + urllib.parse.quote("Teste mae"))[1]) == 1)
t("busca por noiva", len(call("/api/lotes?nome=Teste+noiva")[1]) == 1)
t("filtro período exclui", all(l["data_ocorrencia"] == "2026-11-07" for l in call("/api/lotes?de=2026-11-06&ate=2026-11-30")[1]))
t("filtro tipo", len(call("/api/lotes?tipo=batismo_aguas")[1]) == 2)
call("/api/lotes", {"tipo": "cura_divina", "data": "2026-11-03", "congregacao_id": ct, "comuns": {}, "linhas": [{"nome": "Ana"}]})
t("filtro congregação", len(call(f"/api/lotes?cong={ct}")[1]) == 1)
t("filtros combinados", len(call(f"/api/lotes?cong={ct}&tipo=casamento")[1]) == 0)
# 5) edição, cancelamento, histórico
d = call(f"/api/lotes/{lote}")[1]
s, _ = call(f"/api/lotes/{lote}", {"data": "2026-11-07", "congregacao_id": mz, "comuns": {"local": "Rio"}, "linhas": [{"nome": n} for n in nomes[:14]]}, "PUT")
t("edição remove 1 pessoa (14 ativas)", s == 200 and len(call(f"/api/lotes/{lote}")[1]["linhas"]) == 14)
t("a ação 'conferir' não existe mais", call(f"/api/lotes/{lote}/conferir", {})[0] == 400 and call(f"/api/lotes/{lote}")[1]["situacao"] == "Lançada")
t("cancelar lote preserva registros", call(f"/api/lotes/{lote}/cancelar", {})[0] == 200 and call(f"/api/lotes/{lote}")[1]["situacao"] == "Cancelada")
t("lote cancelado não edita", call(f"/api/lotes/{lote}", {"data": "2026-11-07", "congregacao_id": mz, "linhas": [{"nome": "x"}]}, "PUT")[0] == 400)
t("lote cancelado não cancela de novo", call(f"/api/lotes/{lote}/cancelar", {})[0] == 400)
try:
    with psycopg.connect(url) as c2: c2.execute("UPDATE lotes_ocorrencias SET situacao='Conferida'")
    _aceitou = True
except psycopg.Error: _aceitou = False
t("banco não aceita mais a situação Conferida", not _aceitou)
t("painel não traz mais o contador de conferência", "conf" not in call("/api/painel")[1])
t("histórico registra ações", len(call(f"/api/lotes/{lote}")[1]["historico"]) >= 3)
# 6) congregação inativa
call(f"/api/congregacoes/{ct}", {"nome": "Centro", "codigo": "002", "ativo": False}, "PUT")
t("congregação inativa recusada em novo lote", call("/api/lotes", {"tipo": "cura_divina", "data": "2026-11-03", "congregacao_id": ct, "linhas": [{"nome": "Z"}]})[0] == 400)
t("inativa continua na consulta histórica", len(call(f"/api/lotes?cong={ct}")[1]) == 1)
t("painel com dados reais", call("/api/painel")[1]["cong"] == 1)
t("lista geral traz o slug do tipo (abre a tela do tipo)", "tipo_slug" in call("/api/lotes")[1][0])
t("grid do tipo: casamento lista 1 registro com dados específicos", len(call("/api/registros?tipo=casamento")[1]) == 1 and "noivo" in call("/api/registros?tipo=casamento")[1][0]["d"])
t("grid do tipo respeita filtro de nome e congregação", len(call("/api/registros?tipo=casamento&nome=noiva")[1]) == 1 and len(call("/api/registros?tipo=casamento&nome=zzz")[1]) == 0 and len(call(f"/api/registros?tipo=casamento&cong={ct}")[1]) == 0)
vz = cong("Sem ocorrências")
t("excluir congregação sem ocorrências", call(f"/api/congregacoes/{vz}", None, "DELETE")[0] == 200 and vz not in [x["id"] for x in call("/api/congregacoes")[1]])
s, r = call(f"/api/congregacoes/{mz}", None, "DELETE")
t("congregação com ocorrências NÃO pode ser excluída", s == 400 and mz in [x["id"] for x in call("/api/congregacoes")[1]])
base = {"nome": "Josué José Maria Ferreira Barbosa Miranda", "email": "josuejoseferreira@yahoo.com.br"}
s, r = call("/api/lotes", {"tipo": "novo_convertido", "data": "2026-11-05", "congregacao_id": mz, "comuns": {}, "linhas": [dict(base, cep="123")]})
t("CEP inválido é recusado na linha", s == 400 and r["erros"][0]["linha"] == 1)
s, r = call("/api/lotes", {"tipo": "novo_convertido", "data": "2026-11-05", "congregacao_id": mz, "comuns": {}, "linhas": [dict(base, cep="28680000", uf="rj", logradouro="Rua XV de Novembro", numero="10")]})
d = call(f"/api/lotes/{r.get('id', 0)}")[1].get("linhas", [{}])[0]
t("endereço completo salvo, CEP e UF normalizados", s == 201 and d.get("cep") == "28680-000" and d.get("uf") == "RJ" and d.get("numero") == "10")
# --- segurança, rollback real, idempotência ---
c2 = A.app.test_client()
t("rota da API sem login responde 401", c2.get("/api/lotes").status_code == 401)
t("página inicial sem login redireciona", c2.get("/").status_code == 302)
c2.get("/login")
with c2.session_transaction() as s: tk2 = s["csrf"]
t("login sem token CSRF é recusado", c2.post("/login", data={"login": "admin", "senha": "senha-de-teste-123"}).status_code == 400)
c2.post("/login", data={"login": "admin", "senha": "errada", "csrf": tk2})
t("senha errada não abre sessão", c2.get("/api/lotes").status_code == 401)
t("POST na API sem CSRF é recusado", cl.post("/api/congregacoes", json={"nome": "X"}).status_code == 400)
t("/health responde ok", cl.get("/health").status_code == 200)
t("filtro por situação", all(l["situacao"] == "Lançada" for l in call("/api/lotes?sit=Lançada")[1]) and len(call("/api/lotes?sit=Cancelada")[1]) == 1)
def cnt():
    with psycopg.connect(url) as c:
        return c.execute("select (select count(*) from lotes_ocorrencias),(select count(*) from ocorrencias)").fetchone()
orig = A.gravar_regs
def falha(c, lote_id, slug, regs, uid, acao):
    orig(c, lote_id, slug, regs[:1], uid, acao); raise RuntimeError("falha simulada")
A.gravar_regs = falha; n0 = cnt()
s, _ = call("/api/lotes", {"tipo": "falecimento", "data": "2026-11-01", "congregacao_id": mz, "comuns": {}, "linhas": [{"nome": "A"}, {"nome": "B"}, {"nome": "C"}]})
A.gravar_regs = orig
t("falha no meio da gravação: erro 500 e ROLLBACK integral do lote", s == 500 and cnt() == n0)
r = call("/api/ocorrencias", {"tipo": "falecimento", "data": "2026-10-10", "congregacao_id": mz, "comuns": {}, "campos": {"nome": "Fulano Único", "motivo": "x"}})
g = call("/api/registros?nome=fulano")[1]; oid = g[0]["id"] if g else 0
t("novo cadastro individual aparece no grid geral (Data, Congregação, Ocorrência, Nome)", r[0] == 201 and len(g) == 1 and g[0]["tipo"] == "Falecimento" and g[0]["congregacao"] == "Matriz" and g[0]["nome"] == "Fulano Único")
s, _ = call(f"/api/ocorrencias/{oid}", {"tipo": "falecimento", "data": "2026-10-11", "congregacao_id": mz, "comuns": {}, "campos": {"nome": "Fulano Editado", "motivo": "y"}}, "PUT")
t("editar ocorrência atualiza o grid", s == 200 and call(f"/api/ocorrencias/{oid}")[1]["campos"]["nome"] == "Fulano Editado" and call("/api/registros?nome=editado")[1][0]["data"] == "2026-10-11")
call("/api/lotes", {"tipo": "desligamento", "data": "2026-09-01", "congregacao_id": mz, "comuns": {}, "linhas": [{"nome": "Ana Lote"}, {"nome": "Bia Lote"}, {"nome": "Caio Lote"}]})
rid = call("/api/registros?nome=bia+lote")[1][0]["id"]
call(f"/api/ocorrencias/{rid}", {"tipo": "desligamento", "data": "2026-09-15", "congregacao_id": mz, "comuns": {}, "campos": {"nome": "Bia Lote"}}, "PUT")
t("editar um registro de agrupamento antigo não altera os demais", {x["nome"]: x["data"] for x in call("/api/registros?tipo=desligamento")[1] if "Lote" in x["nome"]} == {"Ana Lote": "2026-09-01", "Bia Lote": "2026-09-15", "Caio Lote": "2026-09-01"})
call(f"/api/congregacoes/{ct}", {"nome": "Centro", "codigo": "002", "ativo": False}, "PUT")
r2 = call("/api/registros?cong=%d" % ct)[1][0]["id"]; o2 = call(f"/api/ocorrencias/{r2}")[1]
t("editar registro de congregação inativa mantendo a congregação", call(f"/api/ocorrencias/{r2}", {"tipo": o2["tipo"], "data": o2["data"], "congregacao_id": ct, "comuns": o2["comuns"], "campos": o2["campos"]}, "PUT")[0] == 200)
s, _ = call(f"/api/ocorrencias/{oid}", None, "DELETE")
with psycopg.connect(url) as c: ex = c.execute("select count(*) from historico_ocorrencias where acao='excluída' and ocorrencia_id=%s", (oid,)).fetchone()[0]
t("excluir remove do grid e preserva histórico (exclusão lógica)", s == 200 and len(call("/api/registros?nome=editado")[1]) == 0 and ex == 1 and call(f"/api/ocorrencias/{oid}")[0] == 404)
call("/api/lotes", {"tipo": "batismo_aguas", "data": "2026-10-04", "congregacao_id": mz, "comuns": {"local": "Rio Teste"}, "linhas": [{"nome": f"Batizando {i:02d}"} for i in range(30)]})
r1 = cl.get("/api/registros?nome=batizando&por=15&pagina=2"); r2 = cl.get("/api/registros?nome=batizando&por=15&pagina=1")
ids = {x["id"] for x in r1.get_json()} | {x["id"] for x in r2.get_json()}
t("30 pessoas no mesmo local: 2 páginas de 15, sem repetição, total 30", len(r1.get_json()) == 15 and len(r2.get_json()) == 15 and len(ids) == 30 and r1.headers["X-Total-Count"] == "30")
t("cada pessoa é uma linha individual com o local do lançamento", all(x["comuns"]["local"] == "Rio Teste" for x in r1.get_json()))
t("tamanho de página configurável (5 e 100)", len(cl.get("/api/registros?por=5").get_json()) == 5 and len(cl.get("/api/registros?por=100").get_json()) >= 30)
def estado():
    with psycopg.connect(url) as c:
        return c.execute("select (select count(*) from lotes_ocorrencias),(select count(*) from ocorrencias),(select count(*) from usuarios),(select count(*) from tipos_ocorrencia),(select senha_hash from usuarios where login='admin'),(select count(*) from schema_migrations)").fetchone()
a = estado(); A.init_db(); b = estado()
t("migrate repetido preserva dados e NÃO redefine a senha do admin", a == b)
t("banco vazio inicializado: 11 tipos, 1 admin, migrations registradas", a[3] == 11 and a[2] == 1 and a[5] == len([f for f in os.listdir(os.path.join(A.BASE, "migrations")) if f.endswith(".sql")]))
t("dados persistem com nova instância da aplicação", len(A.app.test_client().get("/health").get_json()) == 1 and a[0] > 0)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} | Não executados: 0 (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
