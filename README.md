# Oferta Central — afiliados Mercado Livre

Painel local para buscar ofertas do Mercado Livre, preparar mensagens, aprovar ou descartar sugestões, rastrear cliques, agendar publicações no Telegram e compartilhar manualmente no WhatsApp.

## Iniciar no Windows

1. Execute `start-windows.bat` para abrir o launcher visual.
2. O launcher verifica o Docker Desktop, disponibiliza os atalhos de configuração e inicia a aplicação ao clicar em **Start - Abrir tudo**.
3. Se Docker não estiver instalado, use **Como instalar Docker** no launcher. Instale e abra [Docker Desktop](https://www.docker.com/products/docker-desktop/) com WSL 2 habilitado e depois tente novamente.
4. O launcher também tem acesso rápido ao `.env` e ao log. O painel abre em http://localhost:8000. O SQLite é persistido em `data/affiliate.db`.
4. Abra **Configurações**, insira credenciais e salve.

Para parar: `docker compose down`. Para ver logs: use o botão **Abrir log** para ver o registro do launcher ou rode `docker compose logs -f` para os logs do serviço.

## Desenvolvimento sem Docker

Backend: `cd backend`, criar ambiente virtual, `pip install -r requirements.txt` e `uvicorn app.main:app --reload --port 8000`.
Frontend: `cd frontend`, `npm install` e `npm run dev`; painel em http://localhost:5173.

## Fluxo de ofertas

- Na aba **Radar**, busque por palavra-chave e categoria. Em **Configurações**, use uma linha por busca no formato `Categoria: termo` (há seis buscas iniciais de Eletrônicos, Casa e Cozinha, Moda e Beleza). O sistema percorre até três termos por hora e alterna a lista; novas ofertas entram pendentes e recebem copy da IA local.
- A busca de catálogo usa a API oficial autenticada do Mercado Livre. Cadastre um aplicativo Mercado Livre, informe Client ID e Client Secret e autorize a conta pelo painel. Configure no app o redirect `http://localhost:8000/api/marketplace/mercadolivre/callback`.
- O link de afiliado é criado dentro da Central de Afiliados do Mercado Livre. No Radar, copie o link do produto, abra **Gerar link afiliado oficial**, crie o link no portal e cole-o para aprovar. O sistema exige este passo para evitar publicar links comuns como se fossem afiliados.
- Alternativamente, em **Configurações**, informe o valor `matt_tool` realmente fornecido para sua conta. O radar então acrescenta a tag às novas ofertas automaticamente. Ao aprovar uma oferta sem link pré-gerado, o backend preserva a URL do produto e seus parâmetros e acrescenta `matt_tool=<valor>`. Link já convertido pelo portal mantém-se intacto. Endpoint: `POST /api/affiliate/convert`.
- O link que deve ser divulgado é o interno `/r/<id>?source=<canal>`. A rota registra clique, horário UTC e origem no SQLite e redireciona para o link afiliado. `localhost` não pode ser acessado pelos seus seguidores; para divulgação use uma URL pública sua em **URL pública para links rastreáveis**.
- O link `https://www.mercadolivre.com.br/social/sg20250722114920` é um endereço de perfil social, não um valor de `matt_tool`. Use o link de produto gerado pela Central ou o valor de rastreio mostrado para você. O backend rejeita URLs de perfil como código para evitar um link com comissão falsa.
- Após aprovar, a oferta aparece na Vitrine e fica elegível para cópia ou publicação pelo Bot Telegram. Não há disparo pelo WhatsApp.
- O agendador permite distribuir 6 ou 8 posts uniformemente entre o início e o fim definidos. A Bot API posta apenas ofertas aprovadas; preencha o chat ID (necessário para enviar) e o link do grupo (atalho visual opcional) e ligue a automação.
- O link rastreável é `http://localhost:8000/r/<id>?source=telegram`. Para tráfego externo, configure um domínio público acessível; `localhost` só funciona na própria máquina.
- Telegram usa a Bot API oficial. Configure token e chat ID e adicione o bot ao canal/grupo com permissão para postar. A publicação exige aprovação prévia da oferta e o computador ligado.

## IA

Ollama local é a opção padrão e gratuita; o Compose baixa `llama3.2:1b` automaticamente. O Radar gera uma mensagem para os novos produtos e permite gerar novamente pelo painel. O Gemini continua opcional.

## Shopee e Amazon

Esses conectores precisam de credenciais aprovadas nas APIs oficiais de cada programa. A Amazon exige conta aprovada e acesso à Creators API ([requisitos oficiais](https://associados.amazon.com.br/creatorsapi/docs/en-us/onboarding/register-for-creators-api)); a Shopee Affiliate API depende de acesso concedido no portal ([acesso à API](https://help.shopee.sg/portal/10/article/191702-API-Access)). Não raspamos páginas nem fabricamos códigos de afiliado. O Radar nesta versão busca Mercado Livre.

## Pastas

```text
backend/app/main.py        API FastAPI, modelos SQLite, agendador e integrações
frontend/src/App.tsx        Painel React/TypeScript
frontend/src/style.css      Tema escuro responsivo
docker-compose.yml          Execução local persistente
start-windows.bat           Inicialização rápida no Windows
```

## Segurança e limites

Esta configuração é para uso local confiável. Não exponha a porta à internet sem autenticação, HTTPS, proteção de segredos e controle de acesso. Tokens são guardados em texto no SQLite local. Os números de comissão são estimativas informadas no cadastro; a API do Mercado Livre não é consultada. Estatísticas e insight são calculados dos cliques recebidos, sem inferir dados não observados.

## Credenciais do Mercado Livre no Render

No serviço web do Render, cadastre as variáveis `ML_CLIENT_ID` e `ML_CLIENT_SECRET` com os valores do aplicativo no DevCenter. O backend usa essas variáveis prioritariamente, sem depender do SQLite temporário para iniciar a autorização. Localmente, os campos do painel continuam funcionando.

## Banco de dados no Render

O SQLite padrão funciona para execução local. No Render, o sistema de arquivos de um serviço web é temporário; sem persistência configurada, SQLite pode voltar vazio após reinício ou novo deploy. Para manter as configurações, ofertas e cliques:

- **Postgres:** crie um banco no Render, copie a Internal Database URL e adicione ao serviço web como variável `DATABASE_URL`. O backend aceita URLs `postgres://` e `postgresql://` do Render.
- **Disco persistente:** disponível em serviço compatível/plano pago, anexe um disco no caminho `/app/data`. O SQLite padrão passa a ficar dentro do disco.

Depois faça um deploy e confira se o painel mantém uma configuração após reiniciar. A API não retorna os valores de segredos; o painel informa quando uma chave já foi salva, e deixar o campo secreto vazio preserva o valor salvo.
