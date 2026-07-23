# Custom Agent Rules

## Planning Mode Rules
- Sempre ative o Modo de Planejamento (`implementation_plan.md`) antes de realizar análises de dados grandes, como testes em bancos `.db`, validações/testes de KPIs e testes de integração com APIs.
- Monte o plano detalhado primeiro para permitir a revisão e a troca para modelos mais rápidos antes da execução dos testes.
## Temporary Function Toggles & Modifications
- Ao solicitar o desligamento ou modificação de uma função, verifique o histórico recente do Git e as alterações locais (`git diff`) para identificar exatamente onde e como a função está implementada antes de modificá-la.

## API Performance & Constraints
- Para fins de testes, prefira utilizar requisições de validação leve/parciais (ex.: modo debug ou amostras reduzidas) para confirmar se a API respondeu adequadamente, evitando a execução de importações completas e demoradas a menos que seja estritamente necessário.

