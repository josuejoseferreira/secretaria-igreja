-- Agenda semanal simplificada: o compromisso passa a ter só data, horário e descrição.
-- A categoria deixa de ser obrigatória (continua existindo para a agenda anual).
ALTER TABLE agenda_compromissos ALTER COLUMN categoria_id DROP NOT NULL;

-- Eventos / lembretes (ex.: "Festa das crianças", "Encontro de pastores"): um dia ou período.
CREATE TABLE agenda_eventos(
  id serial PRIMARY KEY,
  data_inicio date NOT NULL,
  data_fim date,                                  -- vazio = evento de um dia só
  titulo text NOT NULL CHECK (length(btrim(titulo)) BETWEEN 1 AND 200),
  criado_por int REFERENCES usuarios(id) ON DELETE SET NULL,
  criado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (data_fim IS NULL OR data_fim >= data_inicio)
);
CREATE INDEX agenda_eventos_data_idx ON agenda_eventos(data_inicio);
CREATE UNIQUE INDEX agenda_eventos_unico ON agenda_eventos(data_inicio, lower(btrim(titulo)));
