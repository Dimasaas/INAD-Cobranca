# Runbook: configurar o INAD como servidor de intranet em Windows 10

> **Este documento é um procedimento para um agente de IA executar**, não um
> tutorial narrativo para humanos. Siga os passos na ordem, execute os
> comandos exatamente como estão, verifique a saída indicada em cada um
> antes de seguir pro próximo, e pare para perguntar ao operador humano
> sempre que um passo disser `PERGUNTAR AO OPERADOR`.

## Objetivo

Deixar um computador Windows 10 rodando o INAD (`INAD_Cobranca.exe`) como
servidor, escutando na rede local (intranet), para que outros computadores
da mesma rede acessem o painel pelo navegador em `http://<IP-desta-máquina>:<porta>`.
O banco de dados (`inad_database.db`) vive nesta máquina; os outros
computadores só precisam de um navegador.

Este é um CRM que processa dados pessoais sensíveis de clientes
inadimplentes (nome, CPF, telefone, endereço, saldo devedor) — sujeito à
LGPD. Trate qualquer erro/ambiguidade neste runbook parando e perguntando,
em vez de assumir.

---

## 0. Pré-requisitos e verificação de estado

Antes de qualquer coisa, verifique:

1. Sistema operacional é Windows 10 (ou 11).
2. `INAD_Cobranca-Windows.zip` já foi baixado da aba **Releases** do
   repositório GitHub. Se não, `PERGUNTAR AO OPERADOR` o link ou peça pra
   ele baixar manualmente — este runbook não cobre autenticação no GitHub.
3. Escolha (ou pergunte) a pasta de instalação permanente. Recomendado:
   `C:\INAD\`. Todo o resto deste runbook assume essa pasta — ajuste os
   caminhos se for diferente.
4. `PERGUNTAR AO OPERADOR`: uma lista de nomes das pessoas que vão acessar
   o painel (cada uma recebe um cadastro/token próprio — nunca compartilhe
   um token entre duas pessoas). Sem essa lista, não prossiga para a seção 3.
5. `PERGUNTAR AO OPERADOR` (opcional, tem default): a porta TCP a usar.
   Default: `8000`. Só peça se quiser confirmar.

---

## 1. Extrair o pacote

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\INAD_Cobranca-Windows.zip" -DestinationPath "C:\INAD" -Force
```

Verificação: `C:\INAD\INAD_Cobranca.exe` deve existir, junto com
`index.html`, `inad_analytics.html`, `analytics.js`, `analytics.css`,
`libs\`, `AI_CONTEXT.md`, `README.md`.

```powershell
Test-Path "C:\INAD\INAD_Cobranca.exe"   # deve retornar True
```

Se retornar `False`, o zip não foi extraído corretamente — pare e reporte
ao operador em vez de prosseguir.

---

## 2. Configurar o perfil de rede como Privado

Necessário para o Firewall do Windows liberar conexões de entrada de outros
computadores da mesma rede (Passo 5).

```powershell
Get-NetConnectionProfile
```

Verifique a coluna `NetworkCategory` da conexão ativa (Wi-Fi ou Ethernet).
Se não estiver `Private`:

```powershell
Set-NetConnectionProfile -InterfaceAlias "<NOME_DA_INTERFACE>" -NetworkCategory Private
```

(Substitua `<NOME_DA_INTERFACE>` pelo valor da coluna `InterfaceAlias` do
comando anterior — ex.: `Wi-Fi` ou `Ethernet`.) Se este comando falhar por
falta de permissão de administrador, `PERGUNTAR AO OPERADOR` para rodar
esse passo manualmente com uma sessão elevada, ou para confirmar com o TI
da empresa que a rede já está marcada como privada.

---

## 3. Cadastrar um operador por pessoa (obrigatório antes do passo 5)

Para cada nome recebido na seção 0.4, rode (substituindo `"Nome da Pessoa"`):

```powershell
cd C:\INAD
.\INAD_Cobranca.exe --add-operator "Nome da Pessoa"
```

**Saída esperada** (formato):
```
Operador 'Nome da Pessoa' criado.
Token (guarde em local seguro — não será exibido de novo):
  <TOKEN_LONGO_AQUI>

Uso pelo operador: header 'X-INAD-Token: <TOKEN_LONGO_AQUI>' ou ?token=<TOKEN_LONGO_AQUI> na URL.
```

**Capture o `<TOKEN_LONGO_AQUI>` de cada execução e associe ao nome da
pessoa** — precisará dele na seção 6. O token só é exibido nesta única vez;
não há como recuperá-lo depois (só revogar e recriar).

Repita este comando uma vez para cada pessoa da lista. Depois de terminar,
confirme o cadastro:

```powershell
.\INAD_Cobranca.exe --list-operators
```

Cada nome da lista deve aparecer com status `ativo`.

---

## 4. Criar o script de inicialização em modo rede

Crie o arquivo `C:\INAD\Iniciar_Servidor_Rede.bat` com este conteúdo exato
(ajuste a porta se o operador pediu uma diferente do default):

```batch
@echo off
set INAD_HOST=0.0.0.0
set INAD_PORT=8000
INAD_Cobranca.exe
```

Este é o script que deve ser usado para ligar o servidor a partir de agora
— **não** execute `INAD_Cobranca.exe` diretamente (isso liga em modo
localhost-apenas, inacessível pela rede).

---

## 5. Primeira execução: liberar no Firewall

Rode o script criado no passo 4:

```powershell
Start-Process "C:\INAD\Iniciar_Servidor_Rede.bat"
```

Se aparecer o diálogo **"O Firewall do Windows Defender bloqueou alguns
recursos deste aplicativo"**:
- Marque a caixa **Redes particulares** (não precisa marcar "Redes
  públicas").
- Clique em **Permitir acesso**.

Se o diálogo não aparecer (algumas políticas de empresa suprimem isso),
crie a regra de entrada manualmente:

```powershell
New-NetFirewallRule -DisplayName "INAD Cobranca" -Direction Inbound -Program "C:\INAD\INAD_Cobranca.exe" -Action Allow -Profile Private
```

**Verificação:** com o servidor rodando, confirme localmente:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Deve retornar um JSON com `"status": "ok"`. Se der erro de conexão, o
servidor não subiu — verifique se apareceu algum erro no terminal do
`.bat` (ex.: mensagem `[ERRO] ... nenhum operador está cadastrado` indica
que a seção 3 não foi concluída) antes de prosseguir.

---

## 6. Descobrir o IP da máquina e montar os links de acesso

```powershell
ipconfig
```

Pegue o **Endereço IPv4** da interface ativa (ex.: `192.168.1.50`). Monte
um link por pessoa, usando o token capturado na seção 3:

```
http://<IP>:<PORTA>/index.html?token=<TOKEN_DA_PESSOA>
```

Exemplo: `http://192.168.1.50:8000/index.html?token=abc123...`

Entregue a cada pessoa **o link completo com o token dela** (não o token
sozinho) — pelo canal que o operador preferir (WhatsApp, e-mail). Informe
que devem **salvar esse link nos favoritos do navegador**: o token fica
válido só enquanto a aba/janela continuar aberta (é limpo ao fechar o
navegador), então reabrir exige clicar de novo no link completo.

---

## 7. Manter o servidor disponível

1. Desativar suspensão automática enquanto o painel precisar ficar no ar:
   ```powershell
   powercfg /change standby-timeout-ac 0
   ```
2. (Opcional, `PERGUNTAR AO OPERADOR` se deseja) Iniciar o servidor
   automaticamente no boot do Windows — copiar um atalho do
   `Iniciar_Servidor_Rede.bat` para a pasta de inicialização:
   ```powershell
   $ws = New-Object -ComObject WScript.Shell
   $shortcut = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\INAD.lnk")
   $shortcut.TargetPath = "C:\INAD\Iniciar_Servidor_Rede.bat"
   $shortcut.WorkingDirectory = "C:\INAD"
   $shortcut.Save()
   ```

---

## 8. Checklist final de verificação

Confirme cada item antes de considerar a configuração concluída:

- [ ] `C:\INAD\INAD_Cobranca.exe` existe e roda sem erro imediato.
- [ ] Um operador foi cadastrado para cada nome da lista da seção 0.4
      (`--list-operators` confirma todos como `ativo`).
- [ ] `Iniciar_Servidor_Rede.bat` existe com o conteúdo exato da seção 4.
- [ ] `Invoke-RestMethod http://127.0.0.1:<PORTA>/api/health` retorna
      `"status": "ok"` com o `.bat` rodando.
- [ ] Testado a partir de **outro computador da mesma rede**:
      `http://<IP>:<PORTA>/index.html?token=<UM_DOS_TOKENS>` abre o
      painel (não erro 401/timeout).
- [ ] Cada pessoa da lista recebeu seu próprio link com seu próprio token.

---

## 9. Lembretes de segurança (não pular)

- Nunca reutilize um token entre duas pessoas — cada uma tem o seu, criado
  na seção 3. Se alguém sair da empresa: `INAD_Cobranca.exe
  --revoke-operator "Nome"`.
- Esta máquina deve ter senha de login do Windows e, se possível,
  criptografia de disco (BitLocker) ativa — o INAD não criptografa o
  próprio banco de dados (decisão deliberada do projeto), conta com essa
  proteção do sistema operacional.
- Não exponha esta máquina fora da rede local da empresa (sem port
  forwarding no roteador, sem VPN pública) — o modelo de segurança do INAD
  foi desenhado para intranet local, não acesso pela internet aberta.
- Toda consulta ao perfil de um cliente específico fica registrada
  internamente (quem, qual cliente, quando) — consultável via
  `GET /api/audit?name=<nome>` caso precise auditar acessos depois.

---

## 10. Solução de problemas

| Sintoma | Diagnóstico / ação |
|---|---|
| `Invoke-RestMethod` local falha (seção 5) | Veja a saída do terminal do `.bat` para o erro exato. Confira `inad_errors.log` em `C:\INAD\`. |
| Erro `nenhum operador está cadastrado` ao rodar o `.bat` | Volte à seção 3 — precisa de pelo menos um operador ativo antes de ligar em `INAD_HOST=0.0.0.0`. |
| Outro computador não abre o link | Confirme mesma rede; reconfirme o IP (`ipconfig` — pode ter mudado); confirme a regra de firewall (seção 5) está ativa para `Private`. |
| Outro computador recebe 401 "Não autorizado" | Token errado/incompleto no link, ou operador foi revogado. Recadastre com `--add-operator` se necessário. |
| Depois de reiniciar o Windows, servidor não voltou | Seção 7 (auto-start) não foi configurada, ou foi pulada — o `.bat` precisa ser executado manualmente de novo, ou configure o atalho de Startup. |
