"""Testes de integração: perfis (Administrador / Usuário comum), congregações liberadas e bloqueio no servidor.
Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução)."""
import os, sys
from urllib.parse import quote
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
def tok(client):
    with client.session_transaction() as s: return s.get("csrf", "")
def call(client, path, body=None, m=None, csrf=True):
    r = client.open(path, method=m or ("POST" if body is not None else "GET"), json=body, headers={"X-CSRF-Token": tok(client)} if csrf else {})
    return r.status_code, (r.get_json(silent=True) or {}) if r.is_json else {}
def entrar(login, senha):
    c = A.app.test_client(); c.get("/login"); c.post("/login", data={"login": login, "senha": senha, "csrf": tok(c)}); c.get("/"); return c
res = []
def t(nome, cond): res.append(bool(cond)); print(("OK   " if cond else "FALHA"), nome)
def db(sql, p=()):
    with psycopg.connect(url) as c: return c.execute(sql, p).fetchall()

adm = entrar("admin", "senha-de-teste-123")
def cong(n): call(adm, "/api/congregacoes", {"nome": n}); return [x for x in call(adm, "/api/congregacoes")[1] if x["nome"] == n][0]["id"]
ca, cb, cc = cong("Alfa"), cong("Beta"), cong("Gama")
slug = next(s for s, x in A.TIPOS.items() if "cep" in [k for k, _ in x[3]]) if any("cep" in [k for k, _ in x[3]] for x in A.TIPOS.values()) else next(iter(A.TIPOS))
TP = A.TIPOS[slug]
def linha(nome="Fulano"):
    v = {k: "Teste" for k, _ in TP[3] if k in TP[4]}
    if "cep" in [k for k, _ in TP[3]]: v["cep"] = "23890-000"
    if "uf" in v: v["uf"] = "RJ"
    n = TP[5]; v[n] = nome; return v
def lote(client, cid, nome="Fulano"): return call(client, "/api/lotes", {"tipo": slug, "congregacao_id": cid, "data": "2025-11-10", "comuns": {}, "linhas": [linha(nome)]})
def membro(client, cid, nome, **k):
    d = dict(nome=nome, status="Ativo", status_membresia="Membro", congregacao_id=cid, data_nascimento="1990-11-12"); d.update(k); return call(client, "/api/membros", d)

# ---------- dados de cada congregação (criados pelo administrador)
s, r = lote(adm, ca, "Oc Alfa"); t("admin lança ocorrência (inclui CEP: bug da variável d corrigido)", s == 201)
lA = r["id"]; lB = lote(adm, cb, "Oc Beta")[1]["id"]; lC = lote(adm, cc, "Oc Gama")[1]["id"]
mA = membro(adm, ca, "Mem Alfa")[1]["id"]; mB = membro(adm, cb, "Mem Beta")[1]["id"]; mC = membro(adm, cc, "Mem Gama", cargo="Diácono")[1]["id"]; mN = membro(adm, None, "Mem Sem Cong")[1]["id"]

# ---------- cadastro de usuários pelo administrador
novo = lambda **k: call(adm, "/api/usuarios", dict(dict(nome="Maria", login="maria", senha="senha-maria-1", perfil="usuario", congregacoes=[ca, cb]), **k))
s, r = novo(); t("admin cadastra usuário comum com 2 congregações", s == 201); uid = r["id"]
t("senha fica só como hash", db("SELECT senha_hash FROM usuarios WHERE id=%s", (uid,))[0][0].startswith(("scrypt:", "pbkdf2:")))
t("login duplicado: 400", novo()[0] == 400)
t("senha curta: 400", novo(login="x1x", senha="123")[0] == 400)
t("login inválido: 400", novo(login="a b")[0] == 400)
t("usuário comum sem congregação: 400", novo(login="sem1", congregacoes=[])[0] == 400)
t("congregação inexistente: 400", novo(login="sem2", congregacoes=[9999])[0] == 400)
t("perfil inválido: 400", novo(login="sem3", perfil="root")[0] == 400)
s, r = novo(login="chefe", nome="Chefe", perfil="admin", congregacoes=[]); t("admin cadastra outro administrador", s == 201); uid2 = r["id"]
lista = call(adm, "/api/usuarios")[1]
t("listagem traz perfil e congregações", {u["login"]: (u["perfil"], u["congregacoes"]) for u in lista}["maria"] == ("usuario", sorted([ca, cb])))
t("rota de usuários exige token CSRF", call(adm, "/api/usuarios", dict(nome="x", login="csrf1", senha="12345678", perfil="usuario", congregacoes=[ca]), csrf=False)[0] == 400)

# ---------- usuário comum: tudo que é administrativo é bloqueado no servidor
mar = entrar("maria", "senha-maria-1")
t("comum: GET /api/usuarios = 403", call(mar, "/api/usuarios")[0] == 403)
t("comum: não cadastra usuário (403)", call(mar, "/api/usuarios", dict(nome="Z", login="zzz", senha="12345678", perfil="usuario", congregacoes=[ca]))[0] == 403)
t("comum: não cria administrador (403)", call(mar, "/api/usuarios", dict(nome="Z", login="zzz2", senha="12345678", perfil="admin", congregacoes=[]))[0] == 403)
t("comum: não altera o próprio perfil (403)", call(mar, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="admin", ativo=True, congregacoes=[]), "PUT")[0] == 403)
t("comum: não libera congregação para si (403)", call(mar, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="usuario", ativo=True, congregacoes=[ca, cb, cc]), "PUT")[0] == 403)
t("comum: não altera permissões de outro usuário (403)", call(mar, f"/api/usuarios/{uid2}", dict(nome="Chefe", perfil="usuario", ativo=False, congregacoes=[ca]), "PUT")[0] == 403)
t("no banco nada mudou", db("SELECT perfil,ativo FROM usuarios WHERE id=%s", (uid2,))[0] == ("admin", True) and db("SELECT perfil FROM usuarios WHERE id=%s", (uid,))[0][0] == "usuario" and len(db("SELECT 1 FROM usuario_congregacoes WHERE usuario_id=%s", (uid,))) == 2)
t("comum: não cria/edita/exclui congregação (403)", call(mar, "/api/congregacoes", {"nome": "Nova"})[0] == 403 and call(mar, f"/api/congregacoes/{ca}", {"nome": "X"}, "PUT")[0] == 403 and call(mar, f"/api/congregacoes/{cc}", m="DELETE")[0] == 403)
t("comum: não altera a faixa etária global (403)", call(mar, "/api/config/faixas-etarias", {"crianca": 5, "adolescente": 10, "jovem": 20, "adulto": 50}, "PUT")[0] == 403)
t("comum não vê itens administrativos na página", all(x not in mar.get("/").get_data(as_text=True) for x in ('data-v="us"', 'data-v="cg"', 'data-v="cf"', 'id="us"')))
t("admin vê Usuários no menu", 'data-v="us"' in adm.get("/").get_data(as_text=True))

# ---------- usuário comum só enxerga as congregações liberadas
meta = call(mar, "/api/meta")[1]
t("meta: só as congregações liberadas e perfil", sorted(c["id"] for c in meta["todas"]) == sorted([ca, cb]) and meta["usuario"]["perfil"] == "usuario")
t("lista de congregações: só as liberadas", sorted(c["id"] for c in call(mar, "/api/congregacoes")[1]) == sorted([ca, cb]))
t("ocorrências: só A e B", {r["congregacao"] for r in call(mar, "/api/registros")[1]} == {"Alfa", "Beta"} and {r["congregacao"] for r in call(mar, "/api/lotes")[1]} == {"Alfa", "Beta"})
t("filtro por congregação não liberada: vazio", not call(mar, f"/api/registros?cong={cc}")[1] and not call(mar, f"/api/lotes?cong={cc}")[1])
t("lote de outra congregação: 404 (ver/editar/cancelar)", call(mar, f"/api/lotes/{lC}")[0] == 404 and call(mar, f"/api/lotes/{lC}", {"data": "2025-11-10", "congregacao_id": ca, "comuns": {}, "linhas": [linha()]}, "PUT")[0] == 404 and call(mar, f"/api/lotes/{lC}/cancelar", {})[0] == 404)
oc = db("SELECT o.id FROM ocorrencias o WHERE o.lote_id=%s", (lC,))[0][0]
t("ocorrência de outra congregação: 404 (ver/editar/excluir)", call(mar, f"/api/ocorrencias/{oc}")[0] == 404 and call(mar, f"/api/ocorrencias/{oc}", {"data": "2025-11-10", "congregacao_id": ca, "comuns": {}, "campos": linha()}, "PUT")[0] == 404 and call(mar, f"/api/ocorrencias/{oc}", m="DELETE")[0] == 404)
t("lote C continua intacto", db("SELECT situacao FROM lotes_ocorrencias WHERE id=%s", (lC,))[0][0] == "Lançada" and db("SELECT cancelado_em IS NULL FROM ocorrencias WHERE id=%s", (oc,))[0][0])
s, r = lote(mar, cc, "Invasor"); t("não lança em congregação não liberada (400)", s == 400 and "acesso" in str(r))
s, r = lote(mar, ca, "Ok Maria"); t("lança na congregação liberada (201)", s == 201); lM = r["id"]
t("não move lote próprio para congregação não liberada", call(mar, f"/api/lotes/{lM}", {"data": "2025-11-10", "congregacao_id": cc, "comuns": {}, "linhas": [linha()]}, "PUT")[0] == 400)
ocM = db("SELECT id FROM ocorrencias WHERE lote_id=%s", (lM,))[0][0]
t("não move ocorrência própria para congregação não liberada", call(mar, f"/api/ocorrencias/{ocM}", {"data": "2025-11-10", "congregacao_id": cc, "comuns": {}, "campos": linha("Ok Maria")}, "PUT")[0] == 400)
t("edita ocorrência própria (200)", call(mar, f"/api/ocorrencias/{ocM}", {"data": "2025-11-10", "congregacao_id": cb, "comuns": {}, "campos": linha("Ok Maria 2")}, "PUT")[0] == 200)

nomes = lambda cl: {x["nome"] for x in call(cl, "/api/membros")[1]}
t("membros: só A e B (sem os de C nem os sem congregação)", {"Mem Alfa", "Mem Beta"} <= nomes(mar) and not ({"Mem Gama", "Mem Sem Cong"} & nomes(mar)))
t("admin vê todos os membros", {"Mem Alfa", "Mem Beta", "Mem Gama", "Mem Sem Cong"} <= nomes(adm))
t("membro de outra congregação / sem congregação: 404", call(mar, f"/api/membros/{mC}")[0] == 404 and call(mar, f"/api/membros/{mN}")[0] == 404 and call(mar, f"/api/membros/{mA}")[0] == 200)
base = lambda **k: dict(dict(nome="X", status="Ativo", status_membresia="Membro", congregacao_id=ca, data_nascimento="1990-11-12"), **k)
t("não cadastra membro em congregação não liberada (403)", call(mar, "/api/membros", base(congregacao_id=cc))[0] == 403)
t("não cadastra membro sem congregação (400: campo obrigatório)", call(mar, "/api/membros", base(congregacao_id=None))[0] == 400)
t("não edita membro de outra congregação (404)", call(mar, f"/api/membros/{mC}", base(nome="Hack"), "PUT")[0] == 404)
t("não move membro para congregação não liberada (403)", call(mar, f"/api/membros/{mA}", base(nome="Mem Alfa", congregacao_id=cc), "PUT")[0] == 403)
t("não exclui membro de outra congregação (404)", call(mar, f"/api/membros/{mC}", m="DELETE")[0] == 404 and db("SELECT excluido_em IS NULL FROM membros WHERE id=%s", (mC,))[0][0])
t("edita membro liberado (200)", call(mar, f"/api/membros/{mA}", base(nome="Mem Alfa"), "PUT")[0] == 200)
anv = lambda cl, q="": {x["nome"] for x in call(cl, "/api/aniversariantes?" + q)[1].get("rows", [])}
t("aniversariantes: só das congregações liberadas", {"Mem Alfa", "Mem Beta"} <= anv(mar) and not ({"Mem Gama", "Mem Sem Cong"} & anv(mar)) and {"Mem Gama", "Mem Sem Cong"} <= anv(adm))
t("aniversariantes filtrando congregação alheia: vazio", anv(mar, f"cong={cc}") == set())
imp = mar.get(f"/aniversariantes/imprimir?cong={cc}").get_data(as_text=True)
t("impressão não revela nome de congregação alheia nem seus membros", "Gama" not in imp and "Mem Gama" not in imp)
pm = call(mar, "/api/painel/membros")[1]; pa = call(adm, "/api/painel/membros")[1]
t("resumo dos membros respeita o escopo", pm["ativos"] == 2 and pa["ativos"] == 4)
t("painel: contagem de ocorrências e congregações respeita o escopo", call(mar, "/api/painel")[1]["cong"] == 2 and call(adm, "/api/painel")[1]["cong"] == 3)
rq = lambda cl, d: call(cl, f"/api/relatorios/comparativo?descricao={quote(d)}&de=2025-11-01&ate=2025-11-30")[1]
rm_, ra_ = rq(mar, "membros"), rq(adm, "membros")
t("relatório de membros: só A e B, total sem C e sem 'Sem congregação'", {l["congregacao"] for l in rm_["linhas"]} == {"Alfa", "Beta"} and rm_["total"]["atual"] == 2 and ra_["total"]["atual"] == 4)
t("relatório de cargos não conta o cargo da congregação alheia", rq(mar, "cargo:todos")["total"]["atual"] == 0 and rq(adm, "cargo:todos")["total"]["atual"] == 1)
ro_m = call(mar, f"/api/relatorios/comparativo?descricao={quote('oc:' + slug)}&de=2025-11-01&ate=2025-11-30")[1]; ro_a = call(adm, f"/api/relatorios/comparativo?descricao={quote('oc:' + slug)}&de=2025-11-01&ate=2025-11-30")[1]
t("relatório de ocorrências respeita o escopo", ro_m.get("total", {}).get("atual", -1) < ro_a.get("total", {}).get("atual", -2) and {l["congregacao"] for l in ro_m["linhas"]} <= {"Alfa", "Beta"})
t("impressão do relatório não traz congregação alheia", "Gama" not in mar.get(f"/relatorios/comparativo/imprimir?descricao={quote('membros')}&de=2025-11-01&ate=2025-11-30").get_data(as_text=True))

# ---------- mudanças do administrador valem na hora (permissões vêm do banco, não do cookie)
call(adm, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="usuario", ativo=True, congregacoes=[cc]), "PUT")
t("troca de congregações vale na próxima requisição", {r["congregacao"] for r in call(mar, "/api/registros")[1]} == {"Gama"} and call(mar, f"/api/membros/{mC}")[0] == 200 and call(mar, f"/api/membros/{mA}")[0] == 404)
call(adm, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="usuario", ativo=False, congregacoes=[cc]), "PUT")
t("usuário desativado perde o acesso na hora (sessão aberta)", call(mar, "/api/registros")[0] == 401 and mar.get("/").status_code == 302)
t("usuário desativado não consegue entrar", entrar("maria", "senha-maria-1").get("/api/meta").status_code == 401)
call(adm, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="usuario", ativo=True, congregacoes=[ca]), "PUT")
mar = entrar("maria", "senha-maria-1"); t("reativado volta a entrar", call(mar, "/api/meta")[0] == 200)
call(adm, f"/api/usuarios/{uid}", dict(nome="Maria", perfil="usuario", ativo=True, congregacoes=[ca], senha="nova-senha-99"), "PUT")
t("trocar a senha derruba sessões antigas", call(mar, "/api/meta")[0] == 401 and entrar("maria", "senha-maria-1").get("/api/meta").status_code == 401 and entrar("maria", "nova-senha-99").get("/api/meta").status_code == 200)
t("edição sem senha mantém a senha atual", (call(adm, f"/api/usuarios/{uid}", dict(nome="Maria S.", perfil="usuario", ativo=True, congregacoes=[ca]), "PUT")[0] == 200) and entrar("maria", "nova-senha-99").get("/api/meta").status_code == 200)
t("login não pode ser trocado", db("SELECT login FROM usuarios WHERE id=%s", (uid,))[0][0] == "maria" and call(adm, f"/api/usuarios/{uid}", dict(nome="Maria S.", login="outra", perfil="usuario", ativo=True, congregacoes=[ca]), "PUT")[0] == 200 and db("SELECT login FROM usuarios WHERE id=%s", (uid,))[0][0] == "maria")

# ---------- administradores
chefe = entrar("chefe", "senha-maria-1")
t("segundo administrador tem acesso total", call(chefe, "/api/usuarios")[0] == 200 and len(call(chefe, "/api/congregacoes")[1]) == 3 and len(call(chefe, "/api/membros")[1]) == 4)
t("admin não rebaixa a si mesmo", call(adm, "/api/usuarios/1", dict(nome="Administrador", perfil="usuario", ativo=True, congregacoes=[ca]), "PUT")[0] == 400)
t("admin não desativa a si mesmo", call(adm, "/api/usuarios/1", dict(nome="Administrador", perfil="admin", ativo=False, congregacoes=[]), "PUT")[0] == 400)
t("admin continua ativo", db("SELECT perfil,ativo FROM usuarios WHERE id=1")[0] == ("admin", True))
call(adm, f"/api/usuarios/{uid2}", dict(nome="Chefe", perfil="usuario", ativo=True, congregacoes=[ca]), "PUT")
t("administrador rebaixado perde o acesso administrativo na hora", call(chefe, "/api/usuarios")[0] == 403 and call(chefe, "/api/congregacoes", {"nome": "Y"})[0] == 403 and {x["id"] for x in call(chefe, "/api/congregacoes")[1]} == {ca})
t("usuário inexistente: 404", call(adm, "/api/usuarios/9999", dict(nome="Z", perfil="admin", ativo=True, congregacoes=[]), "PUT")[0] == 404)
t("sem sessão: 401 nas rotas de usuários", call(A.app.test_client(), "/api/usuarios")[0] == 401)
t("migrate repetido não quebra", (A.init_db() or True) and len(db("SELECT 1 FROM usuarios")) == 3)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
