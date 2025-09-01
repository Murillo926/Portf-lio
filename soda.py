import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Select, Button, Modal, TextInput
from pymongo import MongoClient, ReturnDocument
import os
from dotenv import load_dotenv
from datetime import datetime
import random
import string
from typing import Optional, List, Dict, Any
import re
from bson import ObjectId
import asyncio

# Carregar configurações
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
ROBLOX_SERVER_LINK = os.getenv("ROBLOX_SERVER_LINK")
LOJA_CHANNEL_ID = int(os.getenv("LOJA_CHANNEL_ID", 0))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", 0))

if not all([MONGO_URI, TOKEN, ADMIN_ROLE_ID, LOG_CHANNEL_ID, ROBLOX_SERVER_LINK, TICKET_CATEGORY_ID]):
    raise ValueError("Variáveis de ambiente ausentes no arquivo .env")

# Conexão com MongoDB
try:
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.server_info()  # Testar conexão
except Exception as e:
    raise ConnectionError(f"Erro ao conectar ao MongoDB: {str(e)}")

db = mongo["loja_avancada"]
produtos_col = db["produtos"]
pedidos_col = db["pedidos"]
categorias_col = db["categorias"]
tickets_col = db["tickets"]

# ========== MODALS ==========
class ProdutoModal(Modal, title="Adicionar/Editar Produto"):
    nome = TextInput(label="Nome do Produto", required=True)
    preco = TextInput(label="Preço (ex: 10.99)", required=True)
    estoque = TextInput(label="Estoque Disponível", required=True)
    descricao = TextInput(label="Descrição", style=discord.TextStyle.long, required=False)
    imagem_url = TextInput(label="URL da Imagem", required=False)

    def __init__(self, produto: Optional[Dict] = None):
        super().__init__()
        if produto:
            self.nome.default = produto.get("nome", "")
            self.preco.default = str(produto.get("preco", ""))
            self.estoque.default = str(produto.get("estoque", ""))
            self.descricao.default = produto.get("descricao", "")
            self.imagem_url.default = produto.get("imagem_url", "")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validação do preço
            preco_str = str(self.preco).replace(',', '.').strip()
            if not re.match(r'^\d+(\.\d{1,2})?$', preco_str):
                raise ValueError("Formato de preço inválido. Use números com até 2 casas decimais.")
            
            self.preco_valor = float(preco_str)
            if self.preco_valor <= 0:
                raise ValueError("O preço deve ser positivo")

            # Validação do estoque
            estoque_str = str(self.estoque).strip()
            if not estoque_str.isdigit():
                raise ValueError("Estoque deve ser um número inteiro positivo")
            
            self.estoque_valor = int(estoque_str)
            if self.estoque_valor < 0:
                raise ValueError("O estoque não pode ser negativo")

            self.nome_valor = str(self.nome).strip()
            self.descricao_valor = str(self.descricao).strip() if str(self.descricao) else None
            self.imagem_url_valor = str(self.imagem_url).strip() if str(self.imagem_url) else None

            await interaction.response.defer()
        except ValueError as e:
            await interaction.response.send_message(
                f"❌ {str(e)}",
                ephemeral=True
            )

class CategoriaModal(Modal, title="Adicionar/Editar Categoria"):
    nome = TextInput(label="Nome da Categoria", required=True)
    emoji = TextInput(label="Emoji (opcional)", required=False)

    def __init__(self, categoria: Optional[Dict] = None):
        super().__init__()
        if categoria:
            self.nome.default = categoria.get("nome", "")
            self.emoji.default = categoria.get("emoji", "")

    async def on_submit(self, interaction: discord.Interaction):
        self.nome_valor = str(self.nome).strip()
        if not self.nome_valor:
            await interaction.response.send_message("❌ O nome da categoria não pode estar vazio", ephemeral=True)
            return
        
        self.emoji_valor = str(self.emoji).strip() if str(self.emoji) else None
        await interaction.response.defer()

class QuantidadeModal(Modal, title="Quantidade do Produto"):
    quantidade = TextInput(
        label="Quantidade",
        placeholder="Digite a quantidade desejada",
        default="1",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qtd = int(str(self.quantidade))
            if qtd <= 0:
                raise ValueError("A quantidade deve ser positiva")
            self.quantidade_valor = qtd
            await interaction.response.defer()
        except ValueError:
            await interaction.response.send_message(
                "❌ Quantidade inválida. Digite um número positivo.",
                ephemeral=True
            )

# ========== VIEWS ==========
class ProdutoSelect(Select):
    def __init__(self, produtos: List[Dict], placeholder: str = "Selecione um produto..."):
        options = []
        for produto in produtos:
            emoji = "🟢" if produto["estoque"] > 0 else "🔴"
            options.append(discord.SelectOption(
                label=f"{produto['nome']} - R${produto['preco']:.2f}",
                description=f"Estoque: {produto['estoque']} | {produto.get('descricao', '')[:50]}",
                value=str(produto["_id"]),
                emoji=emoji
            ))
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)

class CategoriaSelect(Select):
    def __init__(self, categorias: List[Dict], placeholder: str = "Selecione uma categoria..."):
        options = []
        for categoria in categorias:
            options.append(discord.SelectOption(
                label=categoria["nome"],
                value=str(categoria["_id"]),
                emoji=categoria.get("emoji", "📦")
            ))
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)

class CarrinhoView(View):
    def __init__(self, bot: commands.Bot, produto: Dict, user: discord.Member):
        super().__init__(timeout=1800)
        self.bot = bot
        self.produto = produto
        self.user = user
        self.quantidade = 1
        self.total = produto["preco"]

    @discord.ui.button(label="Definir Quantidade", style=discord.ButtonStyle.primary, emoji="🔢")
    async def definir_quantidade(self, interaction: discord.Interaction, button: Button):
        modal = QuantidadeModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if hasattr(modal, 'quantidade_valor'):
            if modal.quantidade_valor > self.produto["estoque"]:
                await interaction.followup.send(
                    f"❌ Quantidade indisponível! Estoque: {self.produto['estoque']}",
                    ephemeral=True
                )
                return
            
            self.quantidade = modal.quantidade_valor
            self.total = self.produto["preco"] * self.quantidade
            
            embed = discord.Embed(
                title="🛒 Carrinho de Compras",
                description=f"**Produto:** {self.produto['nome']}\n"
                          f"**Quantidade:** {self.quantidade}\n"
                          f"**Preço unitário:** R${self.produto['preco']:.2f}\n"
                          f"**Total:** R${self.total:.2f}",
                color=discord.Color.blue()
            )
            
            if self.produto.get("imagem_url"):
                embed.set_thumbnail(url=self.produto["imagem_url"])
            
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Finalizar Compra", style=discord.ButtonStyle.green, emoji="✅")
    async def finalizar_compra(self, interaction: discord.Interaction, button: Button):
        if self.quantidade > self.produto["estoque"]:
            await interaction.response.send_message(
                f"❌ Quantidade indisponível! Estoque: {self.produto['estoque']}",
                ephemeral=True
            )
            return
        
        # Verificar se já existe um ticket aberto para este usuário
        ticket_existente = tickets_col.find_one({
            "user_id": self.user.id,
            "status": "aberto"
        })
        
        if ticket_existente:
            await interaction.response.send_message(
                "❌ Você já tem um pedido em andamento. Finalize o pedido atual antes de criar um novo.",
                ephemeral=True
            )
            return
        
        # Criar canal de ticket
        guild = interaction.guild
        categoria = guild.get_channel(TICKET_CATEGORY_ID)
        
        if not categoria:
            await interaction.response.send_message(
                "❌ Categoria de tickets não encontrada. Contate um administrador.",
                ephemeral=True
            )
            return
        
        # Gerar ID único para o ticket
        ticket_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        nome_canal = f"ticket-{ticket_id}"
        
        # Criar canal com permissões
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            self.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        }
        
        # Adicionar permissões para administradores
        for role in guild.roles:
            if role.id == ADMIN_ROLE_ID or role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        
        try:
            canal_ticket = await categoria.create_text_channel(
                name=nome_canal,
                overwrites=overwrites
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Erro ao criar canal de ticket: {str(e)}",
                ephemeral=True
            )
            return
        
        # Registrar ticket no banco de dados
        ticket_data = {
            "ticket_id": ticket_id,
            "user_id": self.user.id,
            "channel_id": canal_ticket.id,
            "produto_id": self.produto["_id"],
            "produto_nome": self.produto["nome"],
            "quantidade": self.quantidade,
            "total": self.total,
            "status": "aberto",
            "data_criacao": datetime.now()
        }
        tickets_col.insert_one(ticket_data)
        
        # Enviar mensagem inicial no canal do ticket
        embed = discord.Embed(
            title=f"🛒 Pedido #{ticket_id}",
            description=f"**Cliente:** {self.user.mention}\n"
                      f"**Produto:** {self.produto['nome']}\n"
                      f"**Quantidade:** {self.quantidade}\n"
                      f"**Preço unitário:** R${self.produto['preco']:.2f}\n"
                      f"**Total:** R${self.total:.2f}",
            color=discord.Color.blue()
        )
        
        if self.produto.get("imagem_url"):
            embed.set_thumbnail(url=self.produto["imagem_url"])
        
        view = View(timeout=None)
        pix_btn = Button(label="Pix Copia e Cola", style=discord.ButtonStyle.green, emoji="💸")
        qr_btn = Button(label="QR Code Pix", style=discord.ButtonStyle.green, emoji="📱")
        
        async def pix_callback(inter: discord.Interaction):
            chave_pix = f"{random.randint(1000, 9999)}.{random.randint(1000, 9999)}.{random.randint(1000, 9999)}-{random.randint(10, 99)}"
            
            # Atualizar ticket com informações do pagamento
            tickets_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {
                    "metodo_pagamento": "Pix Copia e Cola",
                    "chave_pix": chave_pix,
                    "status_pagamento": "Aguardando"
                }}
            )
            
            embed = discord.Embed(
                title="🔑 Pix Copia e Cola",
                description=f"**Ticket ID:** `{ticket_id}`\n"
                          f"**Valor:** R${self.total:.2f}\n"
                          f"**Chave Pix:** `{chave_pix}`\n"
                          f"**Beneficiário:** Loja do Servidor\n\n"
                          "Após o pagamento, aguarde a aprovação.",
                color=discord.Color.green()
            )
            
            await inter.response.send_message(embed=embed)
            
            # Registrar pedido na coleção de pedidos
            pedido = {
                "user_id": self.user.id,
                "produto_id": self.produto["_id"],
                "produto": self.produto["nome"],
                "quantidade": self.quantidade,
                "preco_unitario": self.produto["preco"],
                "total": self.total,
                "metodo": "Pix Copia e Cola",
                "chave_pix": chave_pix,
                "status": "Aguardando Pagamento",
                "data": datetime.now(),
                "categoria": self.produto.get("categoria", "Sem categoria"),
                "ticket_id": ticket_id,
                "channel_id": canal_ticket.id
            }
            
            pedidos_col.insert_one(pedido)
        
        async def qr_callback(inter: discord.Interaction):
            # Gerar dados fictícios para o QR Code
            qr_data = f"00020126580014BR.GOV.BCB.PIX0136{random.randint(100000000000000000000000000000, 999999999999999999999999999999)}5204000053039865405{self.total:.2f}5802BR5925LOJA DO SERVIDOR6009SAO PAULO61080540900062250521{random.randint(10000000000000000000, 99999999999999999999)}6304"
            
            # Atualizar ticket com informações do pagamento
            tickets_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {
                    "metodo_pagamento": "QR Code Pix",
                    "qr_data": qr_data,
                    "status_pagamento": "Aguardando"
                }}
            )
            
            embed = discord.Embed(
                title="📱 QR Code Pix",
                description=f"**Ticket ID:** `{ticket_id}`\n"
                          f"**Valor:** R${self.total:.2f}\n\n"
                          "Escaneie o QR Code abaixo com seu aplicativo bancário:",
                color=discord.Color.green()
            )
            
            # URL fictícia para QR Code
            imgur_url = "https://i.imgur.com/xxxxxxx.png"
            embed.set_image(url=imgur_url)
            
            await inter.response.send_message(embed=embed)
            
            # Registrar pedido na coleção de pedidos
            pedido = {
                "user_id": self.user.id,
                "produto_id": self.produto["_id"],
                "produto": self.produto["nome"],
                "quantidade": self.quantidade,
                "preco_unitario": self.produto["preco"],
                "total": self.total,
                "metodo": "QR Code Pix",
                "qr_data": qr_data,
                "status": "Aguardando Pagamento",
                "data": datetime.now(),
                "categoria": self.produto.get("categoria", "Sem categoria"),
                "ticket_id": ticket_id,
                "channel_id": canal_ticket.id
            }
            
            pedidos_col.insert_one(pedido)
        
        pix_btn.callback = pix_callback
        qr_btn.callback = qr_callback
        
        view.add_item(pix_btn)
        view.add_item(qr_btn)
        
        await canal_ticket.send(embed=embed, view=view)
        
        # Adicionar botões de aprovação no canal do ticket
        view_admin = View(timeout=None)
        aprovar_btn = Button(label="✅ Aprovar Pagamento", style=discord.ButtonStyle.green, custom_id=f"aprovar_{ticket_id}")
        rejeitar_btn = Button(label="❌ Rejeitar Pagamento", style=discord.ButtonStyle.red, custom_id=f"rejeitar_{ticket_id}")
        fechar_btn = Button(label="🔒 Fechar Ticket", style=discord.ButtonStyle.gray, custom_id=f"fechar_{ticket_id}")
        
        async def aprovar_callback(inter: discord.Interaction):
            if not any(role.id == ADMIN_ROLE_ID for role in inter.user.roles) and not inter.user.guild_permissions.administrator:
                await inter.response.send_message("❌ Apenas administradores podem aprovar pagamentos.", ephemeral=True)
                return
            
            # Buscar informações do ticket
            ticket = tickets_col.find_one({"ticket_id": ticket_id})
            if not ticket:
                await inter.response.send_message("❌ Ticket não encontrado.", ephemeral=True)
                return
            
            # Buscar informações do pedido
            pedido = pedidos_col.find_one({"ticket_id": ticket_id})
            if not pedido:
                await inter.response.send_message("❌ Pedido não encontrado.", ephemeral=True)
                return
            
            # Verificar se há estoque suficiente
            produto_db = produtos_col.find_one({"_id": self.produto["_id"]})
            if not produto_db or produto_db["estoque"] < self.quantidade:
                await inter.response.send_message("❌ Estoque insuficiente para este produto.", ephemeral=True)
                return
            
            # Atualizar status do pedido
            pedidos_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {
                    "status": "Aprovado",
                    "aprovado_por": inter.user.id,
                    "data_aprovacao": datetime.now()
                }}
            )
            
            # Atualizar estoque
            produtos_col.update_one(
                {"_id": self.produto["_id"]},
                {"$inc": {"estoque": -self.quantidade}}
            )
            
            # Atualizar status do ticket
            tickets_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {
                    "status": "aprovado",
                    "aprovado_por": inter.user.id,
                    "data_aprovacao": datetime.now()
                }}
            )
            
            # Enviar mensagem de confirmação para o usuário
            try:
                user = await self.bot.fetch_user(self.user.id)
                embed_user = discord.Embed(
                    title="✅ Pagamento Aprovado!",
                    description=f"Seu pedido **#{ticket_id}** foi aprovado!\n\n"
                              f"**Produto:** {self.produto['nome']}\n"
                              f"**Quantidade:** {self.quantidade}\n"
                              f"**Total pago:** R${self.total:.2f}\n\n"
                              f"🔗 Link do servidor privado: [Clique aqui]({ROBLOX_SERVER_LINK})\n\n"
                              "Obrigado por sua compra!",
                    color=discord.Color.green()
                )
                
                if self.produto.get("imagem_url"):
                    embed_user.set_thumbnail(url=self.produto["imagem_url"])
                
                await user.send(embed=embed_user)
            except Exception as e:
                print(f"Erro ao enviar mensagem para o usuário: {e}")
            
            # Enviar mensagem no canal do ticket
            embed = discord.Embed(
                title="✅ Pagamento Aprovado",
                description=f"Pagamento aprovado por {inter.user.mention}\n\n"
                          f"**Ticket ID:** {ticket_id}\n"
                          f"**Produto:** {self.produto['nome']}\n"
                          f"**Quantidade:** {self.quantidade}\n"
                          f"**Total:** R${self.total:.2f}\n\n"
                          "O canal será fechado automaticamente em 1 minuto.",
                color=discord.Color.green()
            )
            
            await inter.response.send_message(embed=embed)
            
            # ENVIAR O EMBED DE LOG APÓS A APROVAÇÃO DO PAGAMENTO
            await self.enviar_log_pedido(ticket_id)
            
            # Agendar fechamento do canal
            await asyncio.sleep(60)
            try:
                await canal_ticket.delete()
            except:
                pass
            
            # Atualizar status do ticket
            tickets_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {"status": "fechado"}}
            )
        
        async def rejeitar_callback(inter: discord.Interaction):
            if not any(role.id == ADMIN_ROLE_ID for role in inter.user.roles) and not inter.user.guild_permissions.administrator:
                await inter.response.send_message("❌ Apenas administradores podem rejeitar pagamentos.", ephemeral=True)
                return
            
            # Modal para motivo da rejeição
            class MotivoRejeicaoModal(Modal, title="Motivo da Rejeição"):
                motivo = TextInput(
                    label="Motivo",
                    placeholder="Digite o motivo da rejeição...",
                    style=discord.TextStyle.long,
                    required=True
                )

                def __init__(self, bot: commands.Bot, user: discord.Member):
                    super().__init__()
                    self.bot = bot
                    self.user = user
                
                async def on_submit(self, inner_inter: discord.Interaction):
                    # Atualizar status do pedido
                    pedidos_col.update_one(
                        {"ticket_id": ticket_id},
                        {"$set": {
                            "status": "Rejeitado",
                            "motivo_rejeicao": str(self.motivo),
                            "rejeitado_por": inter.user.id,
                            "data_rejeicao": datetime.now()
                        }}
                    )
                    
                    # Atualizar status do ticket
                    tickets_col.update_one(
                        {"ticket_id": ticket_id},
                        {"$set": {
                            "status": "rejeitado",
                            "motivo_rejeicao": str(self.motivo),
                            "rejeitado_por": inter.user.id,
                            "data_rejeicao": datetime.now()
                        }}
                    )
                    
                    # Enviar mensagem para o usuário
                    try:
                        user = await self.bot.fetch_user(self.user.id)
                        embed_user = discord.Embed(
                            title="❌ Pedido Rejeitado",
                            description=f"Seu pedido **#{ticket_id}** foi rejeitado.\n\n"
                                      f"**Motivo:** {str(self.motivo)}\n\n"
                                      "Caso acredite que houve um engano, entre em contato conosco.",
                            color=discord.Color.red()
                        )
                        await user.send(embed=embed_user)
                    except Exception as e:
                        print(f"Erro ao enviar mensagem para o usuário: {e}")
                    
                    # Enviar mensagem no canal do ticket
                    embed = discord.Embed(
                        title="❌ Pagamento Rejeitado",
                        description=f"Pagamento rejeitado por {inter.user.mention}\n\n"
                                  f"**Motivo:** {str(self.motivo)}\n\n"
                                  "O canal será fechado automaticamente em 1 minuto.",
                        color=discord.Color.red()
                    )
                    
                    await inner_inter.response.send_message(embed=embed)
                    
                    # Agendar fechamento do canal
                    await asyncio.sleep(60)
                    try:
                        await canal_ticket.delete()
                    except:
                        pass
                    
                    # Atualizar status do ticket
                    tickets_col.update_one(
                        {"ticket_id": ticket_id},
                        {"$set": {"status": "fechado"}}
                    )
            
            modal = MotivoRejeicaoModal(self.bot, self.user)
            await inter.response.send_modal(modal)
        
        async def fechar_callback(inter: discord.Interaction):
            if not any(role.id == ADMIN_ROLE_ID for role in inter.user.roles) and not inter.user.guild_permissions.administrator:
                await inter.response.send_message("❌ Apenas administradores podem fechar tickets.", ephemeral=True)
                return
            
            # Atualizar status do ticket
            tickets_col.update_one(
                {"ticket_id": ticket_id},
                {"$set": {"status": "fechado"}}
            )
            
            # Fechar canal
            try:
                await canal_ticket.delete()
            except:
                pass
            
            await inter.response.send_message("✅ Ticket fechado com sucesso.", ephemeral=True)
        
        aprovar_btn.callback = aprovar_callback
        rejeitar_btn.callback = rejeitar_callback
        fechar_btn.callback = fechar_callback
        
        view_admin.add_item(aprovar_btn)
        view_admin.add_item(rejeitar_btn)
        view_admin.add_item(fechar_btn)
        
        await canal_ticket.send("**Painel de Administração**", view=view_admin)
        
        # Confirmar criação do ticket para o usuário
        await interaction.response.send_message(
            f"✅ Seu pedido foi criado! Acesse o canal {canal_ticket.mention} para continuar.",
            ephemeral=True
        )
    
    async def enviar_log_pedido(self, ticket_id: str):
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        
        # Buscar informações do pedido aprovado
        pedido = pedidos_col.find_one({"ticket_id": ticket_id})
        if not pedido:
            return
        
        # Buscar informações do produto para obter o estoque restante
        produto = produtos_col.find_one({"_id": pedido["produto_id"]})
        estoque_restante = produto["estoque"] if produto else 0
        
        embed = discord.Embed(
            title="📢 Novo Pedido Aprovado",
            description=f"👤 Usuário: <@{pedido['user_id']}> (`{pedido['user_id']}`)\n"
                      f"🛒 Produto: {pedido['produto']}\n"
                      f"🔢 Quantidade: {pedido['quantidade']}\n"
                      f"💰 Preço unitário: R${pedido['preco_unitario']:.2f}\n"
                      f"💲 Total: R${pedido['total']:.2f}\n"
                      f"📦 Estoque restante: {estoque_restante}\n"
                      f"🎫 Ticket ID: `{ticket_id}`",
            color=discord.Color.green()
        )
        
        if produto and produto.get("imagem_url"):
            embed.set_thumbnail(url=produto["imagem_url"])
        
        await log_channel.send(embed=embed)

# ========== COMANDOS ==========
class LojaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_member = None
        self.atualizar_painel.start()
    
    def cog_unload(self):
        self.atualizar_painel.cancel()
    
    async def verificar_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator and ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message(
                "❌ Você não tem permissão para usar este comando.",
                ephemeral=True
            )
            return False
        return True
    
    # ========== TASKS ==========
    @tasks.loop(minutes=10800)
    async def atualizar_painel(self):
        try:
            if not LOJA_CHANNEL_ID:
                return
                
            channel = self.bot.get_channel(LOJA_CHANNEL_ID)
            if not channel:
                return
                
            # Limpar mensagens antigas
            try:
                await channel.purge(limit=100)
            except:
                pass
                
            # Obter produtos e categorias
            produtos = list(produtos_col.find())
            categorias = list(categorias_col.find())
            
            if not produtos:
                embed = discord.Embed(
                    title="🛒 Loja do Servidor",
                    description="⚠️ Nenhum produto cadastrado no momento.",
                    color=discord.Color.orange()
                )
                await channel.send(embed=embed)
                return
                
            # Criar embed principal
            embed = discord.Embed(
                title="🛒 Loja do Servidor",
                description="Selecione uma categoria e um produto abaixo para comprar:",
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"Atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
            
            # View com seletores
            view = View(timeout=None)
            
            # Seletor de categorias
            if categorias:
                categoria_select = CategoriaSelect(categorias)
                
                async def categoria_callback(interaction: discord.Interaction):
                    categoria_id = interaction.data["values"][0]
                    produtos_filtrados = [p for p in produtos if str(p.get("categoria_id")) == categoria_id]
                    
                    if not produtos_filtrados:
                        await interaction.response.send_message(
                            "❌ Nenhum produto disponível nesta categoria.",
                            ephemeral=True
                        )
                        return
                    
                    produto_select = ProdutoSelect(produtos_filtrados)
                    
                    async def produto_callback(inter: discord.Interaction):
                        produto_id = inter.data["values"][0]
                        produto = next((p for p in produtos if str(p["_id"]) == produto_id), None)
                        
                        if not produto:
                            await inter.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                            return
                        
                        if produto["estoque"] <= 0:
                            await inter.response.send_message("❌ Produto esgotado no momento.", ephemeral=True)
                            return
                        
                        view_carrinho = CarrinhoView(self.bot, produto, interaction.user)
                        embed = discord.Embed(
                            title=f"🛍️ {produto['nome']}",
                            description=produto.get("descricao", "Sem descrição"),
                            color=discord.Color.green()
                        )
                        embed.add_field(name="💲 Preço Unitário", value=f"R${produto['preco']:.2f}", inline=True)
                        embed.add_field(name="📦 Estoque Disponível", value=produto["estoque"], inline=True)
                        
                        if produto.get("imagem_url"):
                            embed.set_thumbnail(url=produto["imagem_url"])
                        
                        await inter.response.send_message(embed=embed, view=view_carrinho, ephemeral=True)
                    
                    produto_select.callback = produto_callback
                    
                    view_produto = View(timeout=None)
                    view_produto.add_item(produto_select)
                    
                    await interaction.response.send_message(
                        "Selecione um produto:",
                        view=view_produto,
                        ephemeral=True
                    )
                
                categoria_select.callback = categoria_callback
                view.add_item(categoria_select)
            
            # Seletor de todos os produtos (fallback)
            produto_select_all = ProdutoSelect(produtos, "Ou selecione um produto diretamente")
            
            async def produto_all_callback(interaction: discord.Interaction):
                produto_id = interaction.data["values"][0]
                produto = next((p for p in produtos if str(p["_id"]) == produto_id), None)
                
                if not produto:
                    await interaction.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                    return
                
                if produto["estoque"] <= 0:
                    await interaction.response.send_message("❌ Produto esgotado no momento.", ephemeral=True)
                    return
                
                view_carrinho = CarrinhoView(self.bot, produto, interaction.user)
                embed = discord.Embed(
                    title=f"🛍️ {produto['nome']}",
                    description=produto.get("descricao", "Sem descrição"),
                    color=discord.Color.green()
                )
                embed.add_field(name="💲 Preço Unitário", value=f"R${produto['preco']:.2f}", inline=True)
                embed.add_field(name="📦 Estoque Disponível", value=produto["estoque"], inline=True)
                
                if produto.get("imagem_url"):
                    embed.set_thumbnail(url=produto["imagem_url"])
                
                await interaction.response.send_message(embed=embed, view=view_carrinho, ephemeral=True)
            
            produto_select_all.callback = produto_all_callback
            view.add_item(produto_select_all)
            
            # Enviar mensagem principal
            await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Erro ao atualizar painel: {e}")
    
    # ========== COMANDOS DE PRODUTO ==========
    @app_commands.command(name="produto_add", description="Adiciona um novo produto na loja")
    async def produto_add(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        modal = ProdutoModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if not hasattr(modal, 'preco_valor') or not hasattr(modal, 'estoque_valor'):
            return
        
        # Selecionar categoria
        categorias = list(categorias_col.find())
        if not categorias:
            await interaction.followup.send("❌ Nenhuma categoria disponível. Crie uma categoria primeiro.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = CategoriaSelect(categorias, "Selecione uma categoria para o produto")
        
        async def callback(inter: discord.Interaction):
            categoria_id = inter.data["values"][0]
            categoria_obj = next((c for c in categorias if str(c["_id"]) == categoria_id), None)
            
            if not categoria_obj:
                await inter.response.send_message("❌ Categoria não encontrada.", ephemeral=True)
                return
            
            produto = {
                "nome": modal.nome_valor,
                "preco": modal.preco_valor,
                "estoque": modal.estoque_valor,
                "descricao": modal.descricao_valor,
                "imagem_url": modal.imagem_url_valor,
                "categoria_id": categoria_id,
                "categoria": categoria_obj["nome"],
                "data_criacao": datetime.now()
            }
            
            result = produtos_col.insert_one(produto)
            
            await inter.response.send_message(
                f"✅ Produto **{produto['nome']}** adicionado com sucesso!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.followup.send("Selecione uma categoria:", view=view, ephemeral=True)
    
    @app_commands.command(name="produto_edit", description="Edita um produto existente")
    async def produto_edit(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        produtos = list(produtos_col.find())
        if not produtos:
            await interaction.response.send_message("❌ Nenhum produto cadastrado.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = ProdutoSelect(produtos, "Selecione um produto para editar")
        
        async def callback(inter: discord.Interaction):
            produto_id = inter.data["values"][0]
            produto = next((p for p in produtos if str(p["_id"]) == produto_id), None)
            
            if not produto:
                await inter.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                return
            
            modal = ProdutoModal(produto)
            await inter.response.send_modal(modal)
            await modal.wait()
            
            if not hasattr(modal, 'preco_valor') or not hasattr(modal, 'estoque_valor'):
                return
            
            update_data = {
                "nome": modal.nome_valor,
                "preco": modal.preco_valor,
                "estoque": modal.estoque_valor,
                "descricao": modal.descricao_valor,
                "imagem_url": modal.imagem_url_valor,
                "data_atualizacao": datetime.now()
            }
            
            produtos_col.update_one({"_id": produto["_id"]}, {"$set": update_data})
            
            await inter.followup.send(
                f"✅ Produto **{update_data['nome']}** atualizado com sucesso!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.response.send_message("Selecione um produto:", view=view, ephemeral=True)
    
    @app_commands.command(name="produto_del", description="Remove um produto da loja")
    async def produto_del(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        produtos = list(produtos_col.find())
        if not produtos:
            await interaction.response.send_message("❌ Nenhum produto cadastrado.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = ProdutoSelect(produtos, "Selecione um produto para remover")
        
        async def callback(inter: discord.Interaction):
            produto_id = inter.data["values"][0]
            produto = next((p for p in produtos if str(p["_id"]) == produto_id), None)
            
            if not produto:
                await inter.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                return
            
            produtos_col.delete_one({"_id": produto["_id"]})
            
            await inter.response.send_message(
                f"✅ Produto **{produto['nome']}** removido com sucesso!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.response.send_message("Selecione um produto:", view=view, ephemeral=True)
    
    # ========== COMANDOS DE CATEGORIA ==========
    @app_commands.command(name="categoria_add", description="Adiciona uma nova categoria")
    async def categoria_add(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        modal = CategoriaModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if not hasattr(modal, 'nome_valor'):
            return
        
        categoria = {
            "nome": modal.nome_valor,
            "emoji": modal.emoji_valor,
            "data_criacao": datetime.now()
        }
        
        categorias_col.insert_one(categoria)
        
        await interaction.followup.send(
            f"✅ Categoria **{categoria['nome']}** adicionada com sucesso!",
            ephemeral=True
        )
        
        # Atualizar painel
        await self.atualizar_painel()
    
    @app_commands.command(name="categoria_edit", description="Edita uma categoria existente")
    async def categoria_edit(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        categorias = list(categorias_col.find())
        if not categorias:
            await interaction.response.send_message("❌ Nenhuma categoria cadastrada.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = CategoriaSelect(categorias, "Selecione uma categoria para editar")
        
        async def callback(inter: discord.Interaction):
            categoria_id = inter.data["values"][0]
            categoria = next((c for c in categorias if str(c["_id"]) == categoria_id), None)
            
            if not categoria:
                await inter.response.send_message("❌ Categoria não encontrada.", ephemeral=True)
                return
            
            modal = CategoriaModal(categoria)
            await inter.response.send_modal(modal)
            await modal.wait()
            
            if not hasattr(modal, 'nome_valor'):
                return
            
            update_data = {
                "nome": modal.nome_valor,
                "emoji": modal.emoji_valor,
                "data_atualizacao": datetime.now()
            }
            
            categorias_col.update_one({"_id": categoria["_id"]}, {"$set": update_data})
            
            # Atualizar produtos desta categoria
            produtos_col.update_many(
                {"categoria_id": str(categoria["_id"])},
                {"$set": {"categoria": update_data["nome"]}}
            )
            
            await inter.followup.send(
                f"✅ Categoria atualizada para **{update_data['nome']}**!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.response.send_message("Selecione uma categoria:", view=view, ephemeral=True)
    
    @app_commands.command(name="categoria_del", description="Remove uma categoria")
    async def categoria_del(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        categorias = list(categorias_col.find())
        if not categorias:
            await interaction.response.send_message("❌ Nenhuma categoria cadastrada.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = CategoriaSelect(categorias, "Selecione uma categoria para remover")
        
        async def callback(inter: discord.Interaction):
            categoria_id = inter.data["values"][0]
            categoria = next((c for c in categorias if str(c["_id"]) == categoria_id), None)
            
            if not categoria:
                await inter.response.send_message("❌ Categoria não encontrada.", ephemeral=True)
                return
            
            # Verificar se há produtos nesta categoria
            produtos_count = produtos_col.count_documents({"categoria_id": str(categoria["_id"])})
            
            if produtos_count > 0:
                await inter.response.send_message(
                    f"❌ Não é possível remover esta categoria pois existem {produtos_count} produtos vinculados a ela.",
                    ephemeral=True
                )
                return
            
            categorias_col.delete_one({"_id": categoria["_id"]})
            
            await inter.response.send_message(
                f"✅ Categoria **{categoria['nome']}** removida com sucesso!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.response.send_message("Selecione uma categoria:", view=view, ephemeral=True)
    
    # ========== COMANDOS DE ESTOQUE ==========
    @app_commands.command(name="estoque_add", description="Adiciona estoque a um produto")
    async def estoque_add(self, interaction: discord.Interaction, quantidade: int):
        if not await self.verificar_admin(interaction):
            return
        
        produtos = list(produtos_col.find())
        if not produtos:
            await interaction.response.send_message("❌ Nenhum produto cadastrado.", ephemeral=True)
            return
        
        view = View(timeout=None)
        select = ProdutoSelect(produtos, "Selecione um produto para adicionar estoque")
        
        async def callback(inter: discord.Interaction):
            produto_id = inter.data["values"][0]
            produto = next((p for p in produtos if str(p["_id"]) == produto_id), None)
            
            if not produto:
                await inter.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                return
            
            produtos_col.update_one(
                {"_id": produto["_id"]},
                {"$inc": {"estoque": quantidade}}
            )
            
            await inter.response.send_message(
                f"✅ Adicionado {quantidade} unidades ao estoque de **{produto['nome']}**!",
                ephemeral=True
            )
            
            # Atualizar painel
            await self.atualizar_painel()
        
        select.callback = callback
        view.add_item(select)
        
        await interaction.response.send_message("Selecione um produto:", view=view, ephemeral=True)
    
    # ========== COMANDOS DE PEDIDOS ==========
    @app_commands.command(name="pedidos", description="Lista todos os pedidos")
    async def pedidos(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        pedidos = list(pedidos_col.find().sort("data", -1).limit(20))
        
        if not pedidos:
            await interaction.response.send_message("❌ Nenhum pedido encontrado.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📋 Últimos Pedidos",
            color=discord.Color.blue()
        )
        
        for pedido in pedidos:
            status_emoji = "🟢" if pedido["status"] == "Aprovado" else "🟡" if pedido["status"] == "Aguardando Pagamento" else "🔴"
            embed.add_field(
                name=f"{status_emoji} Pedido #{pedido.get('ticket_id', 'N/A')}",
                value=f"👤 <@{pedido['user_id']}>\n🛒 {pedido['produto']} x{pedido['quantidade']}\n💰 R${pedido['total']:.2f}\n📅 {pedido['data'].strftime('%d/%m/%Y %H:%M')}\n🔰 {pedido['status']}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="pedido_info", description="Mostra informações detalhadas de um pedido")
    async def pedido_info(self, interaction: discord.Interaction, ticket_id: str):
        if not await self.verificar_admin(interaction):
            return
        
        pedido = pedidos_col.find_one({"ticket_id": ticket_id})
        
        if not pedido:
            await interaction.response.send_message("❌ Pedido não encontrado.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📋 Pedido #{ticket_id}",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="👤 Usuário", value=f"<@{pedido['user_id']}> (`{pedido['user_id']}`)", inline=True)
        embed.add_field(name="🛒 Produto", value=pedido["produto"], inline=True)
        embed.add_field(name="🔢 Quantidade", value=pedido["quantidade"], inline=True)
        embed.add_field(name="💰 Preço Unitário", value=f"R${pedido['preco_unitario']:.2f}", inline=True)
        embed.add_field(name="💲 Total", value=f"R${pedido['total']:.2f}", inline=True)
        embed.add_field(name="🔰 Status", value=pedido["status"], inline=True)
        embed.add_field(name="📅 Data", value=pedido["data"].strftime('%d/%m/%Y %H:%M'), inline=True)
        embed.add_field(name="💳 Método", value=pedido.get("metodo", "N/A"), inline=True)
        
        if "aprovado_por" in pedido:
            embed.add_field(name="✅ Aprovado por", value=f"<@{pedido['aprovado_por']}>", inline=True)
        
        if "motivo_rejeicao" in pedido:
            embed.add_field(name="❌ Motivo da Rejeição", value=pedido["motivo_rejeicao"], inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # ========== COMANDOS DE TICKETS ==========
    @app_commands.command(name="tickets", description="Lista todos os tickets abertos")
    async def tickets(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        tickets = list(tickets_col.find({"status": "aberto"}))
        
        if not tickets:
            await interaction.response.send_message("✅ Nenhum ticket aberto no momento.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎫 Tickets Abertos",
            color=discord.Color.orange()
        )
        
        for ticket in tickets:
            embed.add_field(
                name=f"#{ticket['ticket_id']} - {ticket['produto_nome']}",
                value=f"👤 <@{ticket['user_id']}>\n🔢 {ticket['quantidade']}x\n💰 R${ticket['total']:.2f}\n📅 {ticket['data_criacao'].strftime('%d/%m/%Y %H:%M')}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # ========== COMANDOS DE RELATÓRIOS ==========
    @app_commands.command(name="vendas", description="Relatório de vendas")
    async def vendas(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        # Vendas totais
        vendas_totais = pedidos_col.count_documents({"status": "Aprovado"})
        total_arrecadado = 0
        for pedido in pedidos_col.find({"status": "Aprovado"}):
            total_arrecadado += pedido["total"]
        
        # Produtos mais vendidos
        pipeline = [
            {"$match": {"status": "Aprovado"}},
            {"$group": {"_id": "$produto", "total_vendido": {"$sum": "$quantidade"}, "total_receita": {"$sum": "$total"}}},
            {"$sort": {"total_vendido": -1}},
            {"$limit": 5}
        ]
        produtos_mais_vendidos = list(pedidos_col.aggregate(pipeline))
        
        embed = discord.Embed(
            title="📊 Relatório de Vendas",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="💰 Total Arrecadado",
            value=f"R${total_arrecadado:.2f}",
            inline=True
        )
        
        embed.add_field(
            name="🛒 Vendas Aprovadas",
            value=str(vendas_totais),
            inline=True
        )
        
        if produtos_mais_vendidos:
            produtos_text = ""
            for i, produto in enumerate(produtos_mais_vendidos, 1):
                produtos_text += f"{i}. {produto['_id']} - {produto['total_vendido']} unid. (R${produto['total_receita']:.2f})\n"
            
            embed.add_field(
                name="🏆 Produtos Mais Vendidos",
                value=produtos_text,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # ========== COMANDOS DE UTILIDADE ==========

    @app_commands.command(name="estoque", description="Verifica o estoque atual")
    async def estoque(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        produtos = list(produtos_col.find().sort("nome", 1))
        if not produtos:
            await interaction.response.send_message("❌ Nenhum produto cadastrado.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📊 Estoque da Loja",
            description="Estoque atual de todos os produtos:",
            color=discord.Color.blue()
        )
        
        for produto in produtos:
            status = "🟢" if produto["estoque"] > 0 else "🔴"
            embed.add_field(
                name=f"{status} {produto['nome']}",
                value=f"Estoque: {produto['estoque']} | Preço: R${produto['preco']:.2f}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="loja_atualizar", description="Atualiza o painel da loja manualmente")
    async def loja_atualizar(self, interaction: discord.Interaction):
        if not await self.verificar_admin(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        await self.atualizar_painel()
        await interaction.followup.send("✅ Painel da loja atualizado com sucesso!", ephemeral=True)
    
    @app_commands.command(name="ping", description="Verifica a latência do bot")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! Latência: {latency}ms", ephemeral=True)

# ========== SETUP ==========
async def setup(bot: commands.Bot):
    await bot.add_cog(LojaCog(bot))