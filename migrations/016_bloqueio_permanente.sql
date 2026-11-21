-- Bloqueio PERMANENTE de contas após 5 senhas incorretas seguidas (vale para todos os perfis).
-- Só ADICIONA colunas/tabela. O bloqueio só termina quando um administrador autorizado desbloqueia.
-- A coluna bloqueado_ate (migration 013) deixa de ser usada pelo sistema, mas é mantida (nada é removido).
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueado             boolean     NOT NULL DEFAULT false;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueado_em          timestamptz;   -- data/hora do ÚLTIMO bloqueio
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueio_motivo       text;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS total_bloqueios       int         NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS desbloqueado_em       timestamptz;   -- data/hora do ÚLTIMO desbloqueio
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS desbloqueado_por      int REFERENCES usuarios(id);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS desbloqueado_por_nome text;

-- Contas de membro que estavam bloqueadas POR TEMPO passam a ficar bloqueadas até a liberação administrativa.
UPDATE usuarios SET bloqueado = true, bloqueado_em = now(), bloqueio_motivo = 'Bloqueio por tempo anterior convertido em bloqueio permanente',
                    total_bloqueios = total_bloqueios + 1
 WHERE bloqueado_ate IS NOT NULL AND bloqueado_ate > now() AND NOT bloqueado;

-- Falhas de login por origem (IP): limita tentativa e erro sem substituir o bloqueio por conta.
-- Guarda só o endereço e a hora (nunca login digitado nem senha).
CREATE TABLE IF NOT EXISTS login_falhas_ip(
  id         bigserial PRIMARY KEY,
  ip         text        NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_login_falhas_ip ON login_falhas_ip(ip, created_at);
