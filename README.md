# 📦 Expiry Bot Web — Sistema de Controle de Validades

## 🧭 Visão Geral

O **Expiry Bot Web** é um sistema completo de **prevenção de perdas e controle de validade** para produtos perecíveis no varejo.
Ele permite importar planilhas de estoque ou notas fiscais eletrônicas (NF-e em XML), registrar entradas e saídas, gerar relatórios em PDF e Excel e enviar alertas automáticos por e-mail.

---

## ⚙️ Funcionalidades Principais

✅ Importar estoque via **planilha Excel/CSV**  
✅ Importar automaticamente produtos perecíveis de uma **Nota Fiscal (XML)**  
✅ Registrar **entradas (recebimentos)** e **saídas (vendas)** manualmente  
✅ Visualizar o **estoque atual**, produtos **a vencer** e **vencidos**  
✅ Gerar relatórios em **Excel** e **PDF**  
✅ Enviar alertas de validade por **e-mail (Gmail)**  
✅ Editar e excluir itens do estoque pela interface web

---

## 💻 Instalação e Configuração

1. Certifique-se de ter o **Python 3.11 ou superior** instalado.
2. Baixe o pacote do Expiry Bot Web e extraia em uma pasta local.
3. No PowerShell ou terminal, entre na pasta extraída:
   ```bash
   cd expiry-bot-web-PTBR
   ```
4. Crie e ative o ambiente virtual:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
5. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
6. Inicie o painel web:
   ```bash
   streamlit run src/painel_expiry_bot.py
   ```

O sistema abrirá automaticamente em seu navegador padrão (geralmente em: `http://localhost:8501`).

---

## 🧾 Como obter notas fiscais em formato XML

Para que o Expiry Bot leia automaticamente os produtos perecíveis, é **obrigatório usar o arquivo XML original da NF-e** (e não a versão impressa/DANFE).

### 🔹 Opção 1 — Receber o XML do fornecedor
Por lei, o fornecedor **deve enviar o arquivo XML por e-mail** ao comprador.  
O e-mail geralmente vem com o assunto parecido com:
```
Envio de Nota Fiscal Eletrônica - NF-e nº 12345
```
E contém um anexo com o nome no formato:
```
NFe35191011111111111111550010000000011000000010.xml
```
Basta salvar esse arquivo e importá-lo no sistema.

### 🔹 Opção 2 — Baixar o XML diretamente pelo QR Code da DANFE
1. Pegue a **nota impressa (DANFE)**.  
2. No canto inferior direito, encontre o **QR Code**.  
3. Escaneie com o celular ou acesse o link que aparece (geralmente `https://www.nfce.fazenda.sp.gov.br/...`).  
4. Ao abrir a página da SEFAZ, procure a opção **“Download do XML da Nota Fiscal”**.  
5. Faça login com o **CNPJ ou CPF do destinatário** e baixe o arquivo `.xml`.  
6. Salve-o em uma pasta e importe no Expiry Bot.

### 🔹 Opção 3 — Buscar pelo portal da SEFAZ
Você também pode baixar diretamente pelo **Portal Nacional da NF-e**:
1. Acesse: [https://www.nfe.fazenda.gov.br](https://www.nfe.fazenda.gov.br)  
2. Vá em **Consulta Completa (NF-e)**  
3. Informe a **chave de acesso de 44 dígitos** da nota fiscal.  
4. Faça login com certificado digital (ou e-CNPJ).  
5. Clique em **Baixar XML da NF-e**.

---

## 📊 Relatórios e Indicadores

- **PDF:** Gera automaticamente o resumo com os KPIs reais (em estoque, a vencer, vencidos e vendidos), além do gráfico de pizza com a distribuição atual.  
- **Excel:** Exporta o relatório detalhado com todos os produtos, lotes e status.

Os relatórios são salvos automaticamente na pasta:
```
/reports
```

---

## 📧 Configuração do envio de alertas por e-mail

1. Crie uma **senha de app do Gmail** (em [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords))  
2. No Expiry Bot, abra a aba ⚙️ **Configurações do Sistema**
3. Preencha os campos:
   - Usuário (Gmail)
   - Senha de app
   - E-mails destinatários
4. Marque **“Habilitar envio de e-mails”**
5. Clique em 💾 **Salvar Configurações**

O sistema enviará alertas automáticos dos produtos próximos ao vencimento.

---

## 🧠 Dicas de uso

- Importe o estoque inicial via Excel antes de começar o controle.  
- Sempre use os **arquivos XML originais das NF-es** — não a versão impressa.  
- Atualize os lotes e quantidades regularmente.  
- Gere relatórios periódicos para apresentação à gestão.  
- Faça backup da pasta `data/` e `reports/` regularmente.

---

## 🆘 Suporte e Manutenção

Em caso de dúvidas, entre em contato com o desenvolvedor ou equipe técnica responsável pelo projeto.

> Expiry Bot Web — Sistema de Prevenção de Perdas e Controle de Validades no Varejo  
> Desenvolvido por **Lucas Ramos Carneiro (Ship-IT)**  
> © 2025 — Todos os direitos reservados.
