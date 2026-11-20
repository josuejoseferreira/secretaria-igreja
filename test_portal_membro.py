"""Testes de integração do Portal do Membro (liberação pelo administrador, 1º acesso, atualização cadastral,
revogação, isolamento entre membros e bloqueios no servidor).
Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução).
Não cobre o JavaScript/layout no navegador (celular e computador): isso está no roteiro manual do relatório."""
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

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
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
def entrar_membro(login, senha):   # o navegador do membro cai em /portal (é lá que o token CSRF da página é gerado)
    c = A.app.test_client(); c.get("/login"); r = c.post("/login", data={"login": login, "senha": senha, "csrf": tok(c)}); c.get("/portal"); return c, r
def cpf(n9):
    d = [int(x) for x in n9]
    for k in (9, 10): d.append(sum(d[i] * (k + 1 - i) for i in range(k)) * 10 % 11 % 10)
    return "".join(map(str, d))
def fmt(c): return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"

adm = entrar("admin", "senha-de-teste-123")
def cong(n): call(adm, "/api/congregacoes", {"nome": n}); return [x for x in call(adm, "/api/congregacoes")[1] if x["nome"] == n][0]["id"]
ca, cb = cong("Alfa"), cong("Beta")
def mem(nome, n9, cid, **k):
    d = dict(nome=nome, data_nascimento="1985-03-20", sexo="Feminino", estado_civil="Casado(a)", cpf=fmt(cpf(n9)), cep="23890-000", logradouro="Rua A", numero="10",
             complemento="Casa", bairro="Centro", cidade="Seropédica", uf="RJ", status="Ativo", status_membresia="Membro", cargo="Diácono", congregacao_id=cid)
    d.update(k); return call(adm, "/api/membros", d)
s, r = mem("Maria das Dores Souza", "111444777", ca); t("cadastro antigo criado pelo admin (fluxo existente)", s == 201); m1 = r["id"]
m2 = mem("Joao Pereira Lima", "529982247", cb)[1]["id"]; m3 = mem("Ana Clara Nunes", "390533447", ca)[1]["id"]
antes_m2 = db("SELECT nome,cep,status_membresia,cargo,congregacao_id,cpf FROM membros WHERE id=%s", (m2,))[0]

# usuário comum (secretário) com a congregação Alfa: NÃO pode liberar acesso (somente administrador)
call(adm, "/api/usuarios", dict(nome="Secretaria Alfa", login="secalfa", senha="senha-sec-123", perfil="usuario", congregacoes=[ca]))
sec = entrar("secalfa", "senha-sec-123")
t("usuário comum NÃO consulta o acesso (403)", call(sec, f"/api/membros/{m1}/acesso")[0] == 403)
t("usuário comum NÃO libera acesso (403)", call(sec, f"/api/membros/{m1}/acesso/liberar", {})[0] == 403)
t("usuário comum NÃO gera nova senha (403)", call(sec, f"/api/membros/{m1}/acesso/nova-senha", {})[0] == 403)
t("usuário comum NÃO revoga (403)", call(sec, f"/api/membros/{m1}/acesso/revogar", {})[0] == 403)
t("sem sessão: 401", call(A.app.test_client(), f"/api/membros/{m1}/acesso/liberar", {}, csrf=False)[0] in (400, 401))
t("nada foi criado pelas tentativas indevidas", db("SELECT count(*) FROM usuarios WHERE perfil='membro'")[0][0] == 0)

# ---------- 1. admin libera o acesso
t("situação inicial: acesso não liberado", call(adm, f"/api/membros/{m1}/acesso")[1]["situacao"] == "nao_liberado")
s, r = call(adm, f"/api/membros/{m1}/acesso/liberar", {}); t("admin libera acesso do membro", s == 200 and r.get("login") and r.get("senha"))
login1, senha1 = r["login"], r["senha"]
t("login gerado é válido e vem do nome (ex.: maria.souza123)", login1.startswith("maria.souza") and A.LOGIN_RE.fullmatch(login1))
t("senha inicial aleatória com 12+ caracteres, maiúscula, minúscula e número", len(senha1) >= 12 and any(x.isupper() for x in senha1) and any(x.islower() for x in senha1) and any(x.isdigit() for x in senha1))
u = db("SELECT perfil,membro_id,trocar_senha,ativo,senha_hash FROM usuarios WHERE login=%s", (login1,))[0]
t("conta vinculada ao cadastro correto, perfil membro, troca de senha obrigatória", u[:4] == ("membro", m1, True, True))
t("senha guardada só como hash", u[4].startswith(("scrypt:", "pbkdf2:")) and senha1 not in u[4])
txt = db("SELECT (SELECT string_agg(row_to_json(x)::text,'') FROM usuarios x)||(SELECT coalesce(string_agg(row_to_json(l)::text,''),'') FROM membro_acesso_log l)||(SELECT coalesce(string_agg(row_to_json(a)::text,''),'') FROM membro_acesso a)")[0][0]
t("senha inicial não aparece em texto puro em nenhuma tabela", senha1 not in txt)
a = db("SELECT situacao,liberado_por,liberado_por_nome,liberado_em IS NOT NULL FROM membro_acesso WHERE membro_id=%s", (m1,))[0]
t("registra data e responsável pela liberação", a == ("ativo", 1, "Administrador", True))
t("liberar de novo com acesso ativo: 409", call(adm, f"/api/membros/{m1}/acesso/liberar", {})[0] == 409)
s, r2 = call(adm, f"/api/membros/{m2}/acesso/liberar", {}); t("outro membro recebe login e senha diferentes", s == 200 and r2["login"] != login1 and r2["senha"] != senha1)
t("liberar acesso não altera Status da membresia nem o cadastro", db("SELECT status_membresia,cargo,nome FROM membros WHERE id=%s", (m1,))[0] == ("Membro", "Diácono", "Maria das Dores Souza"))
t("membro inexistente: 404", call(adm, "/api/membros/99999/acesso/liberar", {})[0] == 404)
t("contas de membro não aparecem na tela de Usuários", all(x["perfil"] != "membro" for x in call(adm, "/api/usuarios")[1]))
uid1 = db("SELECT id FROM usuarios WHERE login=%s", (login1,))[0][0]
t("conta de membro não é editável pela tela de Usuários (404)", call(adm, f"/api/usuarios/{uid1}", dict(nome="X", perfil="admin", ativo=True, congregacoes=[]), "PUT")[0] == 404)
t("não dá para criar conta com perfil membro por /api/usuarios", call(adm, "/api/usuarios", dict(nome="Z", login="zzz", senha="senha-zzz-123", perfil="membro", congregacoes=[]))[0] == 400)

# ---------- 2. primeiro login: troca obrigatória
mb, resp = entrar_membro(login1, senha1)
t("membro entra com as credenciais geradas e vai para /portal", resp.status_code == 302 and resp.headers["Location"].endswith("/portal"))
r = mb.get("/portal"); html = r.data.decode()
t("1º acesso: tela de criar nova senha (sem o formulário)", r.status_code == 200 and "Criar nova senha" in html and "Enviar atualização" not in html)
t("1º acesso: dados do cadastro bloqueados até trocar a senha", call(mb, "/api/portal/meu-cadastro")[0] == 403 and call(mb, "/api/portal/meu-cadastro", {"nome": "X"}, "PUT")[0] == 403)
t("1º acesso: a raiz / manda para o portal, não para o painel", mb.get("/").status_code == 302 and mb.get("/").headers["Location"].endswith("/portal"))
senha_nova = "MinhaSenha2026"
t("senha atual errada: 400", call(mb, "/api/portal/senha", dict(atual="errada", nova=senha_nova, confirmacao=senha_nova))[0] == 400)
t("nova senha curta: 400", call(mb, "/api/portal/senha", dict(atual=senha1, nova="a1", confirmacao="a1"))[0] == 400)
t("nova senha só com letras: 400", call(mb, "/api/portal/senha", dict(atual=senha1, nova="somenteletras", confirmacao="somenteletras"))[0] == 400)
t("nova senha igual à inicial: 400", call(mb, "/api/portal/senha", dict(atual=senha1, nova=senha1, confirmacao=senha1))[0] == 400)
t("confirmação diferente: 400", call(mb, "/api/portal/senha", dict(atual=senha1, nova=senha_nova, confirmacao="outra1234"))[0] == 400)
t("ainda obrigado a trocar após tentativas inválidas", db("SELECT trocar_senha FROM usuarios WHERE id=%s", (uid1,))[0][0] is True)
t("troca a senha inicial com sucesso", call(mb, "/api/portal/senha", dict(atual=senha1, nova=senha_nova, confirmacao=senha_nova))[0] == 200)
t("trocar_senha zerado e sessão atual continua válida", db("SELECT trocar_senha FROM usuarios WHERE id=%s", (uid1,))[0][0] is False and mb.get("/portal").status_code == 200)
html = mb.get("/portal").data.decode()
t("agora o portal mostra o formulário com as 3 seções", "Enviar atualização" in html and "Dados Pessoais" in html and "Endereço" in html and "Dados Eclesiásticos" in html and "Criar nova senha" not in html)
t("senha inicial deixou de funcionar", entrar_membro(login1, senha1)[0].get("/portal").status_code == 302)
t("senha pessoal funciona", entrar_membro(login1, senha_nova)[0].get("/portal").status_code == 200)
t("trocas de senha ficam registradas", [x[0] for x in db("SELECT acao FROM membro_acesso_log WHERE membro_id=%s ORDER BY id", (m1,))] == ["liberou", "troca_senha_inicial"])

# ---------- 3. o membro só enxerga o PRÓPRIO cadastro
s, eu = call(mb, "/api/portal/meu-cadastro")
t("membro consulta o próprio cadastro", s == 200 and eu["nome"] == "Maria das Dores Souza" and eu["cep"] == "23890-000")
t("status da membresia e cargo vêm como somente leitura", eu["leitura"]["status_membresia"] == "Membro" and eu["leitura"]["cargo"] == "Diácono" and "status_membresia" not in {k for k in eu if k != "leitura"})
t("resposta não traz telefone, e-mail, observações nem ids", not any(k in eu for k in ("id", "membro_id", "telefone", "email", "observacoes", "nome_mae", "nome_pai", "cargo", "status")))
t("passar ?id= de outro membro não muda nada (continua o próprio)", call(mb, f"/api/portal/meu-cadastro?id={m2}&membro_id={m2}")[1]["nome"] == "Maria das Dores Souza")
rotas = [("GET", "/api/meta"), ("GET", "/api/membros"), ("GET", f"/api/membros/{m1}"), ("GET", f"/api/membros/{m2}"), ("GET", f"/api/membros/{m2}/foto"), ("GET", "/api/usuarios"), ("GET", "/api/congregacoes"),
         ("GET", "/api/painel"), ("GET", "/api/painel/membros"), ("GET", "/api/registros"), ("GET", "/api/lotes"), ("GET", "/api/aniversariantes"), ("GET", "/api/agenda/semana"),
         ("GET", "/api/config/faixas-etarias"), ("GET", "/api/relatorios/comparativo"), ("GET", f"/api/membros/{m1}/acesso"), ("GET", f"/api/membros/{m2}/acesso")]
t("membro NÃO acessa nenhuma rota administrativa/de consulta de outros (403)", all(call(mb, p, None, m)[0] == 403 for m, p in rotas))
t("membro NÃO cria cadastro", call(mb, "/api/membros", dict(nome="Intruso"))[0] == 403)
t("membro NÃO edita cadastro de terceiros", call(mb, f"/api/membros/{m2}", dict(nome="Hackeado"), "PUT")[0] == 403)
t("membro NÃO edita o cadastro pela rota administrativa (nem o próprio)", call(mb, f"/api/membros/{m1}", dict(nome="X", status_membresia="Congregado"), "PUT")[0] == 403)
t("membro NÃO exclui cadastros (de terceiros nem o próprio)", call(mb, f"/api/membros/{m2}", None, "DELETE")[0] == 403 and call(mb, f"/api/membros/{m1}", None, "DELETE")[0] == 403)
t("membro NÃO mexe em usuários/permissões", call(mb, "/api/usuarios", dict(nome="Root", login="root", senha="senha-root-123", perfil="admin", congregacoes=[]))[0] == 403 and call(mb, f"/api/usuarios/{uid1}", dict(nome="Maria", perfil="admin", ativo=True, congregacoes=[]), "PUT")[0] == 403)
t("membro NÃO libera/revoga acessos", call(mb, f"/api/membros/{m2}/acesso/liberar", {})[0] == 403 and call(mb, f"/api/membros/{m1}/acesso/revogar", {})[0] == 403 and call(mb, f"/api/membros/{m1}/acesso/nova-senha", {})[0] == 403)
t("membro NÃO acessa congregações, configurações, agenda (POST)", call(mb, "/api/congregacoes", {"nome": "X"})[0] == 403 and call(mb, "/api/config/faixas-etarias", {}, "PUT")[0] == 403 and call(mb, "/api/agenda/compromissos", {"titulo": "x"})[0] == 403)
t("páginas administrativas redirecionam para o portal", all(mb.get(p).status_code == 302 and mb.get(p).headers["Location"].endswith("/portal") for p in ("/", "/datashow", "/aniversariantes/imprimir", "/relatorios/comparativo/imprimir")))
t("sem token CSRF o servidor recusa a atualização (400)", mb.open("/api/portal/meu-cadastro", method="PUT", json={"nome": "X"}).status_code == 400)
t("admin não usa o portal (403/redireciona)", call(adm, "/api/portal/meu-cadastro")[0] == 403 and adm.get("/portal").status_code == 302)
t("usuário comum não usa o portal", call(sec, "/api/portal/meu-cadastro")[0] == 403)
t("cadastro de outro membro permanece intacto", db("SELECT nome,cep,status_membresia,cargo,congregacao_id,cpf FROM membros WHERE id=%s", (m2,))[0] == antes_m2)

# ---------- 4. atualização de dados pessoais e endereço
def dados(**k):
    d = dict(nome="Maria das Dores Souza", data_nascimento="1985-03-20", sexo="Feminino", estado_civil="Casado(a)", cpf=fmt(cpf("111444777")), sem_cpf=False, cep="23890-000", logradouro="Rua A",
             numero="10", sem_numero=False, complemento="Casa", bairro="Centro", cidade="Seropédica", uf="RJ", data_conversao="2005-05-05", data_batismo="2006-06-06")
    d.update(k); return d
hist0 = db("SELECT count(*) FROM historico_membro WHERE membro_id=%s", (m1,))[0][0]
s, r = call(mb, "/api/portal/meu-cadastro", dados(), "PUT"); t("enviar sem mudar nada: 200 e 0 alterações, sem novo histórico", s == 200 and r["alterados"] == 0 and db("SELECT count(*) FROM historico_membro WHERE membro_id=%s", (m1,))[0][0] == hist0)
n_alt0 = db("SELECT count(*) FROM membro_alteracoes WHERE membro_id=%s", (m1,))[0][0]
t("sem alterações não gera registro de alteração", n_alt0 == 0)
s, r = call(mb, "/api/portal/meu-cadastro", dados(nome="Maria das Dores Souza Silva", estado_civil="Viúvo(a)", data_conversao="2005-07-07"), "PUT")
t("membro atualiza dados pessoais", s == 200 and r["alterados"] == 3)
m = db("SELECT nome,estado_civil,data_conversao::text,nome_busca FROM membros WHERE id=%s", (m1,))[0]
t("dados pessoais gravados no cadastro dele (e busca por nome atualizada)", m[:3] == ("Maria das Dores Souza Silva", "Viúvo(a)", "2005-07-07") and "silva" in m[3])
s, r = call(mb, "/api/portal/meu-cadastro", dados(nome="Maria das Dores Souza Silva", estado_civil="Viúvo(a)", data_conversao="2005-07-07", cep="23890-001", logradouro="Rua B", numero="", sem_numero=True, bairro="Novo", cidade="Itaguaí"), "PUT")
t("membro atualiza endereço e usa Sem número (S/N)", s == 200 and db("SELECT cep,logradouro,numero,bairro,cidade FROM membros WHERE id=%s", (m1,))[0] == ("23890-001", "Rua B", "S/N", "Novo", "Itaguaí"))
# campos protegidos: o membro tenta alterar o que não pode
s, r = call(mb, "/api/portal/meu-cadastro", dict(dados(nome="Maria das Dores Souza Silva", estado_civil="Viúvo(a)", data_conversao="2005-07-07", cep="23890-001", logradouro="Rua B", sem_numero=True, bairro="Novo", cidade="Itaguaí"),
                                                 status_membresia="Congregado", status="Faleceu", cargo="Pastor", congregacao_id=cb, id=m2, membro_id=m2, data_chegada="2000-01-01", data_saida="2001-01-01", excluido_em="2020-01-01", perfil="admin"), "PUT")
t("tentativa de alterar campos protegidos é ignorada (200, sem alterações)", s == 200 and r["alterados"] == 0)
t("Status da membresia, status, cargo, congregação e datas oficiais NÃO mudaram", db("SELECT status_membresia,status,cargo,congregacao_id,data_chegada,data_saida,excluido_em FROM membros WHERE id=%s", (m1,))[0] == ("Membro", "Ativo", "Diácono", ca, None, None, None))
t("enviar com id de outro membro não altera o outro cadastro", db("SELECT nome,cep,status_membresia,cargo,congregacao_id,cpf FROM membros WHERE id=%s", (m2,))[0] == antes_m2)
t("o perfil da conta não mudou", db("SELECT perfil FROM usuarios WHERE id=%s", (uid1,))[0][0] == "membro")
# validações no servidor
base = dados(nome="Maria das Dores Souza Silva", estado_civil="Viúvo(a)", data_conversao="2005-07-07", cep="23890-001", logradouro="Rua B", sem_numero=True, bairro="Novo", cidade="Itaguaí")
snap = db("SELECT nome,cpf,cep,numero,uf FROM membros WHERE id=%s", (m1,))[0]
for nome, ch in [("nome obrigatório", dict(nome="")), ("data de nascimento obrigatória", dict(data_nascimento="")), ("nascimento futuro", dict(data_nascimento="2999-01-01")), ("sexo inválido", dict(sexo="X")),
                 ("estado civil obrigatório", dict(estado_civil="")), ("CPF obrigatório", dict(cpf="")), ("CPF inválido", dict(cpf="111.111.111-11")), ("CEP inválido", dict(cep="123")), ("UF inválida", dict(uf="R1")),
                 ("logradouro obrigatório", dict(logradouro="")), ("complemento obrigatório", dict(complemento="")), ("bairro obrigatório", dict(bairro="")), ("cidade obrigatória", dict(cidade="")),
                 ("número obrigatório (sem marcar S/N)", dict(numero="", sem_numero=False)), ("número preenchido com S/N marcado", dict(numero="12", sem_numero=True)),
                 ("CPF preenchido com 'Não possui CPF' marcado", dict(sem_cpf=True)), ("data de batismo inválida", dict(data_batismo="31/02/2020")), ("foto inválida", dict(foto="data:image/png;base64,AAAA"))]:
    s, r = call(mb, "/api/portal/meu-cadastro", dict(base, **ch), "PUT"); t(f"servidor recusa: {nome}", s == 400 and r.get("erros"))
t("nenhuma validação reprovada alterou o cadastro", db("SELECT nome,cpf,cep,numero,uf FROM membros WHERE id=%s", (m1,))[0] == snap)
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf=fmt(cpf("529982247"))), "PUT"); t("CPF de outra pessoa: 400 com mensagem amigável", s == 400 and "CPF" in r["erros"][0]["msg"])
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf="", sem_cpf=True), "PUT")
t("'Não possui CPF': CPF deixa de ser obrigatório", s == 200 and db("SELECT cpf,sem_cpf FROM membros WHERE id=%s", (m1,))[0] == (None, True))
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf=fmt(cpf("390533447"))), "PUT"); t("CPF repetido de outro cadastro (Ana) também é recusado", s == 400)
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf=fmt(cpf("987654321"))), "PUT"); t("membro volta a informar um CPF válido", s == 200 and db("SELECT cpf,sem_cpf FROM membros WHERE id=%s", (m1,))[0] == (fmt(cpf("987654321")), False))

# ---------- 5. foto
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf=fmt(cpf("987654321")), foto=PNG), "PUT"); t("membro envia a própria foto", s == 200 and r["alterados"] == 1)
rf = mb.get("/api/portal/foto"); t("membro vê a própria foto (e só ela)", rf.status_code == 200 and rf.mimetype == "image/png" and rf.headers.get("Cache-Control") == "no-store")
t("foto gravada para o membro correto", db("SELECT count(*) FROM membro_foto WHERE membro_id=%s", (m1,))[0][0] == 1 and db("SELECT count(*) FROM membro_foto WHERE membro_id=%s", (m2,))[0][0] == 0)
t("rota administrativa da foto continua bloqueada para o membro", mb.get(f"/api/membros/{m1}/foto").status_code == 403)
t("admin vê a foto pela rota administrativa", adm.get(f"/api/membros/{m1}/foto").status_code == 200)
s, r = call(mb, "/api/portal/meu-cadastro", dict(base, cpf=fmt(cpf("987654321")), foto_remover=True), "PUT"); t("membro remove a foto", s == 200 and db("SELECT count(*) FROM membro_foto WHERE membro_id=%s", (m1,))[0][0] == 0)

# ---------- 6. histórico e auditoria
alts = db("SELECT usuario_id,usuario_nome,campos::text FROM membro_alteracoes WHERE membro_id=%s ORDER BY id", (m1,))
t("cada envio com mudança gerou um registro de alteração da conta do membro", len(alts) >= 5 and all(a[0] == uid1 for a in alts))
t("CPF fica mascarado nos valores antes/depois (proteção de dados)", all(fmt(cpf("111444777")) not in a[2] and fmt(cpf("987654321")) not in a[2] and "111.444.777" not in a[2] for a in alts) and any("***." in a[2] for a in alts))
t("registro traz campos, valor anterior e novo", any('"antes": "Casado(a)"' in a[2] and '"depois": "Vi' in a[2] for a in alts))
hist = call(adm, f"/api/membros/{m1}")[1]["historico"]
t("secretaria identifica no Histórico do membro as alterações feitas pelo próprio membro", any("(membro, via portal)" in h["secretario"] and "Campos alterados" in h["descricao"] for h in hist))
t("descrição do histórico lista só os nomes dos campos (sem valores)", all("Viúvo" not in h["descricao"] and "987" not in h["descricao"] for h in hist if "via portal" in h["secretario"]))
s, ac = call(adm, f"/api/membros/{m1}/acesso")
t("admin vê situação ativa, login, quem liberou, alterações e registro", ac["situacao"] == "ativo" and ac["acesso"]["login"] == login1 and ac["acesso"]["liberado_por_nome"] == "Administrador" and ac["alteracoes"] and ac["log"])
for tb, col in (("membro_alteracoes", "campos"), ("membro_acesso_log", "acao")):
    try:
        with psycopg.connect(url) as c: c.execute(f"UPDATE {tb} SET {col}={col}"); ok = False
    except Exception: ok = True
    try:
        with psycopg.connect(url) as c: c.execute(f"DELETE FROM {tb}"); ok2 = False
    except Exception: ok2 = True
    t(f"banco impede alterar e apagar {tb} (permanente)", ok and ok2)
t("o membro não tem rota para apagar/alterar histórico", call(mb, f"/api/membros/{m1}", dict(historico_novo=[]), "PUT")[0] == 403)

# ---------- 7. sair do sistema
mb2, _ = entrar_membro(login1, senha_nova)
mb2.get("/logout"); t("sair encerra a sessão", call(mb2, "/api/portal/meu-cadastro")[0] == 401 and mb2.get("/portal").status_code == 302)

# ---------- 8. nova senha temporária (admin) derruba sessão e exige nova troca
t("nova senha só para acesso ativo (m3 sem acesso: 409)", call(adm, f"/api/membros/{m3}/acesso/nova-senha", {})[0] == 409)
s, rn = call(adm, f"/api/membros/{m1}/acesso/nova-senha", {}); t("admin gera nova senha temporária", s == 200 and rn["login"] == login1 and rn["senha"] != senha1 and rn["senha"] != senha_nova)
t("a senha anterior e a sessão aberta deixam de valer", call(mb, "/api/portal/meu-cadastro")[0] == 401 and entrar_membro(login1, senha_nova)[0].get("/portal").status_code == 302)
mb, _ = entrar_membro(login1, rn["senha"])
t("entra com a temporária e é obrigado a trocar de novo", "Criar nova senha" in mb.get("/portal").data.decode() and call(mb, "/api/portal/meu-cadastro")[0] == 403)
senha_nova2 = "OutraSenha2027"
t("troca a senha temporária", call(mb, "/api/portal/senha", dict(atual=rn["senha"], nova=senha_nova2, confirmacao=senha_nova2))[0] == 200 and call(mb, "/api/portal/meu-cadastro")[0] == 200)
t("nova senha temporária registrada no log", "nova_senha" in [x[0] for x in db("SELECT acao FROM membro_acesso_log WHERE membro_id=%s", (m1,))])

# ---------- 9. revogação
t("só admin revoga (usuário comum: 403)", call(sec, f"/api/membros/{m1}/acesso/revogar", {})[0] == 403)
t("revogar sem acesso ativo: 409", call(adm, f"/api/membros/{m3}/acesso/revogar", {})[0] == 409)
snap_m1 = db("SELECT nome,status_membresia,status,cargo,cpf,excluido_em FROM membros WHERE id=%s", (m1,))[0]
hist_n = db("SELECT count(*) FROM historico_membro WHERE membro_id=%s", (m1,))[0][0]
s, r = call(adm, f"/api/membros/{m1}/acesso/revogar", {}); t("admin revoga o acesso", s == 200)
t("sessão aberta do membro cai na hora", call(mb, "/api/portal/meu-cadastro")[0] == 401 and mb.get("/portal").status_code == 302)
t("membro com acesso revogado não consegue entrar (senha correta)", entrar_membro(login1, senha_nova2)[0].get("/portal").status_code == 302)
t("revogação registra data e responsável", db("SELECT situacao,revogado_por,revogado_por_nome,revogado_em IS NOT NULL FROM membro_acesso WHERE membro_id=%s", (m1,))[0] == ("revogado", 1, "Administrador", True))
t("conta e cadastro são mantidos; nada é excluído", db("SELECT nome,status_membresia,status,cargo,cpf,excluido_em FROM membros WHERE id=%s", (m1,))[0] == snap_m1 and db("SELECT count(*) FROM usuarios WHERE id=%s", (uid1,))[0][0] == 1 and db("SELECT count(*) FROM historico_membro WHERE membro_id=%s", (m1,))[0][0] == hist_n)
t("situação do acesso independe da membresia (continua Membro/Ativo)", snap_m1[1:3] == ("Membro", "Ativo") and call(adm, f"/api/membros/{m1}/acesso")[1]["situacao"] == "revogado")
t("revogar de novo: 409", call(adm, f"/api/membros/{m1}/acesso/revogar", {})[0] == 409)
t("nova senha com acesso revogado: 409", call(adm, f"/api/membros/{m1}/acesso/nova-senha", {})[0] == 409)

# ---------- 10. liberar novamente
s, rl = call(adm, f"/api/membros/{m1}/acesso/liberar", {}); t("admin libera novamente (novas credenciais)", s == 200 and rl["login"] == login1 and rl["senha"] not in (senha1, senha_nova, senha_nova2, rn["senha"]))
mb, _ = entrar_membro(login1, rl["senha"]); t("membro volta a entrar e é obrigado a trocar a senha", "Criar nova senha" in mb.get("/portal").data.decode())
t("situação volta a ativa e a revogação anterior fica no log permanente", call(adm, f"/api/membros/{m1}/acesso")[1]["situacao"] == "ativo" and "revogou" in [x[0] for x in db("SELECT acao FROM membro_acesso_log WHERE membro_id=%s", (m1,))])
t("senha antiga continua inválida", entrar_membro(login1, senha_nova2)[0].get("/portal").status_code == 302)

# ---------- 11. excluir o cadastro derruba o acesso; bloqueio por tentativas
call(mb, "/api/portal/senha", dict(atual=rl["senha"], nova="TerceiraSenha28", confirmacao="TerceiraSenha28"))
t("membro ativo acessa normalmente", call(mb, "/api/portal/meu-cadastro")[0] == 200)
t("admin exclui o cadastro (exclusão lógica) e o acesso do membro cai", call(adm, f"/api/membros/{m1}", None, "DELETE")[0] == 200 and call(mb, "/api/portal/meu-cadastro")[0] == 401 and entrar_membro(login1, "TerceiraSenha28")[0].get("/portal").status_code == 302)
s, r3 = call(adm, f"/api/membros/{m3}/acesso/liberar", {}); login3 = r3["login"]
for _ in range(5): entrar_membro(login3, "senha-errada-1")
c3, resp3 = entrar_membro(login3, r3["senha"])
t("5 erros seguidos bloqueiam a conta do membro (mesmo com a senha certa)", c3.get("/portal").status_code == 302 and "Muitas tentativas" in resp3.data.decode())
with psycopg.connect(url) as c: c.execute("UPDATE usuarios SET bloqueado_ate=NULL,falhas_login=0 WHERE login=%s", (login3,))
t("terminado o bloqueio, entra normalmente", entrar_membro(login3, r3["senha"])[0].get("/portal").status_code == 200)
for _ in range(8): entrar("admin", "errada")
t("o bloqueio não vale para administradores (login do admin segue igual)", call(entrar("admin", "senha-de-teste-123"), "/api/meta")[0] == 200)

# ---------- 12. cadastros antigos e sistema existente
t("cadastros antigos continuam funcionando (admin lista e abre)", call(adm, "/api/membros")[0] == 200 and call(adm, f"/api/membros/{m2}")[0] == 200 and call(adm, "/api/usuarios")[0] == 200 and call(sec, "/api/membros")[0] == 200)
t("usuário comum continua restrito à congregação dele", {x["id"] for x in call(sec, "/api/membros")[1]} <= {m3})
t("migrate repetido não quebra e preserva os dados", (A.init_db() or True) and db("SELECT count(*) FROM membros")[0][0] == 3 and db("SELECT count(*) FROM membro_acesso")[0][0] == 3)
t("migration 013 registrada", db("SELECT count(*) FROM schema_migrations WHERE arquivo='013_portal_membro.sql'")[0][0] == 1)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res) - sum(res)} (JS/layout no navegador não são cobertos por este script)"); sys.exit(0 if all(res) else 1)
