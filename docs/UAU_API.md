# API UAUWeb (Globaltec / Senior Cloud) — Documentação Otimizada

> Extraído do Swagger UI oficial (`documentacao.html`, dump completo de 27 MB, 52 resources / 437
> endpoints) para consulta rápida por humanos e por IA sem precisar carregar o dump inteiro.
> O PDF original fornecido estava corrompido (sem xref/trailer válidos) — esta versão vem do HTML
> do Swagger, que abriu corretamente.

## Visão geral

- **Base URL**: `https://gamma-api.seniorcloud.com.br:51910/uauAPI`
- **Versões**: `1.0` e `2.0` (path usa `v{version}`, ex.: `/api/v1/Autenticador/AutenticarUsuario`).
  Alguns endpoints legados não têm `{version}` no path (ex.: `/api/Cotacao/ContaCotacoesMob`).
- **Autenticação em duas camadas, presentes em praticamente todo endpoint**:
  - Header `X-INTEGRATION-Authorization`: token fixo de integração (por empresa/contrato).
  - Header `Authorization`: token de sessão do usuário, obtido em
    `POST /Autenticador/AutenticarUsuario` e reenviado em cada chamada subsequente.
- **Erros**: corpo padrão `UAUApi.ResponseHandler`:
  ```json
  { "Detalhe": "string", "Mensagem": "string", "Descricao": "string" }
  ```
  Status comuns: `400` (dados/validação), `401` (não logado), `500` (erro interno).
- **Peculiaridade de serialização**: várias respostas (inclusive o token de login) chegam como
  **string contendo JSON serializado dentro de string** — é preciso fazer `json.loads` duas vezes
  em alguns casos. O código do INAD (`_uau_request` em `run.py`) já trata isso.
- **INAD usa a API em modo SOMENTE LEITURA** — nenhum endpoint de escrita (Gravar*, Inserir*,
  Alterar*, Excluir*, Aprovar* etc.) deve ser chamado pela integração. Ver `AI_CONTEXT.md` e
  a memória `uau-sync-implementation-rules`.

---

## Endpoints usados pela integração INAD

Fluxo real implementado em `run.py` (`_sync_from_uau`): **Autenticar → enumerar titulares de
venda → consultar recebíveis por CPF → filtrar parcelas vencidas (client-side)**.

### 1. `POST /api/v{version}/Autenticador/AutenticarUsuario`

Autenticação padrão via usuário/senha cadastrados no UAU.

**Headers**: `X-INTEGRATION-Authorization` (token de integração).

**Request body** (`AutenticarUsuarioRequest`):
```json
{
  "Login": "string",
  "Senha": "string",
  "UsuarioUAUSite": "string (opcional — necessário só para usar GerarBoleto)"
}
```

**Resposta 200**: `string` — o token de sessão puro (não um objeto). Usar como header
`Authorization` nas chamadas seguintes.

**Erros**: `400` login/senha incorretos · `500` falha ao autenticar.

**Nota de negócio**: o método de login (senha própria vs Active Directory) depende da
configuração da empresa no UAU; login > 8 caracteres é tratado como Cliente/Pessoa, < 8 como
usuário UAU.

---

### 2. `POST /api/v{version}/Pessoas/ConsultarPessoasComVenda`

Consulta os clientes que são titulares de vendas (quitadas ou não).

**Headers**: `Authorization` (token de sessão) + `X-INTEGRATION-Authorization`.

**Request body**: **nenhum parâmetro documentado** — não existe filtro de empresa/obra neste
endpoint. `version` só aparece no path.

**Resposta 200**: `string` — "retorna código e nome da pessoa" (sem schema tipado formal no
Swagger; o parsing no INAD é defensivo, testando várias chaves de nome/cpf possíveis).

**Erros**: `500` erro genérico de processamento.

⚠️ **Divergência corrigida**: o código antigo enviava `{empresa, obra}` no body desta chamada —
esse parâmetro não existe na documentação. O filtro de empresa/obra precisa ser aplicado depois,
sobre a resposta de `ParcelasECobrancasDoCliente` (que traz `Empresa`/`Obra` por venda).

---

### 3. `POST /api/v{version}/Recebiveis/ParcelasECobrancasDoCliente`

Busca as parcelas e cobranças **em aberto** de um cliente, por CPF.

**Headers**: `Authorization` + `X-INTEGRATION-Authorization`.

**Request body** (`ParcelasECobrancasDoClienteRequest`):
```json
{
  "Cpf": "string (obrigatório)",
  "ValorReajustado": true,
  "QtdeParcelas": 0,
  "DataInicioVencimento": "2026-07-22T00:00:00Z",
  "DataFimVencimento": "2026-07-22T00:00:00Z",
  "PesquisaPorNaoTitulares": false
}
```
- `ValorReajustado`: `false` = valor principal da parcela · `true` = valor reajustado/atualizado.
- `QtdeParcelas` (opcional): limita quantas parcelas retornar, a contar da mais antiga a receber.
- `DataInicioVencimento` / `DataFimVencimento` (opcionais): filtro server-side do período de
  vencimento — usar `DataFimVencimento = hoje` reduz o payload e evita trazer parcelas futuras.
- `PesquisaPorNaoTitulares` (opcional, default `false`): `false` busca só clientes tipo
  `0 - Titular` da venda; `true` busca qualquer cliente vinculado à venda.

**Resposta 200** (`RecebiveisResponse`):
```json
{
  "Vendas": [
    {
      "Empresa": 0,
      "Obra": "string",
      "Venda": 0,
      "ItensVenda": [
        { "CodigoProduto": 0, "DescProduto": "string", "CodigoPersonalizacao": 0, "Identificador": "string" }
      ],
      "ParcelasVenda": [
        {
          "NumParcela": 0,
          "TipoParcela": "string",
          "NumGeralParcela": 0,
          "ValorParcela": 0,
          "DataVencimento": "2026-07-22T00:00:00Z",
          "BoletoParcela": [
            { "CodBanco": 0, "SeuNumero": 0, "DataVencimento": "...", "ValorBoleto": 0, "Bolecode": true, "Agrupado": true }
          ],
          "PixParcela": [
            { "Banco": 0, "Txid": "string", "DataVencimento": "...", "ValorPix": 0, "Agrupado": true }
          ]
        }
      ]
    }
  ]
}
```

**Erros**: `400` requisição inválida · `500` erro genérico.

**Uso no INAD**: `Empresa`/`Obra` de cada venda são usados para aplicar o filtro empresa/obra
(que a API não oferece nativamente); parcelas com `DataVencimento < hoje` = inadimplência.

---

## Índice completo de endpoints (437, agrupados por resource)

Legenda rápida de prefixos de nome de método — **somente os que começam com `Consultar`,
`Buscar`, `Obter`, `Retornar`, `Verifica` são seguros para uso read-only**; os demais
(`Gravar`, `Inserir`, `Alterar`, `Atualizar`, `Excluir`, `Aprovar`, `Reprovar`, `Cancelar`,
`Confirmar`, `Renegociar`, `Migrar`, `Importar`, `Gerar`, `Emissao`, `Manter`) são de **escrita**
e não devem ser chamados pela integração INAD.

### AcompanhamentoContratoVenda
- `POST  ` /api/v{version}/AcompanhamentoContratoVenda/GravarAcompanhamento

### AcompanhamentosServicos
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharContrato
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharServicoPL
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharServicoOrcado
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharServicoContrato
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharServicoOrcadoEmLote
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoPL
- `POST  ` /api/v{version}/AcompanhamentosServicos/AcompanharServicoContratoEmLote
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoOrcado
- `POST  ` /api/v{version}/AcompanhamentosServicos/AlterarAcompanhamentoServicoContrato
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoDeContrato
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoOrcadoEmLote
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoOrcadoPorChave
- `POST  ` /api/v{version}/AcompanhamentosServicos/ExcluirAcompanhamentoServicoDeContratoEmLote
- `POST  ` /api/v{version}/AcompanhamentosServicos/ConsultarAcompanhamentoContratoServicoPorServico
- `POST  ` /api/v{version}/AcompanhamentosServicos/ConsultarAcompanhamentoContratoServicoPorContratoEServico

### AcompanharEntrega
- `POST  ` /api/v{version}/AcompanharEntrega/ConsultarProcessos
- `GET   ` /api/v{version}/AcompanharEntrega/AcompanharPreEntrega

### Anexo
- `GET   ` /api/v{version}/Anexo/Ocorrencia
- `POST  ` /api/v{version}/Anexo/ListaOcorrencias
- `POST  ` /api/v{version}/Anexo/AnexarArquivo
- `POST  ` /api/v{version}/Anexo/ExcluirAnexos
- `POST  ` /api/v{version}/Anexo/BaixarArquivos
- `POST  ` /api/v{version}/Anexo/ListarDiretorios
- `POST  ` /api/v{version}/Anexo/ExcluirOcorrencia
- `POST  ` /api/v{version}/Anexo/AnexarBase64Imagem
- `POST  ` /api/v{version}/Anexo/VincularOcorrencia
- `POST  ` /api/v{version}/Anexo/ListaArquivoPorChave
- `POST  ` /api/v{version}/Anexo/ListarArmazenamentos
- `POST  ` /api/v{version}/Anexo/GravarComentarioAnexo
- `POST  ` /api/v{version}/Anexo/RetornaArquivoEmBytes
- `POST  ` /api/v{version}/Anexo/ConsultarChavesComentario
- `POST  ` /api/v{version}/Anexo/AnexarArquivosBase64Request
- `POST  ` /api/v{version}/Anexo/RetornarArquivosEmListaBytes

### Atendimento
- `POST  ` /api/v{version}/Atendimento/GerarPendencia
- `POST  ` /api/v{version}/Atendimento/GravarAtendimento
- `POST  ` /api/v{version}/Atendimento/ConsultarPendencia
- `POST  ` /api/v{version}/Atendimento/ConsultarAtendimento
- `POST  ` /api/v{version}/Atendimento/ConsultarCategDeComentAtivas
- `POST  ` /api/v{version}/Atendimento/ConsultarPendenciaObservacao
- `POST  ` /api/v{version}/Atendimento/VincularArquivoAoAtendimento
- `POST  ` /api/v{version}/Atendimento/ConsultarAtendimentoPorPessoa
- `POST  ` /api/v{version}/Atendimento/ConsultarEmpreendimentosCliente
- `POST  ` /api/v{version}/Atendimento/ConsultarCategoriaAtendimentoWeb
- `POST  ` /api/v{version}/Atendimento/ConsultarNumeroWorkFlowVinculado
- `POST  ` /api/v{version}/Atendimento/ConsultarPendenciasPorNumeroVinculo
- `POST  ` /api/v{version}/Atendimento/GerarAtendimentoPorChatOnlineCliente
- `POST  ` /api/v{version}/Atendimento/ConsultarAtendimentoDetalhadoPorChave
- `POST  ` /api/v{version}/Atendimento/ConsultarConfiguracaoAtendimentoUAUWEB
- `POST  ` /api/v{version}/Atendimento/ConsultarAtendimentoPorCategoriasUauWeb
- `POST  ` /api/v{version}/Atendimento/ConsultarAtendimentoPorPessoaComentario
- `POST  ` /api/v{version}/Atendimento/ConsultarUnidadesDoEmpreendimentoCliente
- `POST  ` /api/v{version}/Atendimento/ConsultarDataPrevistaDeTerminoAtendimento
- `POST  ` /api/v{version}/Atendimento/ConsultarNumeroVinculoCategoriaDeComentarioComWorkFlow

### Autenticador
- `POST  ` /api/v{version}/Autenticador/LogoutUsuario
- `POST  ` /api/v{version}/Autenticador/AutenticarUsuario
- `POST  ` /api/v{version}/Autenticador/AutenticarUsuarioApp
- `POST  ` /api/v{version}/Autenticador/VerificaUsuarioLogado
- `POST  ` /api/v{version}/Autenticador/ConsultarDadosUsrLogado
- `POST  ` /api/v{version}/Autenticador/AutentificarUsuarioTitanium
- `POST  ` /api/v{version}/Autenticador/AutenticarUsuarioCorporativo

### BancoHoras
- `POST  ` /api/v{version}/BancoHoras/LancarBancoHorasFuncionario

### BoletoServices
- `POST  ` /api/v{version}/BoletoServices/GerarPDFCarne
- `POST  ` /api/v{version}/BoletoServices/GerarPDFBoleto
- `POST  ` /api/v{version}/BoletoServices/ObterCodigoDeBarras
- `POST  ` /api/v{version}/BoletoServices/ObterLinhaDigitavel
- `POST  ` /api/v{version}/BoletoServices/AlterarDataVencimento
- `POST  ` /api/v{version}/BoletoServices/ConsultarStatusBoleto
- `POST  ` /api/v{version}/BoletoServices/ObterMensagemDoBoleto
- `POST  ` /api/v{version}/BoletoServices/ConsultarDadosDoBoleto
- `POST  ` /api/v{version}/BoletoServices/ConsultarBoletosDoCliente
- `GET   ` /api/v{version}/BoletoServices/ConsultarBoletosReimpressao

### CartaoPresente
- `GET   ` /api/v{version}/CartaoPresente/ConsultarCartoesDisponiveis

### CessaoRecebiveis
- `POST  ` /api/v{version}/CessaoRecebiveis/ConsultarContrato
- `POST  ` /api/v{version}/CessaoRecebiveis/AprovarContrato
- `POST  ` /api/v{version}/CessaoRecebiveis/InserirContrato
- `GET   ` /api/v{version}/CessaoRecebiveis/GerarVendaDoContrato

### ChavePix
- `POST  ` /api/v{version}/ChavePix/Pessoas/Consultar/{cpfCnpj}
- `POST  ` /api/v{version}/ChavePix/Pessoas/Deletar
- `POST  ` /api/v{version}/ChavePix/Pessoas/Atualizar
- `POST  ` /api/v{version}/ChavePix/Pessoas/Cadastrar

### Pix
- `POST  ` /api/v{version}/Pix/PixPorParcelas
- `POST  ` /api/v{version}/Pix/ReimpressaoPix
- `POST  ` /api/v{version}/Pix/ConsultarPixStatus
- `POST  ` /api/v{version}/Pix/GerarCobrancaVenda
- `POST  ` /api/v{version}/Pix/GerarCobrancaProposta

### Comissao
- `POST  ` /api/v{version}/Comissao/ConsultarVendedores
- `POST  ` /api/v{version}/Comissao/AtualizarStatusComissao
- `POST  ` /api/v{version}/Comissao/ConsultarModeloComissao
- `POST  ` /api/v{version}/Comissao/ConsultarEstruturaComissao

### Composicoes
- `POST  ` /api/v{version}/Composicoes/InserirComposicoes
- `POST  ` /api/v{version}/Composicoes/AtualizarComposicoes
- `POST  ` /api/v{version}/Composicoes/ConsultarTodasComposicoes
- `POST  ` /api/v{version}/Composicoes/ConsultarComposicoesPorChave
- `POST  ` /api/v{version}/Composicoes/ConsultarInsumosDaComposicao
- `POST  ` /api/v{version}/Composicoes/AlterarInsumoComposicoesGeral
- `POST  ` /api/v{version}/Composicoes/InserirInsumoComposicoesGeral
- `POST  ` /api/v{version}/Composicoes/ConsultarComposicoesPorDescricao
- `POST  ` /api/v{version}/Composicoes/ConsultarComposicoesComFiltroLivre
- `POST  ` /api/v{version}/Composicoes/AlterarInsumoComposicoesGeralPesada
- `POST  ` /api/v{version}/Composicoes/InserirInsumoComposicoesGeralPesada

### ConfigGerais
- `POST  ` /api/v{version}/ConfigGerais/RetornarVersaoBD
- `POST  ` /api/v{version}/ConfigGerais/RetornarVersaoWS
- `POST  ` /api/v{version}/ConfigGerais/ObterConfiguracaoDeCasasDecimais

### Contabil
- `POST  ` /api/v{version}/Contabil/ConsultarSaldoDeContas
- `POST  ` /api/v{version}/Contabil/ConsultarContasContabeis

### ContratoMaterialServico
- `POST  ` /api/v{version}/ContratoMaterialServico/AprovarContratos
- `POST  ` /api/v{version}/ContratoMaterialServico/ReprovarContratos
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarItensContrato
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarContratoPorChave
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarContratoPorFornecedor
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarContratoPorServicoMaterial
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarItensVinculoOrcamentoServico
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarSaldoReajustadoPorItemContrato
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarItensVinculoPlanejamentoServico
- `POST  ` /api/v{version}/ContratoMaterialServico/ConsultarContratosItensVinculadoOrcamento
- `POST  ` /api/ContratoMaterialServico/obterListaEstruturaAprovacoes

### CorreioEletronico
- `POST  ` /api/v{version}/CorreioEletronico/EnviarMailInternoUau

### Cotacao
- `POST  ` /api/v{version}/Cotacao/AtualizarItemCotacao
- `POST  ` /api/v{version}/Cotacao/ContaCotacoesAprovMob
- `POST  ` /api/v{version}/Cotacao/AprovarSimulacoesCompra
- `POST  ` /api/v{version}/Cotacao/AdicionarFornecedorCotacao
- `POST  ` /api/v{version}/Cotacao/BuscarItensCotacaoFornecedor
- `POST  ` /api/v{version}/Cotacao/ConsultarItensCotacaoPorObra
- `POST  ` /api/v{version}/Cotacao/BuscarCotacaoAbertaFornecedor
- `POST  ` /api/v{version}/Cotacao/AprovarConfirmacaoCotacaoPorObra
- `POST  ` /api/v{version}/Cotacao/RemoverAprovacaoSimulacoesCompra
- `POST  ` /api/v{version}/Cotacao/AtualizarCondicaoPagamentoEntrega
- `POST  ` /api/v{version}/Cotacao/ConsultarAprovacaoDaCotacaoPorObra
- `POST  ` /api/v{version}/Cotacao/ReprovarConfirmacoesCotacaoPorObra
- `POST  ` /api/Cotacao/ContaCotacoesMob
- `POST  ` /api/v{version}/Cotacao/ConsultarCotacoesConfirmacaoPendente
- `POST  ` /api/v{version}/Cotacao/ConsultarQuantidadeCotacaoPendentePorObra
- `POST  ` /api/v{version}/Cotacao/ConsultarQuantidadeCotacaoPendentePorObraMob
- `POST  ` /api/v{version}/Cotacao/ConsultarJustificativasAprovacaoForaSequencia
- `GET   ` /api/v{version}/Cotacao/InserirAlteraComentFornFrete

### DocumentosDigitais
- `GET   ` /api/v{version}/DocumentosDigitais/DocuSignConsentimento
- `POST  ` /api/v{version}/DocumentosDigitais/AssineOnLineConfigTemplate
- `POST  ` /api/v{version}/DocumentosDigitais/DocuSignEnvelopeStatus
- `POST  ` /api/v{version}/DocumentosDigitais/EnviarEnvelopeDeDocumento
- `POST  ` /api/v{version}/DocumentosDigitais/ConsultarDocumentosEnviados
- `POST  ` /api/v{version}/DocumentosDigitais/ConsultarAssinaturasEnviadas
- `POST  ` /api/v{version}/DocumentosDigitais/ConsultaDocumentosDigitaisAtivos
- `POST  ` /api/v{version}/DocumentosDigitais/ConsultarEnvelopeDocumentosCodigoExterno

### Empresa
- `POST  ` /api/v{version}/Empresa/ConsultarEmpresa
- `POST  ` /api/v{version}/Empresa/ObterEmpresasAtivas
- `POST  ` /api/v{version}/Empresa/ConsultarDadosBasicosEmpresasPorFiltro

### Espelho
- `POST  ` /api/v{version}/Espelho/AlterarStatusUnidade
- `POST  ` /api/v{version}/Espelho/ConsultarEspelhosVenda
- `POST  ` /api/v{version}/Espelho/RetornarMenorPrecoPerson
- `POST  ` /api/v{version}/Espelho/AtualizarCamposCustomizados
- `POST  ` /api/v{version}/Espelho/ConsultarUnidadePerPorChave
- `POST  ` /api/v{version}/Espelho/BuscaUnidadesDeAcordoComWhere
- `POST  ` /api/v{version}/Espelho/AlterarDataEntregaChavesUnidade
- `POST  ` /api/v{version}/Espelho/ConsultarPersonalizacoesComPrecos
- `POST  ` /api/v{version}/Espelho/BuscaUnidadesDeAcordoComWhereDetalhado

### Estrutura
- `POST  ` /api/v{version}/Estrutura/ExcluirEstrutura
- `POST  ` /api/v{version}/Estrutura/InserirEstrutura
- `POST  ` /api/v{version}/Estrutura/ExcluirItemDeEstrutura
- `POST  ` /api/v{version}/Estrutura/InserirItemDeEstrutura
- `POST  ` /api/v{version}/Estrutura/InserirItemNaEstrutura

### Eventos
- `POST  ` /api/v{version}/Eventos/ConsultarLogEventos
- `POST  ` /api/v{version}/Eventos/ConsultarChavesLogDeEventos

### ExtratoDoCliente
- `POST  ` /api/v{version}/ExtratoDoCliente/GerarPDFExtratoCliente
- `POST  ` /api/v{version}/ExtratoDoCliente/GerarPDFExtratoClienteV2
- `POST  ` /api/v{version}/ExtratoDoCliente/ConsultarSaldoCessoesDireitoAnteriores
- `POST  ` /api/v{version}/ExtratoDoCliente/ConsultarDadosDemonstrativoPagtoCliente

### Fiscal
- `POST  ` /api/v{version}/Fiscal/BuscarCAPs
- `POST  ` /api/v{version}/Fiscal/BuscarCodigoServicoFiscal
- `POST  ` /api/v{version}/Fiscal/ImportarLancamentosFiscais
- `POST  ` /api/v{version}/Fiscal/ImportarLancamentosContabeis

### Folha
- `POST  ` /api/v{version}/Folha/GravarAlocacaoMaoObra
- `POST  ` /api/v{version}/Folha/GravarMovimentacaoMensalObra

### Funcionario
- `POST  ` /api/v{version}/Funcionario/ConsultarFuncionario

### InsumosGeral
- `POST  ` /api/v{version}/InsumosGeral/InserirInsumosGeral
- `POST  ` /api/v{version}/InsumosGeral/AtualizarInsumosGeral
- `POST  ` /api/v{version}/InsumosGeral/ConsultarInsumosGeral
- `POST  ` /api/v{version}/InsumosGeral/ConsultarInsumosGeralPorChave
- `POST  ` /api/v{version}/InsumosGeral/ConsultarInsumosGeralPorDescricao

### ListaPrecoReferencia
- `POST  ` /api/v{version}/ListaPrecoReferencia/InserirFornecedores
- `POST  ` /api/v{version}/ListaPrecoReferencia/AtualizarItemFornecedor
- `POST  ` /api/v{version}/ListaPrecoReferencia/ConsultarListaPrecoReferencia

### Localidade
- `POST  ` /api/v{version}/Localidade/ConsultarLocalidadePorCEP

### Medicao
- `POST  ` /api/v{version}/Medicao/ManterMedicao
- `POST  ` /api/v{version}/Medicao/ExcluirMedicao
- `POST  ` /api/v{version}/Medicao/ConsultarMedicao
- `POST  ` /api/v{version}/Medicao/ConsultarItensMedicao
- `POST  ` /api/v{version}/Medicao/AprovarMedicaoContrato
- `POST  ` /api/v{version}/Medicao/ConsultarBoletimMedicao
- `POST  ` /api/v{version}/Medicao/ConsultarMedicaoCompleta
- `POST  ` /api/v{version}/Medicao/ConsultarMedicaoPorServMat
- `POST  ` /api/v{version}/Medicao/ConsultarMedicoesPorStatus
- `POST  ` /api/Medicao/ValidarCNPJAoGravarMedicao
- `POST  ` /api/v{version}/Medicao/ConsultarMedicaoPorContrato
- `POST  ` /api/v{version}/Medicao/ConsultarItensMedicaoPorMedicao
- `POST  ` /api/v{version}/Medicao/ConsultarItensMedicaoPorServMat
- `POST  ` /api/v{version}/Medicao/ConsultarItensMedicaoPorContrato
- `POST  ` /api/v{version}/Medicao/ConsultarItensMedicaoPorItemContrato

### ModeloVenda
- `POST  ` /api/v{version}/ModeloVenda/BuscarPlanoIndexador
- `POST  ` /api/v{version}/ModeloVenda/ConsultarModeloVenda
- `POST  ` /api/v{version}/ModeloVenda/GerarParcelasProposta
- `POST  ` /api/v{version}/ModeloVenda/ConsultarModeloDeVenda
- `POST  ` /api/v{version}/ModeloVenda/MontarModeloRenegociacao
- `POST  ` /api/v{version}/ModeloVenda/ConsultarParcelasModeloVenda
- `POST  ` /api/v{version}/ModeloVenda/ConsultarModeloDeVendaSeguroPorChave

### NotasFiscais
- `POST  ` /api/v{version}/NotasFiscais/ConsultarNFEntrada
- `POST  ` /api/v{version}/NotasFiscais/SalvarArquivoXMLnotafiscalEntrada

### Obras
- `POST  ` /api/v{version}/Obras/ObterObrasAtivas
- `POST  ` /api/v{version}/Obras/ConsultarObraPorChave
- `POST  ` /api/v{version}/Obras/ObterMesesAbertosPorEmpresaObra

### Orcamento
- `POST  ` /api/v{version}/Orcamento/AlterarInsumoOrcamento
- `POST  ` /api/v{version}/Orcamento/ExcluirInsumoOrcamento
- `POST  ` /api/v{version}/Orcamento/InserirInsumoOrcamento
- `POST  ` /api/v{version}/Orcamento/AlterarServicoOrcamento
- `POST  ` /api/v{version}/Orcamento/ExcluirServicoOrcamento
- `POST  ` /api/v{version}/Orcamento/InserirServicoOrcamento
- `POST  ` /api/v{version}/Orcamento/ConsultarInsumosPorChave
- `POST  ` /api/v{version}/Orcamento/AlterarPlanilhaCronograma
- `POST  ` /api/v{version}/Orcamento/ExcluirPlanilhaCronograma
- `POST  ` /api/v{version}/Orcamento/InserirPlanilhaCronograma
- `POST  ` /api/v{version}/Orcamento/ExportarOrcamentoEstrutura
- `POST  ` /api/v{version}/Orcamento/ConsultarEstruturaOrcaPorChave
- `POST  ` /api/v{version}/Orcamento/ConsultarEstruturaOrcaPorServico
- `POST  ` /api/v{version}/Orcamento/ConsultarServicoOrcamentoPorChave
- `POST  ` /api/v{version}/Orcamento/ConsultarServicoOrcadoDesintegrado
- `POST  ` /api/v{version}/Orcamento/ExcluirEstruturaServicoDeOrcamento
- `POST  ` /api/v{version}/Orcamento/ExportarOrcamentoEstruturaPaginada
- `POST  ` /api/v{version}/Orcamento/InserirEstruturaServicoDeOrcamento
- `POST  ` /api/v{version}/Orcamento/ConsultarPlanilhaCronogramaPorChave
- `POST  ` /api/v{version}/Orcamento/AtualizarEstruturaServicoDeOrcamento
- `POST  ` /api/v{version}/Orcamento/ConsultarServicoOrcamentoPorOrcamento

### PedidoCompra
- `POST  ` /api/PedidoCompra/GravaPedidoDeCompra
- `POST  ` /api/v{version}/PedidoCompra/AprovarPedidoCompraServicoApp
- `POST  ` /api/v{version}/PedidoCompra/AprovarPedidoCompraMaterialApp
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServico
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoMaterial
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoPatrimonio
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoComplemento
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoEmergencial
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoAdiantamento
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoRegularizacao
- `POST  ` /api/v{version}/PedidoCompra/ConfirmarRecebimentoOrdemCompraFornecedor
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServicoContrato
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoContratoMaterial
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServicoComplemento
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServicoEmergencial
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServicoAdiantamento
- `POST  ` /api/v{version}/PedidoCompra/GravarPedidoDeCompraDoTipoServicoRegularizacao
- `POST  ` /api/PedidoCompra/ValidarPermissaoAssistentePedidoServico

### Pessoas
- `POST  ` /api/v{version}/Pessoas/GravarPessoa
- `POST  ` /api/v{version}/Pessoas/ManterTelefone
- `POST  ` /api/v{version}/Pessoas/ExcluirTelefone
- `POST  ` /api/v{version}/Pessoas/ConsultarUnidades
- `POST  ` /api/v{version}/Pessoas/AlterarContaPadrao
- `POST  ` /api/v{version}/Pessoas/ConsultarTelefones
- `POST  ` /api/v{version}/Pessoas/AlterarSenhaCliente
- `POST  ` /api/v{version}/Pessoas/GravarContaBancaria
- `POST  ` /api/v{version}/Pessoas/ConsultarTipoEndereco
- `POST  ` /api/v{version}/Pessoas/CriarCredenciaisUAUWeb
- `POST  ` /api/v{version}/Pessoas/ConsultarPessoaPorChave
- `POST  ` /api/v{version}/Pessoas/ConsultarContasBancarias
- `POST  ` /api/v{version}/Pessoas/ConsultarPessoasComVenda
- `POST  ` /api/v{version}/Pessoas/AlterarPessoaAcessoPortal
- `POST  ` /api/v{version}/Pessoas/ConsultarPessoasPorCPFCNPJ
- `POST  ` /api/v{version}/Pessoas/ExcluirBancoEContaPorChave
- `POST  ` /api/v{version}/Pessoas/RecuperarCredenciaisUAUWeb
- `POST  ` /api/v{version}/Pessoas/ConsultarPessoasPorCondicao
- `POST  ` /api/v{version}/Pessoas/ImportarDadosPessoasParaUau
- `POST  ` /api/v{version}/Pessoas/ConsultarTelefonePesPorChave
- `POST  ` /api/v{version}/Pessoas/ConsultarEnderecoPessoasPorChave
- `POST  ` /api/v{version}/Pessoas/ConsultarPessoasFuncionariosAtivos
- `POST  ` /api/v{version}/Pessoas/ConsultarDadosPessoaFisicaPorCodigo
- `POST  ` /api/v{version}/Pessoas/ConsultarDadosPessoaPorCpfCnpjEStatus
- `POST  ` /api/v{version}/Pessoas/ConsultarDadosAdicionaisPessoaPorChave

### Planejamento
- `POST  ` /api/v{version}/Planejamento/AtualizarItemPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarItemPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarSaldoSIPlanejada
- `POST  ` /api/v{version}/Planejamento/InserirServicoPlanejamento
- `POST  ` /api/v{version}/Planejamento/ExportarPlanejamentoProduto
- `POST  ` /api/v{version}/Planejamento/AtualizarInsumosPlanejamento
- `POST  ` /api/v{version}/Planejamento/AtualizarServicoPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarServicoPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarSolicitacaoInsumoPL
- `POST  ` /api/v{version}/Planejamento/InserirEstruturaPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarSolicitacaoServicoPL
- `POST  ` /api/v{version}/Planejamento/InserirServicoPlanejamentoMes
- `POST  ` /api/v{version}/Planejamento/AprovarSolicitacaoPlanejamento
- `POST  ` /api/v{version}/Planejamento/AtualizarEstruturaPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarEstruturaPlanejamento
- `POST  ` /api/v{version}/Planejamento/AtualizarServicoPlanejamentoMes
- `POST  ` /api/v{version}/Planejamento/ConsultarDesembolsoPlanejamento
- `POST  ` /api/v{version}/Planejamento/ConsultarServicoPlanejamentoMes
- `POST  ` /api/v{version}/Planejamento/ConsultarServicoPlanejamentoPorObra
- `POST  ` /api/v{version}/Planejamento/InserirServicoPlanejamentoIntegrado
- `POST  ` /api/v{version}/Planejamento/RecusarSolicitacaoPlanejamentoGeral
- `POST  ` /api/v{version}/Planejamento/AprovarSolicitacaoPlanejamentoEmLote
- `POST  ` /api/v{version}/Planejamento/AtualizarServicoPlanejamentoIntegrado
- `POST  ` /api/v{version}/Planejamento/ConsultarServicoPlanejadoDesintegrado
- `POST  ` /api/v{version}/Planejamento/ConsultarServicoPlanejamentoIntegrado
- `POST  ` /api/v{version}/Planejamento/ConsultarAprovacaoPlPendentePorUsuario
- `POST  ` /api/v{version}/Planejamento/RecusarSolicitacaoPlanejamentoGeralEmLote
- `POST  ` /api/v{version}/Planejamento/AprovarSolicitacaoPlanejamentoInsumosEmLote
- `POST  ` /api/v{version}/Planejamento/AprovarSolicitacaoPlanejamentoServicosEmLote
- `POST  ` /api/v{version}/Planejamento/ConsultarQuantidadeAprovacaoPlPendentePorUsuario
- `POST  ` /api/Planejamento/ObterAcessoUsuarioNaObra

### ProcessoPagamento
- `POST  ` /api/v{version}/ProcessoPagamento/AprovarDVQ
- `POST  ` /api/v{version}/ProcessoPagamento/GerarProcesso
- `POST  ` /api/v{version}/ProcessoPagamento/GerarNotaFiscal
- `POST  ` /api/v{version}/ProcessoPagamento/AprovarProcessos
- `POST  ` /api/v{version}/ProcessoPagamento/EmissaoPagamento
- `POST  ` /api/v{version}/ProcessoPagamento/AcrescimoDesconto
- `POST  ` /api/v{version}/ProcessoPagamento/ConsultarProcessos
- `POST  ` /api/v{version}/ProcessoPagamento/ManutencaoProcesso
- `POST  ` /api/v{version}/ProcessoPagamento/RetornarParcelasDVQ
- `POST  ` /api/v{version}/ProcessoPagamento/GerarProcessoMedicao
- `POST  ` /api/v{version}/ProcessoPagamento/ManutencaoParcelasProcesso
- `POST  ` /api/v{version}/ProcessoPagamento/ConfirmarProcessoParaEmissao
- `POST  ` /api/v{version}/ProcessoPagamento/GerarNotaFiscalProdutoPeloXML
- `POST  ` /api/v{version}/ProcessoPagamento/GerarNotaFiscalServicoPeloXML
- `POST  ` /api/v{version}/ProcessoPagamento/IntegrarProcessoPagamentoUAUWS
- `POST  ` /api/v{version}/ProcessoPagamento/GerarNotaFiscalTransportePeloXML
- `POST  ` /api/ProcessoPagamento/ContaProcessoEmissaoPagamentoResumido
- `POST  ` /api/ProcessoPagamento/ConsultarQuantidadeProcessosAprovarDVQ
- `POST  ` /api/ProcessoPagamento/ValidaDadosPagamentoParaAceitarPixCopiaCola
- `POST  ` /api/ProcessoPagamento/ValidaDadosPagamentoParaAceitarCodigoDeBarras

### Proposta
- `POST  ` /api/v{version}/Proposta/GerarBoleto
- `POST  ` /api/v{version}/Proposta/ExpirarBoletos
- `POST  ` /api/v{version}/Proposta/GravarProposta
- `POST  ` /api/v{version}/Proposta/CancelarProposta
- `POST  ` /api/v{version}/Proposta/RenegociarProposta
- `POST  ` /api/v{version}/Proposta/ConsultarPropostaPorId
- `POST  ` /api/v{version}/Proposta/VincularArquivoProposta
- `POST  ` /api/v{version}/Proposta/GravarPedidoDeRecebimento
- `POST  ` /api/v{version}/Proposta/CancelarPedidoDeRecebimento
- `POST  ` /api/v{version}/Proposta/ConsultarHierarquiaParcelas
- `POST  ` /api/v{version}/Proposta/AtualizarPedidoDeRecebimento
- `POST  ` /api/v{version}/Proposta/ConsultarPedidoDeRecebimento
- `POST  ` /api/v{version}/Proposta/ProcessarRecebimentoParcelas
- `POST  ` /api/v{version}/Proposta/ConsultarPedidoDeRecebimentoUAU
- `POST  ` /api/v{version}/Proposta/RetornarValoresEstruturaComissao
- `POST  ` /api/v{version}/Proposta/RetornaValorComissaoDeducaoParcelas
- `POST  ` /api/Proposta/TraduzirRequestParcelasGeradas
- `POST  ` /api/Proposta/TraduzirRequestParcelasSelecionadas

### Prospect
- `POST  ` /api/v{version}/Prospect/GravarProspect
- `POST  ` /api/v{version}/Prospect/ImportarProspect
- `POST  ` /api/v{version}/Prospect/ListarGrauParentesco
- `POST  ` /api/v{version}/Prospect/MigrarProspectPessoa
- `POST  ` /api/v{version}/Prospect/ConsultarTodosProspects
- `POST  ` /api/v{version}/Prospect/ConsultarProspectPorChave
- `POST  ` /api/v{version}/Prospect/AlterarResponsavelProspect
- `POST  ` /api/v{version}/Prospect/ConsultarProspectComFiltro
- `GET   ` /api/v{version}/Prospect/BuscarGrauParentescoPorCodigo

### Recebiveis
- `GET   ` /api/v{version}/Recebiveis/ConsultarMeiosPreferenciaisRecebimento
- `POST  ` /api/v{version}/Recebiveis/PadraoDeCobranca/{IdEmpresa}/{NumPadraoCobranca}
- `POST  ` /api/v{version}/Recebiveis/ParcelasECobrancasDoCliente
- `POST  ` /api/v{version}/Recebiveis/AlterarMeioPreferencialDeRecebimentoDaParcela

### RelatorioIRPF
- `POST  ` /api/v{version}/RelatorioIRPF/GerarPDFRelIRPF
- `POST  ` /api/v{version}/RelatorioIRPF/GerarPDFRelIRPFV2

### RequisicaoCompra
- `POST  ` /api/v{version}/RequisicaoCompra/AprovarRequisicoesCompra
- `POST  ` /api/v{version}/RequisicaoCompra/DesaprovarRequisicoesCompra

### Reserva
- `POST  ` /api/v{version}/Reserva/GravarReserva
- `POST  ` /api/v{version}/Reserva/ExcluirReserva
- `POST  ` /api/v{version}/Reserva/ConsultarReservas
- `POST  ` /api/v{version}/Reserva/ConsultarReservaVendedor
- `POST  ` /api/v{version}/Reserva/ConsultarReservaPorCodigo
- `POST  ` /api/v{version}/Reserva/ConsultaReservaPorProposta
- `POST  ` /api/v{version}/Reserva/ConsultarDadosControleReserva

### RotinasGerais
- `POST  ` /api/v{version}/RotinasGerais/BuscaCamposPerson
- `POST  ` /api/v{version}/RotinasGerais/BuscaCAPVendaEmpresa
- `POST  ` /api/v{version}/RotinasGerais/InserirConsultaGeral
- `POST  ` /api/v{version}/RotinasGerais/ExecutarConsultaGeral
- `POST  ` /api/v{version}/RotinasGerais/BuscarIndicesDeReajuste
- `POST  ` /api/v{version}/RotinasGerais/BuscarTiposDeVencimento
- `POST  ` /api/v{version}/RotinasGerais/ConsultarPadroesCobranca
- `POST  ` /api/v{version}/RotinasGerais/BuscarCategoriasDeProduto
- `POST  ` /api/v{version}/RotinasGerais/BuscarFinalidadesDeCompra
- `POST  ` /api/v{version}/RotinasGerais/BuscarVeiculosDeDivulgacao
- `POST  ` /api/v{version}/RotinasGerais/ConsultarParamConsultaGeral

### Shopping
- `POST  ` /api/v{version}/Shopping/GravaRendimentos
- `POST  ` /api/v{version}/Shopping/ImportacaoDeParcelas
- `POST  ` /api/v{version}/Shopping/ConsultarRendimentoLojista

### Usuarios
- `POST  ` /api/v{version}/Usuarios/ConsultarUsuariosAtivos
- `POST  ` /api/v{version}/Usuarios/ConsultarGruposDeUsuario

### Venda
- `POST  ` /api/v{version}/Venda/RenegociarVenda
- `POST  ` /api/v{version}/Venda/BuscaParcRenegWeb
- `POST  ` /api/v{version}/Venda/ExclusaoDeBoletos
- `POST  ` /api/v{version}/Venda/ExportarVendasXml
- `POST  ` /api/v{version}/Venda/ImportacaoDeVenda
- `POST  ` /api/v{version}/Venda/AprovDesaprovReneg
- `POST  ` /api/v{version}/Venda/BuscarTiposDeCustas
- `POST  ` /api/v{version}/Venda/ConsultarHistoricos
- `POST  ` /api/v{version}/Venda/ExcluirParcelaCusta
- `POST  ` /api/v{version}/Venda/GerarBoletoBancario
- `POST  ` /api/v{version}/Venda/GerarPDFResumoVenda
- `POST  ` /api/v{version}/Venda/BuscarStatusCobranca
- `POST  ` /api/v{version}/Venda/ConsultarResumoVenda
- `POST  ` /api/v{version}/Venda/GerarVendaDeProposta
- `POST  ` /api/v{version}/Venda/BuscarTiposDeParcelas
- `POST  ` /api/v{version}/Venda/FinalizarRenegociacao
- `POST  ` /api/v{version}/Venda/GravarOcorrenciaAnexo
- `POST  ` /api/v{version}/Venda/AlterarDataProrrogacao
- `POST  ` /api/v{version}/Venda/BuscarParcelasAReceber
- `POST  ` /api/v{version}/Venda/BuscarParametroCobranca
- `POST  ` /api/v{version}/Venda/BuscarParcelasRecebidas
- `POST  ` /api/v{version}/Venda/BuscarStatusDeEscritura
- `POST  ` /api/v{version}/Venda/ConsultarParcelasDaVenda
- `POST  ` /api/v{version}/Venda/GerarPDFEvolucaoContrato
- `POST  ` /api/v{version}/Venda/BuscarRecebimentosDaVenda
- `POST  ` /api/v{version}/Venda/ExportarPessoasDaVendaXml
- `POST  ` /api/v{version}/Venda/GravarPedidoDeRecebimento
- `POST  ` /api/v{version}/Venda/ImportacaoVendaComRetorno
- `POST  ` /api/v{version}/Venda/ManterStatusCobrancaVenda
- `POST  ` /api/v{version}/Venda/VendaValidaParaManutencao
- `POST  ` /api/v{version}/Venda/ConsultarContasReceberCalc
- `POST  ` /api/v{version}/Venda/ImportacaoParcelasDeCustas
- `POST  ` /api/v{version}/Venda/BuscarCampanhaDescontoVenda
- `POST  ` /api/v{version}/Venda/CancelarPedidoDeRecebimento
- `POST  ` /api/v{version}/Venda/AtualizarPedidoDeRecebimento
- `POST  ` /api/v{version}/Venda/ConsultarPedidoDeRecebimento
- `POST  ` /api/v{version}/Venda/ProcessarRecebimentoParcelas
- `POST  ` /api/v{version}/Venda/ManterStatusEscrituracaoVenda
- `POST  ` /api/v{version}/Venda/RetornaChavesVendasPorPeriodo
- `POST  ` /api/v{version}/Venda/ConsultarDemonstrativoCorrecao
- `POST  ` /api/v{version}/Venda/ConsultarPlanoIndexadoresVenda
- `POST  ` /api/v{version}/Venda/GravarNumContratoFinanciamento
- `POST  ` /api/v{version}/Venda/ConsultarEmpreendimentosCliente
- `POST  ` /api/v{version}/Venda/ConsultarPedidoDeRecebimentoUAU
- `POST  ` /api/v{version}/Venda/ConsultarUnidadesCompradasPorCPF
- `POST  ` /api/Venda/ConsultarStatusCobrancaAtiva
- `POST  ` /api/v{version}/Venda/CalcularDescontoCampanhaAntecipacao
- `POST  ` /api/v{version}/Venda/ConsultarCampanhaDescontoDisponivel
- `POST  ` /api/v{version}/Venda/ConsultarDescontoAntecipacaoParcela
- `POST  ` /api/v{version}/Venda/ConsultarUnidadesCompradasUsrLogado
- `POST  ` /api/v{version}/Venda/ConsultarUnidadesCompradasPorCPFCNPJ
- `GET   ` /api/v{version}/Venda/GerarPDFEvolucaoSaldoDevedorFinanciamento

### Webhook
- `POST  ` /api/v{version}/Webhook/ConfirmarRecebimentoOrdemCompra/{token}
- `POST  ` /api/v{version}/Webhook/AtualizarRecebimentoPix
