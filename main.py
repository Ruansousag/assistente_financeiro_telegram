import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
import calendar
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from io import BytesIO, StringIO
import random
import locale
import threading
import os
from flask import Flask, render_template_string, jsonify
import pytz
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

import os
import psycopg

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

# --- CONEXÃO COM O BANCO DE DADOS ---
def get_connection():
    """Função para obter uma nova conexão com o banco de dados"""
    try:
        # Construir a URL de conexão completa usando as variáveis de ambiente do Railway
        database_url = os.getenv("DATABASE_URL")
        
        if database_url:
            # Se DATABASE_URL está disponível, use ela diretamente
            conn = psycopg.connect(database_url)
        else:
            # Caso contrário, construa a conexão usando variáveis individuais
            conn = psycopg.connect(
                host=os.getenv("PGHOST"),
                port=os.getenv("PGPORT", "5432"),
                user=os.getenv("PGUSER"),
                password=os.getenv("PGPASSWORD"),
                dbname=os.getenv("PGDATABASE"),
                sslmode="require"  # Importante para Railway
            )
        
        logging.info("Conexão com banco de dados estabelecida com sucesso")
        return conn
        
    except Exception as e:
        logging.error(f"Erro ao conectar com o banco de dados: {e}")
        raise e

# Variável global para a conexão
conn = None

def init_database():
    """Inicializa a conexão com o banco de dados"""
    global conn
    conn = get_connection()

# --- FUNÇÕES DE BANCO DE DADOS ---
def execute_with_retry(query, params=None, fetch=False):
    """Executa uma query com retry automático em caso de conexão perdida"""
    global conn
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            if conn.closed:
                conn = get_connection()
                
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetch:
                    return cur.fetchall()
                else:
                    result = None
                    if query.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                        if 'RETURNING' in query.upper():
                            result = cur.fetchone()
                        else:
                            result = cur.rowcount
                    conn.commit()
                    return result
                    
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            logging.warning(f"Erro de conexão (tentativa {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                try:
                    conn.close()
                except:
                    pass
                conn = get_connection()
            else:
                raise e
        except Exception as e:
            logging.error(f"Erro na execução da query: {e}")
            raise e

def setup_database():
    """Configura as tabelas do banco de dados"""
    queries = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id VARCHAR(255) UNIQUE NOT NULL,
            first_name VARCHAR(255)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS transacoes (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) REFERENCES users(telegram_id),
            tipo TEXT,
            categoria TEXT,
            valor DECIMAL(10, 2),
            descricao TEXT,
            data DATE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS orcamentos (
            id SERIAL PRIMARY KEY,
            categoria TEXT NOT NULL,
            valor_limite DECIMAL(10, 2) NOT NULL,
            mes INTEGER NOT NULL,
            ano INTEGER NOT NULL,
            UNIQUE(categoria, mes, ano)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS categorias (
            id SERIAL PRIMARY KEY,
            nome TEXT UNIQUE,
            tipo TEXT,
            icone TEXT
        );
        """
    ]
    
    for query in queries:
        execute_with_retry(query)
    
    # Inserir categorias padrão se não existirem
    count_result = execute_with_retry("SELECT COUNT(*) FROM categorias", fetch=True)
    if count_result[0][0] == 0:
        categorias_default = [
            ('Salário', 'receita', '💰'),
            ('Freelance', 'receita', '💻'),
            ('Investimentos', 'receita', '📈'),
            ('Mercado', 'despesa', '🛒'),
            ('Saúde', 'despesa', '🏥'),
            ('Apto', 'despesa', '🏠'),
            ('Aluguel', 'despesa', '🏘️'),
            ('Lazer', 'despesa', '🎉'),
            ('Cartão NUBANK', 'despesa', '💳'),
            ('Cartão BRB', 'despesa', '💳'),
            ('Cartão CAIXA', 'despesa', '💳'),
            ('Cartão CVC', 'despesa', '💳'),
            ('Transporte', 'despesa', '🚗'),
            ('Educação', 'despesa', '📚'),
            ('Diversos', 'ambos', '📦'),
            
            # NOVAS SUBCATEGORIAS DE CARTÃO
            ('Cartão - Farmácia', 'despesa', '💊'),
            ('Cartão - Gasolina', 'despesa', '⛽'),
            ('Cartão - Lanche', 'despesa', '🍔'),
            ('Cartão - Laser', 'despesa', '🎉'),
            ('Cartão - Mercado', 'despesa', '🛒'),
            ('Cartão - Padaria', 'despesa', '🥐'),
            ('Cartão - Passagem', 'despesa', '🚌'),
            ('Cartão - Shopee', 'despesa', '📦'),
            ('Cartão - Streaming', 'despesa', '📺'),
            ('Cartão - Uber', 'despesa', '🚕'),
        ]
        
        for categoria in categorias_default:
            execute_with_retry(
                'INSERT INTO categorias (nome, tipo, icone) VALUES (%s, %s, %s)',
                categoria
            )

def zerar_dados():
    execute_with_retry("DELETE FROM transacoes")
    execute_with_retry("DELETE FROM orcamentos")

def get_categorias(tipo=None):
    if tipo:
        query = "SELECT nome, icone FROM categorias WHERE tipo = %s OR tipo = 'ambos' ORDER BY nome"
        return execute_with_retry(query, (tipo,), fetch=True)
    else:
        query = "SELECT nome, icone FROM categorias ORDER BY nome"
        return execute_with_retry(query, fetch=True)

def add_transacao(user_id, tipo, categoria, valor, descricao, data):
    query = """
        INSERT INTO transacoes (user_id, tipo, categoria, valor, descricao, data) 
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """
    result = execute_with_retry(query, (user_id, tipo, categoria, float(valor), descricao, data))
    return result[0] if result else None

def get_orcamento_status(categoria, mes, ano):
    # Buscar orçamento
    orcamento_result = execute_with_retry(
        'SELECT valor_limite FROM orcamentos WHERE categoria = %s AND mes = %s AND ano = %s',
        (categoria, mes, ano), fetch=True
    )
    
    if not orcamento_result:
        return None, 0, 0, 0
    
    limite = orcamento_result[0][0]
    
    # Buscar gastos
    gasto_result = execute_with_retry(
        "SELECT COALESCE(SUM(valor), 0) FROM transacoes WHERE categoria = %s AND tipo = 'despesa' AND EXTRACT(YEAR FROM data) = %s AND EXTRACT(MONTH FROM data) = %s",
        (categoria, ano, mes), fetch=True
    )
    
    gasto_atual = gasto_result[0][0] if gasto_result else 0
    disponivel = limite - gasto_atual
    percentual_usado = (gasto_atual / limite) * 100 if limite > 0 else 0
    
    return limite, gasto_atual, disponivel, percentual_usado

def set_orcamento(categoria, valor_limite, mes, ano):
    query = """
        INSERT INTO orcamentos (categoria, valor_limite, mes, ano) 
        VALUES (%s, %s, %s, %s) 
        ON CONFLICT(categoria, mes, ano) 
        DO UPDATE SET valor_limite = excluded.valor_limite
    """
    execute_with_retry(query, (categoria, float(valor_limite), mes, ano))

def get_todos_orcamentos(mes, ano):
    query = "SELECT categoria, valor_limite FROM orcamentos WHERE mes = %s AND ano = %s ORDER BY categoria"
    return execute_with_retry(query, (mes, ano), fetch=True)

def get_transacoes_por_categoria(categoria, mes, ano):
    query = """
        SELECT data, descricao, valor FROM transacoes 
        WHERE categoria = %s AND tipo = 'despesa' 
        AND EXTRACT(YEAR FROM data) = %s AND EXTRACT(MONTH FROM data) = %s 
        ORDER BY data
    """
    return execute_with_retry(query, (categoria, ano, mes), fetch=True)

def gerar_relatorio_mensal(mes, ano, detalhado=False):
    global conn
    try:
        if conn.closed:
            conn = get_connection()
            
        if detalhado:
            query = """
                SELECT data, categoria, descricao, tipo, valor, user_id 
                FROM transacoes 
                WHERE EXTRACT(YEAR FROM data) = %s AND EXTRACT(MONTH FROM data) = %s 
                ORDER BY data
            """
        else:
            query = """
                SELECT categoria, tipo, SUM(valor) as total 
                FROM transacoes 
                WHERE EXTRACT(YEAR FROM data) = %s AND EXTRACT(MONTH FROM data) = %s 
                GROUP BY categoria, tipo
            """
        
        df = pd.read_sql_query(query, conn, params=[ano, mes])
        return df
        
    except Exception as e:
        logging.error(f"Erro ao gerar relatório: {e}")
        return pd.DataFrame()

def get_ultimos_lancamentos(limit=7):
    query = """
        SELECT id, data, tipo, categoria, descricao, valor, user_id 
        FROM transacoes 
        ORDER BY id DESC LIMIT %s
    """
    return execute_with_retry(query, (limit,), fetch=True)

def get_transacao(tx_id):
    query = "SELECT * FROM transacoes WHERE id = %s"
    result = execute_with_retry(query, (tx_id,), fetch=True)
    return result[0] if result else None

# Função centralizada para atualizar qualquer campo da transação
def update_transacao_campo(tx_id, campo, novo_valor):
    try:
        # Garante que apenas campos válidos possam ser atualizados
        if campo not in ['valor', 'categoria', 'descricao']:
            return False
            
        # O valor é convertido para float apenas se o campo for 'valor'
        valor_ajustado = float(novo_valor) if campo == 'valor' else novo_valor
        
        query = f"UPDATE transacoes SET {campo} = %s WHERE id = %s"
        params = (valor_ajustado, tx_id)

        result = execute_with_retry(query, params)
        return result > 0
    except Exception as e:
        logging.error(f"Erro ao atualizar transação {tx_id} no campo {campo}: {e}")
        return False
        
def update_transacao_valor(tx_id, novo_valor):
    """Atualiza o valor de uma transação usando a função centralizada."""
    return update_transacao_campo(tx_id, 'valor', novo_valor)

def add_user(user_id, first_name):
    """Adiciona um usuário ao banco de dados"""
    query = """
        INSERT INTO users (telegram_id, first_name) 
        VALUES (%s, %s) 
        ON CONFLICT (telegram_id) DO NOTHING
    """
    execute_with_retry(query, (user_id, first_name))

# --- FUNÇÕES AUXILIARES ---
# Dicionário de tradução dos meses
meses = {
    'January': 'Janeiro',
    'February': 'Fevereiro',
    'March': 'Março',
    'April': 'Abril',
    'May': 'Maio',
    'June': 'Junho',
    'July': 'Julho',
    'August': 'Agosto',
    'September': 'Setembro',
    'October': 'Outubro',
    'November': 'Novembro',
    'December': 'Dezembro'
}

# CONFIGURAÇÕES DO BOT
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_USERS = [id.strip() for id in os.getenv("AUTHORIZED_USERS", "").split(',')]

# Define o fuso horário do Brasil
BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

# Função para obter a data e hora atual no fuso horário do Brasil
def get_brazil_now():
    return datetime.now(BRAZIL_TZ)

# --- SERVIDOR WEB FLASK ---
app = Flask(__name__)

@app.route('/')
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Financeiro - Status</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }
            .container {
                max-width: 600px;
                margin: 0 auto;
                background: rgba(255,255,255,0.1);
                padding: 30px;
                border-radius: 15px;
                backdrop-filter: blur(10px);
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            }
            .status {
                text-align: center;
                padding: 20px;
                background: rgba(0, 255, 0, 0.2);
                border-radius: 10px;
                margin: 20px 0;
            }
            .info {
                background: rgba(255,255,255,0.1);
                padding: 15px;
                border-radius: 8px;
                margin: 10px 0;
            }
            h1 { text-align: center; margin-bottom: 30px; }
            .emoji { font-size: 2em; margin: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Bot Assistente Financeiro</h1>
            <div class="status">
                <div class="emoji">✅</div>
                <h2>Bot Online e Funcionando!</h2>
                <p>Status: <strong>Ativo</strong></p>
                <p>Última verificação: <span id="timestamp"></span></p>
            </div>
            <div class="info">
                <h3>📊 Funcionalidades</h3>
                <ul>
                    <li>💸 Registro de despesas</li>
                    <li>💰 Registro de receitas</li>
                    <li>🎯 Controle de orçamentos</li>
                    <li>📈 Relatórios detalhados</li>
                    <li>📋 Extratos mensais</li>
                </ul>
            </div>
            <div class="info">
                <h3>🔧 Como usar</h3>
                <p>Envie <code>/start</code> no Telegram para começar a usar o bot!</p>
            </div>
        </div>
        <script>
            function updateTimestamp() {
                document.getElementById('timestamp').textContent = new Date().toLocaleString('pt-BR');
            }
            updateTimestamp();
            setInterval(updateTimestamp, 1000);
        </script>
    </body>
    </html>
    ''')

@app.route('/status')
def status():
    return jsonify({
        'status': 'online',
        'bot': 'financial_assistant',
        'timestamp': datetime.now().isoformat(),
        'version': '13.4'
    })

@app.route('/health')
def health():
    try:
        # Teste simples de conexão com o banco
        execute_with_retry("SELECT 1", fetch=True)
        db_status = "healthy"
    except:
        db_status = "error"
    
    return jsonify({
        'status': 'healthy',
        'database': db_status
    })

# --- FUNÇÕES AUXILIARES ---
def format_brl(value):
    if not isinstance(value, (int, float, Decimal)):
        return "R$ 0,00"
    try:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"R$ {value:.2f}".replace('.', ',')

def format_date_br(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
    except (ValueError, TypeError):
        return date_str

def get_previous_month(year, month):
    current_date = datetime(year, month, 1)
    prev_date = current_date - relativedelta(months=1)
    return prev_date.year, prev_date.month

def calc_percent_change(current, previous):
    if previous == 0:
        return " (Novo)" if current > 0 else ""
    change = ((current - previous) / previous) * 100
    return f" ({'+' if change >= 0 else ''}{change:.1f}%)"

def get_alerta_divertido(categoria, percentual_usado):
    alertas = {
        50: [f"🤔 Metade do orçamento de *{categoria}* já foi..."],
        80: [f"🚨 Cuidado! 80% do orçamento de *{categoria}* foi utilizado!"],
        100: [f"🆘 Orçamento de *{categoria}* estourado!"]
    }
    nivel = next((n for n in [100, 80, 50] if percentual_usado >= n), 0)
    return random.choice(alertas[nivel]) if nivel else None

def criar_relatorio_visual(df, mes, ano):
    if df.empty:
        return None
    
    receitas = df[df['tipo'] == 'receita']['total'].sum()
    despesas = df[df['tipo'] == 'despesa']['total'].sum()
    saldo = receitas - despesas
    nome_mes_ano = f"{meses[calendar.month_name[mes]].capitalize()}/{ano}"

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'Relatório Financeiro - {nome_mes_ano}', fontsize=20, weight='bold')

    # Gráfico de despesas
    despesas_cat = df[df['tipo'] == 'despesa']
    if not despesas_cat.empty:
        axes[0, 0].pie(despesas_cat['total'], labels=despesas_cat['categoria'], 
                       autopct='%1.1f%%', startangle=140, 
                       colors=sns.color_palette("Reds_r", len(despesas_cat)))
        axes[0, 0].set_title('Composição das Despesas', fontsize=14)
    else:
        axes[0, 0].text(0.5, 0.5, 'Sem despesas', ha='center', va='center', fontsize=14)
        axes[0, 0].set_title('Composição das Despesas', fontsize=14)

    # Gráfico de receitas
    receitas_cat = df[df['tipo'] == 'receita']
    if not receitas_cat.empty:
        axes[0, 1].pie(receitas_cat['total'], labels=receitas_cat['categoria'], 
                       autopct='%1.1f%%', startangle=140, 
                       colors=sns.color_palette("Greens_r", len(receitas_cat)))
        axes[0, 1].set_title('Composição das Receitas', fontsize=14)
    else:
        axes[0, 1].text(0.5, 0.5, 'Sem receitas', ha='center', va='center', fontsize=14)
        axes[0, 1].set_title('Composição das Receitas', fontsize=14)

    # Resumo financeiro
    cores = ['green', 'red', 'blue' if saldo >= 0 else 'orange']
    sns.barplot(x=['Receitas', 'Despesas', 'Saldo'], y=[receitas, despesas, saldo], 
                ax=axes[1, 0], palette=cores)
    axes[1, 0].set_title('Resumo Financeiro do Mês', fontsize=14)
    axes[1, 0].set_ylabel('Valor (R$)')
    
    for p in axes[1, 0].patches:
        axes[1, 0].annotate(format_brl(p.get_height()),
                            (p.get_x() + p.get_width() / 2., p.get_height()),
                            ha='center', va='center', xytext=(0, 9),
                            textcoords='offset points')

    # Top 5 despesas
    top_despesas = despesas_cat.sort_values('total', ascending=False).head(5)
    if not top_despesas.empty:
        sns.barplot(x='total', y='categoria', data=top_despesas, 
                    ax=axes[1, 1], palette='Reds_r', orient='h')
        axes[1, 1].set_title('Top 5 Despesas', fontsize=14)
        axes[1, 1].set_xlabel('Valor (R$)')
        axes[1, 1].set_ylabel('')
    else:
        axes[1, 1].axis('off')
        axes[1, 1].text(0.5, 0.5, 'Sem despesas\npara o ranking', 
                        ha='center', va='center', fontsize=12)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=300)
    buffer.seek(0)
    plt.close()
    
    return buffer

def criar_relatorio_detalhado(df, mes, ano):
    if df.empty:
        return None
    
    df_sorted = df.sort_values(by='tipo', ascending=False)
    nome_mes_ano = f"{meses[calendar.month_name[mes]].capitalize()}/{ano}"
    buffer = StringIO()
    buffer.write(f"Relatório Detalhado - {nome_mes_ano}\n{'='*50}\n\n")
    
    for _, row in df_sorted.iterrows():
        sinal = '+' if row['tipo'] == 'receita' else '-'
        buffer.write(
            f"Data: {format_date_br(str(row['data']))}\nTipo: {row['tipo'].capitalize()}\nCategoria: {row['categoria']}\n"
        )
        buffer.write(
            f"Descrição: {row['descricao']}\nValor: {sinal}{format_brl(row['valor']).replace('R$ ', '')}\n{'-'*30}\n"
        )
    
    buffer.seek(0)
    return buffer

def criar_relatorio_comparativo(df_atual, df_anterior, mes_atual, ano_atual, mes_anterior, ano_anterior):
    rec_atual = df_atual[df_atual['tipo'] == 'receita']['total'].sum()
    desp_atual = df_atual[df_atual['tipo'] == 'despesa']['total'].sum()
    rec_anterior = df_anterior[df_anterior['tipo'] == 'receita']['total'].sum()
    desp_anterior = df_anterior[df_anterior['tipo'] == 'despesa']['total'].sum()
    
    despesas_atual_cat = df_atual[df_atual['tipo'] == 'despesa'].set_index('categoria')['total']
    despesas_anterior_cat = df_anterior[df_anterior['tipo'] == 'despesa'].set_index('categoria')['total']
    
    df_comp = pd.concat([despesas_atual_cat, despesas_anterior_cat],
                        axis=1, keys=['atual', 'anterior']).fillna(0)
    df_comp['variacao'] = df_comp['atual'] - df_comp['anterior']
    
    fig, ax = plt.subplots(figsize=(12, 8))
    df_comp[['anterior', 'atual']].sort_values(by='atual', ascending=True).plot(
        kind='barh', ax=ax, color=['#ff9999', '#ff4d4d'])
    ax.set_title(
        f"Comparativo de Despesas: {meses[calendar.month_name[mes_anterior]].capitalize()} vs {meses[calendar.month_name[mes_atual]].capitalize()}",
        fontsize=16)
    ax.set_xlabel('Valor (R$)')
    ax.set_ylabel('Categorias')
    ax.legend(['Mês Anterior', 'Mês Atual'])
    
    plt.tight_layout()
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=300)
    buffer.seek(0)
    plt.close()
    
    saldo_atual = rec_atual - desp_atual
    saldo_anterior = rec_anterior - desp_anterior
    
    caption = (
        f"📊 *Comparativo Mensal*\n\n"
        f"*{'─'*10} Resumo Geral {'─'*10}*\n"
        f"💰 Receitas: {format_brl(rec_atual)}{calc_percent_change(rec_atual, rec_anterior)}\n"
        f"💸 Despesas: {format_brl(desp_atual)}{calc_percent_change(desp_atual, desp_anterior)}\n"
        f"*{'💚 Saldo' if saldo_atual >= 0 else '❤️ Saldo'}: {format_brl(saldo_atual)}{calc_percent_change(saldo_atual, saldo_anterior)}*\n\n"
        f"*{'─'*10} Análise das Despesas {'─'*10}*\n")
    
    top_aumentos = df_comp[df_comp['variacao'] > 0].sort_values('variacao', ascending=False).head(3)
    if not top_aumentos.empty:
        caption += "📈 *Principais Aumentos:*\n"
        for cat, row in top_aumentos.iterrows():
            caption += f"  • *{cat}*: +{format_brl(row['variacao'])}{calc_percent_change(row['atual'], row['anterior'])}\n"
    
    return buffer, caption

# --- FUNÇÕES DE MENU E NAVEGAÇÃO ---
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None):
    # Lógica do menu principal (sem alterações)
    # ...
    keyboard = [
        [
            InlineKeyboardButton("💸 Nova Despesa", callback_data="add_despesa"),
            InlineKeyboardButton("💰 Nova Receita", callback_data="add_receita")
        ],
        [
            InlineKeyboardButton("📊 Relatórios", callback_data="relatorios"),
            InlineKeyboardButton("🎯 Orçamentos", callback_data="orcamentos")
        ],
        [
            InlineKeyboardButton("📋 Saldo do Mês", callback_data="saldo"),
            InlineKeyboardButton("📝 Últimos Lançamentos", callback_data="extrato")
        ]
    ]
    
    text = "🏠 *Menu Principal*\n\nO que vamos organizar agora?"
    chat_id = update.effective_chat.id
    
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

# NOVO: Função para gerar o resumo da transação (para edição)
async def send_or_edit_summary(context: ContextTypes.DEFAULT_TYPE, chat_id, tx_id, message_id=None):
    """Gera o resumo de uma transação e envia ou edita a mensagem."""
    tx = get_transacao(tx_id)
    if not tx:
        return

    _id, _user, _tipo, _cat, _valor, _desc, _data, _created = tx
    
    data_obj = _data
    mes_contabilizado = meses[calendar.month_name[data_obj.month]].capitalize()

    feedback = (
        f"{'💸' if _tipo == 'despesa' else '💰'} *Transação Registrada! (EDITADA)*\n\n"
        f"ID da Transação: #{_id}\n"
        f"Categoria: *{_cat}*\n"
        f"Data: *{format_date_br(str(_data))}*\n"
        f"Contabilizado para: *{mes_contabilizado}*\n"
        f"Valor: *{format_brl(_valor)}*\n"
        f"Descrição: _{_desc}_"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Editar Transação", callback_data=f"edit_tx_{_id}")
    ]])

    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=feedback,
                reply_markup=keyboard,
                parse_mode='Markdown')
        except Exception as e:
            # Caso a edição falhe (mensagem muito antiga), envia uma nova
            logging.warning(f"Falha ao editar mensagem {message_id}: {e}. Enviando nova.")
            await context.bot.send_message(
                chat_id=chat_id,
                text=feedback,
                reply_markup=keyboard,
                parse_mode='Markdown')
    else:
        # Se não houver message_id para editar, apenas envia (como na primeira inserção)
        sent_message = await context.bot.send_message(
            chat_id=chat_id, 
            text=feedback, 
            reply_markup=keyboard, 
            parse_mode='Markdown')
        return sent_message.message_id


# --- HANDLERS DE COMANDOS E BOTÕES ---
async def start_command(update, context):
    user_id = str(update.message.from_user.id)
    first_name = update.message.from_user.first_name
    
    # Adiciona o usuário ao banco de dados se ele ainda não existir
    try:
        add_user(user_id, first_name)
    except Exception as e:
        logging.error(f"Erro ao adicionar usuário: {e}")
    
    # Remove a mensagem de start para manter o chat limpo
    await update.message.delete()
    
    # Chama o menu principal
    await show_main_menu(update, context)

async def zerar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    keyboard = [
        [InlineKeyboardButton("🗑️ SIM, APAGAR TUDO!", callback_data="confirmar_zerar")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="menu_principal")]
    ]
    text = (
        "⚠️ *ATENÇÃO!* ⚠️\n\nVocê tem certeza que quer apagar *TODOS* os dados?\n\n"
        "Isso irá remover permanentemente todas as receitas, despesas e orçamentos registrados.\n\n"
        "*ESTA AÇÃO NÃO PODE SER DESFEITA.*")
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown')

async def generic_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para a maioria dos botões, exceto os de seleção de data.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_principal":
        await show_main_menu(update, context, message_id=query.message.message_id)
        return

    if data in ["add_despesa", "add_receita"]:
        tipo = data.split('_')[1]
        context.user_data.clear()
        context.user_data['tipo_transacao'] = tipo
        categorias = get_categorias(tipo)
        keyboard = [[InlineKeyboardButton(f"{icone} {nome}", callback_data=f"cat_{nome}")] 
                   for nome, icone in categorias]
        keyboard.append([InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")])
        await query.edit_message_text(
            f"Selecione a categoria da *{tipo}*:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("cat_"):
        categoria_principal = data[4:]
        tipo_transacao = context.user_data['tipo_transacao']

        # VERIFICAÇÃO PARA SUBCATEGORIAS DE CARTÃO
        if tipo_transacao == 'despesa' and 'CARTÃO' in categoria_principal.upper():
            
            # Filtra apenas as subcategorias que começam com 'Cartão -'
            subcategorias_raw = execute_with_retry(
                "SELECT nome, icone FROM categorias WHERE nome LIKE 'Cartão - %' ORDER BY nome",
                fetch=True
            )
            
            if subcategorias_raw:
                # Armazena a categoria principal (ex: Cartão NUBANK)
                context.user_data['categoria_principal_cartao'] = categoria_principal 

                # Cria o teclado com as subcategorias
                # .split(' - ')[1] remove o prefixo 'Cartão - ' para a exibição
                keyboard = [[InlineKeyboardButton(f"{icone} {nome.split(' - ')[1]}", 
                                                callback_data=f"subcat_{nome}")] 
                            for nome, icone in subcategorias_raw]
                
                # Adiciona um botão para pular a subcategoria e usar a principal
                keyboard.append([InlineKeyboardButton("➡️ Usar Categoria Principal", 
                                                    callback_data=f"subcat_pular_{categoria_principal}")])
                keyboard.append([InlineKeyboardButton("⬅️ Voltar às Categorias", callback_data=f"add_{tipo_transacao}")])
                
                await query.edit_message_text(
                    f"Categoria Principal: *{categoria_principal}*\n\nSelecione o *tipo de gasto* no cartão:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown')
                return # CORREÇÃO CRÍTICA: Retorna aqui para não prosseguir para o passo 'valor_transacao'
        
        # LÓGICA PADRÃO (Se não for cartão ou se pular)
        context.user_data['message_id_to_edit'] = query.message.message_id
        context.user_data['categoria_transacao'] = categoria_principal # Usa a principal se não for um cartão ou se for ignorado
        context.user_data['step'] = 'valor_transacao'
        await query.edit_message_text(
            f"Categoria: *{categoria_principal}*\n\nQual o valor?",
            parse_mode='Markdown')

    # NOVO BLOCO: Processamento da Subcategoria
    elif data.startswith("subcat_"):
        partes = data.split('_')
        
        if partes[1] == 'pular':
            # Usa a categoria principal que já está na URL de callback
            categoria_final = partes[2] 
        else:
            # Usa a subcategoria completa que foi passada no callback
            categoria_final = data[len("subcat_"):]
        
        context.user_data['message_id_to_edit'] = query.message.message_id
        context.user_data['categoria_transacao'] = categoria_final
        context.user_data['step'] = 'valor_transacao'
        
        await query.edit_message_text(
            f"Categoria: *{categoria_final}*\n\nQual o valor?",
            parse_mode='Markdown')

    elif data == "saldo":
        # Lógica do saldo (sem alterações)
        # ...
        hoje = get_brazil_now()
        df = gerar_relatorio_mensal(hoje.month, hoje.year)
        receitas = df[df['tipo'] == 'receita']['total'].sum() if not df.empty else 0
        despesas = df[df['tipo'] == 'despesa']['total'].sum() if not df.empty else 0
        texto = (
            f"💳 *Saldo de {meses[calendar.month_name[hoje.month]].capitalize()}*\n\n"
            f"💰 Receitas: {format_brl(receitas)}\n"
            f"💸 Despesas: {format_brl(despesas)}\n"
            f"*{'💚 Saldo Positivo' if (receitas - despesas) >= 0 else '❤️ Saldo Negativo'}: {format_brl(receitas - despesas)}*"
        )
        keyboard = [[InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")]]
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "extrato":
        # Lógica do extrato (sem alterações)
        # ...
        lancamentos = get_ultimos_lancamentos()
        keyboard = [[InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")]]
        if not lancamentos:
            texto = "Nenhum lançamento encontrado ainda."
        else:
            texto = "📝 *Últimos Lançamentos:*\n\n"
            for tx_id, data_t, tipo, cat, desc, valor, user_id_lanc in lancamentos:
                emoji = "💸" if tipo == 'despesa' else "💰"
                texto += f"{emoji} _{format_date_br(str(data_t))}_ - *{cat}*\n"
                texto += f"   _{desc}_ - *{format_brl(valor)}*\n"

        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "relatorios":
        # Lógica dos relatórios (sem alterações)
        # ...
        keyboard = [
            [InlineKeyboardButton("📊 Mês Atual (Gráfico)", callback_data="rel_grafico")],
            [InlineKeyboardButton("📄 Mês Atual (Detalhado)", callback_data="rel_detalhado")],
            [InlineKeyboardButton("📈 Comparativo Mensal", callback_data="rel_comparativo")],
            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")]
        ]
        await query.edit_message_text(
            "Qual relatório você deseja gerar?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("rel_"):
        # Lógica de geração de relatórios (sem alterações)
        # ...
        tipo_relatorio = data.split('_')[1]
        if tipo_relatorio == 'comparativo':
            await query.edit_message_text("⏳ Gerando relatório comparativo, um momento...")
            hoje = get_brazil_now()
            ano_anterior, mes_anterior = get_previous_month(hoje.year, hoje.month)
            df_atual = gerar_relatorio_mensal(hoje.month, hoje.year)
            df_anterior = gerar_relatorio_mensal(mes_anterior, ano_anterior)
            if df_anterior.empty:
                await query.edit_message_text(
                    "Ainda não há dados do mês anterior para comparar.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Voltar", callback_data="relatorios")
                    ]]))
                return
            buffer, caption = criar_relatorio_comparativo(
                df_atual, df_anterior, hoje.month, hoje.year, mes_anterior, ano_anterior)
            await context.bot.send_photo(chat_id=query.message.chat_id,
                                       photo=buffer, caption=caption, parse_mode='Markdown')
        else:
            detalhado = (tipo_relatorio == 'detalhado')
            hoje = get_brazil_now()
            await query.edit_message_text("⏳ Gerando relatório, um momento...")
            df = gerar_relatorio_mensal(hoje.month, hoje.year, detalhado=detalhado)
            if df.empty:
                await query.edit_message_text(
                    f"Nenhum dado encontrado para {meses[calendar.month_name[hoje.month]].capitalize()}.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Voltar", callback_data="relatorios")
                    ]]))
                return
            if detalhado:
                buffer = criar_relatorio_detalhado(df, hoje.month, hoje.year)
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=buffer,
                    filename=f"relatorio_{hoje.year}_{hoje.month:02d}.txt",
                    caption="Aqui está seu relatório detalhado!")
            else:
                buffer = criar_relatorio_visual(df, hoje.month, hoje.year)
                receitas = df[df['tipo'] == 'receita']['total'].sum()
                despesas = df[df['tipo'] == 'despesa']['total'].sum()
                caption = (
                    f"📊 *Resumo de {meses[calendar.month_name[hoje.month]].capitalize()}*\n\n"
                    f"💰 Receitas Totais: {format_brl(receitas)}\n"
                    f"💸 Despesas Totais: {format_brl(despesas)}\n"
                    f"*{'💚 Saldo' if (receitas - despesas) >= 0 else '❤️ Saldo'}: {format_brl(receitas - despesas)}*\n"
                )
                df_receitas = df[df['tipo'] == 'receita']
                if not df_receitas.empty:
                    caption += "\n------ *Receitas* ------\n"
                    for _, row in df_receitas.sort_values(by='total', ascending=False).iterrows():
                        caption += f"💰 {row['categoria']}: {format_brl(row['total'])}\n"
                df_despesas = df[df['tipo'] == 'despesa']
                if not df_despesas.empty:
                    caption += "\n------ *Despesas* ------\n"
                    for _, row in df_despesas.sort_values(by='total', ascending=False).iterrows():
                        caption += f"💸 {row['categoria']}: {format_brl(row['total'])}\n"
                await context.bot.send_photo(chat_id=query.message.chat_id,
                                           photo=buffer, caption=caption, parse_mode='Markdown')
        await query.delete_message()
        await show_main_menu(update, context)

    elif data == "orcamentos":
        # Lógica dos orçamentos (sem alterações)
        # ...
        keyboard = [
            [InlineKeyboardButton("🎯 Definir/Alterar", callback_data="orc_definir")],
            [InlineKeyboardButton("📋 Ver Orçamentos", callback_data="orc_ver")],
            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="menu_principal")]
        ]
        await query.edit_message_text(
            "Gerenciar orçamentos do *mês atual*:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "confirmar_zerar":
        # Lógica de confirmação de zerar (sem alterações)
        # ...
        zerar_dados()
        await query.edit_message_text("✅ Todos os dados foram apagados com sucesso!")
        await show_main_menu(update, context, message_id=query.message.message_id)

    elif data == "orc_definir":
        # Lógica de definir orçamento (sem alterações)
        # ...
        categorias = get_categorias('despesa')
        keyboard = [[InlineKeyboardButton(f"{icone} {nome}", callback_data=f"orc_cat_{nome}")] 
                   for nome, icone in categorias]
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="orcamentos")])
        await query.edit_message_text(
            "Definir orçamento para qual categoria?",
            reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("orc_cat_"):
        # Lógica de selecionar categoria para orçamento (sem alterações)
        # ...
        context.user_data.clear()
        context.user_data['message_id_to_edit'] = query.message.message_id
        categoria = data[8:]
        context.user_data['categoria_orcamento'] = categoria
        context.user_data['step'] = 'valor_orcamento'
        await query.edit_message_text(
            f"Orçamento para *{categoria}*.\n\nQual o valor limite mensal?",
            parse_mode='Markdown')

    elif data == "orc_ver":
        # Lógica de ver orçamentos (sem alterações)
        # ...
        hoje = get_brazil_now()
        orcamentos = get_todos_orcamentos(hoje.month, hoje.year)
        if not orcamentos:
            await query.edit_message_text(
                "Nenhum orçamento definido para este mês.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎯 Definir um Agora", callback_data="orc_definir")],
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="orcamentos")]
                ]))
            return
        texto = f"📋 *Orçamentos de {meses[calendar.month_name[hoje.month]].capitalize()}*\n\n"
        keyboard = []
        for categoria, limite in orcamentos:
            _, gasto, disponivel, percentual = get_orcamento_status(categoria, hoje.month, hoje.year)
            barra = "▪" * int(percentual / 10) + "▫" * (10 - int(percentual / 10))
            status = "✅" if disponivel >= 0 else "🆘"
            texto += f"*{categoria}* {status}\n`{barra}` {percentual:.1f}%\n"
            texto += f"Gasto: {format_brl(gasto)} de {format_brl(limite)}\n"
            texto += f"Sobra: {format_brl(disponivel)}\n\n"
            keyboard.append([
                InlineKeyboardButton(f"Ver Gastos de {categoria}", 
                                   callback_data=f"orc_gastos_{categoria}")
            ])
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="orcamentos")])
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("orc_gastos_"):
        # Lógica de ver gastos por orçamento (sem alterações)
        # ...
        categoria = data[11:]
        hoje = get_brazil_now()
        transacoes = get_transacoes_por_categoria(categoria, hoje.month, hoje.year)
        texto = f"💸 *Gastos em {categoria}*\n\n"
        if not transacoes:
            texto += "Nenhum gasto este mês."
        else:
            total = 0
            for data_t, desc, valor in transacoes:
                texto += f"_{format_date_br(str(data_t))}_: {desc} - *{format_brl(valor)}*\n"
                total += valor
            texto += f"\n*Total Gasto:* {format_brl(total)}"
        keyboard = [[InlineKeyboardButton("⬅️ Voltar aos Orçamentos", callback_data="orc_ver")]]
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    # NOVO: Inicia o menu de edição da transação
    elif data.startswith("edit_tx_"):
        tx_id = int(data.split("_")[-1])
        tx = get_transacao(tx_id)
        if not tx:
            await query.edit_message_text("Transação não encontrada. 😕",
                                         reply_markup=InlineKeyboardMarkup([[
                                             InlineKeyboardButton("⬅️ Voltar", callback_data="extrato")
                                         ]]))
            return

        _id, _user, _tipo, _cat, _valor, _desc, _data, _created = tx
        
        # Armazena os dados da transação E O ID DA MENSAGEM para o processo de edição
        context.user_data.clear()
        context.user_data['edit_tx_id'] = _id
        context.user_data['edit_tx_tipo'] = _tipo
        context.user_data['message_id_to_edit'] = query.message.message_id # SALVANDO O ID DA MENSAGEM
        
        # Mensagem de resumo para edição
        texto_resumo = (
            f"✏️ *Editar Transação #{_id}*\n\n"
            f"Tipo: *{_tipo.capitalize()}*\n"
            f"Categoria: *{_cat}*\n"
            f"Descrição: _{_desc}_\n"
            f"Valor: *{format_brl(_valor)}*\n"
            f"Data: *{format_date_br(str(_data))}*\n\n"
            f"O que você deseja editar?"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Mudar Valor", callback_data=f"edit_campo_valor_{_id}")],
            [InlineKeyboardButton("🏷️ Mudar Categoria", callback_data=f"edit_campo_categoria_{_id}")],
            [InlineKeyboardButton("📝 Mudar Descrição", callback_data=f"edit_campo_descricao_{_id}")],
            [InlineKeyboardButton("❌ Cancelar Edição", callback_data="extrato")]
        ])

        await query.edit_message_text(
            text=texto_resumo,
            reply_markup=keyboard,
            parse_mode='Markdown')

    # NOVO: Processa o clique para escolher o campo a ser editado (Valor, Categoria, Descrição)
    elif data.startswith("edit_campo_"):
        partes = data.split('_')
        campo = partes[2]
        tx_id = int(partes[3])
        
        # O message_id_to_edit JÁ ESTÁ EM context.user_data, então não limpamos aqui
        message_id_to_edit = context.user_data.get('message_id_to_edit', query.message.message_id)
        context.user_data.clear()
        context.user_data['edit_tx_id'] = tx_id
        context.user_data['message_id_to_edit'] = message_id_to_edit # MANTÉM O ID DA MENSAGEM
        
        if campo == 'valor':
            context.user_data['step'] = 'editar_valor_transacao'
            await query.edit_message_text(
                text="👉 Envie o *novo valor* (ex: 150,50):",
                parse_mode='Markdown')

        elif campo == 'descricao':
            context.user_data['step'] = 'editar_descricao_transacao'
            await query.edit_message_text(
                text="👉 Envie a *nova descrição*:",
                parse_mode='Markdown')

        elif campo == 'categoria':
            context.user_data['step'] = 'editar_categoria_transacao'
            tx = get_transacao(tx_id)
            _id, _user, tipo_tx, _cat, _valor, _desc, _data, _created = tx
            
            categorias = get_categorias(tipo_tx)
            keyboard = [[InlineKeyboardButton(f"{icone} {nome}", callback_data=f"edit_cat_select_{nome}")] 
                       for nome, icone in categorias]
            keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="extrato")])
            
            await query.edit_message_text(
                f"Selecione a *nova categoria*:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')

    # NOVO: Processa a seleção da nova categoria
    elif data.startswith("edit_cat_select_"):
        categoria = data[len("edit_cat_select_"):]
        tx_id = context.user_data.get('edit_tx_id')
        message_id_to_edit = context.user_data.get('message_id_to_edit')
        
        if tx_id and context.user_data.get('step') == 'editar_categoria_transacao':
            # Usa a função centralizada para atualizar a categoria
            sucesso = update_transacao_campo(tx_id, 'categoria', categoria)
            
            if sucesso:
                # NOVO FLUXO: Edita a mensagem com o resumo atualizado
                await send_or_edit_summary(context, query.message.chat_id, tx_id, message_id_to_edit)
                await query.answer(text=f"✅ Categoria atualizada para {categoria}.")
            else:
                await query.edit_message_text(
                    "❌ Falha ao atualizar a categoria. Tente novamente.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancelar", callback_data="extrato")
                    ]]))
            
            context.user_data.clear()
        else:
            await query.edit_message_text("😕 Ocorreu um erro. Por favor, comece de novo.",
                                         reply_markup=InlineKeyboardMarkup([[
                                             InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_principal")
                                         ]]))


async def data_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para os botões de seleção de data.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "data_manual":
        context.user_data['step'] = 'data_manual_transacao'
        await query.edit_message_text("Por favor, digite a data no formato **dd/mm/aaaa**:")
        return

    if data.startswith("data_"):
        date_str = data[5:]
        context.user_data['data_transacao'] = date_str
        context.user_data['data_insercao'] = get_brazil_now().strftime('%Y-%m-%d')
        context.user_data['step'] = 'descricao_transacao'

        data_obj = datetime.strptime(date_str, '%Y-%m-%d')
        mes_nome = meses[calendar.month_name[data_obj.month]].capitalize()
        
        # Apagando a mensagem com os botões de data antes de enviar a próxima
        message_id_to_edit = query.message.message_id
        if message_id_to_edit:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id,
                                               message_id=message_id_to_edit)
            except Exception:
                pass

        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Data de Inserção: *{format_date_br(context.user_data['data_insercao'])}* (Contabilizado para {mes_nome})\n\n"
                 "Agora, uma breve descrição:",
            parse_mode='Markdown')
        context.user_data['message_id_to_edit'] = sent_message.message_id


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para mensagens de texto.
    """
    user_id = update.effective_user.id
    if str(user_id) not in AUTHORIZED_USERS:
        await update.message.reply_text("❌ Desculpe, você não tem permissão para usar este bot.")
        return

    step = context.user_data.get('step')
    if not step:
        await update.message.reply_text(
            "🤔 Não entendi. Por favor, use os botões do menu ou digite /start para começar."
        )
        return

    text = update.message.text
    chat_id = update.effective_chat.id
    message_id_to_edit = context.user_data.get('message_id_to_edit')

    try:
        await update.message.delete()
    except Exception:
        pass

    if step == 'valor_transacao':
        # Lógica de inserção de valor (sem alterações)
        # ...
        try:
            valor = float(text.replace('.', '').replace(',', '.'))
            context.user_data['valor_transacao'] = valor
            context.user_data['step'] = 'data_transacao'

            if message_id_to_edit:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id_to_edit)
                except Exception:
                    pass

            hoje = get_brazil_now()
            proximo_mes = hoje + relativedelta(months=1)
            dia_seguro = min(hoje.day, calendar.monthrange(proximo_mes.year, proximo_mes.month)[1])
            data_proximo_mes = datetime(proximo_mes.year, proximo_mes.month, dia_seguro)

            keyboard = [
                [
                    InlineKeyboardButton(
                        f"📅 Mês Atual ({meses[calendar.month_name[hoje.month]].capitalize()})",
                        callback_data=f"data_{hoje.year}-{hoje.month:02d}-{hoje.day:02d}")
                ],
                [
                    InlineKeyboardButton(
                        f"🗓️ Mês Seguinte ({meses[calendar.month_name[proximo_mes.month]].capitalize()})",
                        callback_data=f"data_{data_proximo_mes.year}-{data_proximo_mes.month:02d}-{data_proximo_mes.day:02d}")
                ],
                [InlineKeyboardButton("✏️ Outra Data", callback_data="data_manual")]
            ]
            sent_message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"Valor: *{format_brl(valor)}*\n\nPara qual mês é este lançamento?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
            context.user_data['message_id_to_edit'] = sent_message.message_id

        except (ValueError, AttributeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Valor inválido. Por favor, use apenas números (ex: 150,50).")
        return

    if step == 'descricao_transacao':
        # Lógica de inserção de descrição (sem alterações)
        # ...
        required_keys = ['tipo_transacao', 'categoria_transacao', 'valor_transacao', 'data_transacao', 'data_insercao']
        if not all(key in context.user_data for key in required_keys):
            logging.error(f"Estado inválido em 'descricao_transacao'. Dados: {context.user_data}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="😕 Ocorreu um erro e me perdi. Por favor, comece de novo.")
            context.user_data.clear()
            await show_main_menu(update, context)
            return

        descricao = text
        
        # Deleta a mensagem temporária de data/descrição
        if message_id_to_edit:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id_to_edit)
            except Exception:
                pass

        tx_id = add_transacao(str(user_id), context.user_data['tipo_transacao'],
                            context.user_data['categoria_transacao'],
                            context.user_data['valor_transacao'],
                            descricao,
                            context.user_data['data_transacao'])

        # Envia a mensagem final de confirmação (com o botão de edição)
        sent_message_id = await send_or_edit_summary(context, chat_id, tx_id)

        # Resto da lógica (alerta de orçamento) mantida
        if context.user_data['tipo_transacao'] == 'despesa':
            data_obj = datetime.strptime(context.user_data['data_transacao'], '%Y-%m-%d')
            _, _, _, percentual = get_orcamento_status(
                context.user_data['categoria_transacao'], data_obj.month, data_obj.year)
            alerta = get_alerta_divertido(context.user_data['categoria_transacao'], percentual)
            if alerta:
                await context.bot.send_message(chat_id=chat_id, text=alerta, parse_mode='Markdown')
        
        context.user_data.clear()
        await show_main_menu(update, context)
        return

    if step == 'valor_orcamento':
        # Lógica de inserção de orçamento (sem alterações)
        # ...
        try:
            valor = float(text.replace('.', '').replace(',', '.'))
            categoria = context.user_data['categoria_orcamento']
            hoje = get_brazil_now()
            set_orcamento(categoria, valor, hoje.month, hoje.year)
            feedback = f"✅ Orçamento de *{categoria}* definido para *{format_brl(valor)}*."
            keyboard = [
                [InlineKeyboardButton("🎯 Definir Outro Orçamento", callback_data="orc_definir")],
                [InlineKeyboardButton("📋 Ver Todos os Orçamentos", callback_data="orc_ver")],
                [InlineKeyboardButton("🏠 Voltar ao Menu Principal", callback_data="menu_principal")]
            ]
            await context.bot.send_message(
                chat_id=chat_id, text=feedback,
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            context.user_data.clear()
        except (ValueError, AttributeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Valor inválido. Por favor, use apenas números (ex: 800).")
        return

    if step == 'data_manual_transacao':
        # Lógica de inserção de data manual (sem alterações)
        # ...
        try:
            data_obj = datetime.strptime(text, '%d/%m/%Y')
            context.user_data['data_transacao'] = data_obj.strftime('%Y-%m-%d')
            context.user_data['data_insercao'] = get_brazil_now().strftime('%Y-%m-%d')
            context.user_data['step'] = 'descricao_transacao'

            mes_nome = meses[calendar.month_name[data_obj.month]].capitalize()

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id_to_edit,
                text=f"Data de Inserção: *{format_date_br(context.user_data['data_insercao'])}* (Contabilizado para {mes_nome})\n\nAgora, uma breve descrição:",
                parse_mode='Markdown')
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Formato de data inválido. Por favor, use **dd/mm/aaaa** (ex: 31/08/2025).")
        return

    if step == 'editar_valor_transacao':
        # Lógica de edição de valor
        try:
            novo_valor = float(text.replace('.', '').replace(',', '.'))
            tx_id = context.user_data.get('edit_tx_id')
            message_id_to_edit = context.user_data.get('message_id_to_edit')
            
            sucesso = update_transacao_valor(tx_id, novo_valor)
            if not sucesso:
                raise ValueError("Falha ao atualizar")

            # NOVO FLUXO: Edita a mensagem com o resumo atualizado
            await send_or_edit_summary(context, chat_id, tx_id, message_id_to_edit)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Valor atualizado com sucesso para *{format_brl(novo_valor)}*!",
                parse_mode='Markdown')
            context.user_data.clear()

        except (ValueError, TypeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Valor inválido. Tente novamente (ex: 150,50) ou toque para cancelar.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancelar", callback_data="extrato")
                ]]))
        return

    # NOVO BLOCO: Lógica de Edição de Descrição
    if step == 'editar_descricao_transacao':
        descricao = text
        tx_id = context.user_data.get('edit_tx_id')
        message_id_to_edit = context.user_data.get('message_id_to_edit')
        
        sucesso = update_transacao_campo(tx_id, 'descricao', descricao)
        
        if sucesso:
            # NOVO FLUXO: Edita a mensagem com o resumo atualizado
            await send_or_edit_summary(context, chat_id, tx_id, message_id_to_edit)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Descrição atualizada para: *{descricao}*.",
                parse_mode='Markdown')
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Falha ao atualizar a descrição. Tente novamente.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancelar", callback_data="extrato")
                ]]))
                
        context.user_data.clear()
        return


async def command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    await show_main_menu(update, context)


async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "▶️ Iniciar e ver o menu"),
        BotCommand("gastou", "💸 Lançar nova despesa"),
        BotCommand("ganhou", "💰 Lançar nova receita"),
        BotCommand("saldo", "📋 Ver saldo do mês"),
        BotCommand("relatorio", "📊 Gerar um relatório"),
        BotCommand("orcamento", "🎯 Gerenciar orçamentos"),
        BotCommand("zerar", "🗑️ Apagar todos os dados"),
    ])


def run_bot():
    """Função para rodar o bot do Telegram"""
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("zerar", zerar_command))
    application.add_handler(
        CommandHandler(["gastou", "ganhou", "saldo", "relatorio", "orcamento"], command_handler))

    # Handlers para botões
    application.add_handler(
        CallbackQueryHandler(data_button_handler, pattern="^(data_manual|data_).+"))
    application.add_handler(
        CallbackQueryHandler(generic_button_handler, pattern="^(?!data_manual|data_).+"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("🤖 Bot assistente financeiro v13.4 (com servidor web) iniciado!")
    application.run_polling()


def run_web_server():
    """Função para rodar o servidor web Flask"""
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Servidor web iniciado na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)


def main():
    """Função principal que inicia tanto o bot quanto o servidor web"""
    print("🚀 Iniciando aplicação híbrida (Bot + Servidor Web)...")

    # Inicia o servidor web em uma thread separada
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # Inicia o banco de dados
    try:
        init_database()
        setup_database()
        print("✅ Banco de dados inicializado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao inicializar banco de dados: {e}")
        return

    # Inicia o bot na thread principal
    run_bot()


if __name__ == '__main__':
    main()
