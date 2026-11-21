-- Auditoria geral e PERMANENTE (o próprio banco impede alterar ou apagar). Só ADICIONA uma tabela.
-- Nunca grava senha, hash, token ou cookie: o sistema filtra esses campos antes de gravar.
CREATE TABLE IF NOT EXISTS auditoria(
  id           bigserial PRIMARY KEY,
  created_at   timestamptz NOT NULL DEFAULT now(),
  usuario_id   int REFERENCES usuarios(id),
  usuario_nome text,
  perfil       text,
  ip           text,
  operacao     text        NOT NULL,
  entidade     text,
  registro_id  text,
  antes        jsonb,
  depois       jsonb,
  motivo       text,
  resultado    text        NOT NULL DEFAULT 'sucesso' CHECK (resultado IN ('sucesso', 'falha', 'negado'))
);
CREATE INDEX IF NOT EXISTS ix_auditoria_data    ON auditoria(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_oper    ON auditoria(operacao, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_usuario ON auditoria(usuario_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_reg     ON auditoria(entidade, registro_id);

-- f_registro_permanente() foi criada na migration 013.
DROP TRIGGER IF EXISTS tg_auditoria_imutavel ON auditoria;
CREATE TRIGGER tg_auditoria_imutavel BEFORE UPDATE OR DELETE ON auditoria
  FOR EACH ROW EXECUTE FUNCTION f_registro_permanente();
