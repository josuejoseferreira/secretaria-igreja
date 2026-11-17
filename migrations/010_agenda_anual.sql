-- Agenda anual: os eventos passam a ter categoria opcional (cor no calendário anual).
-- Eventos já cadastrados ficam "sem categoria" (cinza); nada é apagado.
ALTER TABLE agenda_eventos ADD COLUMN IF NOT EXISTS categoria_id int REFERENCES agenda_categorias(id);
CREATE INDEX IF NOT EXISTS agenda_eventos_fim_idx ON agenda_eventos((COALESCE(data_fim,data_inicio)));
