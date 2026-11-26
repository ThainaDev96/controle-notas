from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from aluno.models import Disciplina, Nota, Turma, Matricula, Avaliacao, NotaAvaliacao
from aluno.calculo_notas import NOTA_MAXIMA, calcular_nota_final, classificar, montar_resumo_formula, calcular_pos_exame, _fmt_pt
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password, password_validators_help_texts
from django.core.exceptions import ValidationError
from django.utils import translation
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)

            if user.is_superuser:
                return redirect("admin:index")

            grupos = user.groups.values_list("name", flat=True)
            if "administrador" in grupos:
                return redirect("dashboard-admin")

            if "professor" in grupos:
                return redirect("professor-dashboard")

            return redirect("minhas-notas")

        messages.error(request, "Usuário ou senha inválidos")

    return render(request, "aluno/login.html")

def logout_view(request):
    logout(request)
    return render(request, "aluno/login.html")

def listar_disciplinas(request):
    disciplinas = Disciplina.objects.filter(ativo=True)
    return render(request, 'aluno/lista.html', {'disciplinas': disciplinas})

def _exige_professor(view):
    """Telas só do professor logado (mesma checagem do dashboard_admin)."""
    @wraps(view)
    def view_do_professor(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.groups.filter(name='professor').exists():
            return HttpResponseForbidden("Acesso restrito ao professor.")
        return view(request, *args, **kwargs)
    return view_do_professor

@_exige_professor
def professor_dashboard(request):
    """Página inicial do professor: só as boas-vindas; o trabalho fica nas telas do menu."""
    return render(request, "professor/dashboard.html")

def _avaliacoes_da_turma(turma):
    # A avaliação é da disciplina; a turma herda as da disciplina dela.
    return Avaliacao.objects.filter(disciplina_id=turma.disciplina_id)

SITUACAO_TEXTO = {
    'cursando': 'Cursando',
    'aprovado': 'Aprovado',
    'exame': 'Exame',
    'reprovado': 'Reprovado',
}

def _situacao_texto(nota):
    """Situação como as tabelas de notas mostram."""
    return SITUACAO_TEXTO.get(nota.situacao, 'Cursando')

def _exame_aberto(nota):
    """O exame só é lançado para quem está em exame e ainda não fez."""
    return nota.situacao == 'exame' and nota.nota_exame is None

def _linhas_tabela_notas(turma, avaliacoes):
    """
    Uma linha por aluno ativo da turma para a tabela de notas, a mesma na Lista e no Cadastro de Notas.
    Matrícula sem Nota ainda = nenhuma nota lançada, igual a "cursando".
    """
    alunos = User.objects.filter(
        matricula__turma_id=turma.id, matricula__turma__ativo=True, matricula__ativo=True
    ).exclude(groups__name='professor').distinct().order_by('first_name')

    matriculas = {
        m.aluno_id: m
        for m in Matricula.objects.filter(turma=turma, ativo=True)
    }
    resumos = {
        n.matricula_id: n
        for n in Nota.objects.filter(matricula__in=matriculas.values())
    }
    # Por aluno e só as lançadas: mesmo com Nota ou Matrícula duplicada, a nota lançada não se perde.
    notas_lancadas = {
        (na.nota.matricula.aluno_id, na.avaliacao_id): na.nota_obtida
        for na in NotaAvaliacao.objects.filter(
            nota__matricula__turma=turma,
            nota__matricula__ativo=True,
            avaliacao__in=avaliacoes,
            nota_obtida__isnull=False,
        ).select_related('nota__matricula')
    }

    linhas = []
    for aluno in alunos:
        resumo = resumos.get(matriculas[aluno.id].id)
        linhas.append({
            'aluno': aluno,
            'notas': [
                {'avaliacao': av, 'valor': notas_lancadas.get((aluno.id, av.id))}
                for av in avaliacoes
            ],
            'nota_id': resumo.id if resumo else None,
            'media_final': resumo.media_final if resumo else None,
            'situacao_display': _situacao_texto(resumo) if resumo else 'Cursando',
            'nota_exame': resumo.nota_exame if resumo else None,
            'exame_aberto': bool(resumo) and _exame_aberto(resumo),
        })
    return linhas

def _recalcular_media_disciplina(matricula):
    turma = matricula.turma
    disciplina = turma.disciplina
    nota, _ = Nota.objects.get_or_create(
        matricula=matricula, defaults={'situacao': 'cursando'}
    )
    # Sempre na mesma ordem: a soma de números quebrados muda conforme a ordem, e o arredondamento junto.
    avaliacoes = list(_avaliacoes_da_turma(turma).order_by('id'))
    notas_lancadas = {
        na.avaliacao_id: na.nota_obtida
        for na in NotaAvaliacao.objects.filter(nota=nota, avaliacao__in=avaliacoes)
        if na.nota_obtida is not None
    }

    if avaliacoes and len(notas_lancadas) == len(avaliacoes):
        notas_avaliacao = [(av, notas_lancadas[av.id]) for av in avaliacoes]
        nota.media_final = calcular_nota_final(disciplina, notas_avaliacao)
        nota.situacao = classificar(nota.media_final)
        # O exame lançado nunca é apagado aqui. Quem já fez o exame não volta para a situação das notas: ele decide.
        if nota.nota_exame is not None and nota.media_final is not None:
            _, nota.situacao = calcular_pos_exame(nota.media_final, nota.nota_exame)
    else:
        nota.media_final = None
        nota.situacao = 'cursando'

    nota.save()
    return nota

def lista_notas(request):
    if request.GET.get('limpar'):
        request.session.pop('filtro_notas_disciplina', None)
        request.session.pop('filtro_notas_turma', None)
        return redirect('lista-notas')

    if request.method == 'POST':
        request.session['filtro_notas_disciplina'] = request.POST.get('disciplina', '')
        request.session['filtro_notas_turma'] = request.POST.get('turma', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('lista-notas')

    disciplina_id = request.session.get('filtro_notas_disciplina', '')
    turma_id      = request.session.get('filtro_notas_turma', '')

    disciplinas = Disciplina.objects.filter(ativo=True)
    turmas = Turma.objects.filter(disciplina_id=disciplina_id, ativo=True).order_by('-ano', 'nome') if disciplina_id else Turma.objects.none()

    avaliacoes = []
    alunos_notas = []
    sem_avaliacoes = False

    turma_obj = Turma.objects.select_related('disciplina').filter(id=turma_id, disciplina_id=disciplina_id).first() if disciplina_id and turma_id else None

    if turma_obj:
        avaliacoes = list(_avaliacoes_da_turma(turma_obj).order_by('id'))
        sem_avaliacoes = len(avaliacoes) == 0

        if not sem_avaliacoes:
            # Garante um registro Nota pra cada aluno matriculado, mesmo sem nenhuma
            # nota lançada ainda, pra editar/excluir aparecerem em todas as linhas.
            for matricula in Matricula.objects.filter(
                turma=turma_obj, turma__ativo=True, ativo=True, nota__isnull=True
            ).exclude(aluno__groups__name='professor'):
                Nota.objects.get_or_create(matricula=matricula, defaults={'situacao': 'cursando'})

            alunos_notas = _linhas_tabela_notas(turma_obj, avaliacoes)

    return render(request, "professor/lista_notas.html", {
        "disciplinas":            disciplinas,
        "turmas":                 turmas,
        "avaliacoes":             avaliacoes,
        "alunos_notas":           alunos_notas,
        "sem_avaliacoes":         sem_avaliacoes,
        "turma_obj":              turma_obj,
        "disciplina_selecionada": disciplina_id,
        "turma_selecionada":      turma_id,
    })

def _notas_do_aluno(aluno, ano=None):
    notas = Nota.objects.filter(
        matricula__aluno=aluno, matricula__ativo=True
    ).select_related('matricula__turma__disciplina')
    if ano:
        notas = notas.filter(matricula__turma__ano=ano)
    return notas

def _turmas_sem_nota(aluno, ano=None):
    """
    Turmas do aluno ainda sem registro de Nota: nenhuma nota lançada, igual a "cursando".
    Disciplina sem avaliação fica de fora, porque não há o que lançar.
    """
    turmas = Turma.objects.filter(
        matricula__aluno=aluno, matricula__ativo=True, ativo=True,
        disciplina__ativo=True, disciplina__avaliacao__isnull=False,
    )
    if ano:
        turmas = turmas.filter(ano=ano)
    com_nota = Nota.objects.filter(matricula__aluno=aluno, matricula__ativo=True).values('matricula__turma_id')
    return list(turmas.exclude(id__in=com_nota).select_related('disciplina').order_by('disciplina__nome').distinct())

def _resumos_do_calculo(notas):
    """A conta da média de cada disciplina que já tem MF, passo a passo, para o PDF do boletim."""
    resumos = []
    for nota in notas:
        if nota.media_final is None:
            continue
        avaliacoes = _avaliacoes_da_turma(nota.turma).order_by('id')
        notas_lancadas = {
            na.avaliacao_id: na.nota_obtida
            for na in NotaAvaliacao.objects.filter(nota=nota, avaliacao__in=avaliacoes)
        }
        notas_avaliacao = [(av, notas_lancadas.get(av.id)) for av in avaliacoes]
        resumo = montar_resumo_formula(nota.disciplina, notas_avaliacao)
        if resumo['tem_dados']:
            resumo['disciplina_nome'] = nota.disciplina.nome
            resumos.append(resumo)
    return resumos

# Regra de aprovação como sai no PDF do boletim. Sem os sinais de maior-ou-igual e de seta,
# que a letra do PDF não tem.
REGRA_DE_APROVACAO_PDF = [
    "Nota final a partir de 7,0: Aprovado",
    "Nota final de 4,0 a 6,99: vai para exame",
    "Nota final abaixo de 4,0: Reprovado",
    "Após o exame: A2 = (nota final + nota do exame) ÷ 2. A2 a partir de 6,0 aprova; abaixo disso, reprova",
]

def boletim_aluno(request):
    if request.GET.get('limpar'):
        request.session.pop('boletim_ano', None)
        return redirect('minhas-notas')

    if request.method == 'POST':
        request.session['boletim_ano'] = request.POST.get('ano', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('minhas-notas')

    ano_selecionado = request.session.get('boletim_ano', str(datetime.now().year))

    notas = _notas_do_aluno(request.user, ano_selecionado)
    turmas_sem_nota = _turmas_sem_nota(request.user, ano_selecionado)

    turmas_aluno = Turma.objects.filter(matricula__aluno=request.user, matricula__ativo=True)

    # O ano atual sempre aparece, mesmo para aluno ainda sem turma.
    anos = sorted(set(turmas_aluno.values_list('ano', flat=True)) | {datetime.now().year}, reverse=True)

    total_aprovadas = notas.filter(situacao='aprovado').count()
    total_exame = notas.filter(situacao='exame').count()
    reprovadas = notas.filter(situacao='reprovado').count()
    total_cursando = notas.filter(situacao='cursando').count() + len(turmas_sem_nota)

    todas_lancadas = total_cursando == 0
    if not todas_lancadas or notas.count() == 0 or total_exame > 0:
        situacao_final = 'em_andamento'
    else:
        situacao_final = 'reprovado' if reprovadas > 2 else 'aprovado'

    if ano_selecionado:
        turmas_aluno = turmas_aluno.filter(ano=ano_selecionado)
    turma_aluno = turmas_aluno.values_list('nome', flat=True).first()

    # O resumo do cálculo e a regra de aprovação não ficam na tela: saem no PDF do Download.
    return render(request, "aluno/minhas_notas.html", {
        "notas":           notas,
        "turmas_sem_nota": turmas_sem_nota,
        "anos":            anos,
        "ano_selecionado": ano_selecionado,
        "situacao_final":  situacao_final,
        "ano_atual":       datetime.now().year,
        "total_aprovadas":   total_aprovadas,
        "total_exame":       total_exame,
        "total_reprovadas":  reprovadas,
        "total_cursando":    total_cursando,
        "turma_aluno":       turma_aluno,
    })

def deletar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    nota.delete()
    messages.success(request, "Notas do aluno excluídas com sucesso!")
    return redirect("lista-notas")

# A nota vai de 0 a 10 com no máximo duas casas depois da vírgula (8, 8,5 ou 8,75; nunca 1,2222222).
CASAS_DA_NOTA = 2
ERRO_CASAS_DA_NOTA = f"A nota pode ter no máximo {CASAS_DA_NOTA} casas depois da vírgula."

def _casas_demais(texto):
    """O número digitado (já com ponto no lugar da vírgula) tem mais casas decimais do que a nota aceita."""
    return '.' in texto and len(texto.split('.', 1)[1].rstrip('0')) > CASAS_DA_NOTA

def _nota_no_formulario(valor):
    """Nota como o formulário de edição mostra: até duas casas, sem zero sobrando (1.9, e não 1.90)."""
    if valor is None:
        return ''
    # Arredonda como na escola (6,125 vira 6,13), e não para o par mais próximo.
    arredondada = Decimal(str(valor)).quantize(Decimal(1).scaleb(-CASAS_DA_NOTA), rounding=ROUND_HALF_UP)
    texto = f"{arredondada:f}"
    return texto.rstrip('0').rstrip('.') if '.' in texto else texto

def _mesma_nota(digitada, gravada):
    """O formulário mostra a nota com até 2 casas: diferença menor que isso não é mudança."""
    if digitada is None or gravada is None:
        return digitada is None and gravada is None
    return abs(digitada - gravada) < 0.0051

def editar_nota(request, id):
    nota = get_object_or_404(Nota.objects.select_related('matricula__turma__disciplina', 'matricula__aluno'), id=id)
    avaliacoes = _avaliacoes_da_turma(nota.turma).order_by('id')

    notas_lancadas = {
        na.avaliacao_id: na.nota_obtida
        for na in NotaAvaliacao.objects.filter(nota=nota, avaliacao__in=avaliacoes)
    }

    if request.method == "POST":
        valores = {}
        erro = None
        for avaliacao in avaliacoes:
            valor = request.POST.get(f'nota_avaliacao_{avaliacao.id}')
            # O campo bloqueado não vem no envio: se a nota entrou pelo Cadastro depois de abrir a tela, ela fica.
            if valor is None:
                continue
            if valor:
                # Aqui só se corrige o que já foi lançado: nota nova entra pelo Cadastro.
                if notas_lancadas.get(avaliacao.id) is None:
                    erro = f"A nota de '{avaliacao.nome}' ainda não foi lançada. Para lançar, use o Cadastro de notas."
                    break
                try:
                    valor_float = float(valor)
                except ValueError:
                    erro = f"Nota inválida em '{avaliacao.nome}'."
                    break
                if _casas_demais(valor.strip().replace(',', '.')):
                    erro = f"{ERRO_CASAS_DA_NOTA} Confira a nota de '{avaliacao.nome}'."
                    break
                if not 0 <= valor_float <= NOTA_MAXIMA:
                    erro = f"A nota de '{avaliacao.nome}' deve estar entre 0 e {NOTA_MAXIMA}."
                    break
                valores[avaliacao.id] = valor_float
            else:
                valores[avaliacao.id] = None

        if erro:
            messages.error(request, erro)
        else:
            # Sem mudar nenhuma nota não grava nem recalcula.
            with transaction.atomic():
                mudou = False
                for avaliacao_id, valor_float in valores.items():
                    if _mesma_nota(valor_float, notas_lancadas.get(avaliacao_id)):
                        continue
                    nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(nota=nota, avaliacao_id=avaliacao_id)
                    nota_avaliacao.nota_obtida = valor_float
                    nota_avaliacao.save()
                    mudou = True

                if mudou:
                    _recalcular_media_disciplina(nota.matricula)
            messages.success(request, "Nota editada com sucesso!")
            return redirect("lista-notas")

    turma_do_aluno = nota.turma

    avaliacoes_com_nota = [
        {'avaliacao': av, 'valor': notas_lancadas.get(av.id), 'valor_texto': _nota_no_formulario(notas_lancadas.get(av.id))}
        for av in avaliacoes
    ]

    aluno_nome = User.objects.filter(id=nota.aluno_id).first()

    return render(request, "professor/cadastrar_notas.html", {
        'aluno_nome': aluno_nome.first_name,
        "nota": nota,
        "editando": True,
        "turma_do_aluno": turma_do_aluno,
        "situacao_texto": _situacao_texto(nota),
        "avaliacoes_com_nota": avaliacoes_com_nota,
        "sem_avaliacoes": not avaliacoes.exists(),
    })

# Filtro do Cadastro de Notas: só os alunos com nota a cadastrar, ou só os que já têm todas (para ver).
FILTROS_LANCAMENTO = {'a_cadastrar', 'cadastradas'}

def _falta_cadastrar(linha):
    """A linha ainda tem nota para cadastrar: alguma avaliação em branco ou o exame aberto."""
    return linha['exame_aberto'] or any(n['valor'] is None for n in linha['notas'])

@_exige_professor
def cadastrar_notas(request):
    if request.GET.get('limpar'):
        request.session.pop('cadastro_notas_disciplina', None)
        request.session.pop('cadastro_notas_turma', None)
        request.session.pop('cadastro_notas_lancamento', None)
        return redirect('cadastrar-notas')

    # "Limpar filtros" tira só o filtro das notas e continua na mesma turma.
    if request.GET.get('limpar_filtro'):
        request.session.pop('cadastro_notas_lancamento', None)
        return redirect('cadastrar-notas')

    # A tabela recarrega aqui depois que o Salvar gravou as notas: mostra a mensagem, como nos outros botões Salvar.
    if request.GET.get('salvo'):
        messages.success(request, 'Notas salvas com sucesso!')
        return redirect('cadastrar-notas')

    if request.method == 'POST' and request.POST.get('acao') == 'filtrar_notas':
        request.session['cadastro_notas_lancamento'] = request.POST.get('lancamento', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário.
        return redirect('cadastrar-notas')

    if request.method == 'POST' and request.POST.get('acao') == 'selecionar_turma':
        disciplina_id = request.POST.get('disciplina')
        turma_id = request.POST.get('turma')

        if not disciplina_id or not turma_id:
            messages.error(request, "Selecione a disciplina e a turma antes de continuar.")
        else:
            request.session['cadastro_notas_disciplina'] = disciplina_id
            request.session['cadastro_notas_turma'] = turma_id

        return redirect('cadastrar-notas')

    # Turma guardada na sessão que não vale mais (inativa ou de outra disciplina) volta para a escolha.
    turma_obj = Turma.objects.select_related('disciplina').filter(
        id=_inteiro(request.session.get('cadastro_notas_turma')),
        disciplina_id=_inteiro(request.session.get('cadastro_notas_disciplina')),
        ativo=True,
    ).first()

    # Valor que não é um dos filtros (ou nenhum) mostra todas as notas.
    lancamento = request.session.get('cadastro_notas_lancamento', '')
    if lancamento not in FILTROS_LANCAMENTO:
        lancamento = ''

    avaliacoes = []
    alunos_notas = []
    tem_alunos = False
    sem_avaliacoes = False
    todas_lancadas = False

    aviso_configuracao = None

    if turma_obj:
        avaliacoes = list(_avaliacoes_da_turma(turma_obj).order_by('id'))
        # Com a configuração incompleta (peso em branco, ponderada sem fechar 100) não se lança nota:
        # a tela mostra o aviso e manda para Configurar avaliações.
        aviso_configuracao = _erro_na_configuracao(turma_obj.disciplina, [avaliacao.valor for avaliacao in avaliacoes])
        sem_avaliacoes = len(avaliacoes) == 0 or bool(aviso_configuracao)

        if not sem_avaliacoes:
            linhas = _linhas_tabela_notas(turma_obj, avaliacoes)
            tem_alunos = bool(linhas)
            # O aviso de "todas lançadas" vale para a turma inteira, com ou sem filtro.
            todas_lancadas = tem_alunos and not any(_falta_cadastrar(linha) for linha in linhas)
            alunos_notas = [
                linha for linha in linhas
                if not lancamento or _falta_cadastrar(linha) == (lancamento == 'a_cadastrar')
            ]

    return render(request, 'professor/cadastrar_notas.html', {
        "editando": False,
        "disciplinas": Disciplina.objects.filter(ativo=True),
        "turma_obj": turma_obj,
        "avaliacoes": avaliacoes,
        "alunos_notas": alunos_notas,
        "tem_alunos": tem_alunos,
        "lancamento_selecionado": lancamento,
        "aviso_configuracao": aviso_configuracao,
        "sem_avaliacoes": sem_avaliacoes,
        "todas_lancadas": todas_lancadas,
    })

def _erro_no_valor_da_avaliacao(valor):
    # Escrito assim para recusar também "nan" e "inf", que o float() aceita e estragariam a MF.
    if not 0 <= (valor or 0) < float('inf'):
        return "O valor da avaliação deve ser um número de 0 para cima."
    return None

def _erro_no_total_das_avaliacoes(disciplina, novo_total):
    limite = disciplina.limite_valor_avaliacoes
    if limite is None:
        return None

    novo_total = round(novo_total, 2)
    if novo_total > limite:
        return f"A soma dos valores das avaliações não pode passar de {limite:g}. Ficaria em {novo_total:g}."
    return None

def _validar_total_avaliacoes(disciplina, valor_novo, excluir_id=None):
    valor_novo = valor_novo or 0

    erro = _erro_no_valor_da_avaliacao(valor_novo)
    if erro:
        return erro

    if disciplina.limite_valor_avaliacoes is None:
        return None
    return _erro_no_total_das_avaliacoes(disciplina, disciplina.total_valor_avaliacoes(excluir_id=excluir_id) + valor_novo)

MODOS_CALCULO_VALIDOS = {'soma', 'ponderada', 'aritmetica'}

# A configuração das avaliações é feita uma vez, no começo do ano letivo: depois de salva, não muda mais.
ERRO_CONFIGURACAO_TRAVADA = "Essa disciplina já foi configurada para o ano letivo. A configuração não pode ser alterada."

def _configuracao_travada(disciplina):
    """A disciplina já tem avaliações salvas: a configuração só pode ser vista."""
    return disciplina.avaliacao_set.exists()

def _tem_nota_lancada(disciplina):
    """Alguma nota já foi lançada em avaliação dessa disciplina: nem refazer a configuração é mais possível."""
    return NotaAvaliacao.objects.filter(avaliacao__disciplina=disciplina, nota_obtida__isnull=False).exists()

# Cada disciplina pode ter no máximo esta quantidade de avaliações.
MAXIMO_AVALIACOES = 10

def _erro_na_configuracao(disciplina, pesos):
    """
    O que falta para a configuração das avaliações estar completa, dados os pesos de todas elas.
    Na soma e na ponderada toda avaliação precisa de peso e os pesos fecham exatamente o total do
    modo (10 pontos na soma, 100% na ponderada): o professor distribui a nota inteira no começo do ano.
    Na média aritmética o peso não entra na conta: o valor de cada avaliação pode ficar em branco
    e, se for informado, vai de 0 a 10, como a nota.
    """
    limite = disciplina.limite_valor_avaliacoes
    if not pesos:
        return None
    if limite is None:
        if any(peso is not None and peso > NOTA_MAXIMA for peso in pesos):
            return f"Na média aritmética o valor de cada avaliação vai de 0 a {NOTA_MAXIMA}."
        return None
    if any(peso is None for peso in pesos):
        return "Informe o peso de todas as avaliações."
    # Na média ponderada o peso é uma porcentagem inteira (20%, 35%...): quanto cada avaliação vale
    # fica a critério do professor, desde que os pesos fechem 100%.
    if disciplina.modo_calculo == 'ponderada' and any(peso <= 0 or peso != int(peso) for peso in pesos):
        return "Na média ponderada o peso de cada avaliação é uma porcentagem inteira, a partir de 1% (por exemplo, 20%)."
    total = round(sum(pesos), 2)
    if total != limite:
        if disciplina.modo_calculo == 'ponderada':
            return f"Na média ponderada os pesos precisam fechar {limite}%. Hoje somam {total:g}%."
        return f"Na soma dos pesos os pesos precisam fechar {limite} pontos. Hoje somam {total:g}."
    return None

def _erro_na_quantidade_de_avaliacoes(quantidade):
    if quantidade > MAXIMO_AVALIACOES:
        return f"Cada disciplina pode ter no máximo {MAXIMO_AVALIACOES} avaliações."
    return None

def configurar_avaliacoes(request):
    if request.GET.get('limpar'):
        request.session.pop('config_aval_disciplina', None)
        request.session.pop('config_aval_modo', None)
        return redirect('configurar-avaliacoes')

    if request.method == 'POST':
        request.session['config_aval_disciplina'] = request.POST.get('disciplina', '')
        request.session['config_aval_modo'] = request.POST.get('modo', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('configurar-avaliacoes')

    disciplina_id = request.session.get('config_aval_disciplina', '')
    modo = request.session.get('config_aval_modo', '')

    # "Trocar": volta pro estado de seleção sem apagar o que já estava escolhido,
    # pra reabrir os selects pré-preenchidos em vez de zerar a disciplina/modo.
    voltando_para_selecao = request.GET.get('trocar') == '1'

    disciplina_atual = None
    avaliacoes = Avaliacao.objects.none()
    modo_valido = modo in MODOS_CALCULO_VALIDOS

    if disciplina_id and modo_valido and not voltando_para_selecao:
        disciplina_atual = get_object_or_404(Disciplina, id=disciplina_id)

        if disciplina_atual.modo_calculo != modo:
            # Só bloqueia a troca se já havia um modo definido de fato. Se a disciplina
            # nunca teve modo_calculo salvo (ex.: avaliações cadastradas antes desse campo
            # existir, ou por outra tela que não passa por essa configuração), não há o
            # que "trocar" ainda: é a primeira configuração, então deixa salvar o modo.
            tem_avaliacoes = bool(disciplina_atual.modo_calculo) and \
                disciplina_atual.avaliacao_set.exists()
            if tem_avaliacoes:
                # Defesa em profundidade: o select de modo já vem travado no front-end
                # quando a disciplina tem avaliação, então isso só ocorre em uso fora do padrão.
                messages.error(request, "Essa disciplina já tem avaliações cadastradas. Apague as avaliações antes de trocar o modo.")
                return redirect(f"{reverse('configurar-avaliacoes')}?trocar=1")
            # Escolher o modo só abre a configuração: ele vai para o banco junto com a primeira
            # avaliação salva (cadastrar_avaliacao_ajax), não ao clicar em Configurar.
            disciplina_atual.modo_calculo = modo

        avaliacoes = Avaliacao.objects.select_related('disciplina').filter(
            disciplina_id=disciplina_id
        ).order_by('id')

    disciplinas = Disciplina.objects.filter(ativo=True).annotate(
        total_avaliacoes=Count('avaliacao')
    )

    return render(request, 'professor/configurar_avaliacoes.html', {
        'maximo_avaliacoes': MAXIMO_AVALIACOES,
        # Com avaliações salvas a tela é só de visualização; sem nenhuma nota lançada ainda dá para refazer.
        'travada': bool(avaliacoes),
        'pode_refazer': bool(avaliacoes) and not _tem_nota_lancada(disciplina_atual),
        'limite_dos_pesos': disciplina_atual.limite_valor_avaliacoes if disciplina_atual else None,
        'avaliacoes': avaliacoes,
        'disciplinas': disciplinas,
        'disciplina_selecionada': disciplina_id,
        'modo_selecionado': modo,
        'disciplina_atual': disciplina_atual,
        'sem_avaliacoes': bool(disciplina_id and modo_valido and not avaliacoes),
    })

def cadastrar_avaliacao(request):
    if request.method == 'POST':
        nome = request.POST.get('nome')
        tipo = request.POST.get('tipo')
        valor = request.POST.get('valor') or None
        disciplina_id = request.POST.get('disciplina')

        if not nome or not disciplina_id:
            messages.error(request, "Preencha o nome e selecione a disciplina antes de salvar.")
            return render(request, "professor/cadastrar_avaliacao.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
                "tipo": tipo,
                "valor": valor,
            })

        disciplina = get_object_or_404(Disciplina, id=disciplina_id)

        if not disciplina.modo_calculo:
            # Cadastrar avaliação sem a disciplina ter um modo de cálculo definido deixa o
            # dado inconsistente (avaliação "solta", sem cálculo associado). Manda primeiro
            # pra tela de configuração, que é quem define o modo antes de liberar o cadastro.
            messages.error(request, "Defina o modo de cálculo dessa disciplina antes de cadastrar avaliações.")
            return redirect("configurar-avaliacoes")

        erro = (ERRO_CONFIGURACAO_TRAVADA if _configuracao_travada(disciplina) else None) \
            or _validar_total_avaliacoes(disciplina, float(valor) if valor else 0)
        if erro:
            messages.error(request, erro)
            return render(request, "professor/cadastrar_avaliacao.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
                "tipo": tipo,
                "valor": valor,
            })

        Avaliacao.objects.create(nome=nome, tipo=tipo, valor=valor, disciplina_id=disciplina_id)
        messages.success(request, "Avaliação cadastrada com sucesso!")
        return redirect("configurar-avaliacoes")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "professor/cadastrar_avaliacao.html", {
        "disciplinas": disciplinas,
    })

def editar_avaliacao(request, id):
    # Avaliação salva faz parte da configuração do ano letivo: não é mais editada.
    get_object_or_404(Avaliacao, id=id)
    messages.error(request, ERRO_CONFIGURACAO_TRAVADA)
    return redirect("configurar-avaliacoes")

def deletar_avaliacao(request, id):
    # Avaliação salva não é excluída sozinha: para mudar, só refazendo a configuração inteira,
    # e apenas enquanto não houver nota lançada (refazer_configuracao).
    get_object_or_404(Avaliacao, id=id)
    messages.error(request, ERRO_CONFIGURACAO_TRAVADA)
    return redirect("configurar-avaliacoes")

@require_POST
@_exige_professor
def refazer_configuracao(request):
    """
    Botão "Refazer configuração": apaga as avaliações da disciplina e o modo de cálculo, para o
    professor configurar de novo do zero. Só enquanto nenhuma nota foi lançada.
    """
    disciplina = get_object_or_404(Disciplina, id=_inteiro(request.POST.get('disciplina')))
    with transaction.atomic():
        if _tem_nota_lancada(disciplina):
            messages.error(request, "Essa disciplina já tem notas lançadas. A configuração não pode mais ser refeita.")
            return redirect("configurar-avaliacoes")
        disciplina.avaliacao_set.all().delete()
        disciplina.modo_calculo = None
        disciplina.save(update_fields=['modo_calculo'])

    # Volta para a escolha do modo de cálculo, com a disciplina já selecionada.
    request.session['config_aval_disciplina'] = str(disciplina.id)
    request.session.pop('config_aval_modo', None)
    messages.success(request, "Configuração apagada. Escolha o modo de cálculo e configure de novo.")
    return redirect("configurar-avaliacoes")

@require_POST
def cadastrar_avaliacao_ajax(request):
    nome = request.POST.get('nome')
    tipo = request.POST.get('tipo') or 'prova'
    valor = request.POST.get('valor') or None
    disciplina_id = request.POST.get('disciplina')

    if not nome or not disciplina_id:
        return JsonResponse({'ok': False, 'erro': 'Informe o nome e a disciplina antes de salvar.'})

    disciplina = get_object_or_404(Disciplina, id=disciplina_id)

    # Salvar a primeira avaliação é que salva a configuração: o modo escolhido na tela
    # (guardado na sessão) vai para o banco agora. Com avaliação já salva, o modo não muda.
    primeira = not disciplina.avaliacao_set.exists()
    if primeira:
        modo = request.session.get('config_aval_modo', '')
        if request.session.get('config_aval_disciplina') == str(disciplina.id) and modo in MODOS_CALCULO_VALIDOS:
            disciplina.modo_calculo = modo
    if not disciplina.modo_calculo:
        return JsonResponse({'ok': False, 'erro': 'Escolha o modo de cálculo dessa disciplina antes de salvar.'})

    erro = (ERRO_CONFIGURACAO_TRAVADA if not primeira else None) \
        or _validar_total_avaliacoes(disciplina, float(valor) if valor else 0)
    if erro:
        return JsonResponse({'ok': False, 'erro': erro})

    with transaction.atomic():
        if primeira:
            disciplina.save(update_fields=['modo_calculo'])
        avaliacao = Avaliacao.objects.create(
            nome=nome, tipo=tipo, valor=valor, disciplina_id=disciplina_id
        )

    return JsonResponse({
        'ok': True,
        'id': avaliacao.id,
        'nome': avaliacao.nome,
        'tipo': avaliacao.tipo,
        'tipo_display': avaliacao.get_tipo_display(),
        'valor': avaliacao.valor,
        'disciplina_nome': avaliacao.disciplina.nome,
    })

@require_POST
def editar_avaliacao_ajax(request):
    # Avaliação salva faz parte da configuração do ano letivo: não é mais editada.
    get_object_or_404(Avaliacao, id=_inteiro(request.POST.get('id')))
    return JsonResponse({'ok': False, 'erro': ERRO_CONFIGURACAO_TRAVADA})

TIPOS_AVALIACAO = {tipo for tipo, _ in Avaliacao._meta.get_field('tipo').choices}
TAMANHO_NOME_AVALIACAO = Avaliacao._meta.get_field('nome').max_length

def _peso_digitado(texto):
    """Peso digitado na tabela, aceitando vírgula ou ponto: None se ficou em branco. Levanta ValueError se não for número."""
    texto = (texto or '').strip().replace(',', '.')
    return float(texto) if texto else None

def _erro_no_nome_ou_tipo(nome, tipo):
    if not nome:
        return 'Informe o nome da avaliação.'
    if len(nome) > TAMANHO_NOME_AVALIACAO:
        return f'O nome da avaliação pode ter até {TAMANHO_NOME_AVALIACAO} caracteres.'
    if tipo not in TIPOS_AVALIACAO:
        return 'Tipo inválido.'
    return None

@require_POST
@_exige_professor
def salvar_avaliacoes_ajax(request):
    """
    Botão Salvar da tela Configurar Avaliações: grava de uma vez a configuração da disciplina, que
    a partir daí fica travada. Tudo é conferido antes; se algo for recusado, nada é gravado.
    """
    with transaction.atomic():
        # Trava a disciplina: dois envios ao mesmo tempo não passam juntos do limite dos pesos.
        disciplina = get_object_or_404(Disciplina.objects.select_for_update(), id=_inteiro(request.POST.get('disciplina')))
        gravadas = list(Avaliacao.objects.filter(disciplina=disciplina).order_by('id'))
        if gravadas:
            return JsonResponse({'ok': False, 'erro': ERRO_CONFIGURACAO_TRAVADA})

        novas = []
        for nome, tipo, valor in zip(request.POST.getlist('nova_nome'), request.POST.getlist('nova_tipo'), request.POST.getlist('nova_valor')):
            nome, tipo = nome.strip(), tipo or 'prova'
            erro = _erro_no_nome_ou_tipo(nome, tipo)
            if erro:
                return JsonResponse({'ok': False, 'erro': erro})
            try:
                valor = _peso_digitado(valor)
            except ValueError:
                return JsonResponse({'ok': False, 'erro': 'Valor inválido.'})
            novas.append(Avaliacao(nome=nome, tipo=tipo, valor=valor, disciplina=disciplina))

        # Salvar a primeira avaliação é que salva a configuração: o modo escolhido na tela
        # (guardado na sessão) vai para o banco agora. Com avaliação já salva, o modo não muda.
        primeira = not gravadas and bool(novas)
        if primeira:
            modo = request.session.get('config_aval_modo', '')
            if request.session.get('config_aval_disciplina') == str(disciplina.id) and modo in MODOS_CALCULO_VALIDOS:
                disciplina.modo_calculo = modo
        if novas and not disciplina.modo_calculo:
            return JsonResponse({'ok': False, 'erro': 'Escolha o modo de cálculo dessa disciplina antes de salvar.'})

        erro = _erro_na_quantidade_de_avaliacoes(len(gravadas) + len(novas)) if novas else None
        if erro:
            return JsonResponse({'ok': False, 'erro': erro})

        # Os mesmos limites de quando cada avaliação era salva sozinha, agora sobre o total final.
        for avaliacao in gravadas + novas:
            erro = _erro_no_valor_da_avaliacao(avaliacao.valor)
            if erro:
                return JsonResponse({'ok': False, 'erro': erro})
        erro = _erro_no_total_das_avaliacoes(disciplina, sum(avaliacao.valor or 0 for avaliacao in gravadas + novas))
        if erro:
            return JsonResponse({'ok': False, 'erro': erro})
        # A configuração só é gravada completa, para o cálculo da média nunca dar erro lá na frente.
        erro = _erro_na_configuracao(disciplina, [avaliacao.valor for avaliacao in gravadas + novas])
        if erro:
            return JsonResponse({'ok': False, 'erro': erro})

        if primeira:
            disciplina.save(update_fields=['modo_calculo'])
        for avaliacao in novas:
            avaliacao.save()

    # A tela recarrega depois da resposta e mostra a mensagem, como nos outros botões Salvar.
    messages.success(request, 'Avaliações salvas com sucesso!')
    return JsonResponse({'ok': True})

def alunos_por_turma(request):
    turma_nome = request.GET.get('turma_nome')
    if not turma_nome:
        return JsonResponse({'alunos': []})

    alunos = User.objects.filter(
        matricula__turma__nome=turma_nome,
        matricula__turma__ativo=True,
        matricula__ativo=True
    ).exclude(
        groups__name='professor'
    ).distinct().values('id', 'first_name', 'username').order_by('first_name')

    return JsonResponse({'alunos': list(alunos)})

def disciplinas_por_turma(request):
    turma_nome = request.GET.get('turma')
    ano = request.GET.get('ano', '')
    if not turma_nome:
        return JsonResponse({'disciplinas': []})

    # As condições da turma ficam no mesmo filter() para valerem para a mesma linha de Turma
    filtro = {'turma__nome': turma_nome, 'turma__ativo': True, 'ativo': True}
    if ano.isdigit():
        filtro['turma__ano'] = ano

    disciplinas = Disciplina.objects.filter(**filtro).distinct().order_by('nome').values('id', 'nome')

    return JsonResponse({'disciplinas': list(disciplinas)})

def turmas_por_disciplina(request):
    disciplina_id = request.GET.get('disciplina')
    if not disciplina_id:
        return JsonResponse({'turmas': []})

    turmas = Turma.objects.filter(
        disciplina_id=disciplina_id, ativo=True
    ).order_by('-ano', 'nome').values('id', 'nome', 'ano')

    return JsonResponse({'turmas': list(turmas)})

def _nota_texto(valor):
    """Nota do jeito que o template mostra (ex.: 8.5), para a célula não mudar ao recarregar a página."""
    return str(valor) if valor is not None else '-'

def _numero_digitado(request):
    """Texto digitado na célula, aceitando vírgula ou ponto."""
    return (request.POST.get('valor') or '').strip().replace(',', '.')

@require_POST
@_exige_professor
def editar_nota_avaliacao_ajax(request):
    """
    Salva a nota digitada na célula da tabela de notas, na Lista de Notas e no Cadastro de Notas.
    Com so_lancar (Cadastro) só grava em avaliação ainda sem nota: nota já lançada não muda por lá.
    Sem so_lancar (Lista) só muda a nota já lançada: nota nova não entra por lá.
    """
    so_lancar = request.POST.get('so_lancar') == '1'

    avaliacao = Avaliacao.objects.filter(id=_inteiro(request.POST.get('avaliacao'))).first()
    if not avaliacao:
        return JsonResponse({'ok': False, 'erro': 'Avaliação não encontrada.'})

    # Peso em branco na soma ou na ponderada faria a conta da média dar erro: a nota não entra.
    if avaliacao.disciplina.limite_valor_avaliacoes is not None and \
            Avaliacao.objects.filter(disciplina_id=avaliacao.disciplina_id, valor__isnull=True).exists():
        return JsonResponse({'ok': False, 'erro': 'Há avaliação sem peso nessa disciplina. Acerte em Configurar avaliações antes de lançar notas.'})

    texto = _numero_digitado(request)
    if texto:
        try:
            valor = float(texto)
        except ValueError:
            return JsonResponse({'ok': False, 'erro': 'Nota inválida.'})

        # Escrito assim para recusar também "nan", que não é maior nem menor que nenhum número.
        if not 0 <= valor <= NOTA_MAXIMA:
            return JsonResponse({'ok': False, 'erro': f'A nota deve estar entre 0 e {NOTA_MAXIMA}.'})
        if _casas_demais(texto):
            return JsonResponse({'ok': False, 'erro': ERRO_CASAS_DA_NOTA})
    elif so_lancar:
        return JsonResponse({'ok': False, 'erro': 'Informe a nota.'})
    else:
        # Na Lista, limpar a célula apaga a nota.
        valor = None

    with transaction.atomic():
        # Trava a matrícula: as notas do mesmo aluno entram uma de cada vez, então o registro Nota
        # não duplica e o último recálculo da média já enxerga todas elas.
        matricula = Matricula.objects.select_for_update(of=('self',)).filter(
            aluno_id=_inteiro(request.POST.get('aluno')),
            turma_id=_inteiro(request.POST.get('turma')),
            ativo=True,
            turma__ativo=True,
            turma__disciplina_id=avaliacao.disciplina_id,
        ).first()
        if not matricula:
            return JsonResponse({'ok': False, 'erro': 'Aluno não encontrado nessa turma.'})

        if not so_lancar and valor is not None and not NotaAvaliacao.objects.filter(
            nota__matricula=matricula, avaliacao=avaliacao, nota_obtida__isnull=False
        ).exists():
            return JsonResponse({'ok': False, 'erro': 'Essa nota ainda não foi lançada. Para lançar, use o Cadastro de notas.'})

        nota, _ = Nota.objects.get_or_create(matricula=matricula, defaults={'situacao': 'cursando'})
        nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(nota=nota, avaliacao=avaliacao)

        # Sem mudar a nota não grava nem recalcula.
        gravou = 0
        if valor != nota_avaliacao.nota_obtida:
            a_gravar = NotaAvaliacao.objects.filter(id=nota_avaliacao.id)
            if so_lancar:
                # Só grava se continuar sem nota: quem chega depois não sobrescreve.
                a_gravar = a_gravar.filter(nota_obtida__isnull=True)
            gravou = a_gravar.update(nota_obtida=valor)

        if gravou:
            nota = _recalcular_media_disciplina(matricula)
        else:
            nota_avaliacao.refresh_from_db()
            valor = nota_avaliacao.nota_obtida

    ja_lancada = so_lancar and not gravou
    resposta = {
        'ok': not ja_lancada,
        'valor': '' if valor is None else str(valor),
        # Média zero com traço, como o |default:"-" da tabela.
        'media_final': _nota_texto(nota.media_final or None),
        'situacao': _situacao_texto(nota),
        'nota_exame': _nota_texto(nota.nota_exame),
        'nota_id': nota.id,
        'exame_aberto': _exame_aberto(nota),
    }
    if ja_lancada:
        resposta['ja_lancada'] = True
        resposta['erro'] = 'Essa nota já tinha sido lançada e não foi alterada. Para corrigir, use a Lista de notas.'
    return JsonResponse(resposta)

@require_POST
@_exige_professor
def lancar_nota_exame_ajax(request):
    nota = get_object_or_404(Nota, id=_inteiro(request.POST.get('id')))
    valor = _numero_digitado(request)

    if nota.situacao != 'exame':
        return JsonResponse({'ok': False, 'erro': 'Essa nota não está em exame.'})

    if not valor:
        return JsonResponse({'ok': False, 'erro': 'Informe a nota do exame.'})

    try:
        valor_float = float(valor)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'Nota inválida.'})

    # Escrito assim para recusar também "nan", que não é maior nem menor que nenhum número.
    if not 0 <= valor_float <= 10:
        return JsonResponse({'ok': False, 'erro': 'A nota do exame deve estar entre 0 e 10.'})
    if _casas_demais(valor):
        return JsonResponse({'ok': False, 'erro': ERRO_CASAS_DA_NOTA})

    _, situacao_final = calcular_pos_exame(nota.media_final, valor_float)
    nota.nota_exame = valor_float
    nota.situacao = situacao_final
    nota.save()

    return JsonResponse({
        'ok': True,
        'situacao': SITUACAO_TEXTO[situacao_final],
        'nota_exame': _nota_texto(valor_float),
    })

def _inteiro(valor):
    """Número inteiro vindo de formulário ou da sessão, ou None se não for um."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None

def gerar_relatorio(request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="boletim.pdf"'

    width, height = A4
    c = canvas.Canvas(response, pagesize=A4)

    # O ano vem do link de Download, o mesmo que está na tela; sem ele, o guardado na sessão.
    ano = request.GET.get('ano', '')
    if not ano.isdigit():
        ano = request.session.get('boletim_ano', str(datetime.now().year))

    turma_aluno = Turma.objects.filter(
        matricula__aluno=request.user, matricula__ativo=True, ano=ano
    ).values_list('nome', flat=True).first()

    # Cabeçalho
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 60, "Boletim Escolar")

    c.setFont("Helvetica", 12)
    c.drawString(72, height - 85, f"Aluno: {request.user.first_name}")
    c.drawString(72, height - 105, f"Turma: {turma_aluno or '-'}  ·  Ano: {ano}")

    c.line(72, height - 115, width - 72, height - 115)

    # Cabeçalho da tabela
    y = height - 140
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72,  y, "Disciplina")
    c.drawString(370, y, "MF")
    c.drawString(430, y, "Situação")

    c.line(72, y - 5, width - 72, y - 5)

    # Linhas de notas
    notas = _notas_do_aluno(request.user, ano)
    turmas_sem_nota = _turmas_sem_nota(request.user, ano)
    y -= 25
    c.setFont("Helvetica", 10)

    linhas = [
        (nota.disciplina, nota.media_final if nota.media_final is not None else '-', nota.get_situacao_display())
        for nota in notas
    ] + [(turma.disciplina, '-', 'Cursando') for turma in turmas_sem_nota]

    for disciplina, media, situacao in linhas:
        c.drawString(72,  y, str(disciplina))
        c.drawString(370, y, str(media))
        c.drawString(430, y, situacao)
        y -= 20

        if y < 72:
            c.showPage()
            y = height - 72

    # Situação Final
    total_cursando = notas.filter(situacao='cursando').count() + len(turmas_sem_nota)
    total_reprovadas = notas.filter(situacao='reprovado').count()
    total_aprovadas = notas.filter(situacao='aprovado').count()
    total_exame = notas.filter(situacao='exame').count()

    todas_lancadas = total_cursando == 0
    if not todas_lancadas or notas.count() == 0 or total_exame > 0:
        situacao_final = 'Em andamento'
    else:
        situacao_final = 'Reprovado de ano' if total_reprovadas > 2 else 'Aprovado de ano'

    y -= 10
    c.line(72, y, width - 72, y)
    y -= 18

    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y, f"Situação Final: {situacao_final}")
    y -= 16

    c.setFont("Helvetica", 10)
    resumo = f"{total_aprovadas} aprovadas  ·  {total_exame} em exame  ·  {total_reprovadas} reprovadas"
    if situacao_final == 'Em andamento':
        resumo += f"  ·  {total_cursando} cursando"
    c.drawString(72, y, resumo)

    largura_do_texto = width - 144

    def descer(quanto):
        """Desce a linha; no fim da folha, continua no alto da próxima."""
        nonlocal y
        y -= quanto
        if y < 72:
            c.showPage()
            y = height - 72

    def secao(titulo):
        descer(28)
        c.line(72, y + 16, width - 72, y + 16)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, y, titulo)

    def paragrafo(texto, recuo=0):
        # Texto mais largo que a folha continua na linha de baixo.
        c.setFont("Helvetica", 10)
        for linha in simpleSplit(texto, "Helvetica", 10, largura_do_texto - recuo):
            descer(15)
            c.drawString(72 + recuo, y, linha)

    # Resumo do cálculo: a conta da média de cada disciplina que já tem MF
    resumos = _resumos_do_calculo(notas)
    if resumos:
        secao("Resumo do cálculo")
        for item in resumos:
            descer(18)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(72, y, item['disciplina_nome'])
            paragrafo(item['formula_texto'], recuo=12)

    secao("Regra de aprovação")
    for regra in REGRA_DE_APROVACAO_PDF:
        paragrafo(regra)

    c.save()
    return response

# O total de cada disciplina vai de 0 a 10 nos três modos de cálculo.
ESCALA_TOTAL = 10

def _turmas_dashboard():
    """
    Turmas ativas de disciplinas ativas que têm avaliação. Disciplina sem avaliação fica de fora,
    como no boletim do aluno e nas telas do professor: não há nota para lançar nem situação para contar.
    """
    return Turma.objects.filter(
        ativo=True, disciplina__ativo=True, disciplina__in=Disciplina.objects.filter(avaliacao__isnull=False)
    )

def _ano_dashboard(request, turmas):
    """Anos letivos que têm turma e o ano escolhido em ?ano= (padrão: o ano atual, senão o mais recente)."""
    ano_atual = datetime.now().year
    anos = sorted(set(turmas.values_list('ano', flat=True)), reverse=True) or [ano_atual]

    ano = request.GET.get('ano', '')
    ano = int(ano) if ano.isdigit() else None
    if ano not in anos:
        ano = ano_atual if ano_atual in anos else anos[0]
    return anos, ano

def _registros_dashboard(ano):
    """
    Uma linha por matrícula ativa do ano letivo: aluno, turma, disciplina, total e situação.
    O total e a situação são os já gravados em Nota (os mesmos que o boletim mostra);
    nada é recalculado aqui.
    """
    matriculas = Matricula.objects.filter(
        ativo=True, turma__in=_turmas_dashboard().filter(ano=ano)
    ).select_related('aluno', 'turma__disciplina').order_by('aluno__first_name', 'turma__disciplina__nome')

    notas = {n.matricula_id: n for n in Nota.objects.filter(matricula__in=matriculas)}

    registros = []
    for matricula in matriculas:
        nota = notas.get(matricula.id)
        registros.append({
            'aluno_id': matricula.aluno_id,
            'aluno': matricula.aluno.get_full_name() or matricula.aluno.username,
            'turma': matricula.turma.nome,
            'disciplina_id': matricula.turma.disciplina_id,
            'disciplina': matricula.turma.disciplina.nome,
            # Matrícula sem Nota ainda = nenhuma nota lançada, igual a "cursando".
            'total': nota.media_final if nota else None,
            'situacao': nota.situacao if nota else 'cursando',
        })
    return registros

def _resumo_dashboard(registros):
    """Média dos totais, alunos distintos e contagem por situação de um grupo de registros."""
    totais = [r['total'] for r in registros if r['total'] is not None]
    situacoes = [r['situacao'] for r in registros]
    return {
        'media': round(sum(totais) / len(totais), 1) if totais else None,
        'alunos': len({r['aluno_id'] for r in registros}),
        'aprovado': situacoes.count('aprovado'),
        'exame': situacoes.count('exame'),
        'reprovado': situacoes.count('reprovado'),
        'cursando': situacoes.count('cursando'),
    }

def _media_texto(media):
    return _fmt_pt(media) if media is not None else '-'

def _resumo_por_turma(turmas_ano, registros):
    """
    No banco cada Turma é uma turma + disciplina; "Turma 3A" no dashboard são todas
    as linhas de Turma com esse nome no ano letivo. Devolve cada turma com a média,
    os alunos e o resumo de cada disciplina dela.
    """
    disciplinas_por_turma = {}
    for turma in turmas_ano.select_related('disciplina').order_by('nome', 'disciplina__nome'):
        disciplinas_por_turma.setdefault(turma.nome, {})[turma.disciplina_id] = turma.disciplina.nome

    turmas = []
    for nome, disciplinas in disciplinas_por_turma.items():
        registros_turma = [r for r in registros if r['turma'] == nome]
        resumo_turma = _resumo_dashboard(registros_turma)
        turmas.append({
            'nome': nome,
            'media': resumo_turma['media'],
            'media_texto': _media_texto(resumo_turma['media']),
            'alunos': resumo_turma['alunos'],
            'disciplinas': [
                {
                    'id': disciplina_id,
                    'nome': disciplina_nome,
                    **_resumo_dashboard([r for r in registros_turma if r['disciplina_id'] == disciplina_id]),
                }
                for disciplina_id, disciplina_nome in disciplinas.items()
            ],
        })
    return turmas

def _marcar_menor_media(cards):
    """
    O card de menor média fica em vermelho, como a barra de menor média.
    Só vale quando há médias diferentes para comparar.
    """
    medias = {card['media'] for card in cards if card['media'] is not None}
    menor_media = min(medias) if len(medias) > 1 else None
    for card in cards:
        card['menor_media'] = card['media'] is not None and card['media'] == menor_media

def dashboard_admin(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.groups.filter(name='administrador').exists():
        return HttpResponseForbidden("Acesso restrito ao administrador.")

    anos, ano = _ano_dashboard(request, _turmas_dashboard())
    registros = _registros_dashboard(ano)
    geral = _resumo_dashboard(registros)

    turmas = _resumo_por_turma(_turmas_dashboard().filter(ano=ano), registros)
    _marcar_menor_media(turmas)

    disciplinas_ano = {d['id']: d['nome'] for turma in turmas for d in turma['disciplinas']}

    dados_graficos = {
        'escala': ESCALA_TOTAL,
        'geral': {
            'alunos': geral['alunos'],
            'disciplinas': [
                {
                    'id': disciplina_id,
                    'nome': disciplina_nome,
                    **_resumo_dashboard([r for r in registros if r['disciplina_id'] == disciplina_id]),
                }
                for disciplina_id, disciplina_nome in sorted(disciplinas_ano.items(), key=lambda item: item[1])
            ],
        },
        'turmas': turmas,
    }

    # Só quem está em exame: é com esses alunos que ainda dá para agir.
    # A ordem é a que veio do banco (nome do aluno, depois disciplina).
    alunos_risco = [r for r in registros if r['situacao'] == 'exame']

    return render(request, "dashboard_admin.html", {
        "anos":            anos,
        "ano_selecionado": ano,
        "media_geral":     _media_texto(geral['media']),
        "total_alunos":    geral['alunos'],
        "turmas":          turmas,
        "dados_graficos":  dados_graficos,
        "alunos_risco":    alunos_risco,
    })

def gestao_disciplinas(request):
    # 1. Busca inicial: todas as disciplinas ativas, já trazendo o professor junto
    disciplinas = Disciplina.objects.filter(ativo=True).select_related("professor")

    # 2. Botão "Limpar filtros": apaga a memória e recarrega a página limpa
    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_professor', None)
        request.session.pop('filtro_gestao_disciplina', None)
        return redirect('gestao-disciplinas')

    # 3. Memória: se clicou em Filtrar (POST) anota na sessão o que foi escolhido nos dropdowns e volta por GET
    if request.method == 'POST':
        request.session['filtro_gestao_professor'] = request.POST.get('professor', '')
        request.session['filtro_gestao_disciplina'] = request.POST.get('disciplina', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('gestao-disciplinas')

    # 4. Ao abrir a página (GET) relembra o filtro
    professor_id  = request.session.get('filtro_gestao_professor', '')
    disciplina_id = request.session.get('filtro_gestao_disciplina', '')

    # 5. Aplica os filtros escolhidos
    if professor_id:
        disciplinas = disciplinas.filter(professor_id=professor_id)

    if disciplina_id:
        disciplinas = disciplinas.filter(id=disciplina_id)

    # 6. Preenche os dropdowns e entrega tudo pro template
    professores = User.objects.filter(groups__name='professor')
    todas_disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "gestao/disciplinas_lista.html", {
        "disciplinas":             disciplinas,
        "professores":             professores,
        "todas_disciplinas":       todas_disciplinas,
        "professor_selecionado":   professor_id,
        "disciplina_selecionada":  disciplina_id,
    })

def cadastrar_disciplina(request):
    if request.method == 'POST':
        nome = request.POST.get('nome')
        professor_id = request.POST.get('professor')

        if not nome or not professor_id:
            messages.error(request, "Preencha o nome e selecione o professor antes de salvar.")
            return render(request, "gestao/cadastrar_disciplina.html", {
                "professores": User.objects.filter(groups__name='professor'),
                "nome": nome,
            })

        Disciplina.objects.create(nome=nome, professor_id=professor_id)
        messages.success(request, "Disciplina cadastrada com sucesso!")
        return redirect("gestao-disciplinas")

    professores = User.objects.filter(groups__name='professor')

    return render(request, "gestao/cadastrar_disciplina.html", {
        "professores": professores,
    })

def deletar_disciplina(request, id):
    disciplina = get_object_or_404(Disciplina, id=id)

    # Disciplina com turma cadastrada não pode ser excluída, senão as turmas ficariam sem disciplina válida
    if Turma.objects.filter(disciplina=disciplina, ativo=True).exists():
        messages.error(request, "Essa disciplina possui turmas cadastradas e não pode ser excluída!")
        return redirect("gestao-disciplinas")

    disciplina.ativo = False
    disciplina.save()
    messages.success(request, "Disciplina excluída com sucesso!")
    return redirect("gestao-disciplinas")

def editar_disciplina(request, id):
    disciplina = get_object_or_404(Disciplina, id=id)

    if request.method == "POST":
        disciplina.nome = request.POST.get("nome")
        disciplina.professor_id = request.POST.get("professor")
        disciplina.save()
        messages.success(request, "Disciplina editada com sucesso!")
        return redirect("gestao-disciplinas")

    professores = User.objects.filter(groups__name='professor')

    return render(request, "gestao/editar_disciplina.html", {
        "disciplina": disciplina,
        "professores": professores,
    })

def gestao_turmas(request):
    turmas = Turma.objects.filter(ativo=True).select_related("disciplina")

    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_turma_disciplina', None)
        request.session.pop('filtro_gestao_turma_nome', None)
        return redirect('gestao-turmas')

    if request.method == 'POST':
        request.session['filtro_gestao_turma_disciplina'] = request.POST.get('disciplina', '')
        request.session['filtro_gestao_turma_nome'] = request.POST.get('turma', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('gestao-turmas')

    disciplina_id = request.session.get('filtro_gestao_turma_disciplina', '')
    turma_nome    = request.session.get('filtro_gestao_turma_nome', '')

    if disciplina_id:
        turmas = turmas.filter(disciplina_id=disciplina_id)

    if turma_nome:
        turmas = turmas.filter(nome=turma_nome)

    todas_disciplinas = Disciplina.objects.filter(ativo=True)
    nomes_turmas = Turma.objects.filter(ativo=True).values_list('nome', flat=True).distinct()

    return render(request, "gestao/turmas_lista.html", {
        "turmas":                  turmas,
        "todas_disciplinas":       todas_disciplinas,
        "nomes_turmas":            nomes_turmas,
        "disciplina_selecionada":  disciplina_id,
        "turma_selecionada":       turma_nome,
    })


def _turma_duplicada(nome, disciplina_id, ano, excluir_id=None):
    """Já existe turma ativa com esse nome, nessa disciplina e nesse ano? (ignora maiúsculas/minúsculas)"""
    turmas = Turma.objects.filter(nome__iexact=nome, disciplina_id=disciplina_id, ano=ano, ativo=True)
    if excluir_id:
        turmas = turmas.exclude(id=excluir_id)
    return turmas.exists()

def cadastrar_turma(request):
    if request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        disciplina_id = request.POST.get('disciplina')
        ano = request.POST.get('ano')

        if not nome or not disciplina_id or not ano:
            messages.error(request, "Preencha o nome, o ano e selecione a disciplina antes de salvar.")
            return render(request, "gestao/cadastrar_turma.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
                "ano": ano,
                "disciplina_selecionada": disciplina_id,
            })

        if _turma_duplicada(nome, disciplina_id, ano):
            messages.error(request, "Essa disciplina já está cadastrada para essa turma!")
            return render(request, "gestao/cadastrar_turma.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
                "ano": ano,
                "disciplina_selecionada": disciplina_id,
            })

        Turma.objects.create(nome=nome, disciplina_id=disciplina_id, ano=ano)
        messages.success(request, "Turma cadastrada com sucesso!")
        return redirect("gestao-turmas")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "gestao/cadastrar_turma.html", {
        "disciplinas": disciplinas,
        "ano": datetime.now().year,
    })

def deletar_turma(request, id):
    turma = get_object_or_404(Turma, id=id)
    turma.ativo = False
    turma.save()
    messages.success(request, "Turma excluída com sucesso!")
    return redirect("gestao-turmas")

def editar_turma(request, id):
    turma = get_object_or_404(Turma, id=id)

    # A disciplina atual da turma sempre entra na lista. É uma proteção para dados antigos:
    # se ela estiver excluída e ficar de fora, nenhuma opção fica marcada e o formulário
    # abre mostrando outra disciplina.
    disciplinas = Disciplina.objects.filter(Q(ativo=True) | Q(id=turma.disciplina_id))

    if request.method == "POST":
        turma.nome = (request.POST.get("nome") or "").strip()
        turma.disciplina_id = request.POST.get("disciplina")
        turma.ano = request.POST.get("ano")

        duplicada = (
            turma.nome and turma.disciplina_id and turma.ano
            and _turma_duplicada(turma.nome, turma.disciplina_id, turma.ano, excluir_id=turma.id)
        )
        if duplicada:
            messages.error(request, "Essa disciplina já está cadastrada para essa turma!")
            # Não grava: volta para o formulário com o que foi digitado, para a pessoa corrigir
            turma.disciplina_id = int(turma.disciplina_id)
            return render(request, "gestao/editar_turma.html", {
                "turma": turma,
                "disciplinas": disciplinas,
            })

        turma.save()
        messages.success(request, "Turma editada com sucesso!")
        return redirect("gestao-turmas")

    return render(request, "gestao/editar_turma.html", {
        "turma": turma,
        "disciplinas": disciplinas,
    })

def _disciplinas_da_turma(turma_nome, ano):
    """
    No banco cada linha de Turma é uma turma + disciplina. Devolve as linhas que formam
    a turma desse nome nesse ano, uma por disciplina (mesmo critério de disciplinas_por_turma).
    """
    return Turma.objects.filter(nome=turma_nome, ano=ano, ativo=True, disciplina__ativo=True)

def _outra_turma_no_ano(aluno_id, turma_nome, ano, ignorar_ids=()):
    """
    O aluno só pode estar em uma turma por ano. Devolve o nome de outra turma em que ele
    já está matriculado nesse ano, ou None. ignorar_ids são as linhas da matrícula em edição.
    """
    return (
        Matricula.objects.filter(aluno_id=aluno_id, turma__ano=ano, ativo=True)
        .exclude(turma__nome=turma_nome)
        .exclude(id__in=ignorar_ids)
        .values_list('turma__nome', flat=True)
        .first()
    )

def _contexto_cadastro_matricula(aluno_selecionado='', turma_selecionada='', ano_selecionado=None):
    """Listas do formulário de matrícula e o que já estava escolhido, para voltar à tela após um erro."""
    # Turmas de cada ano: o ano é escolhido primeiro e a lista de turmas mostra só as daquele ano
    turmas_por_ano = {}
    for nome, ano in Turma.objects.filter(ativo=True).values_list('nome', 'ano'):
        turmas_por_ano.setdefault(str(ano), set()).add(nome)
    turmas_por_ano = {ano: sorted(nomes) for ano, nomes in turmas_por_ano.items()}
    anos = sorted((int(ano) for ano in turmas_por_ano), reverse=True)

    # Padrão: o ano atual, ou o mais recente que tiver turma
    if ano_selecionado is None and anos:
        ano_selecionado = datetime.now().year if datetime.now().year in anos else anos[0]

    return {
        "alunos": User.objects.filter(groups__name='aluno').order_by('first_name'),
        "turmas": turmas_por_ano.get(str(ano_selecionado), []),
        "turmas_por_ano": turmas_por_ano,
        "anos": anos,
        "aluno_selecionado": aluno_selecionado,
        "turma_selecionada": turma_selecionada,
        "ano_selecionado": ano_selecionado,
    }

def cadastrar_matricula(request):
    if request.method == 'POST':
        aluno_id = request.POST.get('aluno', '')
        turma_nome = request.POST.get('turma', '')
        ano = request.POST.get('ano', '')

        contexto = _contexto_cadastro_matricula(
            aluno_selecionado=aluno_id,
            turma_selecionada=turma_nome,
            ano_selecionado=int(ano) if ano.isdigit() else None,
        )

        if not aluno_id or not turma_nome or not ano.isdigit():
            messages.error(request, "Selecione o aluno, a turma e o ano antes de salvar.")
            return render(request, "gestao/cadastrar_matricula.html", contexto)

        # O aluno é matriculado na turma; o sistema cria uma linha para cada disciplina dela.
        turmas = list(_disciplinas_da_turma(turma_nome, ano))
        if not turmas:
            messages.error(request, "Esta turma ainda não tem disciplinas.")
            return render(request, "gestao/cadastrar_matricula.html", contexto)

        outra_turma = _outra_turma_no_ano(aluno_id, turma_nome, ano)
        if outra_turma:
            messages.error(request, f"Esse aluno já está matriculado na turma {outra_turma} em {ano}!")
            return render(request, "gestao/cadastrar_matricula.html", contexto)

        # Tudo ou nada: se a criação de alguma linha falhar, nenhuma fica gravada.
        with transaction.atomic():
            ja_matriculado = set(
                Matricula.objects.filter(aluno_id=aluno_id, turma__in=turmas, ativo=True).values_list('turma_id', flat=True)
            )
            # Não duplica: só entram as disciplinas em que o aluno ainda não está
            novas = [turma for turma in turmas if turma.id not in ja_matriculado]
            for turma in novas:
                Matricula.objects.create(aluno_id=aluno_id, turma_id=turma.id)

        if not novas:
            messages.error(request, "Esse aluno já está matriculado nessa turma!")
            return render(request, "gestao/cadastrar_matricula.html", contexto)

        messages.success(request, f"Aluno vinculado à turma {turma_nome}!")
        return redirect("gestao-matriculas")

    return render(request, "gestao/cadastrar_matricula.html", _contexto_cadastro_matricula())

def deletar_matricula(request, id):
    matricula = get_object_or_404(Matricula.objects.select_related('turma'), id=id)

    # Excluir desvincula o aluno da turma: saem todas as linhas de disciplina dessa turma nesse ano
    Matricula.objects.filter(
        aluno_id=matricula.aluno_id,
        turma__nome=matricula.turma.nome,
        turma__ano=matricula.turma.ano,
        ativo=True,
    ).update(ativo=False)

    messages.success(request, f"Aluno removido da turma {matricula.turma.nome}!")
    return redirect("gestao-matriculas")

def editar_matricula(request, id):
    # O id é de uma das linhas de disciplina; a edição vale para a matrícula inteira do aluno na turma
    matricula = get_object_or_404(Matricula.objects.select_related('turma'), id=id)
    turma_atual = matricula.turma

    if request.method == "POST":
        aluno_id = request.POST.get("aluno", "")
        turma_nome = request.POST.get("turma", "")
        ano = request.POST.get("ano", "")

        contexto = _contexto_cadastro_matricula(
            aluno_selecionado=aluno_id,
            turma_selecionada=turma_nome,
            ano_selecionado=int(ano) if ano.isdigit() else None,
        )
        contexto["matricula"] = matricula

        if not aluno_id.isdigit() or not turma_nome or not ano.isdigit():
            messages.error(request, "Selecione o aluno, a turma e o ano antes de salvar.")
            return render(request, "gestao/editar_matricula.html", contexto)

        turmas = list(_disciplinas_da_turma(turma_nome, ano))
        if not turmas:
            messages.error(request, "Esta turma ainda não tem disciplinas.")
            return render(request, "gestao/editar_matricula.html", contexto)

        # Tudo ou nada: se alguma linha falhar, nenhuma alteração fica gravada.
        with transaction.atomic():
            # Linhas atuais da matrícula: uma por disciplina da turma em que o aluno está hoje
            linhas = list(Matricula.objects.filter(
                aluno_id=matricula.aluno_id,
                turma__nome=turma_atual.nome,
                turma__ano=turma_atual.ano,
                ativo=True,
            ).select_related('turma'))
            ids_linhas = [linha.id for linha in linhas]

            # Um aluno só pode estar em uma turma por ano. A conferência vale quando a edição muda
            # o aluno, a turma ou o ano; salvar sem mudar nada continua permitido.
            mudou = (
                aluno_id != str(matricula.aluno_id)
                or turma_nome != turma_atual.nome
                or int(ano) != turma_atual.ano
            )
            outra_turma = _outra_turma_no_ano(aluno_id, turma_nome, ano, ignorar_ids=ids_linhas) if mudou else None

            # Disciplinas da turma escolhida em que o aluno escolhido já está por outra matrícula
            ocupadas = set(
                Matricula.objects.filter(aluno_id=aluno_id, turma__in=turmas, ativo=True)
                .exclude(id__in=ids_linhas)
                .values_list('turma_id', flat=True)
            )
            ja_matriculado = len(ocupadas) == len(turmas)

            if not ja_matriculado and not outra_turma:
                # Cada linha acompanha o aluno para a mesma disciplina na turma escolhida, como a
                # edição sempre fez (a linha é atualizada, não recriada). Disciplina que a turma
                # escolhida não tem é desvinculada; a que faltar é criada. Nada fica duplicado.
                destino_por_disciplina = {turma.disciplina_id: turma for turma in turmas}
                for linha in linhas:
                    destino = destino_por_disciplina.get(linha.turma.disciplina_id)
                    if destino and destino.id not in ocupadas:
                        linha.aluno_id = aluno_id
                        linha.turma_id = destino.id
                        ocupadas.add(destino.id)
                    else:
                        linha.ativo = False
                    linha.save()

                for turma in turmas:
                    if turma.id not in ocupadas:
                        Matricula.objects.create(aluno_id=aluno_id, turma_id=turma.id)

        if outra_turma:
            messages.error(request, f"Esse aluno já está matriculado na turma {outra_turma} em {ano}!")
            return render(request, "gestao/editar_matricula.html", contexto)

        if ja_matriculado:
            messages.error(request, "Esse aluno já está matriculado nessa turma!")
            return render(request, "gestao/editar_matricula.html", contexto)

        messages.success(request, f"Vínculo do aluno com a turma {turma_nome} atualizado!")
        return redirect("gestao-matriculas")

    contexto = _contexto_cadastro_matricula(
        aluno_selecionado=str(matricula.aluno_id),
        turma_selecionada=turma_atual.nome,
        ano_selecionado=turma_atual.ano,
    )
    contexto["matricula"] = matricula

    return render(request, "gestao/editar_matricula.html", contexto)

def gestao_matriculas(request):
    matriculas = Matricula.objects.filter(ativo=True).select_related("aluno", "turma", "turma__disciplina")

    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_mat_turma', None)
        request.session.pop('filtro_gestao_mat_aluno', None)
        return redirect('gestao-matriculas')

    if request.method == 'POST':
        request.session['filtro_gestao_mat_turma'] = request.POST.get('turma', '')
        request.session['filtro_gestao_mat_aluno'] = request.POST.get('aluno', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('gestao-matriculas')

    turma_nome = request.session.get('filtro_gestao_mat_turma', '')
    aluno_id = request.session.get('filtro_gestao_mat_aluno', '')

    # O filtro de turma é pelo nome da turma ("3A"), não por turma + disciplina
    todas_turmas = sorted(set(Turma.objects.filter(ativo=True).values_list('nome', flat=True)))
    if turma_nome not in todas_turmas:
        turma_nome = ''   # valor antigo guardado na sessão (era o id de uma linha de turma)

    if turma_nome:
        matriculas = matriculas.filter(turma__nome=turma_nome)

    if aluno_id:
        matriculas = matriculas.filter(aluno_id=aluno_id)

    # Uma linha por aluno, turma e ano: as linhas de cada disciplina são agrupadas aqui,
    # na consulta, sem mudar nenhuma tabela.
    grupos = {}
    for matricula in matriculas.order_by('turma__disciplina__nome'):
        chave = (matricula.aluno_id, matricula.turma.nome, matricula.turma.ano)
        grupo = grupos.setdefault(chave, {
            'id': matricula.id,
            'aluno': matricula.aluno,
            'turma_nome': matricula.turma.nome,
            'ano': matricula.turma.ano,
            'disciplinas': [],
        })
        grupo['disciplinas'].append(matricula.turma.disciplina.nome)

    alunos = User.objects.filter(groups__name='aluno')

    return render(request, "gestao/matriculas_lista.html", {
        "matriculas":          list(grupos.values()),
        "todas_turmas":        todas_turmas,
        "alunos":              alunos,
        "turma_selecionada":   turma_nome,
        "aluno_selecionado":   aluno_id,
    })

def gestao_usuarios(request):
    usuarios = User.objects.filter(is_active=True, is_superuser=False, groups__isnull=False).prefetch_related("groups").distinct()

    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_usuario_grupo', None)
        return redirect('gestao-usuarios')

    if request.method == 'POST':
        request.session['filtro_gestao_usuario_grupo'] = request.POST.get('grupo', '')
        # Volta por GET: atualizar a página depois não reenvia o formulário, e a seta voltar do navegador reabre o filtro.
        return redirect('gestao-usuarios')

    grupo_nome = request.session.get('filtro_gestao_usuario_grupo', '')

    if grupo_nome:
        usuarios = usuarios.filter(groups__name=grupo_nome)

    grupos = Group.objects.filter(name__in=['aluno', 'professor', 'administrador'])

    return render(request, "gestao/usuarios_lista.html", {
        "usuarios":          usuarios,
        "grupos":            grupos,
        "grupo_selecionado": grupo_nome,
    })

def _instrucoes_senha():
    """Regras de senha do sistema (AUTH_PASSWORD_VALIDATORS do settings), em português, para mostrar no formulário."""
    with translation.override('pt-br'):
        return [str(texto) for texto in password_validators_help_texts()]

def _erros_senha(senha, confirmacao, usuario):
    """
    Erros da senha digitada: confirmação diferente ou alguma regra de AUTH_PASSWORD_VALIDATORS
    não atendida. Lista vazia quando a senha pode ser usada.
    """
    if senha != confirmacao:
        return ["As senhas digitadas não são iguais."]

    # As mensagens do Django saem em português, mesmo com o sistema sem LANGUAGE_CODE definido
    with translation.override('pt-br'):
        try:
            validate_password(senha, user=usuario)
        except ValidationError as erro:
            return list(erro.messages)
    return []

def cadastrar_usuario(request):
    grupos = Group.objects.filter(name__in=['aluno', 'professor', 'administrador'])
    contexto = {"grupos": grupos, "instrucoes_senha": _instrucoes_senha()}

    if request.method == 'POST':
        username   = request.POST.get('username')
        first_name = request.POST.get('first_name')
        password   = request.POST.get('password')
        password2  = request.POST.get('password2')
        grupo_nome = request.POST.get('grupo')

        # Se o formulário voltar com erro, mantém o que foi digitado (menos as senhas)
        contexto.update({"username": username, "first_name": first_name, "grupo_selecionado": grupo_nome})

        if not username or not first_name or not password or not password2 or not grupo_nome:
            messages.error(request, "Preencha todos os campos antes de salvar.")
            return render(request, "gestao/cadastrar_usuario.html", contexto)

        if User.objects.filter(username=username).exists():
            messages.error(request, "Já existe um usuário com esse nome de usuário!")
            return render(request, "gestao/cadastrar_usuario.html", contexto)

        erros = _erros_senha(password, password2, User(username=username, first_name=first_name))
        if erros:
            for erro in erros:
                messages.error(request, erro)
            return render(request, "gestao/cadastrar_usuario.html", contexto)

        usuario = User.objects.create(username=username, first_name=first_name)
        usuario.set_password(password)
        usuario.save()
        usuario.groups.add(Group.objects.get(name=grupo_nome))

        messages.success(request, "Usuário cadastrado com sucesso!")
        return redirect("gestao-usuarios")

    return render(request, "gestao/cadastrar_usuario.html", contexto)

def deletar_usuario(request, id):
    usuario = get_object_or_404(User, id=id)
    usuario.is_active = False
    usuario.save()
    messages.success(request, "Usuário excluído com sucesso!")
    return redirect("gestao-usuarios")

def editar_usuario(request, id):
    usuario = get_object_or_404(User, id=id)
    grupos = Group.objects.filter(name__in=['aluno', 'professor', 'administrador'])

    if request.method == "POST":
        usuario.username = request.POST.get("username")
        usuario.first_name = request.POST.get("first_name")
        grupo_nome = request.POST.get("grupo")

        password = request.POST.get("password")
        password2 = request.POST.get("password2")

        # A senha é opcional na edição: só é validada e trocada se algum dos dois campos vier preenchido
        if password or password2:
            erros = _erros_senha(password, password2, usuario)
            if erros:
                for erro in erros:
                    messages.error(request, erro)
                # Nada é gravado: volta para o formulário com o que foi digitado (menos as senhas)
                return render(request, "gestao/editar_usuario.html", {
                    "usuario":          usuario,
                    "grupos":           grupos,
                    "grupo_atual":      grupos.filter(name=grupo_nome).first() or usuario.groups.first(),
                    "instrucoes_senha": _instrucoes_senha(),
                })
            usuario.set_password(password)

        usuario.save()

        usuario.groups.clear()
        usuario.groups.add(Group.objects.get(name=grupo_nome))

        messages.success(request, "Usuário editado com sucesso!")
        return redirect("gestao-usuarios")

    grupo_atual = usuario.groups.first()

    return render(request, "gestao/editar_usuario.html", {
        "usuario":          usuario,
        "grupos":           grupos,
        "grupo_atual":      grupo_atual,
        "instrucoes_senha": _instrucoes_senha(),
    })
