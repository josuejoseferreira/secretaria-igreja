"""Testes de integração da FOTO do membro, nome da mãe/pai e observações (nova página de cadastro).
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
import base64
mz, ct = cong("Matriz"), cong("Centro")
BASE = dict(data_nascimento="1990-05-01", sexo="Masculino", estado_civil="Solteiro(a)", sem_cpf=True, cep="28680-000", logradouro="Rua A", numero="1",
            complemento="Casa", bairro="Centro", cidade="Cachoeiras de Macacu", uf="RJ")   # cadastro completo (campos obrigatórios)
def F(**k):
    d = dict(BASE); d.update(k)
    if d.get("cpf"): d["sem_cpf"] = False
    return d
def novo(**k):
    d = F(nome="Fulano", status="Ativo", status_membresia="Membro", congregacao_id=mz); d.update(k); return call("/api/membros", F(**d))
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200; PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 80; WEBP = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"\x00" * 30
du = lambda mime, b: f"data:{mime};base64," + base64.b64encode(b).decode()
def foto(id, client=cl): return client.get(f"/api/membros/{id}/foto")

# ---------- campos novos
s, j = novo(nome="Ana Teste", nome_mae="  Maria   Souza ", nome_pai="José Souza", observacoes="Linha 1\nLinha 2"); a = j["id"]
t("cria membro com mãe, pai e observações (201)", s == 201)
m = call(f"/api/membros/{a}")[1]
t("campos novos gravados e normalizados", m["nome_mae"] == "Maria Souza" and m["nome_pai"] == "José Souza" and m["observacoes"] == "Linha 1\nLinha 2")
t("sem foto: tem_foto falso e rota de foto 404", m["tem_foto"] is False and m["foto_v"] is None and foto(a).status_code == 404)
t("mãe acima de 200 caracteres: 400", novo(nome="X", nome_mae="m" * 201)[0] == 400)
t("observações acima de 2000 caracteres: 400", novo(nome="X", observacoes="o" * 2001)[0] == 400)
t("editar sem mexer na foto não cria foto", call(f"/api/membros/{a}", F(nome="Ana Teste", status="Ativo", status_membresia="Membro", congregacao_id=mz), "PUT")[0] == 200 and foto(a).status_code == 404)
t("editar limpando mãe/pai/obs grava vazio", (lambda x: x["nome_mae"] is None and x["observacoes"] is None)(call(f"/api/membros/{a}")[1]))

# ---------- foto
s, j = novo(nome="Com Foto", foto=du("image/jpeg", JPG)); b = j["id"]
t("cadastro NOVO já com foto (201)", s == 201)
r = foto(b)
t("foto servida com o conteúdo e tipo corretos", r.status_code == 200 and r.data == JPG and r.mimetype == "image/jpeg")
t("cabeçalhos de segurança/cache da foto", r.headers.get("X-Content-Type-Options") == "nosniff" and "private" in r.headers.get("Cache-Control", "") and r.headers.get("ETag"))
t("foto vinculada ao membro certo (e só a ele)", foto(a).status_code == 404 and db("SELECT count(*) FROM membro_foto")[0][0] == 1)
t("304 quando o navegador já tem a imagem (ETag)", cl.get(f"/api/membros/{b}/foto", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304)
m = call(f"/api/membros/{b}")[1]
t("ficha traz tem_foto e foto_v (para renovar o cache)", m["tem_foto"] is True and isinstance(m["foto_v"], int))
t("listagem não carrega bytes da foto", all("imagem" not in x and "foto" not in x for x in call("/api/membros")[1]))
t("substituir foto por PNG", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto=du("image/png", PNG)), "PUT")[0] == 200 and foto(b).data == PNG and foto(b).mimetype == "image/png")
t("WEBP aceito", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto=du("image/webp", WEBP)), "PUT")[0] == 200 and foto(b).mimetype == "image/webp")
t("há uma única linha de foto por membro", db("SELECT count(*) FROM membro_foto WHERE membro_id=%s", (b,))[0][0] == 1)

# ---------- validações da foto (nada é gravado quando a foto é inválida)
antes = db("SELECT count(*) FROM membros")[0][0]
for rot, val in (("SVG", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="), ("GIF", "data:image/gif;base64,R0lGODlhAQABAAAAACw="), ("conteúdo não é imagem", du("image/jpeg", b"<script>alert(1)</script>")),
                 ("tipo declarado diferente do real", du("image/png", JPG)), ("lixo", "abc123"), ("acima de 2 MB", du("image/jpeg", b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024)))):
    s, j = novo(nome="Invalida " + rot, foto=val)
    t(f"foto inválida ({rot}): 400 e membro NÃO é criado", s in (400, 413) and db("SELECT count(*) FROM membros")[0][0] == antes)
t("foto inválida na edição não altera a foto atual", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto="data:image/gif;base64,R0lG"), "PUT")[0] == 400 and foto(b).mimetype == "image/webp")
t("foto inválida junto de erro de dados: nada gravado", call(f"/api/membros/{b}", F(nome="", status="Ativo", status_membresia="Membro", foto=du("image/png", PNG)), "PUT")[0] == 400 and foto(b).mimetype == "image/webp")

# ---------- remover
t("remover foto", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto_remover=True), "PUT")[0] == 200 and foto(b).status_code == 404 and call(f"/api/membros/{b}")[1]["tem_foto"] is False)
t("remover foto que não existe não dá erro", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto_remover=True), "PUT")[0] == 200)

# ---------- histórico continua funcionando junto com a foto
t("histórico + foto na mesma gravação (transação única)", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto=du("image/jpeg", JPG), historico_novo=[dict(data="2026-11-01", descricao="Foto cadastrada")]), "PUT")[0] == 200
  and len(call(f"/api/membros/{b}")[1]["historico"]) == 1 and foto(b).status_code == 200)
t("histórico continua imutável no banco", bloqueado("UPDATE historico_membro SET descricao='x'") and bloqueado("DELETE FROM historico_membro"))

# ---------- permissões: a foto segue a congregação do membro
call("/api/usuarios", dict(nome="Só Centro", login="centro", senha="senha-centro-1", perfil="usuario", ativo=True, congregacoes=[ct]))
cc = A.app.test_client(); entrar(cc, "centro", "senha-centro-1")
t("usuário de outra congregação não vê a foto (404)", foto(b, cc).status_code == 404)
s, j = novo(nome="Do Centro", congregacao_id=ct, foto=du("image/jpeg", JPG)); c_id = j["id"]
t("administrador vê a foto de qualquer congregação", foto(c_id).status_code == 200)
cc2 = A.app.test_client(); entrar(cc2, "centro", "senha-centro-1")
t("usuário vê a foto da própria congregação", foto(c_id, cc2).status_code == 200)
t("usuário de outra congregação não pode trocar a foto de membro alheio", call(f"/api/membros/{b}", F(nome="Com Foto", status="Ativo", status_membresia="Membro", congregacao_id=mz, foto=du("image/png", PNG)), "PUT", client=cc)[0] in (403, 404) and foto(b).mimetype == "image/jpeg")
t("sem sessão: 302/401 na foto", A.app.test_client().get(f"/api/membros/{c_id}/foto").status_code == 401)
t("sem CSRF: 400 ao enviar foto", cl.open("/api/membros", method="POST", json=F(nome="Z", status="Ativo", status_membresia="Membro", foto=du("image/jpeg", JPG))).status_code == 400)

# ---------- exclusão lógica e migração
call(f"/api/membros/{c_id}", m="DELETE")
t("membro excluído (lógico): foto deixa de ser servida", foto(c_id).status_code == 404)
t("foto sobrevive no banco, junto do cadastro lógico (histórico preservado)", db("SELECT count(*) FROM membro_foto WHERE membro_id=%s", (c_id,))[0][0] == 1)
antes = db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM membro_foto)")[0]; A.init_db()
t("migrate repetido preserva membros e fotos", db("SELECT (SELECT count(*) FROM membros),(SELECT count(*) FROM membro_foto)")[0] == antes)
t("a tabela de foto limita tipo e tamanho no próprio banco", bloqueado("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,'\\x00','image/gif',1)", (a,)) and bloqueado("INSERT INTO membro_foto(membro_id,imagem,tipo,tamanho) VALUES(%s,'\\x00','image/jpeg',3000000)", (a,)))
print(f"\nAprovados: {sum(res)} | Reprovados: {len(res)-sum(res)} (JS no navegador não é coberto por este script)"); sys.exit(0 if all(res) else 1)
