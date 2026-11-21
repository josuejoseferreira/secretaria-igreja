-- PORTAL DO MEMBRO: fluxo de aprovação. O que o membro envia fica SEPARADO do cadastro oficial até a Secretaria decidir.
-- Só ADICIONA uma tabela. Nada é apagado: fichas aprovadas, recusadas e substituídas ficam para sempre (o banco impede DELETE).
CREATE TABLE IF NOT EXISTS portal_fichas(
  id                     bigserial PRIMARY KEY,
  tipo                   text        NOT NULL CHECK (tipo IN ('atualizacao', 'novo_cadastro')),
  membro_id              int REFERENCES membros(id),            -- cadastro oficial (nulo em 'novo_cadastro' até a aprovação)
  dados                  jsonb       NOT NULL,                  -- o que o membro enviou (imutável)
  foto                   bytea,                                 -- foto enviada, separada da foto oficial
  foto_tipo              text,
  remover_foto           boolean     NOT NULL DEFAULT false,
  situacao               text        NOT NULL DEFAULT 'pendente' CHECK (situacao IN ('pendente', 'aprovada', 'recusada', 'substituida')),
  enviada_em             timestamptz NOT NULL DEFAULT now(),
  enviada_por_nome       text        NOT NULL,
  enviada_por_usuario_id int REFERENCES usuarios(id),
  decidida_em            timestamptz,
  decidida_por           int REFERENCES usuarios(id),
  decidida_por_nome      text,
  justificativa          text,                                  -- obrigatória na recusa
  dados_anteriores       jsonb,                                 -- versão do cadastro oficial no momento da aprovação
  membro_criado_id       int REFERENCES membros(id),
  CHECK ((tipo = 'atualizacao' AND membro_id IS NOT NULL) OR (tipo = 'novo_cadastro' AND membro_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_portal_fichas_sit ON portal_fichas(situacao, enviada_em);
CREATE INDEX IF NOT EXISTS ix_portal_fichas_membro ON portal_fichas(membro_id, id);

-- Depois de enviada, a ficha não muda (só a decisão é preenchida) e uma ficha já decidida fica travada.
CREATE OR REPLACE FUNCTION f_portal_ficha_protege() RETURNS trigger AS $$
BEGIN
  IF OLD.situacao <> 'pendente' THEN RAISE EXCEPTION 'Ficha já decidida não pode ser alterada.'; END IF;
  IF NEW.dados IS DISTINCT FROM OLD.dados OR NEW.foto IS DISTINCT FROM OLD.foto OR NEW.tipo <> OLD.tipo OR NEW.membro_id IS DISTINCT FROM OLD.membro_id
     OR NEW.remover_foto <> OLD.remover_foto OR NEW.enviada_em <> OLD.enviada_em OR NEW.enviada_por_nome <> OLD.enviada_por_nome THEN
    RAISE EXCEPTION 'O conteúdo enviado pelo membro não pode ser alterado.';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS tg_portal_fichas_protege ON portal_fichas;
CREATE TRIGGER tg_portal_fichas_protege BEFORE UPDATE ON portal_fichas FOR EACH ROW EXECUTE FUNCTION f_portal_ficha_protege();
DROP TRIGGER IF EXISTS tg_portal_fichas_sem_exclusao ON portal_fichas;
CREATE TRIGGER tg_portal_fichas_sem_exclusao BEFORE DELETE ON portal_fichas FOR EACH ROW EXECUTE FUNCTION f_bloqueia_exclusao();
