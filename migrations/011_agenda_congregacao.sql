-- Agenda por congregação: cada congregação monta o seu anúncio semanal.
-- Só ACRESCENTA colunas/índices. Nada é apagado: os compromissos já cadastrados ficam com congregacao_id vazio
-- ("Sem congregação") e só o administrador os enxerga, até escolher a congregação de cada um ao editar.
ALTER TABLE agenda_compromissos ADD COLUMN congregacao_id int REFERENCES congregacoes(id);
CREATE INDEX agenda_compromissos_cong_idx ON agenda_compromissos(congregacao_id, data);

-- O mesmo compromisso (data, horário e descrição) pode existir em congregações diferentes.
DROP INDEX agenda_compromissos_unico;
CREATE UNIQUE INDEX agenda_compromissos_unico ON agenda_compromissos(
  COALESCE(congregacao_id, 0), data, COALESCE(hora,'00:00'::time), lower(btrim(titulo)));

-- Um evento em destaque por semana E por congregação.
ALTER TABLE agenda_destaques ADD COLUMN congregacao_id int REFERENCES congregacoes(id);
UPDATE agenda_destaques d SET congregacao_id = c.congregacao_id FROM agenda_compromissos c WHERE c.id = d.compromisso_id;
ALTER TABLE agenda_destaques DROP CONSTRAINT agenda_destaques_pkey;
CREATE UNIQUE INDEX agenda_destaques_unico ON agenda_destaques(semana, COALESCE(congregacao_id, 0));
