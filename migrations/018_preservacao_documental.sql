-- PRESERVAÇÃO DOCUMENTAL: nenhum registro oficial é apagado definitivamente pelas operações normais.
-- Só ADICIONA gatilhos e uma tabela de versões. Nenhum dado existente é alterado.
-- Observação: quem é dono do banco/superusuário ainda pode desligar gatilhos; por isso o app deve conectar
-- com um usuário SEM esse poder (ver instruções de backup e acesso na Etapa 8).

CREATE OR REPLACE FUNCTION f_bloqueia_exclusao() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Exclusão definitiva não é permitida: o registro oficial é preservado (use arquivar/inativar).';
END $$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['membros', 'membro_acesso', 'ocorrencias', 'lotes_ocorrencias', 'historico_ocorrencias', 'congregacoes', 'usuarios']
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS tg_%s_sem_exclusao ON %I', t, t);
    EXECUTE format('CREATE TRIGGER tg_%s_sem_exclusao BEFORE DELETE ON %I FOR EACH ROW EXECUTE FUNCTION f_bloqueia_exclusao()', t, t);
  END LOOP;
END $$;

-- Foto do membro: substituir ou remover guarda a versão anterior (o app continua trocando/removendo normalmente).
CREATE TABLE IF NOT EXISTS membro_foto_versoes(
  id                 bigserial PRIMARY KEY,
  membro_id          int         NOT NULL REFERENCES membros(id),
  imagem             bytea       NOT NULL,
  tipo               text        NOT NULL,
  tamanho            int         NOT NULL,
  foto_atualizada_em timestamptz,
  arquivada_em       timestamptz NOT NULL DEFAULT now(),
  operacao           text        NOT NULL CHECK (operacao IN ('substituida', 'removida'))
);
CREATE INDEX IF NOT EXISTS ix_membro_foto_versoes ON membro_foto_versoes(membro_id, id);

CREATE OR REPLACE FUNCTION f_membro_foto_arquivar() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO membro_foto_versoes(membro_id, imagem, tipo, tamanho, foto_atualizada_em, operacao)
    VALUES (OLD.membro_id, OLD.imagem, OLD.tipo, OLD.tamanho, OLD.atualizado_em, 'removida');
    RETURN OLD;
  END IF;
  IF NEW.imagem IS DISTINCT FROM OLD.imagem THEN
    INSERT INTO membro_foto_versoes(membro_id, imagem, tipo, tamanho, foto_atualizada_em, operacao)
    VALUES (OLD.membro_id, OLD.imagem, OLD.tipo, OLD.tamanho, OLD.atualizado_em, 'substituida');
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_membro_foto_arquivar ON membro_foto;
CREATE TRIGGER tg_membro_foto_arquivar BEFORE UPDATE OR DELETE ON membro_foto
  FOR EACH ROW EXECUTE FUNCTION f_membro_foto_arquivar();
DROP TRIGGER IF EXISTS tg_membro_foto_versoes_imutavel ON membro_foto_versoes;
CREATE TRIGGER tg_membro_foto_versoes_imutavel BEFORE UPDATE OR DELETE ON membro_foto_versoes
  FOR EACH ROW EXECUTE FUNCTION f_registro_permanente();
