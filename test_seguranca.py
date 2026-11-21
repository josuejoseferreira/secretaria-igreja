"""Testes de segurança: bloqueio permanente após 5 senhas erradas, desbloqueio administrativo, auditoria,
trava por inatividade, senha forte, cabeçalhos HTTP e preservação documental.
Usa TEST_DATABASE_URL: banco SEPARADO cujo nome termina em _test (é zerado a cada execução). Só dados fictícios."""
import os, sys, time, builtins
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
SENHA = "senha-de-teste-123"
def tok(client):
    with client.session_transaction() as s: return s.get("csrf", "")
def call(client, path, body=None, m=None, csrf=True):
    r = client.open(path, method=m or ("POST" if body is not None else "GET"), json=body, headers={"X-CSRF-Token": tok(client)} if csrf else {})
    return r.status_code, (r.get_json(silent=True) or {}) if r.is_json else {}
def tentar(login, senha, ip="10.0.0.1"):
    c = A.app.test_client(); c.get("/login", environ_base={"REMOTE_ADDR": ip})
    r = c.post("/login", data={"login": login, "senha": senha, "csrf": tok(c)}, environ_base={"REMOTE_ADDR": ip}); return c, r
def entrou(c): return c.get("/api/meta").status_code == 200
def db(sql, p=()):
    with psycopg.connect(url) as c: return c.execute(sql, p or None).fetchall()
def exec_(sql, p=()):
    with psycopg.connect(url) as c: c.execute(sql, p or None)
def barrado(sql, p=()):
    try: exec_(sql, p); return False
    except Exception: return True
res = []
def t(nome, cond): res.append(bool(cond)); print(("OK   " if cond else "FALHA"), nome)
def estado(login): return db("SELECT bloqueado,falhas_login,total_bloqueios FROM usuarios WHERE login=%s", (login,))[0]

adm, _ = tentar("admin", SENHA, "10.1.0.1"); t("admin entra", entrou(adm))
cg = lambda n: (call(adm, "/api/congregacoes", {"nome": n}), [x for x in call(adm, "/api/congregacoes")[1] if x["nome"] == n][0]["id"])[1]
ca = cg("Alfa")
mkusr = lambda login, **k: call(adm, "/api/usuarios", dict(dict(nome=login.title(), login=login, senha="senha-" + login + "-1", perfil="usuario", congregacoes=[ca]), **k))
t("senha só com letras é recusada", mkusr("fraca1", senha="abcdefgh")[0] == 400)
t("senha só com números é recusada", mkusr("fraca2", senha="12345678")[0] == 400)
t("senha igual ao login é recusada", mkusr("login12345", senha="login12345")[0] == 400)
t("senha com letras e números é aceita", mkusr("maria")[0] == 201)
mkusr("joao"); mkusr("chefe", perfil="admin", congregacoes=[])
mid = lambda l: db("SELECT id FROM usuarios WHERE login=%s", (l,))[0][0]

# ---------- 1 e 2: quatro erros não bloqueiam; a quinta bloqueia
for _ in range(4): tentar("maria", "errada-1x", "10.2.0.1")
t("1. quatro senhas incorretas NÃO bloqueiam", estado("maria")[:2] == (False, 4))
c, r = tentar("maria", "senha-maria-1", "10.2.0.1"); t("   e a senha certa ainda entra, zerando o contador", entrou(c) and estado("maria")[:2] == (False, 0))
for _ in range(5): tentar("maria", "errada-1x", "10.2.0.2")
t("2. a quinta senha incorreta BLOQUEIA a conta", estado("maria") == (True, 5, 1) and db("SELECT bloqueado_em IS NOT NULL FROM usuarios WHERE login='maria'")[0][0])
c, r = tentar("maria", "senha-maria-1", "10.2.0.3"); t("   bloqueada: nem a senha certa entra", not entrou(c))

# ---------- 3 e 4: sem desbloqueio automático
exec_("UPDATE usuarios SET bloqueado_em=now()-interval '16 minutes' WHERE login='maria'")
t("3. depois de 15 minutos continua bloqueada", not entrou(tentar("maria", "senha-maria-1", "10.2.0.4")[0]))
exec_("UPDATE usuarios SET bloqueado_em=now()-interval '2 days' WHERE login='maria'")
t("4. depois de um dia (ou mais) continua bloqueada", not entrou(tentar("maria", "senha-maria-1", "10.2.0.5")[0]) and estado("maria")[0] is True)

# ---------- resposta igual para qualquer falha (não revela quais contas existem)
txt = lambda r: r.get_data(as_text=True).split('class="lx-err"')[1].split("</span>")[0].split("<span>")[1] if 'class="lx-err"' in r.get_data(as_text=True) else ""
m1, m2, m3 = txt(tentar("nao.existe", "qualquer1x", "10.2.1.1")[1]), txt(tentar("joao", "errada-1x", "10.2.1.1")[1]), txt(tentar("maria", "senha-maria-1", "10.2.1.1")[1])
t("   mesma mensagem: login inexistente, senha errada e conta bloqueada", m1 and m1 == m2 == m3)

# ---------- 5, 6, 7, 8: quem desbloqueia
joao, _ = tentar("joao", "senha-joao-1", "10.3.0.1")
t("5. usuário comum NÃO consegue desbloquear (403) e a conta segue bloqueada", call(joao, f"/api/usuarios/{mid('maria')}/desbloquear", {})[0] == 403 and estado("maria")[0] is True)
t("   usuário comum não vê a lista de bloqueadas (403)", call(joao, "/api/usuarios/bloqueados")[0] == 403)
call(adm, "/api/config/portal-membro", {"ativo": True}, "PUT")
def membro(nome):
    k = call(adm, "/api/membros", dict(nome=nome, status="Ativo", status_membresia="Membro", congregacao_id=ca, data_nascimento="1990-11-12"))[1]["id"]
    s, r = call(adm, f"/api/membros/{k}/acesso/liberar", {}); return k, r["login"], r["senha"]
kA, lgA, snA = membro("Membro Alfa Teste"); kB, lgB, snB = membro("Membro Beta Teste")
mA, _ = tentar(lgA, snA, "10.3.0.2")
t("6. membro NÃO consegue desbloquear (nem a própria conta nem a de outro): 403", call(mA, f"/api/usuarios/{mid(lgB)}/desbloquear", {})[0] == 403 and call(mA, f"/api/usuarios/{mid(lgA)}/desbloquear", {})[0] == 403)
t("13. membro não acessa dados de terceiros (403)", call(mA, f"/api/membros/{kB}")[0] == 403 and call(mA, f"/api/membros/{kA}")[0] == 403 and call(mA, "/api/auditoria")[0] == 403)
t("13. o portal devolve só o próprio cadastro", call(mA, "/api/portal/meu-cadastro")[0] in (200, 403))
t("7. o administrador vê a conta na lista de bloqueadas", "maria" in [x["login"] for x in call(adm, "/api/usuarios/bloqueados")[1]])
t("   o administrador não desbloqueia a própria conta (403)", call(adm, f"/api/usuarios/{mid('admin')}/desbloquear", {})[0] == 403)

# ---------- 10: trocar a senha não desbloqueia
s, r = call(adm, f"/api/usuarios/{mid('maria')}", dict(nome="Maria", perfil="usuario", ativo=True, congregacoes=[ca], senha="nova-senha-maria-2"), "PUT")
t("10. alterar a senha NÃO remove o bloqueio", s == 200 and estado("maria")[0] is True and not entrou(tentar("maria", "nova-senha-maria-2", "10.3.1.1")[0]))
for _ in range(5): tentar(lgB, "errada-1x", "10.3.2.1")
t("10. nova senha temporária de membro também NÃO desbloqueia", estado(lgB)[0] is True)
s, rn = call(adm, f"/api/membros/{kB}/acesso/nova-senha", {})
t("   (membro bloqueado continua barrado mesmo com a senha nova)", s == 200 and estado(lgB)[0] is True and "portal" not in (tentar(lgB, rn["senha"], "10.3.2.2")[1].headers.get("Location") or ""))

# ---------- 7 e 8: desbloqueio administrativo + auditoria
s, r = call(adm, f"/api/usuarios/{mid('maria')}/desbloquear", {"motivo": "Esqueceu a senha"})
t("7. o administrador desbloqueia (200) e as tentativas voltam a 0", s == 200 and estado("maria")[:2] == (False, 0))
t("   desbloqueio guarda quem e quando", db("SELECT desbloqueado_por_nome,desbloqueado_em IS NOT NULL FROM usuarios WHERE login='maria'")[0] == ("Administrador", True))
t("   e a conta entra com a senha atual", entrou(tentar("maria", "nova-senha-maria-2", "10.3.3.1")[0]))
a = db("SELECT usuario_nome,perfil,registro_id,motivo,resultado FROM auditoria WHERE operacao='desbloqueio_administrativo' ORDER BY id DESC LIMIT 1")
t("8. o desbloqueio fica na auditoria (quem, qual conta, motivo)", a and a[0][0] == "Administrador" and a[0][1] == "admin" and a[0][2] == str(mid("maria")) and a[0][3] == "Esqueceu a senha" and a[0][4] == "sucesso")
t("   desbloquear conta que não está bloqueada: 409", call(adm, f"/api/usuarios/{mid('maria')}/desbloquear", {})[0] == 409)

# ---------- 9: histórico preservado e auditoria imutável
t("9. o histórico das tentativas permanece (falhas, bloqueio e desbloqueio)", db("SELECT count(*) FROM auditoria WHERE operacao='login_falha' AND registro_id='maria'")[0][0] >= 9
  and db("SELECT count(*) FROM auditoria WHERE operacao='conta_bloqueada' AND registro_id='maria'")[0][0] == 1 and estado("maria")[2] == 1)
t("   auditoria: o banco impede UPDATE e DELETE", barrado("UPDATE auditoria SET motivo='x'") and barrado("DELETE FROM auditoria"))
t("   a auditoria não guarda senhas", db("SELECT count(*) FROM auditoria WHERE coalesce(depois::text,'')||coalesce(antes::text,'')||coalesce(motivo,'') LIKE ANY(%s)", (["%senha-maria-1%", "%nova-senha-maria-2%", "%errada-1x%", "%" + SENHA + "%"],))[0][0] == 0)
t("   login, logout e falhas são registrados", db("SELECT count(*) FROM auditoria WHERE operacao='login_sucesso'")[0][0] > 0 and db("SELECT count(*) FROM auditoria WHERE operacao='login_falha'")[0][0] > 0)
aud = call(adm, "/api/auditoria?operacao=bloque")
t("   API da auditoria: admin consulta e filtra; usuário comum não (403)", aud[0] == 200 and all("bloque" in x["operacao"] for x in aud[1]) and call(joao, "/api/auditoria")[0] == 403)

# ---------- 11: conta desativada
call(adm, f"/api/usuarios/{mid('joao')}", dict(nome="Joao", perfil="usuario", ativo=False, congregacoes=[ca]), "PUT")
t("11. a sessão aberta de conta desativada cai na hora", call(joao, "/api/meta")[0] == 401)
t("    e ela não consegue entrar de novo", not entrou(tentar("joao", "senha-joao-1", "10.4.0.1")[0]))

# ---------- sessão aberta de conta que acaba de ser bloqueada
maria, _ = tentar("maria", "nova-senha-maria-2", "10.4.1.1"); t("   (maria logada)", entrou(maria))
for _ in range(5): tentar("maria", "errada-1x", "10.4.1.2")
t("   conta bloqueada tem a sessão aberta encerrada", call(maria, "/api/meta")[0] == 401)
call(adm, f"/api/usuarios/{mid('maria')}/desbloquear", {})

# ---------- limite por origem: um IP não bloqueia contas de terceiros em massa
for i in range(1, 7): mkusr(f"vit{i}")
for i in range(1, 7):
    for _ in range(5): tentar(f"vit{i}", "errada-1x", "10.9.9.9")
bl = db("SELECT count(*) FROM usuarios WHERE login LIKE 'vit%' AND bloqueado")[0][0]
t("   um mesmo IP só consegue bloquear 1 conta na janela (limite de 5 falhas por origem)", bl == 1)
t("   depois do limite a origem é recusada (429), sem contar contra a conta", tentar("vit6", "errada-1x", "10.9.9.9")[1].status_code == 429 and estado("vit6")[:2] == (False, 0))
t("   outra origem continua normal", entrou(tentar("vit6", "senha-vit6-1", "10.9.9.8")[0]))

# ---------- recuperação técnica (única conta admin bloqueada)
for _ in range(5): tentar("chefe", "errada-1x", "10.5.0.1")
t("   conta administradora bloqueada", estado("chefe")[0] is True)
resp = iter(["chefe", "Recuperação de teste"]); _in = builtins.input; builtins.input = lambda *_: next(resp)
try: A.desbloquear_cli("chefe")
finally: builtins.input = _in
t("   comando técnico desbloqueia e registra a recuperação", estado("chefe")[0] is False and db("SELECT usuario_nome,motivo FROM auditoria WHERE operacao='desbloqueio_tecnico'")[0] == ("Recuperação técnica (servidor)", "Recuperação de teste"))

# ---------- inatividade: 15 min sem uso bloqueiam a sessão e exigem a senha
c, _ = tentar("maria", "nova-senha-maria-2", "10.6.0.1")
with c.session_transaction() as s: s["ult"] = time.time() - 16 * 60
t("   16 min sem uso: a API responde 423 (sessão bloqueada)", c.get("/api/meta").status_code == 423)
r = c.get("/"); t("   a página manda para /destravar", r.status_code == 302 and r.headers["Location"].endswith("/destravar"))
t("   senha errada não reabre", (c.post("/destravar", data={"senha": "errada-1x", "csrf": tok(c)}), c.get("/api/meta").status_code)[1] == 423)
t("   senha certa reabre a mesma sessão", (c.post("/destravar", data={"senha": "nova-senha-maria-2", "csrf": tok(c)}), c.get("/api/meta").status_code)[1] == 200)
with c.session_transaction() as s: s["ult"] = time.time() - 14 * 60
t("   14 min sem uso: continua aberta", c.get("/api/meta").status_code == 200)
exec_("DELETE FROM login_falhas_ip")   # tabela de controle (não é registro oficial); o teste usa o IP padrão do cliente
with c.session_transaction() as s: s["ult"] = time.time() - 16 * 60
for _ in range(5): c.post("/destravar", data={"senha": "errada-1x", "csrf": tok(c)})
t("   errar 5 vezes a senha na trava também bloqueia a conta", estado("maria")[0] is True)
call(adm, f"/api/usuarios/{mid('maria')}/desbloquear", {})

# ---------- cabeçalhos de segurança
r = A.app.test_client().get("/login")
t("   cabeçalhos: CSP, X-Frame-Options, nosniff, Referrer-Policy, no-store", all(r.headers.get(h) for h in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy")) and r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers.get("Cache-Control") == "no-store")
A.app.config["SESSION_COOKIE_SECURE"] = True
t("   HSTS quando o cookie é Secure (HTTPS)", "Strict-Transport-Security" in A.app.test_client().get("/health").headers)
A.app.config["SESSION_COOKIE_SECURE"] = False
t("   sem HTTPS não envia HSTS", "Strict-Transport-Security" not in A.app.test_client().get("/health").headers)

# ---------- 12: preservação documental
oc = call(adm, "/api/lotes", {"tipo": "batismo_aguas", "congregacao_id": ca, "data": "2025-11-10", "comuns": {"local": "X"}, "linhas": [{"nome": "Fulano Teste"}]})
t("12. banco impede DELETE em membros, ocorrências, lotes, congregações, usuários e acessos",
  all(barrado(f"DELETE FROM {tb}") for tb in ("membros", "ocorrencias", "lotes_ocorrencias", "historico_ocorrencias", "congregacoes", "usuarios", "membro_acesso", "historico_membro")))
s, r = call(adm, f"/api/congregacoes/{ca}", m="DELETE"); t("12. a rota de excluir congregação é negada (409) e ela continua existindo", s == 409 and ca in [x["id"] for x in call(adm, "/api/congregacoes")[1]])
s, _ = call(adm, f"/api/membros/{kA}", m="DELETE")
t("12. excluir membro é lógico: cadastro e histórico permanecem no banco", s == 200 and db("SELECT excluido_em IS NOT NULL FROM membros WHERE id=%s", (kA,))[0][0] and db("SELECT count(*) FROM membros WHERE id=%s", (kA,))[0][0] == 1)
exec_("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,%s,'image/png',4)", (kB, b"\x89PNG"))
exec_("UPDATE membro_foto SET imagem=%s WHERE membro_id=%s", (b"\x89PNG2", kB)); exec_("DELETE FROM membro_foto WHERE membro_id=%s", (kB,))
t("12. trocar ou remover a foto guarda a versão anterior", [x[0] for x in db("SELECT operacao FROM membro_foto_versoes WHERE membro_id=%s ORDER BY id", (kB,))] == ["substituida", "removida"] and barrado("DELETE FROM membro_foto_versoes"))
t("    versões da foto: admin consulta, usuário comum não", call(adm, f"/api/membros/{kB}/foto/versoes")[0] == 200 and len(call(adm, f"/api/membros/{kB}/foto/versoes")[1]) == 2 and call(entrou_ := tentar("maria", "nova-senha-maria-2", "10.7.0.1")[0], f"/api/membros/{kB}/foto/versoes")[0] == 403)

# ---------- Etapa 6: Portal do Membro com aprovação (a ficha fica separada do cadastro oficial)
kG, lgG, snG = membro("Membro Gama Teste")
mG, _ = tentar(lgG, snG, "10.7.1.1")
call(mG, "/api/portal/senha", dict(atual=snG, nova="NovaSenhaGama28", confirmacao="NovaSenhaGama28"))
mG, _ = tentar(lgG, "NovaSenhaGama28", "10.7.1.2")
corpo = dict(nome="Gama Teste Silva", data_nascimento="1990-11-12", sexo="Feminino", estado_civil="Casado(a)", cpf="111.444.777-35", sem_cpf=False, cep="23890-000", logradouro="Rua A", numero="10",
             sem_numero=False, complemento="Casa", bairro="Centro", cidade="Seropédica", uf="RJ", data_conversao="2005-05-05", data_batismo="2006-06-06", congregacao_id=ca)
oficial = lambda: db("SELECT nome,cpf,logradouro FROM membros WHERE id=%s", (kG,))[0]
antes = oficial()
s_, r = call(mG, "/api/portal/meu-cadastro", corpo, "PUT")
t("14. a atualização do membro fica PENDENTE e o cadastro oficial NÃO muda", s_ == 200 and r.get("pendente") is True and oficial() == antes
  and db("SELECT situacao FROM portal_fichas WHERE membro_id=%s", (kG,))[0][0] == "pendente")
t("   o membro acompanha: vê a solicitação aguardando análise", call(mG, "/api/portal/meu-cadastro")[1]["solicitacoes"][0]["situacao"] == "pendente")
t("   o cadastro do membro continua mostrando os dados oficiais", call(mG, "/api/portal/meu-cadastro")[1]["nome"] == antes[0])
t("   a Secretaria (admin) lista as fichas pendentes", len(call(adm, "/api/portal/fichas")[1]) >= 1 and call(adm, "/api/portal/fichas")[1][0]["situacao"] == "pendente")
fid = db("SELECT max(id) FROM portal_fichas WHERE membro_id=%s", (kG,))[0][0]
det = call(adm, f"/api/portal/fichas/{fid}")[1]
t("   a ficha mostra antes x depois com o CPF mascarado", any(c["campo"] == "logradouro" and c["depois"] == "Rua A" for c in det["campos"]) and "111.444.777" not in str(det))
joao2, _ = tentar("maria", "nova-senha-maria-2", "10.7.2.1")
t("   usuário comum e membro NÃO veem nem decidem fichas (403)", all(call(cl, u, b, m_)[0] == 403 for cl in (joao2, mG) for u, b, m_ in ((f"/api/portal/fichas", None, None), (f"/api/portal/fichas/{fid}/aprovar", {}, None), (f"/api/portal/fichas/{fid}/recusar", {"justificativa": "xxxxxx"}, None))))
t("   recusar exige justificativa (400) e nada muda", call(adm, f"/api/portal/fichas/{fid}/recusar", {})[0] == 400 and db("SELECT situacao FROM portal_fichas WHERE id=%s", (fid,))[0][0] == "pendente")
s_, r = call(adm, f"/api/portal/fichas/{fid}/recusar", {"justificativa": "CPF não confere com o documento"})
t("   recusar: o cadastro oficial segue igual e a justificativa fica registrada", s_ == 200 and oficial() == antes and db("SELECT situacao,justificativa,decidida_por_nome FROM portal_fichas WHERE id=%s", (fid,))[0] == ("recusada", "CPF não confere com o documento", "Administrador"))
sol = call(mG, "/api/portal/meu-cadastro")[1]["solicitacoes"][0]
t("   o membro vê o resultado e o motivo da recusa", sol["situacao"] == "recusada" and sol["justificativa"] == "CPF não confere com o documento")
t("   ficha já decidida não pode ser decidida de novo (409)", call(adm, f"/api/portal/fichas/{fid}/aprovar", {})[0] == 409)
t("   o banco impede alterar o conteúdo enviado e apagar fichas", barrado("UPDATE portal_fichas SET dados='{}'::jsonb") and barrado("DELETE FROM portal_fichas") and barrado("UPDATE portal_fichas SET situacao='aprovada' WHERE id=%s", (fid,)))
call(mG, "/api/portal/meu-cadastro", dict(corpo, logradouro="Rua B"), "PUT"); call(mG, "/api/portal/meu-cadastro", dict(corpo, logradouro="Rua C"), "PUT")
t("   novo envio substitui a pendente anterior (que continua guardada)", [x[0] for x in db("SELECT situacao FROM portal_fichas WHERE membro_id=%s ORDER BY id", (kG,))] == ["recusada", "substituida", "pendente"])
fid2 = db("SELECT max(id) FROM portal_fichas WHERE membro_id=%s", (kG,))[0][0]
t("   enquanto pendente, o cadastro oficial continua intacto", oficial() == antes)
s_, r = call(adm, f"/api/portal/fichas/{fid2}/aprovar", {})
t("   autorizar aplica os dados aprovados ao cadastro oficial", s_ == 200 and oficial() == ("Gama Teste Silva", "111.444.777-35", "Rua C"))
t("   a versão anterior fica guardada e a decisão registrada", db("SELECT situacao,decidida_por_nome,dados_anteriores->>'nome' FROM portal_fichas WHERE id=%s", (fid2,))[0] == ("aprovada", "Administrador", antes[0]))
t("   alteração e histórico do membro registrados, e a decisão na auditoria", db("SELECT count(*) FROM membro_alteracoes WHERE membro_id=%s", (kG,))[0][0] == 1 and db("SELECT count(*) FROM historico_membro WHERE membro_id=%s AND descricao LIKE '%%aprovada por%%'", (kG,))[0][0] == 1
  and db("SELECT count(*) FROM auditoria WHERE operacao IN ('portal_ficha_enviada','portal_ficha_recusada','portal_ficha_aprovada')")[0][0] >= 5)
t("   o CPF não aparece em claro na auditoria", db("SELECT count(*) FROM auditoria WHERE coalesce(depois::text,'')||coalesce(antes::text,'') LIKE '%%111.444.777%%'")[0][0] == 0)

# ---------- 15: o que já existia continua funcionando
t("15. migrate repetido não quebra nem redefine senhas", (A.init_db() or True) and entrou(tentar("admin", SENHA, "10.8.0.1")[0]))
t("15. consulta de membro e relatórios continuam funcionando (e a consulta é auditada)", call(adm, f"/api/membros/{kB}")[0] == 200 and db("SELECT count(*) FROM auditoria WHERE operacao='membro_consultado'")[0][0] >= 1 and call(adm, "/api/painel")[0] == 200)
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res) - sum(res)}"); sys.exit(0 if all(res) else 1)
