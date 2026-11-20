-- Portal do Membro: o próprio membro entra com conta individual para consultar/atualizar o SEU cadastro.
-- Só ADICIONA: um perfil novo, colunas nulas/com padrão e tabelas novas. Nenhum cadastro, usuário ou
-- permissão existente é alterado.

-- 1) Novo perfil 'membro' (admin e usuario continuam exatamente como estão).
ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS ck_usuarios_perfil;
ALTER TABLE usuarios ADD CONSTRAINT ck_usuarios_perfil CHECK (perfil IN ('admin', 'usuario', 'membro'));

-- 2) Vínculo seguro conta -> cadastro (identificador interno, gravado só pelo servidor), troca obrigatória
--    da senha inicial e proteção contra tentativas repetidas de senha (usada só em contas de membro).
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS membro_id     int REFERENCES membros(id);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS trocar_senha  boolean NOT NULL DEFAULT false;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS falhas_login  int NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueado_ate timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_membro ON usuarios(membro_id) WHERE membro_id IS NOT NULL;
-- Conta de perfil 'membro' sempre tem cadastro vinculado; as demais nunca têm.
ALTER TABLE usuarios ADD CONSTRAINT ck_usuarios_membro CHECK ((perfil = 'membro') = (membro_id IS NOT NULL));

-- 3) Situação do acesso de cada membro (sem linha = "Acesso não liberado"). Independe do status da membresia.
CREATE TABLE membro_acesso(
  membro_id         int PRIMARY KEY REFERENCES membros(id),
  usuario_id        int NOT NULL UNIQUE REFERENCES usuarios(id),
  situacao          text NOT NULL CHECK (situacao IN ('ativo', 'revogado')),
  liberado_em       timestamptz NOT NULL DEFAULT now(),
  liberado_por      int NOT NULL REFERENCES usuarios(id),
  liberado_por_nome text NOT NULL,
  senha_gerada_em   timestamptz NOT NULL DEFAULT now(),
  revogado_em       timestamptz,
  revogado_por      int REFERENCES usuarios(id),
  revogado_por_nome text,
  ultimo_acesso_em  timestamptz
);

-- 4) Registros PERMANENTES (o próprio banco impede alterar ou apagar): quem liberou, gerou senha nova ou revogou,
--    e o que o membro alterou no próprio cadastro. Nenhuma senha é gravada aqui (nem em nenhum outro lugar além do hash).
CREATE TABLE membro_acesso_log(
  id         serial PRIMARY KEY,
  membro_id  int NOT NULL REFERENCES membros(id),
  acao       text NOT NULL CHECK (acao IN ('liberou', 'nova_senha', 'revogou', 'troca_senha_inicial', 'troca_senha')),
  por_id     int NOT NULL REFERENCES usuarios(id),
  por_nome   text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_membro_acesso_log ON membro_acesso_log(membro_id, id);

CREATE TABLE membro_alteracoes(
  id           serial PRIMARY KEY,
  membro_id    int NOT NULL REFERENCES membros(id),
  usuario_id   int NOT NULL REFERENCES usuarios(id),
  usuario_nome text NOT NULL,
  campos       jsonb NOT NULL,            -- {"campo": {"antes": ..., "depois": ...}}  (CPF sempre mascarado)
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_membro_alteracoes ON membro_alteracoes(membro_id, id);

CREATE OR REPLACE FUNCTION f_registro_permanente() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Este registro é permanente e não pode ser alterado ou apagado.';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER tg_membro_acesso_log_imutavel BEFORE UPDATE OR DELETE ON membro_acesso_log
  FOR EACH ROW EXECUTE FUNCTION f_registro_permanente();
CREATE TRIGGER tg_membro_alteracoes_imutavel BEFORE UPDATE OR DELETE ON membro_alteracoes
  FOR EACH ROW EXECUTE FUNCTION f_registro_permanente();
