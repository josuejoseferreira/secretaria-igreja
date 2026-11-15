-- Agenda: programação semanal, agenda anual e evento em destaque.
-- Só cria tabelas novas; não altera nenhuma tabela existente.
CREATE TABLE agenda_categorias(
  id serial PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  nome text NOT NULL,
  cor text NOT NULL,
  ordem int NOT NULL,
  mostra_em_eventos boolean NOT NULL DEFAULT false  -- entra na tela "Eventos da semana" do Datashow
);
INSERT INTO agenda_categorias(slug,nome,cor,ordem,mostra_em_eventos) VALUES
 ('culto','Cultos e eventos','#2563EB',1,true),
 ('reuniao','Reuniões','#16A34A',2,false),
 ('administrativa','Atividades administrativas','#F59E0B',3,false),
 ('especial','Eventos especiais','#7C3AED',4,true);

CREATE TABLE agenda_compromissos(
  id serial PRIMARY KEY,
  data date NOT NULL,
  hora time,                                   -- opcional (compromisso sem horário definido)
  titulo text NOT NULL CHECK (length(btrim(titulo)) BETWEEN 1 AND 200),
  categoria_id int NOT NULL REFERENCES agenda_categorias(id),
  local text CHECK (length(local) <= 120),
  responsavel text CHECK (length(responsavel) <= 120),
  observacao text CHECK (length(observacao) <= 1000),
  criado_por int REFERENCES usuarios(id) ON DELETE SET NULL,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agenda_compromissos_data_idx ON agenda_compromissos(data, hora);
-- Evita o mesmo compromisso (mesma data, horário e descrição) cadastrado duas vezes.
CREATE UNIQUE INDEX agenda_compromissos_unico ON agenda_compromissos(data, COALESCE(hora,'00:00'::time), lower(btrim(titulo)));

-- Um evento em destaque por semana; a semana é identificada pela segunda-feira.
CREATE TABLE agenda_destaques(
  semana date PRIMARY KEY CHECK (EXTRACT(ISODOW FROM semana) = 1),
  compromisso_id int NOT NULL REFERENCES agenda_compromissos(id) ON DELETE CASCADE,
  definido_por int REFERENCES usuarios(id) ON DELETE SET NULL,
  definido_em timestamptz NOT NULL DEFAULT now()
);
