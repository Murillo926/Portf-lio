import os  # Permite interagir com o sistema operacional, como acessar arquivos e diretórios
import discord  # Biblioteca principal para criar e gerenciar bots no Discord
from discord.ext import commands  # Módulo que facilita o uso de comandos no bot
# Biblioteca para carregar variáveis de ambiente de um arquivo .env
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do arquivo .env (como o token do bot)
load_dotenv()

# Ativa todas as "intents" (permissões especiais) para que o bot possa interagir com eventos no Discord
intents = discord.Intents.all()

# Inicializa o bot com um prefixo para comandos "-" e define as intents necessárias
# O argumento `help_command=None` desativa o comando de ajuda padrão para permitir personalização
bot = commands.Bot(command_prefix="-", intents=intents, help_command=None)

# Função assíncrona para carregar todos os "cogs" (módulos adicionais do bot)   


async def load_cogs():
    # Percorre recursivamente a pasta "cogs" e suas subpastas
    for root, dirs, files in os.walk("cogs"):
        for file in files:
            if file.endswith(".py"):  # Verifica se o arquivo é um script Python
                # Constrói o caminho completo do arquivo
                cog_path = os.path.join(root, file)
                # Converte o caminho do arquivo para o formato de módulo do Python
                cog_module = os.path.splitext(
                    cog_path)[0].replace(os.path.sep, ".")
                try:
                    # Carrega o módulo como uma extensão (cog) no bot
                    await bot.load_extension(cog_module)
                    print(f"Cog carregada com sucesso: {cog_module}")
                except Exception as e:
                    # Exibe uma mensagem de erro caso a cog falhe ao carregar
                    print(f":X: Erro ao carregar cog {cog_module}: {e}")



# Evento chamado quando o bot é iniciado com sucesso
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")  # Exibe o nome do bot no console
    # Exibe o número de servidores nos quais o bot está
    print(f"Servidores: {len(bot.guilds)}")
    print(f"Carregando Cogs...")
    await load_cogs()  # Carrega os módulos adicionais do bot
    print(f"As Cogs foram carregadas!")

    try:
        # Sincroniza os comandos do bot na árvore de comandos do Discord
        synced = await bot.tree.sync()
        print(f"Comandos sincronizados: {len(synced)} comandos carregados.")
    except Exception as e:
        # Exibe erro caso a sincronização falhe
        print(f"Erro ao sincronizar comandos: {e}")

# Obtém o token do bot a partir das variáveis de ambiente e inicia o bot
if __name__ == '__main__':
    bot.run(os.getenv("DISCORD_TOKEN"))
