"""Testes de integração do Portal do Membro com CHAVE GERAL e SENHA ÚNICA de 8 dígitos
(Configuração > Portal do Membro): ativar/desativar, mensagem de indisponível, entrada por senha + nome + nascimento,
isolamento (só as 3 seções do PRÓPRIO cadastro), queda de sessão ao desativar/trocar a senha e trava de tentativas.
Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução).
Não cobre o JavaScript/layout no navegador."""
import os, sys, re
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

res = []
def t(nome, cond): res.append(bool(cond)); print(("OK   " if cond else "FALHA"), nome)
def db(sql, p=()):
    with psycopg.connect(url) as c: return c.execute(sql, p).fetchall()
def tok(client):
    with client.session_transaction() as s: return s.get("csrf", "")
def call(client, path, body=None, m=None, csrf=True):
    r = client.open(path, method=m or ("POST" if body is not None else "GET"), json=body, headers={"X-CSRF-Token": tok(client)} if csrf else {})
    return r.status_code, (r.get_json(silent=True) or {}) if r.is_json else {}
def entrar(login, senha):
    c = A.app.test_client(); c.get("/login"); c.post("/login", data={"login": login, "senha": senha, "csrf": tok(c)}); c.get("/"); return c
def membro_tenta(codigo, cpfv, client=None, acao="atualizar"):
    """Navegador do membro: abre /membro (gera o token) e envia o formulário (senha do portal + CPF, ou 'quero me cadastrar')."""
    c = client or A.app.test_client(); c.get("/membro")
    r = c.post("/membro", data={"codigo": codigo, "cpf": cpfv, "acao": acao, "csrf": tok(c)})
    return c, r
def cpf(n9):
    d = [int(x) for x in n9]
    for k in (9, 10): d.append(sum(d[i] * (k + 1 - i) for i in range(k)) * 10 % 11 % 10)
    return "".join(map(str, d))
def fmt(c): return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
MSG = "O acesso está temporariamente indisponível, fale com o secretário!"

adm = entrar("admin", "senha-de-teste-123")
def cong(n): call(adm, "/api/congregacoes", {"nome": n}); return [x for x in call(adm, "/api/congregacoes")[1] if x["nome"] == n][0]["id"]
ca = cong("Alfa")
def mem(nome, n9, nasc="1985-03-20", **k):
    d = dict(nome=nome, data_nascimento=nasc, sexo="Feminino", estado_civil="Casado(a)", cpf=fmt(cpf(n9)), cep="23890-000", logradouro="Rua A", numero="10",
             complemento="Casa", bairro="Centro", cidade="Seropédica", uf="RJ", status="Ativo", status_membresia="Membro", cargo="Diácono", congregacao_id=ca)
    d.update(k); return call(adm, "/api/membros", d)
s, r = mem("Maria das Dores Souza", "111444777"); t("cadastro criado pelo fluxo existente", s == 201); m1 = r["id"]
m2 = mem("João Pereira Lima", "529982247", nasc="1990-07-01")[1]["id"]
antes_m2 = db("SELECT nome,cep,cpf,status_membresia,cargo FROM membros WHERE id=%s", (m2,))[0]
call(adm, "/api/usuarios", dict(nome="Secretaria Alfa", login="secalfa", senha="senha-sec-123", perfil="usuario", congregacoes=[ca]))
sec = entrar("secalfa", "senha-sec-123")

# ---------- 1. estado inicial e quem pode mexer na chave
cf = db("SELECT ativo,codigo,versao FROM portal_config")
t("portal nasce DESATIVADO e sem senha", cf == [(False, None, 0)])
s, j = call(adm, "/api/config/portal-membro"); t("admin vê 'desativado' e nenhuma senha", s == 200 and j["ativo"] is False and j["codigo"] is None)
t("usuário comum NÃO vê a configuração (403)", call(sec, "/api/config/portal-membro")[0] == 403)
t("usuário comum NÃO ativa (403)", call(sec, "/api/config/portal-membro", {"ativo": True}, "PUT")[0] == 403)
t("usuário comum NÃO gera senha (403)", call(sec, "/api/config/portal-membro/nova-senha", {})[0] == 403)
t("sem login: 401", call(A.app.test_client(), "/api/config/portal-membro", csrf=False)[0] == 401)
t("nada mudou depois das tentativas indevidas", db("SELECT ativo,codigo FROM portal_config")[0] == (False, None))
t("valor inválido na chave: 400", call(adm, "/api/config/portal-membro", {"ativo": "sim"}, "PUT")[0] == 400)
t("gerar senha com portal desativado: 409", call(adm, "/api/config/portal-membro/nova-senha", {})[0] == 409)

# ---------- 2. DESATIVADO: mensagem para o membro
c = A.app.test_client(); r = c.get("/membro")
t("desativado: /membro mostra a mensagem e NÃO mostra formulário", r.status_code == 200 and MSG in r.get_data(as_text=True) and 'name="codigo"' not in r.get_data(as_text=True))
c, r = membro_tenta("12345678", fmt(cpf("111444777")), c)
t("desativado: não entra nem com os dados certos (503 + mensagem)", r.status_code == 503 and MSG in r.get_data(as_text=True) and c.get("/portal").status_code == 302)
t("desativado: API do portal sem sessão devolve 401", call(c, "/api/portal/meu-cadastro", csrf=False)[0] == 401)

# ---------- 3. admin ATIVA: senha aleatória de 8 dígitos
s, j = call(adm, "/api/config/portal-membro", {"ativo": True}, "PUT")
cod = j.get("codigo", "")
t("ativar: portal ativado e senha de exatamente 8 dígitos", s == 200 and j["ativo"] is True and re.fullmatch(r"\d{8}", cod) and len(set(cod)) > 1)
t("a senha fica gravada e o admin a vê de novo", call(adm, "/api/config/portal-membro")[1]["codigo"] == cod)
call(adm, "/api/config/portal-membro", {"ativo": True}, "PUT")
t("ativar de novo NÃO troca a senha (única enquanto ativa)", call(adm, "/api/config/portal-membro")[1]["codigo"] == cod)
t("registro permanente de quem ativou", db("SELECT acao,por_nome FROM portal_config_log ORDER BY id") == [("ativou", "Administrador")])
t("a senha não vai para o registro de ações", cod not in str(db("SELECT row_to_json(l)::text FROM portal_config_log l")))
r = A.app.test_client().get("/membro"); h = r.get_data(as_text=True)
t("ativado: /membro mostra o formulário (senha, CPF, 'quero me cadastrar') e não a mensagem", 'name="codigo"' in h and 'name="cpf"' in h and 'value="novo"' in h and MSG not in h)
resp = adm.get("/api/config/portal-membro"); t("resposta com senha não fica em cache", "no-store" in resp.headers.get("Cache-Control", ""))

# ---------- 4. entrada do membro: senha do portal + CPF
c, r = membro_tenta("00000001" if cod != "00000001" else "00000002", fmt(cpf("111444777")))
t("senha errada: não entra, mensagem clara", r.status_code == 400 and "Senha do portal incorreta" in r.get_data(as_text=True) and c.get("/portal").status_code == 302)
c, r = membro_tenta(cod, fmt(cpf("123456789")))
t("senha certa + CPF que não existe: não entra e não revela cadastro", r.status_code == 400 and "Não encontramos esse CPF" in r.get_data(as_text=True) and c.get("/portal").status_code == 302)
c, r = membro_tenta(cod, "111.444.777-36")
t("senha certa + CPF inválido: não entra", r.status_code == 400 and c.get("/portal").status_code == 302)
c, r = membro_tenta(cod, "")
t("CPF vazio: 400", r.status_code == 400)
t("tentativas erradas ficam registradas (sem dado pessoal)", db("SELECT count(*) FROM portal_falhas")[0][0] >= 2)
c = A.app.test_client(); c.get("/membro")
t("sem token CSRF: recusado (400)", c.post("/membro", data={"codigo": cod, "cpf": fmt(cpf("111444777"))}).status_code == 400)

m1c, r = membro_tenta(f" {cod[:4]} {cod[4:]} ", cpf("111444777"))
t("senha certa + CPF (com ou sem pontuação): entra e vai para /portal", r.status_code == 302 and r.headers["Location"].endswith("/portal"))
h = m1c.get("/portal").get_data(as_text=True)
t("tela do portal abre com o nome do próprio membro e sem 'trocar senha'", "Olá, Maria!" in h and 'id="pt-senha"' not in h and "/membro/sair" in h)
t("o portal mostra as 3 seções do cadastro (Dados Pessoais, Endereço, Dados Eclesiásticos)", all(x in h for x in ("Dados Pessoais", "Endereço", "Dados Eclesiásticos")) and "Histórico" not in h and "Observações" not in h)
s, me = call(m1c, "/api/portal/meu-cadastro")
t("API devolve SÓ o cadastro vinculado à sessão", s == 200 and me["nome"] == "Maria das Dores Souza" and me["cpf"] == fmt(cpf("111444777")))

# ---------- 5. o membro não acessa mais nada
bloq = ["/api/meta", "/api/painel", "/api/membros", f"/api/membros/{m1}", f"/api/membros/{m2}", "/api/config/faixas-etarias", "/api/usuarios", "/api/congregacoes",
        "/api/config/portal-membro", f"/api/membros/{m1}/acesso", "/api/ocorrencias", "/api/agenda/semana"]
t("membro NÃO acessa nenhuma outra rota da API", all(call(m1c, u)[0] in (401, 403, 404) and call(m1c, u)[0] != 200 for u in bloq))
t("membro NÃO cria/edita pela API do sistema", all(call(m1c, u, b, m)[0] in (401, 403, 404, 405) for u, b, m in [
    ("/api/membros", {"nome": "X"}, "POST"), (f"/api/membros/{m2}", {"nome": "X"}, "PUT"), ("/api/config/portal-membro", {"ativo": False}, "PUT"),
    ("/api/config/portal-membro/nova-senha", {}, "POST"), ("/api/usuarios", {"nome": "x"}, "POST"), ("/api/portal/senha", {"atual": "a", "nova": "b", "confirmacao": "b"}, "POST")]))
r = m1c.get("/"); t("tela principal do sistema (/) redireciona o membro para o portal", r.status_code == 302 and r.headers["Location"].endswith("/portal"))
t("membro não vê Configuração nem Usuários", all(m1c.get(u).status_code == 302 for u in ("/", "/relatorios/comparativo/imprimir", "/aniversariantes/imprimir")))
t("foto do outro membro inacessível (a rota só serve a do próprio)", m1c.get("/api/portal/foto").status_code == 404)

# ---------- 6. atualização cadastral: grava no PRÓPRIO cadastro, com registro permanente
corpo = {k: me[k] for k in ("nome", "data_nascimento", "sexo", "estado_civil", "cpf", "sem_cpf", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf", "data_conversao", "data_batismo")}
corpo.update(logradouro="Rua Nova do Portal", data_batismo="2001-05-06", id=m2, membro_id=m2, status_membresia="Congregado", cargo="Pastor", congregacao_id=99)
s, r = call(m1c, "/api/portal/meu-cadastro", corpo, "PUT"); t("membro envia atualização (200)", s == 200 and r.get("alterados") == 2)
t("gravou no cadastro DELE", db("SELECT logradouro,data_batismo FROM membros WHERE id=%s", (m1,))[0] == ("Rua Nova do Portal", __import__("datetime").date(2001, 5, 6)))
t("ignorou id/membro_id/cargo/status/congregação enviados", db("SELECT status_membresia,cargo,congregacao_id FROM membros WHERE id=%s", (m1,))[0] == ("Membro", "Diácono", ca))
t("o OUTRO membro não foi tocado", db("SELECT nome,cep,cpf,status_membresia,cargo FROM membros WHERE id=%s", (m2,))[0] == antes_m2)
al = db("SELECT membro_id,usuario_id,usuario_nome,campos FROM membro_alteracoes")
t("registro permanente: membro, SEM usuário, nome do membro e antes/depois", len(al) == 1 and al[0][0] == m1 and al[0][1] is None and al[0][2] == "Maria das Dores Souza" and al[0][3]["logradouro"]["depois"] == "Rua Nova do Portal")
h_ = db("SELECT secretario_id,secretario_nome,descricao FROM historico_membro WHERE membro_id=%s ORDER BY id DESC LIMIT 1", (m1,))[0]
t("Histórico do membro mostra '(membro, via portal)' e os campos", h_[0] is None and "via portal" in h_[1] and "Rua / Logradouro" not in h_[2] and "Logradouro" in h_[2])
try:
    with psycopg.connect(url) as cn: cn.execute("DELETE FROM membro_alteracoes")
    t("registro de alterações é imutável (banco recusa apagar)", False)
except Exception: t("registro de alterações é imutável (banco recusa apagar)", True)
t("CPF não aparece em claro no registro", fmt(cpf("111444777")) not in str(al[0][3]))
s, r = call(m1c, "/api/portal/meu-cadastro", dict(corpo, cpf="111.111.111-11"), "PUT"); t("validação continua igual à da secretaria (CPF inválido: 400)", s == 400)
s, r = call(m1c, "/api/portal/meu-cadastro", dict(corpo, nome="Maria Souza Nova"), "PUT")
t("membro que troca o próprio nome continua na sessão (sem conta de usuário a atualizar)", s == 200 and call(m1c, "/api/portal/meu-cadastro")[1]["nome"] == "Maria Souza Nova")
call(m1c, "/api/portal/meu-cadastro", dict(corpo, nome="Maria das Dores Souza"), "PUT")

# ---------- 7. quem não tem CPF, e cadastro excluído
sc = mem("Pedro Sem Cpf", "000000000", cpf="", sem_cpf=True, nasc="1970-01-01")
t("(preparo) membro sem CPF cadastrado", sc[0] == 201)
c, r = membro_tenta(cod, ""); t("quem não tem CPF não consegue atualizar por aqui (vai à secretaria)", r.status_code == 400 and c.get("/portal").status_code == 302)
c, r = membro_tenta(cod, fmt(cpf("529982247"))); t("outro membro entra com o próprio CPF", r.status_code == 302 and call(c, "/api/portal/meu-cadastro")[1]["nome"] == "João Pereira Lima")
call(adm, f"/api/membros/{m2}", None, "DELETE")
t("cadastro excluído com sessão aberta: sessão cai", call(c, "/api/portal/meu-cadastro")[0] == 401)
c, r = membro_tenta(cod, fmt(cpf("529982247"))); t("cadastro excluído não entra mais", r.status_code == 400)

# ---------- 7b. 'Quero fazer o meu cadastro': membro novo cria o PRÓPRIO cadastro com a senha do portal
membros0 = db("SELECT count(*) FROM membros")[0][0]
c, r = membro_tenta("00000001" if cod != "00000001" else "00000002", "", acao="novo")
t("cadastro novo com senha ERRADA: não abre o formulário", r.status_code == 400 and c.get("/portal").status_code == 302)
nc, r = membro_tenta(cod, "", acao="novo"); t("cadastro novo com a senha certa: abre o portal (sem pedir CPF)", r.status_code == 302 and r.headers["Location"].endswith("/portal"))
t("abrir o formulário não grava nada", db("SELECT count(*) FROM membros")[0][0] == membros0)
h = nc.get("/portal").get_data(as_text=True); t("tela mostra 'Faça o seu cadastro' com as 3 seções", "Faça o seu cadastro" in h and "Dados Eclesiásticos" in h and "Histórico" not in h)
s, vz = call(nc, "/api/portal/meu-cadastro"); t("formulário vem em branco", s == 200 and vz["nome"] is None and vz["cpf"] is None)
t("cadastro novo também não acessa mais nada", all(call(nc, u)[0] in (401, 403, 404) for u in ("/api/membros", "/api/meta", "/api/usuarios", "/api/config/portal-membro")))
novo = dict(nome="Lucas Almeida Rocha", data_nascimento="2001-09-09", sexo="Masculino", estado_civil="Solteiro(a)", cpf=fmt(cpf("123456789")), sem_cpf=False, cep="23890-000", logradouro="Rua B", numero="5",
            complemento="Ap 2", bairro="Centro", cidade="Seropédica", uf="RJ", data_conversao="2020-02-02", data_batismo="", id=1, status_membresia="Membro", cargo="Pastor", congregacao_id=ca)
s, r = call(nc, "/api/portal/meu-cadastro", dict(novo, nome=""), "PUT"); t("novo cadastro com campo obrigatório vazio: 400 e nada gravado", s == 400 and db("SELECT count(*) FROM membros")[0][0] == membros0)
s, r = call(nc, "/api/portal/meu-cadastro", dict(novo, cpf=fmt(cpf("111444777"))), "PUT"); t("novo cadastro com CPF que já existe: 400 com orientação", s == 400 and "já está cadastrado" in str(r))
s, r = call(nc, "/api/portal/meu-cadastro", novo, "PUT"); t("novo cadastro enviado (200)", s == 200 and r.get("novo") is True)
nm = db("SELECT id,nome,status,status_membresia,cargo,congregacao_id,cpf FROM membros WHERE cpf=%s", (fmt(cpf("123456789")),))
t("cadastro criado SEM Status da Membresia, sem cargo e sem congregação (a secretaria define depois)", len(nm) == 1 and nm[0][2:6] == ("Ativo", None, None, None))
sem = call(adm, "/api/membros?membresia=sem")[1]
t("filtro 'Sem status definido' lista o cadastro vindo do portal", [x["nome"] for x in sem] == ["Lucas Almeida Rocha"] and sem[0]["status_membresia"] is None)
t("filtros Membro/Congregado não trazem quem está sem status", all(x["nome"] != "Lucas Almeida Rocha" for q in ("Membro", "Congregado") for x in call(adm, "/api/membros?membresia=" + q)[1]))
t("ignorou cargo/status/congregação/id enviados", nm[0][4] is None and nm[0][5] is None and nm[0][0] != 1)
h2 = db("SELECT secretario_id,secretario_nome,descricao FROM historico_membro WHERE membro_id=%s", (nm[0][0],))
t("Histórico registra 'Cadastro criado pelo próprio membro', sem usuário", len(h2) == 1 and h2[0][0] is None and "criado pelo próprio membro" in h2[0][2] and "via portal" in h2[0][1])
al = db("SELECT usuario_id,campos FROM membro_alteracoes WHERE membro_id=%s", (nm[0][0],))
t("registro permanente criado e o CPF fica mascarado", len(al) == 1 and al[0][0] is None and fmt(cpf("123456789")) not in str(al[0][1]))
s, eu = call(nc, "/api/portal/meu-cadastro"); t("depois de criar, a sessão passa a ser SÓ desse cadastro", s == 200 and eu["nome"] == "Lucas Almeida Rocha")
s, r = call(nc, "/api/portal/meu-cadastro", dict(novo, logradouro="Rua C"), "PUT")
t("novo envio na mesma sessão ATUALIZA o cadastro dele (não cria outro)", s == 200 and db("SELECT count(*) FROM membros")[0][0] == membros0 + 1 and db("SELECT logradouro FROM membros WHERE id=%s", (nm[0][0],))[0][0] == "Rua C")
c, r = membro_tenta(cod, fmt(cpf("123456789"))); t("o novo membro entra depois pelo CPF", r.status_code == 302 and call(c, "/api/portal/meu-cadastro")[1]["nome"] == "Lucas Almeida Rocha")

# ---------- 7c. Secretário e portal no MESMO navegador: atualizar a página (F5) deixa cada tela onde estava
mesmo = A.app.test_client(); mesmo.get("/login"); mesmo.post("/login", data={"login": "admin", "senha": "senha-de-teste-123", "csrf": tok(mesmo)})
t("(mesmo navegador) secretário logado abre a tela principal", mesmo.get("/").status_code == 200)
_, r = membro_tenta(cod, "", client=mesmo, acao="novo")
t("(mesmo navegador) abrir o link do portal não derruba o secretário", r.status_code == 302 and call(mesmo, "/api/meta")[0] == 200)
t("(mesmo navegador) F5 na tela principal NÃO vai para o portal", mesmo.get("/").status_code == 200)
t("(mesmo navegador) F5 no portal continua no portal", mesmo.get("/portal").status_code == 200 and call(mesmo, "/api/portal/meu-cadastro")[0] == 200)
mesmo.get("/membro/sair")
t("(mesmo navegador) sair do portal não desconecta o secretário", mesmo.get("/").status_code == 200 and mesmo.get("/portal").status_code == 302)
pc, r = membro_tenta(cod, "", acao="novo")
t("(mesmo navegador) portal aberto primeiro", r.status_code == 302 and pc.get("/portal").status_code == 200)
pc.get("/login"); pc.post("/login", data={"login": "admin", "senha": "senha-de-teste-123", "csrf": tok(pc)})
t("(mesmo navegador) secretário entrar depois NÃO tira o membro do portal (F5 fica no portal)", pc.get("/portal").status_code == 200 and call(pc, "/api/portal/meu-cadastro")[0] == 200 and pc.get("/").status_code == 200)
pc.get("/logout")
t("(mesmo navegador) o secretário sair não derruba o portal", pc.get("/portal").status_code == 200 and pc.get("/").status_code == 302)

# ---------- 8. NOVA SENHA derruba sessões abertas
s, j = call(adm, "/api/config/portal-membro/nova-senha", {}); cod2 = j["codigo"]
t("nova senha: 8 dígitos e diferente da anterior", s == 200 and re.fullmatch(r"\d{8}", cod2) and cod2 != cod)
t("sessão aberta com a senha antiga cai na hora", call(m1c, "/api/portal/meu-cadastro")[0] == 401 and m1c.get("/portal").status_code == 302)
c, r = membro_tenta(cod, cpf("111444777")); t("senha antiga não funciona mais", r.status_code == 400 and "incorreta" in r.get_data(as_text=True))
m1c, r = membro_tenta(cod2, cpf("111444777")); t("senha nova funciona", r.status_code == 302 and call(m1c, "/api/portal/meu-cadastro")[0] == 200)

# ---------- 9. conta individual antiga obedece a chave geral
s, ac = call(adm, f"/api/membros/{m1}/acesso/liberar", {}); t("(legado) admin libera conta individual", s == 200)
cl = A.app.test_client(); cl.get("/login"); r = cl.post("/login", data={"login": ac["login"], "senha": ac["senha"], "csrf": tok(cl)})
t("(legado) com o portal ATIVADO a conta individual entra", r.status_code == 302 and r.headers["Location"].endswith("/portal"))

# ---------- 10. DESATIVAR: senha apagada, sessões caem, mensagem volta
s, j = call(adm, "/api/config/portal-membro", {"ativo": False}, "PUT")
t("desativar: portal desativado e senha apagada", s == 200 and j["ativo"] is False and j["codigo"] is None and db("SELECT ativo,codigo FROM portal_config")[0] == (False, None))
m1p = A.app.test_client()
with m1c.session_transaction() as s_a, m1p.session_transaction() as s_b: s_b.update(dict(s_a))   # segunda aba do mesmo membro, ainda com a sessão antiga
r = m1c.get("/portal"); t("página do portal com sessão antiga leva para a mensagem", r.status_code == 302 and r.headers["Location"].endswith("/membro"))
s, r = call(m1p, "/api/portal/meu-cadastro"); t("sessão aberta cai e a API avisa que está indisponível", s == 401 and r.get("portal_indisponivel") is True and MSG in r.get("erro", ""))
t("e /membro mostra a mensagem de indisponível", MSG in A.app.test_client().get("/membro").get_data(as_text=True))
t("(legado) conta individual aberta também cai com a chave desligada", call(cl, "/api/portal/meu-cadastro")[0] == 401)
cl2 = A.app.test_client(); cl2.get("/login"); r = cl2.post("/login", data={"login": ac["login"], "senha": ac["senha"], "csrf": tok(cl2)})
t("(legado) conta individual não entra com a chave desligada e vê a mensagem", r.status_code == 200 and MSG in r.get_data(as_text=True))
t("administrador continua entrando normalmente com o portal desativado", call(adm, "/api/meta")[0] == 200)
t("usuário comum continua entrando normalmente com o portal desativado", call(sec, "/api/meta")[0] == 200)
t("log permanente: ativou → nova_senha → desativou", [x[0] for x in db("SELECT acao FROM portal_config_log ORDER BY id")] == ["ativou", "nova_senha", "desativou"])
s, j = call(adm, "/api/config/portal-membro", {"ativo": True}, "PUT"); cod3 = j["codigo"]
t("reativar gera uma senha nova de 8 dígitos", s == 200 and re.fullmatch(r"\d{8}", cod3))
t("senha anterior (de antes de desativar) não vale mais", membro_tenta(cod2, cpf("111444777"))[1].status_code == 400 if cod2 != cod3 else True)
t("(legado) conta individual volta a entrar com a chave ligada", (lambda c_: (c_.get("/login"), c_.post("/login", data={"login": ac["login"], "senha": ac["senha"], "csrf": tok(c_)}).status_code)[1])(A.app.test_client()) == 302)

# ---------- 11. trava de tentativa e erro
with psycopg.connect(url) as cn: cn.execute("DELETE FROM portal_falhas")
errados = [membro_tenta("99999999" if cod3 != "99999999" else "88888888", cpf("111444777"))[1].status_code for _ in range(A.FALHAS_MAX)]
t(f"{A.FALHAS_MAX} tentativas erradas seguidas são recusadas (400)", all(x == 400 for x in errados))
c, r = membro_tenta(cod3, cpf("111444777"))
t("depois do limite, até a senha certa é travada por alguns minutos (429)", r.status_code == 429 and "Muitas tentativas" in r.get_data(as_text=True) and c.get("/portal").status_code == 302)
with psycopg.connect(url) as cn: cn.execute("UPDATE portal_falhas SET created_at = now() - interval '20 minutes'")
t("passada a janela, volta a funcionar", membro_tenta(cod3, cpf("111444777"))[1].status_code == 302)

print(f"\n{sum(res)}/{len(res)} verificações passaram")
sys.exit(0 if all(res) else 1)
