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
call(adm, "/api/congregacoes", {"nome": "Sede"}); call(adm, "/api/congregacoes", {"nome": "Ribeira"})
cgA, cgB = [next(x["id"] for x in call(adm, "/api/congregacoes")[1] if x["nome"] == n) for n in ("Sede", "Ribeira")]
call(adm, "/api/usuarios", dict(nome="Comum", login="comum", senha="senha-comum-1", perfil="usuario", ativo=True, congregacoes=[cgA]))   # usuário comum: só a Sede
com = entrar("comum", "senha-comum-1")
def comp(client, **k):
    b = dict(data="2026-11-18", hora="19:30", titulo="Culto de Doutrina", congregacao_id=cgA); b.update(k)
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
t("remove destaque", call(adm, "/api/agenda/destaque", dict(semana="2026-11-16", compromisso_id=None, congregacao_id=cgA), "PUT")[0] == 200 and semana(adm, "2026-11-18")["destaque"] is None)
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
cg = cgA
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

# ---------- repetir em outros dias (cópias independentes)
sid = comp(adm, data="2026-12-01", hora="19:00", titulo="Culto de Oração")[1]["id"]   # terça
def rep(body, i=None): return call(adm, f"/api/agenda/compromissos/{i or sid}/repetir", body)
s, j = rep(dict(dias=[2, 4], semanas=1, simular=True))
t("simulação lista 2 datas novas e não cria nada", s == 200 and j["simulacao"] and [x["data"] for x in j["datas"]] == ["2026-12-02", "2026-12-04"] and j["criados"] == 0 and db("SELECT count(*) FROM agenda_compromissos WHERE titulo='Culto de Oração'")[0][0] == 1)
s, j = rep(dict(dias=[2, 4], semanas=1))
t("repete na quarta e na sexta (2 criados)", s == 200 and j["criados"] == 2 and j["ignorados"] == 0)
t("cópias com mesmo horário e descrição", db("SELECT count(*) FROM agenda_compromissos WHERE titulo='Culto de Oração' AND hora='19:00' AND data IN ('2026-12-02','2026-12-04')")[0][0] == 2)
s, j = rep(dict(dias=[2, 3], semanas=1))
t("dia que já tem o compromisso é ignorado, só o novo é criado", s == 200 and j["criados"] == 1 and j["ignorados"] == 1 and [x["status"] for x in j["datas"]] == ["ja_existe", "nova"])
s, j = rep(dict(dias=[1], semanas=4))
t("mesmo dia por 4 semanas cria 3 cópias (origem não duplica)", s == 200 and j["criados"] == 3 and db("SELECT count(*) FROM agenda_compromissos WHERE titulo='Culto de Oração' AND data IN ('2026-12-08','2026-12-15','2026-12-22')")[0][0] == 3)
t("repetir de novo não duplica nada", rep(dict(dias=[2, 4], semanas=1))[0] == 400 or rep(dict(dias=[2, 4], semanas=1))[1]["criados"] == 0)
t("cópias são independentes: editar uma não altera as outras", call(adm, "/api/agenda/compromissos/%d" % db("SELECT id FROM agenda_compromissos WHERE data='2026-12-02' AND titulo='Culto de Oração'")[0][0], dict(data="2026-12-02", hora="20:00", titulo="Culto de Oração"), "PUT")[0] == 200 and db("SELECT hora::text FROM agenda_compromissos WHERE data='2026-12-04' AND titulo='Culto de Oração'")[0][0].startswith("19:00"))
t("a cópia não herda o destaque da origem", call(adm, "/api/agenda/destaque", dict(semana="2026-12-01", compromisso_id=sid), "PUT")[0] == 200 and db("SELECT count(*) FROM agenda_destaques WHERE semana='2026-12-01'")[0][0] == 1 and (semana(adm, "2026-12-02")["destaque"] or {}).get("id") == sid)
call(adm, "/api/agenda/destaque", dict(semana="2026-12-01", compromisso_id=None, congregacao_id=cgA), "PUT")
t("sem dias: 400", rep(dict(dias=[], semanas=1))[0] == 400)
t("dia inválido: 400", rep(dict(dias=[9], semanas=1))[0] == 400)
t("semanas fora de 1 a 52: 400", rep(dict(dias=[1], semanas=0))[0] == 400 and rep(dict(dias=[1], semanas=99))[0] == 400)
t("só o dia da origem na mesma semana: 400 (nada novo)", rep(dict(dias=[1], semanas=1))[0] == 400)
t("compromisso inexistente: 404", rep(dict(dias=[2], semanas=1), 9999)[0] == 404)
t("usuário comum também repete", call(com, f"/api/agenda/compromissos/{sid}/repetir", dict(dias=[5], semanas=1))[0] == 200)
t("sem CSRF: 400", call(adm, f"/api/agenda/compromissos/{sid}/repetir", dict(dias=[5], semanas=1), csrf=False)[0] == 400)

# ---------- agenda anual, categorias dos eventos e duplicar evento
cats = call(adm, "/api/agenda/categorias")[1]
t("4 categorias padrão com cor", [c["slug"] for c in cats] == ["culto", "reuniao", "administrativa", "especial"] and all(c["cor"].startswith("#") for c in cats))
t("cadastro rápido de evento não define categoria", db("SELECT categoria_id FROM agenda_eventos WHERE titulo='Festa dos varões'")[0][0] is None)
s, j = evento(adm, data_inicio="2026-08-14", data_fim="2026-08-16", titulo="Festa das crianças", categoria_id=cats[3]["id"]); fest = j["id"]
t("evento com categoria (201)", s == 201 and db("SELECT categoria_id FROM agenda_eventos WHERE id=%s", (fest,))[0][0] == cats[3]["id"])
t("categoria inexistente: 400", evento(adm, titulo="Cat inválida", categoria_id=999)[0] == 400)
t("editar sem enviar categoria preserva a categoria", call(adm, f"/api/agenda/eventos/{fest}", dict(data_inicio="2026-08-14", data_fim="2026-08-16", titulo="Festa das crianças (ed.)"), "PUT")[0] == 200 and db("SELECT categoria_id FROM agenda_eventos WHERE id=%s", (fest,))[0][0] == cats[3]["id"])
t("editar enviando categoria vazia remove a categoria", call(adm, f"/api/agenda/eventos/{fest}", dict(data_inicio="2026-08-14", data_fim="2026-08-16", titulo="Festa das crianças", categoria_id=""), "PUT")[0] == 200 and db("SELECT categoria_id FROM agenda_eventos WHERE id=%s", (fest,))[0][0] is None)
call(adm, f"/api/agenda/eventos/{fest}", dict(data_inicio="2026-08-14", data_fim="2026-08-16", titulo="Festa das crianças", categoria_id=cats[3]["id"]), "PUT")
an = call(adm, "/api/agenda/anual?ano=2026")[1]
t("anual traz eventos do ano com categoria e cor", an["ano"] == 2026 and any(e["titulo"] == "Festa das crianças" and e["cor"] == cats[3]["cor"] and e["categoria"] == cats[3]["nome"] for e in an["eventos"]))
t("anual traz os compromissos do ano", any(c["titulo"] == "Culto de Oração" for c in an["compromissos"]) and len(an["categorias"]) == 4)
evento(adm, data_inicio="2026-12-30", data_fim="2027-01-02", titulo="Retiro de virada")
t("evento que cruza o ano aparece nos dois anos", any(e["titulo"] == "Retiro de virada" for e in call(adm, "/api/agenda/anual?ano=2026")[1]["eventos"]) and any(e["titulo"] == "Retiro de virada" for e in call(adm, "/api/agenda/anual?ano=2027")[1]["eventos"]))
t("evento de outro ano não aparece", not any(e["titulo"] == "Festa das crianças" for e in call(adm, "/api/agenda/anual?ano=2027")[1]["eventos"]))
t("ano inválido: 400", call(adm, "/api/agenda/anual?ano=1900")[0] == 400)
def dup(body, i=None): return call(adm, f"/api/agenda/eventos/{i or fest}/duplicar", body)
s, j = dup(dict(datas=["2026-09-14", "2026-10-14"], simular=True))
t("simulação da duplicação não cria nada", s == 200 and j["simulacao"] and [x["status"] for x in j["datas"]] == ["nova", "nova"] and db("SELECT count(*) FROM agenda_eventos WHERE titulo='Festa das crianças'")[0][0] == 1)
s, j = dup(dict(datas=["2026-09-14", "2026-10-14"]))
t("duplica em 2 datas, mantendo a duração e a categoria", s == 200 and j["criados"] == 2 and db("SELECT count(*) FROM agenda_eventos WHERE titulo='Festa das crianças' AND data_fim=data_inicio+2 AND categoria_id=%s", (cats[3]["id"],))[0][0] == 3)
s, j = dup(dict(datas=["2026-09-14", "2026-11-14"]))
t("data que já tem o evento é ignorada", s == 200 and j["criados"] == 1 and j["ignorados"] == 1)
t("só a data original: 400", dup(dict(datas=["2026-08-14"]))[0] == 400)
t("lista vazia / data inválida: 400", dup(dict(datas=[]))[0] == 400 and dup(dict(datas=["2026-02-30"]))[0] == 400)
t("duplicar evento inexistente: 404", dup(dict(datas=["2026-09-01"]), 9999)[0] == 404)
t("mais de 60 datas: 400", dup(dict(datas=[f"2027-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 8)]))[0] == 400)
t("sem CSRF ao duplicar: 400", call(adm, f"/api/agenda/eventos/{fest}/duplicar", dict(datas=["2026-09-20"]), csrf=False)[0] == 400)

# ---------- resumo do Dashboard
call(adm, "/api/agenda/destaque", dict(semana="2026-12-01", compromisso_id=sid), "PUT")
r = call(com, "/api/agenda/resumo?data=2026-12-01")[1]
t("resumo: destaque da semana, compromissos de hoje, próximos e eventos", r["destaque"]["id"] == sid and any(c["id"] == sid for c in r["compromissos_hoje"]) and len(r["proximos"]) >= 1 and all(p["data"] > "2026-12-01" for p in r["proximos"]) and len(r["eventos"]) <= 3)
t("resumo: eventos já encerrados não entram", not any(e["titulo"] == "Festa das crianças" for e in r["eventos"]))
t("resumo: traz total de aniversariantes da semana", isinstance(r["aniversariantes_semana"], int) and isinstance(r["aniversariantes_hoje"], list))
t("resumo: data inválida 400", call(adm, "/api/agenda/resumo?data=x")[0] == 400)
t("resumo: sem sessão 401", call(A.app.test_client(), "/api/agenda/resumo")[0] == 401 and call(A.app.test_client(), "/api/agenda/anual")[0] == 401)
call(adm, "/api/agenda/destaque", dict(semana="2026-12-01", compromisso_id=None, congregacao_id=cgA), "PUT")

# ---------- exclusão
t("excluir inexistente: 404", call(adm, "/api/agenda/compromissos/9999", m="DELETE")[0] == 404 and call(adm, "/api/agenda/eventos/9999", m="DELETE")[0] == 404)
t("exclui evento", call(com, f"/api/agenda/eventos/{ev1}", m="DELETE")[0] == 200)
t("exclui o compromisso destacado", call(com, f"/api/agenda/compromissos/{id1}", m="DELETE")[0] == 200)
t("destaque some junto", semana(adm, "2026-11-18")["destaque"] is None and db("SELECT count(*) FROM agenda_destaques")[0][0] == 0)

# ---------- AGENDA POR CONGREGAÇÃO (cada congregação monta o seu anúncio semanal)
S = "2026-11-18"   # semana de 16 a 22/11/2026
def sem_c(client, cong=None, d=S): return call(client, "/api/agenda/semana?data=" + d + (f"&cong={cong}" if cong is not None else ""))
t("novo compromisso sem congregação: 400", comp(adm, titulo="Sem cong", congregacao_id=None)[0] == 400)
t("congregação inexistente: 400", comp(adm, titulo="Cong fantasma", congregacao_id=99999)[0] == 400)
t("usuário comum não cria em congregação não liberada: 403", comp(com, titulo="Invasão", congregacao_id=cgB)[0] == 403)
t("mesmo compromisso em duas congregações é permitido (sem 409)", comp(adm, titulo="Culto de Ensino", congregacao_id=cgA)[0] == 201 and comp(adm, titulo="Culto de Ensino", congregacao_id=cgB)[0] == 201)
t("mesmo compromisso na MESMA congregação continua barrado: 409", comp(adm, titulo="culto de ensino", congregacao_id=cgB)[0] == 409)
idB = comp(adm, titulo="Vigília da Ribeira", congregacao_id=cgB)[1]["id"]
tit = lambda j: {c["titulo"] for d in j["dias"] for c in d["compromissos"]}
t("filtro por congregação mostra só a escolhida", "Vigília da Ribeira" in tit(sem_c(adm, cgB)[1]) and "Vigília da Ribeira" not in tit(sem_c(adm, cgA)[1]))
t("administrador sem filtro vê todas as congregações, com o nome de cada uma", "Vigília da Ribeira" in tit(sem_c(adm)[1]) and any(c["congregacao"] == "Ribeira" for d in sem_c(adm)[1]["dias"] for c in d["compromissos"]))
t("usuário comum sem filtro vê só a(s) sua(s) congregação(ões)", "Vigília da Ribeira" not in tit(sem_c(com)[1]) and "Reunião de obreiros" in tit(sem_c(com)[1]))
t("usuário comum filtrando congregação alheia: 403", sem_c(com, cgB)[0] == 403)
t("filtro inválido: 400", sem_c(adm, "abc")[0] == 400)
t("usuário comum não acessa a visão 'sem congregação' (0): 403", sem_c(com, 0)[0] == 403)
t("usuário comum não edita compromisso de outra congregação: 404", call(com, f"/api/agenda/compromissos/{idB}", dict(data=S, titulo="X"), "PUT")[0] == 404)
t("usuário comum não exclui compromisso de outra congregação: 404", call(com, f"/api/agenda/compromissos/{idB}", m="DELETE")[0] == 404)
t("usuário comum não repete compromisso de outra congregação: 404", call(com, f"/api/agenda/compromissos/{idB}/repetir", dict(dias=[5], semanas=1))[0] == 404)
id_com = db("SELECT id FROM agenda_compromissos WHERE titulo='Reunião de obreiros'")[0][0]
t("usuário comum não move compromisso para congregação não liberada: 403", call(com, f"/api/agenda/compromissos/{id_com}", dict(data=S, titulo="Reunião de obreiros", congregacao_id=cgB), "PUT")[0] == 403)
r = call(adm, f"/api/agenda/compromissos/{idB}/repetir", dict(dias=[5], semanas=1))[1]
t("repetir copia na mesma congregação", r["criados"] == 1 and db("SELECT count(*) FROM agenda_compromissos WHERE titulo='Vigília da Ribeira' AND congregacao_id=%s", (cgB,))[0][0] == 2)
# destaques: um por semana E por congregação
idA2 = comp(adm, titulo="Culto de Santa Ceia", data="2026-11-20", congregacao_id=cgA)[1]["id"]
t("destaca na congregação A", call(adm, "/api/agenda/destaque", dict(semana=S, compromisso_id=idA2), "PUT")[0] == 200)
t("destaca na congregação B na mesma semana (não substitui o da A)", call(adm, "/api/agenda/destaque", dict(semana=S, compromisso_id=idB), "PUT")[0] == 200)
wa, wb, wt = sem_c(adm, cgA)[1], sem_c(adm, cgB)[1], sem_c(adm)[1]
t("cada congregação tem o seu destaque", wa["destaque"]["id"] == idA2 and wb["destaque"]["id"] == idB and len(wa["destaques"]) == 1)
t("visão geral traz os destaques de todas", {x["id"] for x in wt["destaques"]} == {idA2, idB} and wt["multi"] is True)
t("remover o destaque da A não mexe no da B", call(adm, "/api/agenda/destaque", dict(semana=S, compromisso_id=None, congregacao_id=cgA), "PUT")[0] == 200 and sem_c(adm, cgA)[1]["destaque"] is None and sem_c(adm, cgB)[1]["destaque"]["id"] == idB)
t("remover destaque sem informar a congregação: 400", call(adm, "/api/agenda/destaque", dict(semana=S, compromisso_id=None), "PUT")[0] == 400)
t("usuário comum não destaca compromisso de outra congregação: 404", call(com, "/api/agenda/destaque", dict(semana=S, compromisso_id=idB), "PUT")[0] == 404)
t("usuário comum não remove destaque de outra congregação: 403", call(com, "/api/agenda/destaque", dict(semana=S, compromisso_id=None, congregacao_id=cgB), "PUT")[0] == 403)
t("trocar o compromisso de congregação desfaz o destaque dele", call(adm, f"/api/agenda/compromissos/{idB}", dict(data=S, titulo="Vigília da Ribeira", congregacao_id=cgA), "PUT")[0] == 200 and sem_c(adm, cgB)[1]["destaque"] is None)
# Datashow, aniversariantes, anual, resumo
d = call(adm, f"/api/agenda/datashow?data={S}&cong={cgA}")[1]
t("Datashow da congregação traz o nome dela e só os compromissos dela", d["congregacao"]["nome"] == "Sede" and all(c["congregacao_id"] == cgA for x in d["dias"] for c in x["compromissos"]))
t("Datashow: usuário comum não abre congregação alheia: 403", call(com, f"/api/agenda/datashow?data={S}&cong={cgB}")[0] == 403)
t("aniversariantes da semana aceitam o filtro de congregação", call(adm, f"/api/agenda/aniversariantes?data={S}&cong={cgB}")[1]["rows"] == [] and len(call(adm, f"/api/agenda/aniversariantes?data={S}&cong={cgA}")[1]["rows"]) == 3)
an_ = call(adm, f"/api/agenda/anual?ano=2026&cong={cgB}")[1]
t("agenda anual filtra os compromissos pela congregação", all(c["congregacao_id"] == cgB for c in an_["compromissos"]) and call(com, f"/api/agenda/anual?ano=2026&cong={cgB}")[0] == 403)
t("resumo do início respeita o filtro", call(adm, f"/api/agenda/resumo?data={S}&cong={cgB}")[0] == 200 and call(com, f"/api/agenda/resumo?data={S}&cong={cgB}")[0] == 403)
# compromissos antigos (anteriores a esta função): ficam sem congregação, só o administrador vê e atribui
leg = db("INSERT INTO agenda_compromissos(data,titulo) VALUES('2026-11-19','Compromisso antigo') RETURNING id")[0][0]
t("antigo aparece só para o administrador (com e sem filtro 0)", "Compromisso antigo" in tit(sem_c(adm)[1]) and "Compromisso antigo" in tit(sem_c(adm, 0)[1]) and "Compromisso antigo" not in tit(sem_c(com)[1]) and "Compromisso antigo" not in tit(sem_c(adm, cgA)[1]))
t("usuário comum não edita nem exclui o antigo: 404", call(com, f"/api/agenda/compromissos/{leg}", dict(data=S, titulo="Z"), "PUT")[0] == 404 and call(com, f"/api/agenda/compromissos/{leg}", m="DELETE")[0] == 404)
t("administrador edita o antigo sem escolher congregação (continua sem)", call(adm, f"/api/agenda/compromissos/{leg}", dict(data="2026-11-19", titulo="Compromisso antigo", congregacao_id=""), "PUT")[0] == 200 and db("SELECT congregacao_id FROM agenda_compromissos WHERE id=%s", (leg,))[0][0] is None)
t("administrador atribui congregação ao antigo", call(adm, f"/api/agenda/compromissos/{leg}", dict(data="2026-11-19", titulo="Compromisso antigo", congregacao_id=cgA), "PUT")[0] == 200 and "Compromisso antigo" in tit(sem_c(com)[1]))
t("não dá para tirar a congregação de um compromisso que já tem: 400", call(adm, f"/api/agenda/compromissos/{leg}", dict(data="2026-11-19", titulo="Compromisso antigo", congregacao_id=""), "PUT")[0] == 400)

# ---------- segurança
t("sem sessão: 401 nas rotas da agenda", all(call(A.app.test_client(), u)[0] == 401 for u in ("/api/agenda/semana", "/api/agenda/eventos", "/api/agenda/aniversariantes", "/api/agenda/datashow", "/api/agenda/anual", "/api/agenda/resumo")))
t("sem CSRF: 400", call(adm, "/api/agenda/compromissos", dict(data="2026-11-18", titulo="K"), csrf=False)[0] == 400)
t("migrate repetido não quebra a agenda", (A.init_db() or True) and len(db("SELECT 1 FROM agenda_categorias")) == 4)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
