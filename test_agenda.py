"""Testes de integração da AGENDA (semana, eventos, aniversariantes, Datashow, permissões).
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
call(adm, "/api/usuarios", dict(nome="Comum", login="comum", senha="senha-comum-1", perfil="usuario", ativo=True, congregacoes=[]))
com = entrar("comum", "senha-comum-1")
def comp(client, **k):
    b = dict(data="2026-11-18", hora="19:30", titulo="Culto de Doutrina"); b.update(k)
    return call(client, "/api/agenda/compromissos", b)
def semana(client, d): return call(client, "/api/agenda/semana?data=" + d)[1]
def evento(client, **k):
    b = dict(data_inicio="2026-11-20", data_fim="2026-11-22", titulo="Retiro"); b.update(k)
    return call(client, "/api/agenda/eventos", b)

# ---------- cadastro simples: data + horário + descrição
s, j = comp(adm)
t("cria compromisso só com data, horário e descrição (201)", s == 201 and j["id"] > 0); id1 = j["id"]
t("mesmo compromisso (data+hora+texto, sem diferenciar maiúsculas) é barrado: 409", comp(adm, titulo="  culto de DOUTRINA ")[0] == 409)
t("mesma descrição em outro horário é permitida", comp(adm, hora="20:30")[0] == 201)
t("compromisso sem horário é permitido", comp(adm, hora="", titulo="Limpeza do templo", data="2026-11-16")[0] == 201)
t("data inválida: 400", comp(adm, data="2026-02-30")[0] == 400)
t("horário inválido: 400", comp(adm, hora="25:00", titulo="X")[0] == 400)
t("descrição vazia: 400", comp(adm, titulo="  ")[0] == 400)
t("usuário comum também cadastra", comp(com, titulo="Reunião de obreiros", hora="20:00")[0] == 201)
t("categoria fica vazia no banco (não é mais pedida)", db("SELECT count(*) FROM agenda_compromissos WHERE categoria_id IS NOT NULL")[0][0] == 0)

# ---------- semana
w = semana(adm, "2026-11-18")
t("semana vai de segunda a domingo", w["inicio"] == "2026-11-16" and w["fim"] == "2026-11-22" and len(w["dias"]) == 7 and w["dias"][0]["nome"] == "SEGUNDA-FEIRA" and w["dias"][6]["nome"] == "DOMINGO")
t("semana anterior/próxima corretas", w["anterior"] == "2026-11-09" and w["proxima"] == "2026-11-23")
t("qualquer dia da semana (domingo) cai na mesma semana", semana(adm, "2026-11-22")["inicio"] == "2026-11-16")
t("compromissos no dia certo, por horário (sem horário primeiro)", [c["titulo"] for c in w["dias"][0]["compromissos"]] == ["Limpeza do templo"] and [c["hora"] for c in w["dias"][2]["compromissos"]] == ["19:30", "20:00", "20:30"])
t("outra semana vem vazia", all(not d["compromissos"] for d in semana(adm, "2026-11-25")["dias"]))
t("virada de ano: 2027-01-01 está na semana de 2026-12-28", semana(adm, "2027-01-01")["inicio"] == "2026-12-28")
t("data inválida na consulta: 400", call(adm, "/api/agenda/semana?data=abc")[0] == 400)

# ---------- edição
t("edita compromisso", call(adm, f"/api/agenda/compromissos/{id1}", dict(data="2026-11-19", hora="19:00", titulo="Culto de Doutrina (editado)"), "PUT")[0] == 200)
t("edição refletida na semana", any(c["id"] == id1 and c["hora"] == "19:00" for c in semana(adm, "2026-11-19")["dias"][3]["compromissos"]))
t("editar para virar duplicado: 409", call(adm, f"/api/agenda/compromissos/{id1}", dict(data="2026-11-18", hora="20:00", titulo="Reunião de obreiros"), "PUT")[0] == 409)
t("editar inexistente: 404", call(adm, "/api/agenda/compromissos/9999", dict(data="2026-11-18", titulo="Z"), "PUT")[0] == 404)

# ---------- destaque
t("destaca compromisso da semana", call(adm, "/api/agenda/destaque", dict(semana="2026-11-18", compromisso_id=id1), "PUT")[0] == 200)
t("destaque aparece na semana (qualquer dia dela)", (semana(com, "2026-11-22")["destaque"] or {}).get("id") == id1)
t("destaque não aparece em outra semana", semana(adm, "2026-11-25")["destaque"] is None)
t("compromisso de outra semana não pode ser destaque: 400", call(adm, "/api/agenda/destaque", dict(semana="2026-11-25", compromisso_id=id1), "PUT")[0] == 400)
t("remove destaque", call(adm, "/api/agenda/destaque", dict(semana="2026-11-16", compromisso_id=None), "PUT")[0] == 200 and semana(adm, "2026-11-18")["destaque"] is None)
call(adm, "/api/agenda/destaque", dict(semana="2026-11-16", compromisso_id=id1), "PUT")

# ---------- eventos (lembretes)
s, j = evento(adm); t("cria evento com período (201)", s == 201); ev1 = j["id"]
t("evento de um dia (sem data final)", evento(adm, data_inicio="2026-11-28", data_fim="", titulo="Encontro de pastores")[0] == 201)
t("evento duplicado: 409", evento(adm, titulo=" RETIRO ")[0] == 409)
t("data final anterior à inicial: 400", evento(adm, data_fim="2026-11-19", titulo="Z")[0] == 400)
t("evento sem descrição: 400", evento(adm, titulo="")[0] == 400)
t("usuário comum cadastra evento", evento(com, data_inicio="2026-12-05", data_fim=None, titulo="Festa dos varões")[0] == 201)
L = call(adm, "/api/agenda/eventos?data=2026-11-18")[1]
t("lista eventos a partir da semana, em ordem de data", [e["titulo"] for e in L] == ["Retiro", "Encontro de pastores", "Festa dos varões"])
t("evento já encerrado não aparece nas semanas seguintes", [e["titulo"] for e in call(adm, "/api/agenda/eventos?data=2026-11-30")[1]] == ["Festa dos varões"])
t("evento em andamento ainda aparece (período cruza a semana)", "Retiro" in [e["titulo"] for e in call(adm, "/api/agenda/eventos?data=2026-11-22")[1]])
t("edita evento", call(adm, f"/api/agenda/eventos/{ev1}", dict(data_inicio="2026-11-20", data_fim="2026-11-23", titulo="Retiro de jovens"), "PUT")[0] == 200)
t("editar evento inexistente: 404", call(adm, "/api/agenda/eventos/9999", dict(data_inicio="2026-11-20", titulo="Z"), "PUT")[0] == 404)

# ---------- aniversariantes da semana (reaproveita o cadastro de membros)
cg = call(adm, "/api/congregacoes", {"nome": "Sede"})[0] and call(adm, "/api/congregacoes")[1][0]["id"]
def nasc(nome, d, **k): return call(adm, "/api/membros", dict(nome=nome, status="Ativo", status_membresia="Membro", congregacao_id=cg, data_nascimento=d, **k))[0]
t("membros de teste criados", all(x == 201 for x in [nasc("Ana Segunda", "1990-11-16"), nasc("Bruno Domingo", "1985-11-22"), nasc("Carla Fora", "1990-11-23"), nasc("Davi Quarta", "2001-11-18"), nasc("Eva Falecida", "1950-11-17", status="Faleceu")]))
an = call(adm, "/api/agenda/aniversariantes?data=2026-11-19")[1]
t("aniversariantes de segunda a domingo, em ordem do dia", [r["nome"] for r in an["rows"]] == ["Ana Segunda", "Davi Quarta", "Bruno Domingo"])
t("traz a data na semana e o dia", an["rows"][0]["data"] == "2026-11-16" and an["rows"][2]["dia_semana"] == "DOMINGO")
t("quem faz aniversário fora da semana ou faleceu não aparece", not any(r["nome"] in ("Carla Fora", "Eva Falecida") for r in an["rows"]))
nasc("Fim de Ano", "1990-01-01"); nasc("Natal", "1990-12-30")
t("semana que vira o ano (28/12 a 03/01) inclui dezembro e janeiro", [r["nome"] for r in call(adm, "/api/agenda/aniversariantes?data=2027-01-01")[1]["rows"]] == ["Natal", "Fim de Ano"])

# ---------- Datashow
d = call(adm, "/api/agenda/datashow?data=2026-11-18")[1]
t("datashow traz 7 dias, destaque, eventos e aniversariantes", len(d["dias"]) == 7 and d["destaque"]["id"] == id1 and len(d["eventos"]) >= 2 and len(d["aniversariantes"]) == 3)
c = adm.get("/datashow?data=2026-11-18")
t("página /datashow abre para quem está logado", c.status_code == 200 and b"DE" in c.data and "Datashow" in c.get_data(as_text=True))
t("sem sessão: /datashow redireciona ao login", A.app.test_client().get("/datashow").status_code == 302)

# ---------- exclusão
t("excluir inexistente: 404", call(adm, "/api/agenda/compromissos/9999", m="DELETE")[0] == 404 and call(adm, "/api/agenda/eventos/9999", m="DELETE")[0] == 404)
t("exclui evento", call(com, f"/api/agenda/eventos/{ev1}", m="DELETE")[0] == 200)
t("exclui o compromisso destacado", call(com, f"/api/agenda/compromissos/{id1}", m="DELETE")[0] == 200)
t("destaque some junto", semana(adm, "2026-11-18")["destaque"] is None and db("SELECT count(*) FROM agenda_destaques")[0][0] == 0)

# ---------- segurança
t("sem sessão: 401 nas rotas da agenda", all(call(A.app.test_client(), u)[0] == 401 for u in ("/api/agenda/semana", "/api/agenda/eventos", "/api/agenda/aniversariantes", "/api/agenda/datashow")))
t("sem CSRF: 400", call(adm, "/api/agenda/compromissos", dict(data="2026-11-18", titulo="K"), csrf=False)[0] == 400)
t("migrate repetido não quebra a agenda", (A.init_db() or True) and len(db("SELECT 1 FROM agenda_categorias")) == 4)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
