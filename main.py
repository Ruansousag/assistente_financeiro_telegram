import sqlite3
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# --- CONFIGURAÇÃO INICIAL ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

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

# COLOQUE O TOKEN DO SEU BOT AQUI
TOKEN = "8116373945:AAHB-Cgn7Gx6GIWT3J17k9qttoPjP9i7zUg"

# COLOQUE OS IDs DE USUÁRIO DO TELEGRAM AUTORIZADOS AQUI
AUTHORIZED_USERS = [7047256417, 8314716058]

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
            h1 { text-align: center;
                margin-bottom: 30px; }
            .emoji { font-size: 2em; margin: 10px;
            }
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
        'version': '13.3'
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


# --- FUNÇÕES AUXILIARES ---
def format_brl(value):
    if not isinstance(value, (int, float)):
        return "R$ 0,00"
    try:
        return f"R$ {value:,.2f}".replace(",",
                                           "X").replace(".",
                                                          ",").replace("X", ".")
    except locale.Error:
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


# --- CLASSE DE GERENCIAMENTO DO BANCO DE DADOS ---
class FinancialBotDB:

    def __init__(self, db_name='financeiro.db'):
        self.db_name = db_name
        self.init_database()

    def _get_connection(self):
        return sqlite3.connect(self.db_name)

    def init_database(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tipo TEXT,
                    categoria TEXT, valor REAL, descricao TEXT, data DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orcamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, categoria TEXT NOT NULL,
                    valor_limite REAL NOT NULL, mes INTEGER NOT NULL, ano INTEGER NOT NULL,
                    UNIQUE(categoria, mes, ano)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categorias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT UNIQUE,
                    tipo TEXT, icone TEXT
                )
            ''')
            cursor.execute("SELECT COUNT(*) FROM categorias")
            if cursor.fetchone()[0] == 0:
                categorias_default = [('Salário', 'receita', '💰'),
                                      ('Freelance', 'receita', '💻'),
                                      ('Investimentos', 'receita', '📈'),
                                      ('Mercado', 'despesa', '🛒'),
                                      ('Saúde', 'despesa', '🏥'),
                                      ('Casa', 'despesa', '🏠'),
                                      ('Aluguel', 'despesa', '🏘️'),
                                      ('Lazer', 'despesa', '🎉'),
                                      ('Cartão', 'despesa', '💳'),
                                      ('Transporte', 'despesa', '🚗'),
                                      ('Educação', 'despesa', '📚'),
                                      ('Diversos', 'ambos', '📦')]
                cursor.executemany(
                    'INSERT INTO categorias (nome, tipo, icone) VALUES (?, ?, ?)',
                    categorias_default)
            conn.commit()

    def zerar_dados(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transacoes")
            cursor.execute("DELETE FROM orcamentos")
            conn.commit()

    def get_categorias(self, tipo=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT nome, icone FROM categorias WHERE tipo = ? OR tipo = 'ambos' ORDER BY nome" if tipo else "SELECT nome, icone FROM categorias ORDER BY nome"
            cursor.execute(query, (tipo, ) if tipo else ())
            return cursor.fetchall()

    def add_transacao(self, user_id, tipo, categoria, valor, descricao, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transacoes (user_id, tipo, categoria, valor, descricao, data) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, tipo, categoria, float(valor), descricao, data))
            conn.commit()
            return cursor.lastrowid

    def get_orcamento_status(self, categoria, mes, ano):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT valor_limite FROM orcamentos WHERE categoria = ? AND mes = ? AND ano = ?',
                (categoria, mes, ano))
            orcamento = cursor.fetchone()
            if not orcamento:
                return None, 0, 0, 0
            limite = orcamento[0]
            cursor.execute(
                "SELECT COALESCE(SUM(valor), 0) FROM transacoes WHERE categoria = ? AND tipo = 'despesa' AND strftime('%Y-%m', data) = ?",
                (categoria, f"{ano:04d}-{mes:02d}"))
            gasto_atual = cursor.fetchone()[0]
            disponivel = limite - gasto_atual
            percentual_usado = (gasto_atual /
                                limite) * 100 if limite > 0 else 0
            return limite, gasto_atual, disponivel, percentual_usado

    def set_orcamento(self, categoria, valor_limite, mes, ano):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO orcamentos (categoria, valor_limite, mes, ano) VALUES (?, ?, ?, ?) ON CONFLICT(categoria, mes, ano) DO UPDATE SET valor_limite = excluded.valor_limite",
                (categoria, float(valor_limite), mes, ano))
            conn.commit()

    def get_todos_orcamentos(self, mes, ano):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT categoria, valor_limite FROM orcamentos WHERE mes = ? AND ano = ? ORDER BY categoria",
                (mes, ano))
            return cursor.fetchall()

    def get_transacoes_por_categoria(self, categoria, mes, ano):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data, descricao, valor FROM transacoes WHERE categoria = ? AND tipo = 'despesa' AND strftime('%Y-%m', data) = ? ORDER BY data",
                (categoria, f"{ano:04d}-{mes:02d}"))
            return cursor.fetchall()

    def gerar_relatorio_mensal(self, mes, ano, detalhado=False):
        with self._get_connection() as conn:
            periodo_str = f"{ano:04d}-{mes:02d}"
            query = "SELECT data, categoria, descricao, tipo, valor, user_id FROM transacoes WHERE strftime('%Y-%m', data) = ? ORDER BY data" if detalhado else "SELECT categoria, tipo, SUM(valor) as total FROM transacoes WHERE strftime('%Y-%m', data) = ? GROUP BY categoria, tipo"
            return pd.read_sql_query(query, conn, params=[periodo_str])

    def get_ultimos_lancamentos(self, limit=7):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, data, tipo, categoria, descricao, valor, user_id FROM transacoes ORDER BY id DESC LIMIT ?",
                (limit, ))
            return cursor.fetchall()

    def get_transacao(self, tx_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transacoes WHERE id = ?", (tx_id, ))
            return cursor.fetchone()

    def update_transacao_valor(self, tx_id, novo_valor):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE transacoes SET valor = ? WHERE id = ?",
                               (novo_valor, tx_id))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logging.error(f"Erro ao atualizar transação {tx_id}: {e}")
                return False


db = FinancialBotDB()


# --- FUNÇÕES DE LÓGICA E VISUALIZAÇÃO ---
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
    fig.suptitle(f'Relatório Financeiro - {nome_mes_ano}',
                 fontsize=20,
                 weight='bold')

    despesas_cat = df[df['tipo'] == 'despesa']
    if not despesas_cat.empty:
        axes[0, 0].pie(despesas_cat['total'],
                       labels=despesas_cat['categoria'],
                       autopct='%1.1f%%',
                       startangle=140,
                       colors=sns.color_palette("Reds_r", len(despesas_cat)))
        axes[0, 0].set_title('Composição das Despesas', fontsize=14)
    else:
        axes[0, 0].text(0.5,
                        0.5,
                        'Sem despesas',
                        ha='center',
                        va='center',
                        fontsize=14)
        axes[0, 0].set_title('Composição das Despesas', fontsize=14)

    receitas_cat = df[df['tipo'] == 'receita']
    if not receitas_cat.empty:
        axes[0, 1].pie(receitas_cat['total'],
                       labels=receitas_cat['categoria'],
                       autopct='%1.1f%%',
                       startangle=140,
                       colors=sns.color_palette("Greens_r", len(receitas_cat)))
        axes[0, 1].set_title('Composição das Receitas', fontsize=14)
    else:
        axes[0, 1].text(0.5,
                        0.5,
                        'Sem receitas',
                        ha='center',
                        va='center',
                        fontsize=14)
        axes[0, 1].set_title('Composição das Receitas', fontsize=14)

    cores = ['green', 'red', 'blue' if saldo >= 0 else 'orange']
    sns.barplot(x=['Receitas', 'Despesas', 'Saldo'],
                y=[receitas, despesas, saldo],
                ax=axes[1, 0],
                palette=cores)
    axes[1, 0].set_title('Resumo Financeiro do Mês', fontsize=14)
    axes[1, 0].set_ylabel('Valor (R$)')
    for p in axes[1, 0].patches:
        axes[1, 0].annotate(format_brl(p.get_height()),
                            (p.get_x() + p.get_width() / 2., p.get_height()),
                            ha='center',
                            va='center',
                            xytext=(0, 9),
                            textcoords='offset points')

    top_despesas = despesas_cat.sort_values('total', ascending=False).head(5)
    if not top_despesas.empty:
        sns.barplot(x='total',
                    y='categoria',
                    data=top_despesas,
                    ax=axes[1, 1],
                    palette='Reds_r',
                    orient='h')
        axes[1, 1].set_title('Top 5 Despesas', fontsize=14)
        axes[1, 1].set_xlabel('Valor (R$)')
        axes[1, 1].set_ylabel('')
    else:
        axes[1, 1].axis('off')
        axes[1, 1].text(0.5,
                        0.5,
                        'Sem despesas\npara o ranking',
                        ha='center',
                        va='center',
                        fontsize=12)

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
            f"Data: {format_date_br(row['data'])}\nTipo: {row['tipo'].capitalize()}\nCategoria: {row['categoria']}\n"
        )
        buffer.write(
            f"Descrição: {row['descricao']}\nValor: {sinal}{format_brl(row['valor']).replace('R$ ', '')}\n{'-'*30}\n"
        )
    buffer.seek(0)
    return buffer


def criar_relatorio_comparativo(df_atual, df_anterior, mes_atual, ano_atual,
                                mes_anterior, ano_anterior):
    rec_atual = df_atual[df_atual['tipo'] == 'receita']['total'].sum()
    desp_atual = df_atual[df_atual['tipo'] == 'despesa']['total'].sum()
    rec_anterior = df_anterior[df_anterior['tipo'] == 'receita']['total'].sum()
    desp_anterior = df_anterior[df_anterior['tipo'] ==
                                'despesa']['total'].sum()
    despesas_atual_cat = df_atual[df_atual['tipo'] == 'despesa'].set_index(
        'categoria')['total']
    despesas_anterior_cat = df_anterior[
        df_anterior['tipo'] == 'despesa'].set_index('categoria')['total']
    df_comp = pd.concat([despesas_atual_cat, despesas_anterior_cat],
                        axis=1,
                        keys=['atual', 'anterior']).fillna(0)
    df_comp['variacao'] = df_comp['atual'] - df_comp['anterior']
    fig, ax = plt.subplots(figsize=(12, 8))
    df_comp[['anterior', 'atual'
             ]].sort_values(by='atual',
                            ascending=True).plot(kind='barh',
                                                 ax=ax,
                                                 color=['#ff9999', '#ff4d4d'])
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
    caption = (
        f"📊 *Comparativo Mensal*\n\n"
        f"*{'─'*10} Resumo Geral {'─'*10}*\n"
        f"💰 Receitas: {format_brl(rec_atual)}{calc_percent_change(rec_atual, rec_anterior)}\n"
        f"💸 Despesas: {format_brl(desp_atual)}{calc_percent_change(desp_atual, desp_anterior)}\n"
        f"*{'💚 Saldo' if (rec_atual - desp_atual) >= 0 else '❤️ Saldo'}: {format_brl(rec_atual - desp_atual)}*\n\n"
        f"*{'─'*10} Análise das Despesas {'─'*10}*\n")
    top_aumentos = df_comp[df_comp['variacao'] > 0].sort_values(
        'variacao', ascending=False).head(3)
    if not top_aumentos.empty:
        caption += "📈 *Principais Aumentos:*\n"
        for cat, row in top_aumentos.iterrows():
            caption += f"  • *{cat}*: +{format_brl(row['variacao'])}{calc_percent_change(row['atual'], row['anterior'])}\n"
    return buffer, caption


# --- FUNÇÕES DE MENU E NAVEGAÇÃO ---
async def show_main_menu(update: Update,
                         context: ContextTypes.DEFAULT_TYPE,
                         message_id=None):
    keyboard = [[
        InlineKeyboardButton("💸 Nova Despesa", callback_data="add_despesa"),
        InlineKeyboardButton("💰 Nova Receita", callback_data="add_receita")
    ],
                [
                    InlineKeyboardButton("📊 Relatórios",
                                         callback_data="relatorios"),
                    InlineKeyboardButton("🎯 Orçamentos",
                                         callback_data="orcamentos")
                ],
                [
                    InlineKeyboardButton("📋 Saldo do Mês",
                                         callback_data="saldo"),
                    InlineKeyboardButton("📝 Últimos Lançamentos",
                                         callback_data="extrato")
                ]]
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


# --- HANDLERS DE COMANDOS E BOTÕES ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in AUTHORIZED_USERS:
        await update.message.reply_text(
            "❌ Desculpe, você não tem permissão para usar este bot.")
        return
    await update.message.delete()
    await show_main_menu(update, context)


async def zerar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    keyboard = [[
        InlineKeyboardButton("🗑️ SIM, APAGAR TUDO!",
                             callback_data="confirmar_zerar")
    ], [InlineKeyboardButton("❌ Cancelar", callback_data="menu_principal")]]
    text = (
        "⚠️ *ATENÇÃO!* ⚠️\n\nVocê tem certeza que quer apagar *TODOS* os dados?\n\n"
        "Isso irá remover permanentemente todas as receitas, despesas e orçamentos registrados.\n\n"
        "*ESTA AÇÃO NÃO PODE SER DESFEITA.*")
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=text,
                                   reply_markup=InlineKeyboardMarkup(keyboard),
                                   parse_mode='Markdown')


async def generic_button_handler(update: Update,
                                 context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para a maioria dos botões, exceto os de seleção de data.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_principal":
        await show_main_menu(update,
                             context,
                             message_id=query.message.message_id)
        return

    if data in ["add_despesa", "add_receita"]:
        tipo = data.split('_')[1]
        context.user_data.clear()
        context.user_data['tipo_transacao'] = tipo
        categorias = db.get_categorias(tipo)
        keyboard = [[
            InlineKeyboardButton(f"{icone} {nome}",
                                 callback_data=f"cat_{nome}")
        ] for nome, icone in categorias]
        keyboard.append([
            InlineKeyboardButton("⬅️ Voltar ao Menu",
                                 callback_data="menu_principal")
        ])
        await query.edit_message_text(
            f"Selecione a categoria da *{tipo}*:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("cat_"):
        context.user_data['message_id_to_edit'] = query.message.message_id
        categoria = data[4:]
        context.user_data['categoria_transacao'] = categoria
        context.user_data['step'] = 'valor_transacao'
        await query.edit_message_text(
            f"Categoria: *{categoria}*\n\nQual o valor?",
            parse_mode='Markdown')

    elif data == "saldo":
        hoje = get_brazil_now()
        df = db.gerar_relatorio_mensal(hoje.month, hoje.year)
        receitas = df[df['tipo'] ==
                      'receita']['total'].sum() if not df.empty else 0
        despesas = df[df['tipo'] ==
                      'despesa']['total'].sum() if not df.empty else 0
        texto = (
            f"💳 *Saldo de {meses[calendar.month_name[hoje.month]].capitalize()}*\n\n"
            f"💰 Receitas: {format_brl(receitas)}\n"
            f"💸 Despesas: {format_brl(despesas)}\n"
            f"*{'💚 Saldo Positivo' if (receitas - despesas) >= 0 else '❤️ Saldo Negativo'}: {format_brl(receitas - despesas)}*"
        )
        keyboard = [[
            InlineKeyboardButton("⬅️ Voltar ao Menu",
                                 callback_data="menu_principal")
        ]]
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "extrato":
        lancamentos = db.get_ultimos_lancamentos()
        keyboard = [[
            InlineKeyboardButton("⬅️ Voltar ao Menu",
                                 callback_data="menu_principal")
        ]]
        if not lancamentos:
            texto = "Nenhum lançamento encontrado ainda."
        else:
            texto = "📝 *Últimos Lançamentos:*\n\n"
            for tx_id, data_t, tipo, cat, desc, valor, user_id_lanc in lancamentos:
                emoji = "💸" if tipo == 'despesa' else "💰"
                texto += f"{emoji} _{format_date_br(data_t)}_ - *{cat}*\n"
                texto += f"   _{desc}_ - *{format_brl(valor)}*\n"

        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "relatorios":
        keyboard = [[
            InlineKeyboardButton("📊 Mês Atual (Gráfico)",
                                 callback_data="rel_grafico")
        ],
                    [
                        InlineKeyboardButton("📄 Mês Atual (Detalhado)",
                                             callback_data="rel_detalhado")
                    ],
                    [
                        InlineKeyboardButton("📈 Comparativo Mensal",
                                             callback_data="rel_comparativo")
                    ],
                    [
                        InlineKeyboardButton("⬅️ Voltar ao Menu",
                                             callback_data="menu_principal")
                    ]]
        await query.edit_message_text(
            "Qual relatório você deseja gerar?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("rel_"):
        tipo_relatorio = data.split('_')[1]
        if tipo_relatorio == 'comparativo':
            await query.edit_message_text(
                "⏳ Gerando relatório comparativo, um momento...")
            hoje = get_brazil_now()
            ano_anterior, mes_anterior = get_previous_month(
                hoje.year, hoje.month)
            df_atual = db.gerar_relatorio_mensal(hoje.month, hoje.year)
            df_anterior = db.gerar_relatorio_mensal(mes_anterior, ano_anterior)
            if df_anterior.empty:
                await query.edit_message_text(
                    "Ainda não há dados do mês anterior para comparar.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Voltar",
                                             callback_data="relatorios")
                    ]]))
                return
            buffer, caption = criar_relatorio_comparativo(
                df_atual, df_anterior, hoje.month, hoje.year, mes_anterior,
                ano_anterior)
            await context.bot.send_photo(chat_id=query.message.chat_id,
                                         photo=buffer,
                                         caption=caption,
                                         parse_mode='Markdown')
        else:
            detalhado = (tipo_relatorio == 'detalhado')
            hoje = get_brazil_now()
            await query.edit_message_text("⏳ Gerando relatório, um momento...")
            df = db.gerar_relatorio_mensal(hoje.month,
                                          hoje.year,
                                          detalhado=detalhado)
            if df.empty:
                await query.edit_message_text(
                    f"Nenhum dado encontrado para {meses[calendar.month_name[hoje.month]].capitalize()}.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Voltar",
                                             callback_data="relatorios")
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
                    for _, row in df_receitas.sort_values(
                            by='total', ascending=False).iterrows():
                        caption += f"💰 {row['categoria']}: {format_brl(row['total'])}\n"
                df_despesas = df[df['tipo'] == 'despesa']
                if not df_despesas.empty:
                    caption += "\n------ *Despesas* ------\n"
                    for _, row in df_despesas.sort_values(
                            by='total', ascending=False).iterrows():
                        caption += f"💸 {row['categoria']}: {format_brl(row['total'])}\n"
                await context.bot.send_photo(chat_id=query.message.chat_id,
                                             photo=buffer,
                                             caption=caption,
                                             parse_mode='Markdown')
        await query.delete_message()
        await show_main_menu(update, context)

    elif data == "orcamentos":
        keyboard = [[
            InlineKeyboardButton("🎯 Definir/Alterar",
                                 callback_data="orc_definir")
        ], [InlineKeyboardButton("📋 Ver Orçamentos", callback_data="orc_ver")],
                    [
                        InlineKeyboardButton("⬅️ Voltar ao Menu",
                                             callback_data="menu_principal")
                    ]]
        await query.edit_message_text(
            "Gerenciar orçamentos do *mês atual*:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data == "confirmar_zerar":
        db.zerar_dados()
        await query.edit_message_text(
            "✅ Todos os dados foram apagados com sucesso!")
        await show_main_menu(update,
                             context,
                             message_id=query.message.message_id)

    elif data == "orc_definir":
        categorias = db.get_categorias('despesa')
        keyboard = [[
            InlineKeyboardButton(f"{icone} {nome}",
                                 callback_data=f"orc_cat_{nome}")
        ] for nome, icone in categorias]
        keyboard.append(
            [InlineKeyboardButton("⬅️ Voltar", callback_data="orcamentos")])
        await query.edit_message_text(
            "Definir orçamento para qual categoria?",
            reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("orc_cat_"):
        context.user_data.clear()
        context.user_data['message_id_to_edit'] = query.message.message_id
        categoria = data[8:]
        context.user_data['categoria_orcamento'] = categoria
        context.user_data['step'] = 'valor_orcamento'
        await query.edit_message_text(
            f"Orçamento para *{categoria}*.\n\nQual o valor limite mensal?",
            parse_mode='Markdown')

    elif data == "orc_ver":
        hoje = get_brazil_now()
        orcamentos = db.get_todos_orcamentos(hoje.month, hoje.year)
        if not orcamentos:
            await query.edit_message_text(
                "Nenhum orçamento definido para este mês.",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("🎯 Definir um Agora",
                                             callback_data="orc_definir")
                    ],
                     [
                         InlineKeyboardButton("⬅️ Voltar",
                                              callback_data="orcamentos")
                     ]]))
            return
        texto = f"📋 *Orçamentos de {meses[calendar.month_name[hoje.month]].capitalize()}*\n\n"
        keyboard = []
        for categoria, limite in orcamentos:
            _, gasto, disponivel, percentual = db.get_orcamento_status(
                categoria, hoje.month, hoje.year)
            barra = "▪" * int(
                percentual / 10) + "▫" * (10 - int(percentual / 10))
            status = "✅" if disponivel >= 0 else "🆘"
            texto += f"*{categoria}* {status}\n`{barra}` {percentual:.1f}%\n"
            texto += f"Gasto: {format_brl(gasto)} de {format_brl(limite)}\n"
            texto += f"Sobra: {format_brl(disponivel)}\n\n"
            keyboard.append([
                InlineKeyboardButton(f"Ver Gastos de {categoria}",
                                     callback_data=f"orc_gastos_{categoria}")
            ])
        keyboard.append(
            [InlineKeyboardButton("⬅️ Voltar", callback_data="orcamentos")])
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("orc_gastos_"):
        categoria = data[11:]
        hoje = get_brazil_now()
        transacoes = db.get_transacoes_por_categoria(categoria, hoje.month,
                                                     hoje.year)
        texto = f"💸 *Gastos em {categoria}*\n\n"
        if not transacoes:
            texto += "Nenhum gasto este mês."
        else:
            total = 0
            for data_t, desc, valor in transacoes:
                texto += f"_{format_date_br(data_t)}_: {desc} - *{format_brl(valor)}*\n"
                total += valor
            texto += f"\n*Total Gasto:* {format_brl(total)}"
        keyboard = [[
            InlineKeyboardButton("⬅️ Voltar aos Orçamentos",
                                 callback_data="orc_ver")
        ]]
        await query.edit_message_text(
            texto,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown')

    elif data.startswith("edit_tx_"):
        tx_id = int(data.split("_")[-1])
        tx = db.get_transacao(tx_id)
        if not tx:
            await query.edit_message_text("Transação não encontrada. 😕",
                                          reply_markup=InlineKeyboardMarkup([[
                                              InlineKeyboardButton(
                                                  "⬅️ Voltar",
                                                  callback_data="extrato")
                                          ]]))
            return

        _id, _user, _tipo, _cat, _valor, _desc, _data, _created = tx
        context.user_data.clear()
        context.user_data['step'] = 'editar_valor_transacao'
        context.user_data['edit_tx_id'] = _id
        context.user_data['message_id_to_edit'] = query.message.message_id

        await query.edit_message_text(
            text=
            f"✏️ *Editar valor*\n\nCategoria: *{_cat}*\nData: *{format_date_br(_data)}*\nDescrição: _{_desc}_\nValor atual: *{format_brl(_valor)}*\n\n👉 Envie o *novo valor* (ex: 150,50):",
            parse_mode='Markdown')


async def data_button_handler(update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para os botões de seleção de data.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "data_manual":
        context.user_data['step'] = 'data_manual_transacao'
        await query.edit_message_text(
            "Por favor, digite a data no formato **dd/mm/aaaa**:")
        return

    if data.startswith("data_"):
        date_str = data[5:]
        context.user_data['data_transacao'] = date_str
        context.user_data['data_insercao'] = get_brazil_now().strftime(
            '%Y-%m-%d')
        context.user_data['step'] = 'descricao_transacao'

        data_obj = datetime.strptime(date_str, '%Y-%m-%d')
        mes_nome = meses[calendar.month_name[data_obj.month]].capitalize()
        
        # AQUI: Apagando a mensagem com os botões de data antes de enviar a próxima.
        message_id_to_edit = query.message.message_id
        if message_id_to_edit:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id,
                                                 message_id=message_id_to_edit)
            except Exception:
                pass

        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=
            f"Data de Inserção: *{format_date_br(context.user_data['data_insercao'])}* (Contabilizado para {mes_nome})\n\n"
            "Agora, uma breve descrição:",
            parse_mode='Markdown')
        context.user_data['message_id_to_edit'] = sent_message.message_id


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para mensagens de texto.
    """
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
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
        try:
            valor = float(text.replace('.', '').replace(',', '.'))
            context.user_data['valor_transacao'] = valor
            context.user_data['step'] = 'data_transacao'

            if message_id_to_edit:
                try:
                    await context.bot.delete_message(chat_id=chat_id,
                                                     message_id=message_id_to_edit)
                except Exception:
                    pass

            hoje = get_brazil_now()
            proximo_mes = hoje + relativedelta(months=1)
            dia_seguro = min(
                hoje.day,
                calendar.monthrange(proximo_mes.year, proximo_mes.month)[1])
            data_proximo_mes = datetime(proximo_mes.year, proximo_mes.month,
                                        dia_seguro)

            keyboard = [
                [
                    InlineKeyboardButton(
                        f"📅 Mês Atual ({meses[calendar.month_name[hoje.month]].capitalize()})",
                        callback_data=
                        f"data_{hoje.year}-{hoje.month:02d}-{hoje.day:02d}")
                ],
                [
                    InlineKeyboardButton(
                        f"🗓️ Mês Seguinte ({meses[calendar.month_name[proximo_mes.month]].capitalize()})",
                        callback_data=
                        f"data_{data_proximo_mes.year}-{data_proximo_mes.month:02d}-{data_proximo_mes.day:02d}"
                    )
                ],
                [
                    InlineKeyboardButton("✏️ Outra Data",
                                         callback_data="data_manual")
                ]
            ]
            sent_message = await context.bot.send_message(
                chat_id=chat_id,
                text=
                f"Valor: *{format_brl(valor)}*\n\nPara qual mês é este lançamento?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
            context.user_data['message_id_to_edit'] = sent_message.message_id

        except (ValueError, AttributeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text=
                "❌ Valor inválido. Por favor, use apenas números (ex: 150,50).")
        return

    if step == 'descricao_transacao':
        required_keys = [
            'tipo_transacao', 'categoria_transacao', 'valor_transacao',
            'data_transacao', 'data_insercao'
        ]
        if not all(key in context.user_data for key in required_keys):
            logging.error(
                f"Estado inválido em 'descricao_transacao'. Dados: {context.user_data}"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text="😕 Ocorreu um erro e me perdi. Por favor, comece de novo."
            )
            context.user_data.clear()
            await show_main_menu(update, context)
            return

        descricao = text
        if message_id_to_edit:
            try:
                await context.bot.delete_message(chat_id=chat_id,
                                                 message_id=message_id_to_edit)
            except Exception:
                pass

        tx_id = db.add_transacao(user_id, context.user_data['tipo_transacao'],
                                 context.user_data['categoria_transacao'],
                                 context.user_data['valor_transacao'],
                                 descricao,
                                 context.user_data['data_transacao'])

        if not tx_id:
            feedback = "⚠️ Transação duplicada detectada! Não foi adicionada."
            await context.bot.send_message(chat_id=chat_id,
                                           text=feedback,
                                           parse_mode='Markdown')
        else:
            data_obj = datetime.strptime(context.user_data['data_transacao'],
                                         '%Y-%m-%d')
            mes_contabilizado = meses[calendar.month_name[
                data_obj.month]].capitalize()

            feedback = (
                f"{'💸' if context.user_data['tipo_transacao'] == 'despesa' else '💰'} *Transação Registrada!*\n\n"
                f"Categoria: *{context.user_data['categoria_transacao']}*\n"
                f"Data: *{format_date_br(context.user_data['data_insercao'])}*\n"
                f"Contabilizado para: *{mes_contabilizado}*\n"
                f"Valor: *{format_brl(context.user_data['valor_transacao'])}*\n"
                f"Descrição: _{descricao}_")

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✏️ Editar valor",
                                     callback_data=f"edit_tx_{tx_id}")
            ]])

            if context.user_data['tipo_transacao'] == 'despesa':
                data_obj = datetime.strptime(
                    context.user_data['data_transacao'], '%Y-%m-%d')
                _, _, _, percentual = db.get_orcamento_status(
                    context.user_data['categoria_transacao'], data_obj.month,
                    data_obj.year)
                alerta = get_alerta_divertido(
                    context.user_data['categoria_transacao'], percentual)
                if alerta:
                    feedback += f"\n\n{alerta}"

            await context.bot.send_message(chat_id=chat_id,
                                           text=feedback,
                                           reply_markup=keyboard,
                                           parse_mode='Markdown')

        context.user_data.clear()
        await show_main_menu(update, context)
        return

    if step == 'valor_orcamento':
        try:
            valor = float(text.replace('.', '').replace(',', '.'))
            categoria = context.user_data['categoria_orcamento']
            hoje = get_brazil_now()
            db.set_orcamento(categoria, valor, hoje.month, hoje.year)
            feedback = f"✅ Orçamento de *{categoria}* definido para *{format_brl(valor)}*."
            keyboard = [[
                InlineKeyboardButton("🎯 Definir Outro Orçamento",
                                     callback_data="orc_definir")
            ],
                        [
                            InlineKeyboardButton("📋 Ver Todos os Orçamentos",
                                                 callback_data="orc_ver")
                        ],
                        [
                            InlineKeyboardButton(
                                "🏠 Voltar ao Menu Principal",
                                callback_data="menu_principal")
                        ]]
            await context.bot.send_message(
                chat_id=chat_id,
                text=feedback,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
            context.user_data.clear()
        except (ValueError, AttributeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text=
                "❌ Valor inválido. Por favor, use apenas números (ex: 800).")
        return

    if step == 'data_manual_transacao':
        try:
            data_obj = datetime.strptime(text, '%d/%m/%Y')
            context.user_data['data_transacao'] = data_obj.strftime('%Y-%m-%d')
            context.user_data['data_insercao'] = get_brazil_now().strftime(
                '%Y-%m-%d')
            context.user_data['step'] = 'descricao_transacao'

            mes_nome = meses[calendar.month_name[data_obj.month]].capitalize()

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id_to_edit,
                text=
                f"Data de Inserção: *{format_date_br(context.user_data['data_insercao'])}* (Contabilizado para {mes_nome})\n\nAgora, uma breve descrição:",
                parse_mode='Markdown')
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id,
                text=
                "❌ Formato de data inválido. Por favor, use **dd/mm/aaaa** (ex: 31/08/2025)."
            )
        return

    if step == 'editar_valor_transacao':
        try:
            novo_valor = float(text.replace('.', '').replace(',', '.'))
            tx_id = context.user_data.get('edit_tx_id')
            sucesso = db.update_transacao_valor(tx_id, novo_valor)
            if not sucesso:
                raise ValueError("Falha ao atualizar")

            await context.bot.send_message(
                chat_id=chat_id,
                text=
                f"✅ Valor atualizado com sucesso para *{format_brl(novo_valor)}*!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("🔄 Ver Extrato Atualizado",
                                             callback_data="extrato")
                    ],
                     [
                         InlineKeyboardButton("🏠 Menu Principal",
                                              callback_data="menu_principal")
                     ]]))
            context.user_data.clear()

        except (ValueError, TypeError):
            await context.bot.send_message(
                chat_id=chat_id,
                text=
                "❌ Valor inválido. Tente novamente (ex: 150,50) ou toque para cancelar.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancelar", callback_data="extrato")
                ]]))
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
    application = Application.builder().token(TOKEN).post_init(
        post_init).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("zerar", zerar_command))
    application.add_handler(
        CommandHandler(["gastou", "ganhou", "saldo", "relatorio", "orcamento"],
                       command_handler))

    # Handlers para botões
    application.add_handler(
        CallbackQueryHandler(data_button_handler,
                             pattern="^(data_manual|data_).+"))
    application.add_handler(
        CallbackQueryHandler(generic_button_handler,
                             pattern="^(?!data_manual|data_).+"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("🤖 Bot assistente financeiro v13.3 (com servidor web) iniciado!")
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

    # Inicia o bot na thread principal
    run_bot()


if __name__ == '__main__':
    main()
