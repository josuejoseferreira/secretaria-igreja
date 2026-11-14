-- Remove a etapa de "conferência" das ocorrências: toda ocorrência registrada já vale.
-- Passam a existir só as situações Lançada (ativa) e Cancelada (excluída). Nenhum registro é apagado.
UPDATE lotes_ocorrencias SET situacao='Lançada' WHERE situacao='Conferida';
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint
           WHERE conrelid='lotes_ocorrencias'::regclass AND contype='c' AND pg_get_constraintdef(oid) ILIKE '%Conferida%'
  LOOP
    EXECUTE format('ALTER TABLE lotes_ocorrencias DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;
ALTER TABLE lotes_ocorrencias ADD CONSTRAINT ck_lotes_situacao CHECK (situacao IN ('Lançada','Cancelada'));
