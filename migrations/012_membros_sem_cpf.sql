-- Cadastro de membros: opção "Não possui CPF".
-- Apenas ADICIONA uma coluna com valor padrão FALSE: nenhum cadastro existente é alterado.
-- (O "Sem número (S/N)" do endereço é gravado no próprio campo numero como 'S/N'; não precisa de coluna.)
ALTER TABLE membros ADD COLUMN IF NOT EXISTS sem_cpf boolean NOT NULL DEFAULT false;
