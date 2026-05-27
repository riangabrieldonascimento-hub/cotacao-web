from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from sqlalchemy import or_
import os

# =====================================
# CONFIGURAÇÃO DO APP
# =====================================

basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
os.makedirs(instance_path, exist_ok=True)

app = Flask(__name__, instance_path=instance_path)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cotacao_web_2026_dev_key')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


def _database_uri():
    uri = os.environ.get('DATABASE_URL')
    if uri:
        if uri.startswith('postgres://'):
            uri = uri.replace('postgres://', 'postgresql://', 1)
        return uri
    db_file = os.environ.get(
        'DATABASE_PATH',
        os.path.join(instance_path, 'database.db'),
    )
    return 'sqlite:///' + db_file.replace('\\', '/')


app.config['SQLALCHEMY_DATABASE_URI'] = _database_uri()

db = SQLAlchemy(app)

# =====================================
# SISTEMA DE LOGIN
# =====================================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar esta página."
login_manager.login_message_category = "info"

# =====================================
# MODELOS DE DADOS
# =====================================

class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(50), nullable=False)  # Ex: 'gestor', 'comprador'

class Cotacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), unique=True)
    solicitante = db.Column(db.String(150), nullable=False)
    finalidade = db.Column(db.String(300))
    observacoes = db.Column(db.Text)
    status = db.Column(db.String(50), default='EM COTACAO')
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    itens = db.relationship('ItemCotacao', backref='cotacao', lazy=True, cascade="all, delete-orphan")
    fornecedores = db.relationship('Fornecedor', backref='cotacao', lazy=True, cascade="all, delete-orphan")

class ItemCotacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cotacao_id = db.Column(db.Integer, db.ForeignKey('cotacao.id'))
    descricao = db.Column(db.String(300), nullable=False)
    unidade = db.Column(db.String(20))
    quantidade = db.Column(db.Float, default=1.0)

class Fornecedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cotacao_id = db.Column(db.Integer, db.ForeignKey('cotacao.id'))
    nome = db.Column(db.String(150), nullable=False)
    contato = db.Column(db.String(150))
    telefone = db.Column(db.String(50))
    email = db.Column(db.String(150))
    frete_total = db.Column(db.Float, default=0.0)
    condicao_pagamento = db.Column(db.String(150))

    precos = db.relationship('PrecoFornecedor', backref='fornecedor', lazy=True, cascade="all, delete-orphan")

class PrecoFornecedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fornecedor_id = db.Column(db.Integer, db.ForeignKey('fornecedor.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('item_cotacao.id'))

    valor_unitario = db.Column(db.Float, default=0.0)
    icms = db.Column(db.Float, default=0.0)  # Porcentagem
    ipi = db.Column(db.Float, default=0.0)   # Porcentagem
    st = db.Column(db.Float, default=0.0)    # Valor fixo
    desconto = db.Column(db.Float, default=0.0) # Valor fixo
    prazo = db.Column(db.Integer, default=0) # Dias


# Status da cotação
STATUS_EM_COTACAO = 'EM COTACAO'
STATUS_AGUARDANDO_APROVACAO = 'AGUARDANDO APROVACAO'
STATUS_APROVADO = 'APROVADO'
STATUS_REPROVADO = 'REPROVADO'

STATUS_LABELS = {
    STATUS_EM_COTACAO: 'Em cotação',
    STATUS_AGUARDANDO_APROVACAO: 'Aguardando aprovação',
    STATUS_APROVADO: 'Aprovado',
    STATUS_REPROVADO: 'Reprovado',
}


def cotacao_bloqueada(cotacao):
    """Não permite alterar itens, fornecedores ou preços."""
    return cotacao.status in (
        STATUS_AGUARDANDO_APROVACAO,
        STATUS_APROVADO,
        STATUS_REPROVADO,
    )


def normalizar_status(status):
    """Padroniza status gravados no banco (legado ou com espaços)."""
    if not status:
        return ''
    s = status.strip().upper()
    legado = {
        'PENDENTE': STATUS_EM_COTACAO,
        'EM ANALISE': STATUS_EM_COTACAO,
        'CONCLUIDA': STATUS_APROVADO,
        'CANCELADA': STATUS_REPROVADO,
    }
    return legado.get(s, s)


def cotacao_aguardando_gestor(cotacao):
    return normalizar_status(cotacao.status) == STATUS_AGUARDANDO_APROVACAO


def sincronizar_status_aprovacao(cotacao):
    """Se todos os itens já foram aprovados, garante status APROVADO no banco."""
    if normalizar_status(cotacao.status) == STATUS_APROVADO:
        return False
    if _concluir_aprovacao_se_completa(cotacao):
        db.session.commit()
        return True
    return False


class AprovacaoItem(db.Model):
    """Fornecedor escolhido pelo gestor para cada produto da cotação."""
    id = db.Column(db.Integer, primary_key=True)
    cotacao_id = db.Column(db.Integer, db.ForeignKey('cotacao.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('item_cotacao.id'), nullable=False)
    fornecedor_id = db.Column(db.Integer, db.ForeignKey('fornecedor.id'), nullable=False)
    aprovado_por_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    data_aprovacao = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship('ItemCotacao', backref=db.backref('aprovacao', uselist=False))
    fornecedor = db.relationship('Fornecedor')
    aprovado_por = db.relationship('Usuario')

    __table_args__ = (
        db.UniqueConstraint('cotacao_id', 'item_id', name='uq_aprovacao_item_por_cotacao'),
    )


def calcular_total_item(item, preco):
    detalhe = detalhar_custo_item(item, preco)
    return detalhe['custo_total'] if detalhe else None


def detalhar_custo_item(item, preco):
    """Retorna custo total do produto (qtd × unitário + impostos − desconto + ST)."""
    if not preco or preco.valor_unitario == 0:
        return None
    subtotal = preco.valor_unitario * item.quantidade
    icms_valor = subtotal * (preco.icms / 100)
    ipi_valor = subtotal * (preco.ipi / 100)
    custo_total = round(subtotal + icms_valor + ipi_valor + preco.st - preco.desconto, 2)
    return {
        'valor_unitario': preco.valor_unitario,
        'quantidade': item.quantidade,
        'subtotal': round(subtotal, 2),
        'icms_percent': preco.icms,
        'ipi_percent': preco.ipi,
        'icms': round(icms_valor, 2),
        'ipi': round(ipi_valor, 2),
        'st': round(preco.st, 2),
        'desconto': round(preco.desconto, 2),
        'custo_total': custo_total,
        'prazo': preco.prazo,
    }


def montar_oferta_aprovacao(item, fornecedor, preco):
    """Dados completos da proposta para exibição ao gestor."""
    detalhe = detalhar_custo_item(item, preco)
    if detalhe is None:
        return None
    return {
        **detalhe,
        'fornecedor_id': fornecedor.id,
        'fornecedor_nome': fornecedor.nome,
        'fornecedor_contato': fornecedor.contato or '',
        'fornecedor_telefone': fornecedor.telefone or '',
        'fornecedor_email': fornecedor.email or '',
        'condicao_pagamento': fornecedor.condicao_pagamento or 'Não informada',
        'frete_total': round(fornecedor.frete_total or 0, 2),
        'item_descricao': item.descricao,
        'item_unidade': item.unidade or 'un',
        'total': detalhe['custo_total'],
    }


def obter_resultado_aprovacao(cotacao):
    """Lista itens com fornecedor vencedor e dados completos da proposta."""
    resultado_vencedores = []
    for item in cotacao.itens:
        aprovacao = AprovacaoItem.query.filter_by(
            cotacao_id=cotacao.id,
            item_id=item.id,
        ).first()
        if not aprovacao:
            continue
        fornecedor = aprovacao.fornecedor
        preco = PrecoFornecedor.query.filter_by(
            fornecedor_id=aprovacao.fornecedor_id,
            item_id=item.id,
        ).first()
        oferta = montar_oferta_aprovacao(item, fornecedor, preco)
        if oferta:
            resultado_vencedores.append({
                'item': item,
                'oferta': oferta,
                'aprovacao': aprovacao,
            })
    total_vencedores = round(
        sum(v['oferta']['custo_total'] for v in resultado_vencedores),
        2,
    )
    return resultado_vencedores, total_vencedores


@app.template_filter('moeda_br')
def moeda_br(valor):
    if valor is None:
        return 'R$ 0,00'
    s = f'{float(valor):,.2f}'
    return 'R$ ' + s.replace(',', 'X').replace('.', ',').replace('X', '.')


@app.template_filter('status_label')
def status_label(status):
    return STATUS_LABELS.get(status, status or '—')


def _gestor_pode_aprovar(cotacao):
    if current_user.perfil != 'gestor':
        flash('Apenas gestores podem aprovar cotações.', 'danger')
        return False
    if not cotacao_aguardando_gestor(cotacao):
        flash('A cotação precisa estar aguardando aprovação.', 'warning')
        return False
    return True


def _concluir_aprovacao_se_completa(cotacao):
    """Define status APROVADO quando todos os produtos tiverem fornecedor escolhido."""
    db.session.flush()
    total_itens = ItemCotacao.query.filter_by(cotacao_id=cotacao.id).count()
    if total_itens == 0:
        return False
    aprovados = AprovacaoItem.query.filter_by(cotacao_id=cotacao.id).count()
    if (
        aprovados >= total_itens
        and cotacao_aguardando_gestor(cotacao)
    ):
        cotacao.status = STATUS_APROVADO
        return True
    return False


def _registrar_aprovacao(cotacao, item_id, fornecedor_id):
    item = ItemCotacao.query.filter_by(id=item_id, cotacao_id=cotacao.id).first()
    if not item:
        return False, 'Produto inválido para esta cotação.'

    fornecedor = Fornecedor.query.filter_by(id=fornecedor_id, cotacao_id=cotacao.id).first()
    if not fornecedor:
        return False, 'Fornecedor inválido para esta cotação.'

    preco = PrecoFornecedor.query.filter_by(fornecedor_id=fornecedor_id, item_id=item_id).first()
    if calcular_total_item(item, preco) is None:
        return False, f'Sem preço válido de {fornecedor.nome} para {item.descricao}.'

    aprovacao = AprovacaoItem.query.filter_by(cotacao_id=cotacao.id, item_id=item_id).first()
    if aprovacao:
        aprovacao.fornecedor_id = fornecedor_id
        aprovacao.aprovado_por_id = current_user.id
        aprovacao.data_aprovacao = datetime.utcnow()
    else:
        db.session.add(AprovacaoItem(
            cotacao_id=cotacao.id,
            item_id=item_id,
            fornecedor_id=fornecedor_id,
            aprovado_por_id=current_user.id
        ))
    return True, None

# =====================================
# CARREGAMENTO DE USUÁRIO
# =====================================

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# =====================================
# ROTAS PRINCIPAIS
# =====================================

@app.route('/')
def inicio():
    if current_user.is_authenticated:
        return redirect(url_for('painel'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('painel'))

    if request.method == 'POST':
        usuario = request.form.get('usuario')
        senha = request.form.get('senha')
        usuario_db = Usuario.query.filter_by(usuario=usuario).first()

        if usuario_db and check_password_hash(usuario_db.senha, senha):
            login_user(usuario_db)
            return redirect(url_for('painel'))
        
        flash('Usuário ou senha inválidos', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/painel')
@login_required
def painel():
    busca = (request.args.get('q') or '').strip()
    status = (request.args.get('status') or '').strip().upper()
    data_inicio_raw = (request.args.get('data_inicio') or '').strip()
    data_fim_raw = (request.args.get('data_fim') or '').strip()

    query = Cotacao.query

    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            or_(
                Cotacao.numero.ilike(termo),
                Cotacao.solicitante.ilike(termo),
                Cotacao.finalidade.ilike(termo),
            )
        )

    status_validos = {
        STATUS_EM_COTACAO,
        STATUS_AGUARDANDO_APROVACAO,
        STATUS_APROVADO,
        STATUS_REPROVADO,
    }
    if status in status_validos:
        query = query.filter(Cotacao.status == status)
    else:
        status = ''

    data_inicio = None
    data_fim = None
    try:
        if data_inicio_raw:
            data_inicio = datetime.strptime(data_inicio_raw, '%Y-%m-%d')
            query = query.filter(Cotacao.data_criacao >= data_inicio)
    except ValueError:
        data_inicio_raw = ''
        flash('Data inicial inválida. Use o formato correto.', 'warning')

    try:
        if data_fim_raw:
            data_fim = datetime.strptime(data_fim_raw, '%Y-%m-%d')
            fim_dia = data_fim.replace(hour=23, minute=59, second=59, microsecond=999999)
            query = query.filter(Cotacao.data_criacao <= fim_dia)
    except ValueError:
        data_fim_raw = ''
        flash('Data final inválida. Use o formato correto.', 'warning')

    if data_inicio and data_fim and data_inicio > data_fim:
        flash('A data inicial não pode ser maior que a data final.', 'warning')
        return redirect(url_for(
            'painel',
            q=busca,
            status=status,
            data_inicio=data_inicio_raw,
            data_fim='',
        ))

    cotacoes = query.order_by(Cotacao.id.desc()).all()
    return render_template(
        'painel.html',
        usuario=current_user,
        cotacoes=cotacoes,
        filtro_q=busca,
        filtro_status=status,
        filtro_data_inicio=data_inicio_raw,
        filtro_data_fim=data_fim_raw,
    )


def _exigir_gestor():
    if current_user.perfil != 'gestor':
        flash('Apenas gestores podem acessar esta área.', 'danger')
        return False
    return True


PERFIS_USUARIO = ('gestor', 'comprador')


# =====================================
# GERENCIAMENTO DE USUÁRIOS
# =====================================

@app.route('/usuarios')
@login_required
def listar_usuarios():
    if not _exigir_gestor():
        return redirect(url_for('painel'))
    usuarios = Usuario.query.order_by(Usuario.usuario).all()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_required
def novo_usuario():
    if not _exigir_gestor():
        return redirect(url_for('painel'))

    if request.method == 'POST':
        login = (request.form.get('usuario') or '').strip()
        senha = request.form.get('senha') or ''
        perfil = request.form.get('perfil')

        if not login:
            flash('Informe o nome de usuário.', 'warning')
            return redirect(url_for('novo_usuario'))
        if len(senha) < 4:
            flash('A senha deve ter pelo menos 4 caracteres.', 'warning')
            return redirect(url_for('novo_usuario'))
        if perfil not in PERFIS_USUARIO:
            flash('Perfil inválido.', 'warning')
            return redirect(url_for('novo_usuario'))
        if Usuario.query.filter_by(usuario=login).first():
            flash('Este nome de usuário já está em uso.', 'warning')
            return redirect(url_for('novo_usuario'))

        db.session.add(Usuario(
            usuario=login,
            senha=generate_password_hash(senha),
            perfil=perfil,
        ))
        db.session.commit()
        flash(f'Usuário "{login}" criado com sucesso.', 'success')
        return redirect(url_for('listar_usuarios'))

    return render_template('usuario_form.html', usuario_edit=None)


@app.route('/usuarios/<int:user_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_usuario(user_id):
    if not _exigir_gestor():
        return redirect(url_for('painel'))

    usuario_edit = Usuario.query.get_or_404(user_id)

    if request.method == 'POST':
        login = (request.form.get('usuario') or '').strip()
        senha = request.form.get('senha') or ''
        perfil = request.form.get('perfil')

        if not login:
            flash('Informe o nome de usuário.', 'warning')
            return redirect(url_for('editar_usuario', user_id=user_id))
        if perfil not in PERFIS_USUARIO:
            flash('Perfil inválido.', 'warning')
            return redirect(url_for('editar_usuario', user_id=user_id))

        outro = Usuario.query.filter(
            Usuario.usuario == login,
            Usuario.id != user_id
        ).first()
        if outro:
            flash('Este nome de usuário já está em uso.', 'warning')
            return redirect(url_for('editar_usuario', user_id=user_id))

        if senha and len(senha) < 4:
            flash('A nova senha deve ter pelo menos 4 caracteres.', 'warning')
            return redirect(url_for('editar_usuario', user_id=user_id))

        if usuario_edit.id == current_user.id and perfil != 'gestor':
            flash('Você não pode remover seu próprio perfil de gestor.', 'warning')
            return redirect(url_for('editar_usuario', user_id=user_id))

        usuario_edit.usuario = login
        usuario_edit.perfil = perfil
        if senha:
            usuario_edit.senha = generate_password_hash(senha)

        db.session.commit()
        flash(f'Usuário "{login}" atualizado.', 'success')
        return redirect(url_for('listar_usuarios'))

    return render_template('usuario_form.html', usuario_edit=usuario_edit)


@app.route('/usuarios/<int:user_id>/excluir', methods=['POST'])
@login_required
def excluir_usuario(user_id):
    if not _exigir_gestor():
        return redirect(url_for('painel'))

    usuario_del = Usuario.query.get_or_404(user_id)

    if usuario_del.id == current_user.id:
        flash('Você não pode excluir seu próprio usuário.', 'warning')
        return redirect(url_for('listar_usuarios'))

    if usuario_del.perfil == 'gestor':
        total_gestores = Usuario.query.filter_by(perfil='gestor').count()
        if total_gestores <= 1:
            flash('Não é possível excluir o único gestor do sistema.', 'warning')
            return redirect(url_for('listar_usuarios'))

    nome = usuario_del.usuario
    db.session.delete(usuario_del)
    db.session.commit()
    flash(f'Usuário "{nome}" excluído.', 'info')
    return redirect(url_for('listar_usuarios'))


# =====================================
# GERENCIAMENTO DE COTAÇÕES
# =====================================

@app.route('/nova-cotacao', methods=['GET', 'POST'])
@login_required
def nova_cotacao():
    if request.method == 'POST':
        ultima = Cotacao.query.order_by(Cotacao.id.desc()).first()
        numero = f"COT-{ultima.id + 1:04d}" if ultima else "COT-0001"

        nova = Cotacao(
            numero=numero,
            solicitante=request.form.get('solicitante'),
            finalidade=request.form.get('finalidade'),
            observacoes=request.form.get('observacoes'),
            status=STATUS_EM_COTACAO,
        )

        try:
            db.session.add(nova)
            db.session.commit()
            flash(f'Cotação {numero} criada com sucesso!', 'success')
            return redirect(url_for('visualizar_cotacao', cotacao_id=nova.id))
        except Exception as e:
            db.session.rollback()
            flash('Erro ao criar cotação. Tente novamente.', 'danger')

    return render_template('nova_cotacao.html')

@app.route('/cotacao/<int:cotacao_id>')
@login_required
def visualizar_cotacao(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    resultado_vencedores, total_vencedores = obter_resultado_aprovacao(cotacao)
    return render_template(
        'visualizar_cotacao.html',
        cotacao=cotacao,
        resultado_vencedores=resultado_vencedores,
        total_vencedores=total_vencedores,
        itens_aprovados=len(resultado_vencedores),
        total_itens=len(cotacao.itens),
    )

@app.route('/cotacao/<int:cotacao_id>/enviar-aprovacao', methods=['POST'])
@login_required
def enviar_aprovacao(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if cotacao.status != STATUS_EM_COTACAO:
        flash('Somente cotações em cotação podem ser enviadas para aprovação.', 'warning')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
    if not cotacao.itens:
        flash('Adicione pelo menos um item antes de enviar para aprovação.', 'warning')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    cotacao.status = STATUS_AGUARDANDO_APROVACAO
    db.session.commit()
    flash('Cotação enviada para aprovação. Nenhuma alteração é permitida nesta etapa.', 'success')
    return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/reabrir', methods=['POST'])
@login_required
def reabrir_cotacao(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if cotacao.status != STATUS_AGUARDANDO_APROVACAO:
        flash('Somente cotações aguardando aprovação podem ser reabertas.', 'warning')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    AprovacaoItem.query.filter_by(cotacao_id=cotacao.id).delete()
    cotacao.status = STATUS_EM_COTACAO
    db.session.commit()
    flash('Cotação reaberta para edição (status: Em cotação).', 'info')
    return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/retornar-aguardando', methods=['POST'])
@login_required
def retornar_para_aguardando(cotacao_id):

    cotacao = Cotacao.query.get_or_404(cotacao_id)

    # =====================================
    # SOMENTE GESTOR
    # =====================================

    if current_user.perfil != 'gestor':
        flash('Apenas gestores podem alterar este status.', 'danger')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    # =====================================
    # SOMENTE APROVADAS
    # =====================================

    status_atual = normalizar_status(cotacao.status)

    if status_atual not in [STATUS_APROVADO, STATUS_REPROVADO]:

        flash(
            f'Esta cotação não está aprovada. Status atual: {status_atual}',
            'warning'
        )

        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    # =====================================
    # REMOVE APROVAÇÕES
    # =====================================

    AprovacaoItem.query.filter_by(
        cotacao_id=cotacao.id
    ).delete()

    # =====================================
    # VOLTA STATUS
    # =====================================

    cotacao.status = STATUS_AGUARDANDO_APROVACAO

    db.session.commit()

    flash(
        'Cotação retornada para aguardando aprovação.',
        'success'
    )

    return redirect(url_for('comparativo', cotacao_id=cotacao.id))


# =====================================
# APROVAÇÃO DA COTAÇÃO
# =====================================

@app.route('/cotacao/<int:cotacao_id>/aprovar-item', methods=['POST'])
@login_required
def aprovar_item(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if not _gestor_pode_aprovar(cotacao):
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    try:
        item_id = int(request.form.get('item_id'))
        fornecedor_id = int(request.form.get('fornecedor_id'))
    except (TypeError, ValueError):
        flash('Seleção de produto ou fornecedor inválida.', 'danger')
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    ok, msg = _registrar_aprovacao(cotacao, item_id, fornecedor_id)
    if not ok:
        flash(msg, 'warning')
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    db.session.flush()
    if _concluir_aprovacao_se_completa(cotacao):
        db.session.commit()
        flash('Todos os produtos foram aprovados. Cotação concluída com status Aprovado.', 'success')
    else:
        db.session.commit()
        flash('Produto aprovado com sucesso.', 'success')
    return redirect(url_for('comparativo', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/aprovar-lote', methods=['POST'])
@login_required
def aprovar_lote(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if not _gestor_pode_aprovar(cotacao):
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    selecoes = request.form.getlist('selecao')
    if not selecoes:
        flash('Selecione ao menos um produto com fornecedor para aprovar em lote.', 'warning')
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    aprovados = 0
    erros = []
    for sel in selecoes:
        try:
            item_id, fornecedor_id = map(int, sel.split(':'))
        except ValueError:
            erros.append('Seleção inválida no lote.')
            continue
        ok, msg = _registrar_aprovacao(cotacao, item_id, fornecedor_id)
        if ok:
            aprovados += 1
        elif msg:
            erros.append(msg)

    db.session.flush()
    concluiu = _concluir_aprovacao_se_completa(cotacao)
    db.session.commit()
    if concluiu:
        flash('Todos os produtos foram aprovados. Cotação concluída com status Aprovado.', 'success')
    elif aprovados:
        flash(f'{aprovados} produto(s) aprovado(s) em lote.', 'success')
    for e in erros[:3]:
        flash(e, 'warning')
    if len(erros) > 3:
        flash(f'Mais {len(erros) - 3} item(ns) não puderam ser aprovados.', 'warning')
    return redirect(url_for('comparativo', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/desaprovar-item', methods=['POST'])
@login_required
def desaprovar_item(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if not _gestor_pode_aprovar(cotacao):
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    try:
        item_id = int(request.form.get('item_id'))
    except (TypeError, ValueError):
        flash('Produto inválido.', 'danger')
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    aprovacao = AprovacaoItem.query.filter_by(cotacao_id=cotacao.id, item_id=item_id).first()
    if aprovacao:
        db.session.delete(aprovacao)
        db.session.commit()
        flash('Aprovação do produto removida.', 'info')
    return redirect(url_for('comparativo', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/aprovar', methods=['POST'])
@login_required
def aprovar_cotacao(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if not _gestor_pode_aprovar(cotacao):
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    db.session.flush()
    total_itens = ItemCotacao.query.filter_by(cotacao_id=cotacao.id).count()
    aprovados = AprovacaoItem.query.filter_by(cotacao_id=cotacao.id).count()
    if total_itens == 0:
        flash('A cotação não possui produtos para aprovar.', 'warning')
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))
    if aprovados < total_itens:
        flash(
            f'Aprove todos os produtos antes de concluir ({aprovados}/{total_itens} aprovados).',
            'warning'
        )
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    cotacao.status = STATUS_APROVADO
    db.session.commit()
    flash('Cotação aprovada pelo gestor!', 'success')
    return redirect(url_for('comparativo', cotacao_id=cotacao.id))


@app.route('/cotacao/<int:cotacao_id>/reprovar', methods=['POST'])
@login_required
def reprovar_cotacao(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if not _gestor_pode_aprovar(cotacao):
        return redirect(url_for('comparativo', cotacao_id=cotacao.id))

    AprovacaoItem.query.filter_by(cotacao_id=cotacao.id).delete()
    cotacao.status = STATUS_REPROVADO
    db.session.commit()
    flash('Cotação reprovada pelo gestor.', 'warning')
    return redirect(url_for('comparativo', cotacao_id=cotacao.id))

@app.route('/cotacao/<int:cotacao_id>/excluir', methods=['POST'])
@login_required
def excluir_cotacao(cotacao_id):

    cotacao = Cotacao.query.get_or_404(cotacao_id)

    try:

        # =====================================
        # EXCLUIR APROVAÇÕES
        # =====================================

        aprovacoes = AprovacaoItem.query.filter_by(
            cotacao_id=cotacao.id
        ).all()

        for aprovacao in aprovacoes:
            db.session.delete(aprovacao)

        # =====================================
        # EXCLUIR PREÇOS
        # =====================================

        for fornecedor in cotacao.fornecedores:

            precos = PrecoFornecedor.query.filter_by(
                fornecedor_id=fornecedor.id
            ).all()

            for preco in precos:
                db.session.delete(preco)

        # =====================================
        # EXCLUIR FORNECEDORES
        # =====================================

        for fornecedor in cotacao.fornecedores:
            db.session.delete(fornecedor)

        # =====================================
        # EXCLUIR ITENS
        # =====================================

        for item in cotacao.itens:
            db.session.delete(item)

        # =====================================
        # EXCLUIR COTAÇÃO
        # =====================================

        db.session.delete(cotacao)

        # =====================================
        # SALVAR
        # =====================================

        db.session.commit()

        flash('Cotação excluída com sucesso.', 'success')

    except Exception as e:

        db.session.rollback()

        print(f'ERRO AO EXCLUIR COTAÇÃO: {e}')

        flash('Erro ao excluir cotação.', 'danger')

    return redirect(url_for('painel'))

# =====================================
# ITENS E FORNECEDORES
# =====================================

@app.route('/cotacao/<int:cotacao_id>/item', methods=['GET', 'POST'])
@login_required
def adicionar_item(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if cotacao_bloqueada(cotacao):
        flash('Esta cotação está bloqueada para alterações.', 'danger')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
        
    if request.method == 'POST':
        item = ItemCotacao(
            cotacao_id=cotacao.id,
            descricao=request.form.get('descricao'),
            unidade=request.form.get('unidade'),
            quantidade=float(request.form.get('quantidade', 1))
        )
        db.session.add(item)
        db.session.commit()
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
    return render_template('adicionar_item.html', cotacao=cotacao)

@app.route('/item/<int:item_id>/excluir', methods=['POST'])
@login_required
def excluir_item(item_id):
    item = ItemCotacao.query.get_or_404(item_id)
    cotacao = item.cotacao
    if cotacao_bloqueada(cotacao):
        flash('Esta cotação está bloqueada para alterações.', 'danger')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
        
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

@app.route('/cotacao/<int:cotacao_id>/fornecedores', methods=['GET', 'POST'])
@login_required
def fornecedores(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    if cotacao_bloqueada(cotacao):
        if request.method == 'POST':
            flash('Esta cotação está bloqueada para alterações.', 'danger')
            return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
            
    if request.method == 'POST':
        fornecedor = Fornecedor(
            cotacao_id=cotacao.id,
            nome=request.form.get('nome'),
            contato=request.form.get('contato'),
            telefone=request.form.get('telefone'),
            email=request.form.get('email')
        )
        db.session.add(fornecedor)
        db.session.commit()
        return redirect(url_for('fornecedores', cotacao_id=cotacao.id))
    return render_template('fornecedores.html', cotacao=cotacao)

@app.route('/fornecedor/<int:fornecedor_id>/excluir', methods=['POST'])
@login_required
def excluir_fornecedor(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    cotacao = fornecedor.cotacao
    if cotacao_bloqueada(cotacao):
        flash('Esta cotação está bloqueada para alterações.', 'danger')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))
        
    db.session.delete(fornecedor)
    db.session.commit()
    return redirect(url_for('fornecedores', cotacao_id=cotacao.id))

@app.route('/fornecedor/<int:fornecedor_id>/precos', methods=['GET', 'POST'])
@login_required
def lancar_precos(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    cotacao = fornecedor.cotacao

    if cotacao_bloqueada(cotacao):
        if request.method == 'POST':
            flash('Esta cotação está bloqueada para alterações.', 'danger')
            return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    if request.method == 'POST':
        fornecedor.frete_total = float(request.form.get('frete_total', 0) or 0)
        fornecedor.condicao_pagamento = request.form.get('condicao_pagamento')

        for item in cotacao.itens:
            preco = PrecoFornecedor.query.filter_by(
                fornecedor_id=fornecedor.id,
                item_id=item.id
            ).first()

            if not preco:
                preco = PrecoFornecedor(fornecedor_id=fornecedor.id, item_id=item.id)
                db.session.add(preco)

            preco.valor_unitario = float(request.form.get(f'valor_{item.id}', 0) or 0)
            preco.icms = float(request.form.get(f'icms_{item.id}', 0) or 0)
            preco.ipi = float(request.form.get(f'ipi_{item.id}', 0) or 0)
            preco.st = float(request.form.get(f'st_{item.id}', 0) or 0)
            preco.desconto = float(request.form.get(f'desconto_{item.id}', 0) or 0)
            preco.prazo = int(request.form.get(f'prazo_{item.id}', 0) or 0)

        db.session.commit()
        flash(f'Preços atualizados para {fornecedor.nome}', 'success')
        return redirect(url_for('visualizar_cotacao', cotacao_id=cotacao.id))

    return render_template('lancar_precos.html', fornecedor=fornecedor, cotacao=cotacao)

# =====================================
# COMPARATIVO E CÁLCULOS
# =====================================

@app.route('/cotacao/<int:cotacao_id>/comparativo')
@login_required
def comparativo(cotacao_id):
    cotacao = Cotacao.query.get_or_404(cotacao_id)
    sincronizar_status_aprovacao(cotacao)
    db.session.refresh(cotacao)
    fornecedores_blocos = []
    aprovacoes_map = {
        a.item_id: a
        for a in AprovacaoItem.query.filter_by(cotacao_id=cotacao.id).all()
    }
    matriz_aprovacao = []
    resumo_aprovados = []

    for fornecedor in cotacao.fornecedores:
        itens_lista = []
        total_geral_fornecedor = 0

        for item in cotacao.itens:
            preco = PrecoFornecedor.query.filter_by(
                fornecedor_id=fornecedor.id,
                item_id=item.id
            ).first()

            total_item = calcular_total_item(item, preco)
            if total_item is None:
                continue

            total_geral_fornecedor += total_item
            subtotal = preco.valor_unitario * item.quantidade
            icms_valor = subtotal * (preco.icms / 100)
            ipi_valor = subtotal * (preco.ipi / 100)

            itens_lista.append({
                "item_id": item.id,
                "produto": item.descricao,
                "unidade": item.unidade,
                "quantidade": item.quantidade,
                "valor_unitario": preco.valor_unitario,
                "icms": round(icms_valor, 2),
                "ipi": round(ipi_valor, 2),
                "st": round(preco.st, 2),
                "desconto": round(preco.desconto, 2),
                "total": total_item,
                "prazo": preco.prazo,
                "aprovado": (
                    aprovacoes_map.get(item.id)
                    and aprovacoes_map[item.id].fornecedor_id == fornecedor.id
                ),
            })

        if itens_lista:
            total_com_frete = total_geral_fornecedor + fornecedor.frete_total
            fornecedores_blocos.append({
                "fornecedor": fornecedor,
                "itens": itens_lista,
                "subtotal_itens": round(total_geral_fornecedor, 2),
                "frete": fornecedor.frete_total,
                "total_geral": round(total_com_frete, 2)
            })

    fornecedores_blocos.sort(key=lambda x: x['total_geral'])

    for item in cotacao.itens:
        ofertas = []
        for fornecedor in cotacao.fornecedores:
            preco = PrecoFornecedor.query.filter_by(
                fornecedor_id=fornecedor.id,
                item_id=item.id
            ).first()
            oferta = montar_oferta_aprovacao(item, fornecedor, preco)
            if oferta is not None:
                ofertas.append(oferta)

        ofertas.sort(key=lambda o: o['total'])
        aprovacao = aprovacoes_map.get(item.id)
        linha = {
            'item': item,
            'ofertas': ofertas,
            'aprovacao': aprovacao,
            'melhor_fornecedor_id': ofertas[0]['fornecedor_id'] if ofertas else None,
        }
        matriz_aprovacao.append(linha)

        if aprovacao:
            preco_ap = PrecoFornecedor.query.filter_by(
                fornecedor_id=aprovacao.fornecedor_id,
                item_id=item.id
            ).first()
            resumo_aprovados.append({
                'item': item,
                'fornecedor': aprovacao.fornecedor,
                'total': calcular_total_item(item, preco_ap) or 0,
            })

    total_itens = len(cotacao.itens)
    itens_aprovados = len(aprovacoes_map)
    pode_finalizar = total_itens > 0 and itens_aprovados == total_itens

    resultado_vencedores, total_vencedores = obter_resultado_aprovacao(cotacao)
    matriz_pendentes = [linha for linha in matriz_aprovacao if not linha['aprovacao']]

    return render_template(
        "comparativo.html",
        cotacao=cotacao,
        fornecedores_blocos=fornecedores_blocos,
        matriz_aprovacao=matriz_aprovacao,
        matriz_pendentes=matriz_pendentes,
        resultado_vencedores=resultado_vencedores,
        resumo_aprovados=resumo_aprovados,
        total_itens=total_itens,
        itens_aprovados=itens_aprovados,
        pode_finalizar=pode_finalizar,
        total_vencedores=total_vencedores,
    )

# =====================================
# INICIALIZAÇÃO DO BANCO
# =====================================

def migrar_status_antigos():
    """Converte status legados para o fluxo atual."""
    mapa = {
        'PENDENTE': STATUS_EM_COTACAO,
        'EM ANALISE': STATUS_EM_COTACAO,
        'CONCLUIDA': STATUS_APROVADO,
        'CANCELADA': STATUS_REPROVADO,
    }
    for cotacao in Cotacao.query.all():
        if cotacao.status in mapa:
            cotacao.status = mapa[cotacao.status]
    db.session.commit()


def criar_banco():
    with app.app_context():
        db.create_all()
        migrar_status_antigos()
        # Cria usuários padrão se não existirem
        if not Usuario.query.filter_by(usuario='admin').first():
            db.session.add(Usuario(
                usuario='admin',
                senha=generate_password_hash('admin123'),
                perfil='gestor'
            ))
            db.session.add(Usuario(
                usuario='compras',
                senha=generate_password_hash('compras123'),
                perfil='comprador'
            ))
            db.session.commit()

def init_app():
    with app.app_context():
        criar_banco()


init_app()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))