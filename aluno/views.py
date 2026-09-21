from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from aluno.models import Disciplina, Nota, Turma, Matricula, Avaliacao, NotaAvaliacao
from aluno.calculo_notas import calcular_nota_final, classificar, teto_nota_avaliacao, montar_resumo_formula, calcular_pos_exame
from django.contrib.auth.models import User, Group
from django.db.models import Count
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from datetime import datetime
import json
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

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
                return redirect("gestao-dashboard")

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

def professor_dashboard(request):
    return render(request, "professor/dashboard.html")

def _avaliacoes_da_turma(turma):
    # A avaliação é da disciplina; a turma herda as da disciplina dela.
    return Avaliacao.objects.filter(disciplina_id=turma.disciplina_id)

def _matricula_ativa(aluno_id, turma):
    return get_object_or_404(Matricula, aluno_id=aluno_id, turma=turma, ativo=True)

def _recalcular_media_disciplina(matricula):
    turma = matricula.turma
    disciplina = turma.disciplina
    nota, _ = Nota.objects.get_or_create(
        matricula=matricula, defaults={'situacao': 'cursando'}
    )
    avaliacoes = list(_avaliacoes_da_turma(turma))
    notas_lancadas = {
        na.avaliacao_id: na.nota_obtida
        for na in NotaAvaliacao.objects.filter(nota=nota, avaliacao__in=avaliacoes)
        if na.nota_obtida is not None
    }

    if avaliacoes and len(notas_lancadas) == len(avaliacoes):
        notas_avaliacao = [(av, notas_lancadas[av.id]) for av in avaliacoes]
        nota.media_final = calcular_nota_final(disciplina, notas_avaliacao)
        nota.situacao = classificar(nota.media_final)
    else:
        nota.media_final = None
        nota.situacao = 'cursando'

    nota.nota_exame = None
    nota.save()
    return nota

def lista_notas(request):
    if request.GET.get('limpar'):
        request.session.pop('filtro_notas_disciplina', None)
        request.session.pop('filtro_notas_turma', None)
        return redirect('lista-notas')

    disciplina_id = request.POST.get('disciplina', '')
    turma_id      = request.POST.get('turma', '')

    if request.method == 'POST':
        request.session['filtro_notas_disciplina'] = disciplina_id
        request.session['filtro_notas_turma'] = turma_id
    else:
        disciplina_id = request.session.get('filtro_notas_disciplina', '')
        turma_id      = request.session.get('filtro_notas_turma', '')

    disciplinas = Disciplina.objects.filter(ativo=True)
    turmas = Turma.objects.filter(disciplina_id=disciplina_id, ativo=True).order_by('-ano', 'nome') if disciplina_id else Turma.objects.none()

    avaliacoes = []
    alunos_notas = []
    sem_avaliacoes = False

    turma_obj = Turma.objects.filter(id=turma_id, disciplina_id=disciplina_id).first() if disciplina_id and turma_id else None

    if turma_obj:
        avaliacoes = list(_avaliacoes_da_turma(turma_obj).order_by('id'))
        sem_avaliacoes = len(avaliacoes) == 0

        if not sem_avaliacoes:
            alunos = User.objects.filter(
                matricula__turma_id=turma_id, matricula__turma__ativo=True, matricula__ativo=True
            ).exclude(groups__name='professor').distinct().order_by('first_name')

            matriculas = {
                m.aluno_id: m
                for m in Matricula.objects.filter(turma=turma_obj, ativo=True)
            }
            resumos = {
                n.matricula.aluno_id: n
                for n in Nota.objects.filter(
                    matricula__in=matriculas.values()
                ).select_related('matricula')
            }

            # Garante um registro Nota pra cada aluno matriculado, mesmo sem nenhuma
            # nota lançada ainda, pra editar/excluir aparecerem em todas as linhas.
            for aluno in alunos:
                if aluno.id not in resumos:
                    resumos[aluno.id], _ = Nota.objects.get_or_create(
                        matricula=matriculas[aluno.id], defaults={'situacao': 'cursando'}
                    )

            notas_lancadas = {
                (na.nota.matricula.aluno_id, na.avaliacao_id): na.nota_obtida
                for na in NotaAvaliacao.objects.filter(
                    nota__in=resumos.values(), avaliacao__in=avaliacoes
                ).select_related('nota__matricula')
            }

            situacao_display = {
                'cursando': 'Cursando',
                'aprovado': 'Aprovado',
                'exame': 'Exame',
                'reprovado': 'Reprovado',
            }

            for aluno in alunos:
                resumo = resumos[aluno.id]
                alunos_notas.append({
                    'aluno': aluno,
                    'notas': [
                        {'avaliacao_id': av.id, 'valor': notas_lancadas.get((aluno.id, av.id))}
                        for av in avaliacoes
                    ],
                    'nota_id': resumo.id,
                    'media_final': resumo.media_final,
                    'situacao_raw': resumo.situacao,
                    'situacao_display': situacao_display.get(resumo.situacao, 'Cursando'),
                    'nota_exame': resumo.nota_exame,
                })

    return render(request, "professor/lista_notas.html", {
        "disciplinas":            disciplinas,
        "turmas":                 turmas,
        "avaliacoes":             avaliacoes,
        "alunos_notas":           alunos_notas,
        "sem_avaliacoes":         sem_avaliacoes,
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

def boletim_aluno(request):
    ano_selecionado = request.POST.get('ano', '')

    if request.method == 'POST':
        request.session['boletim_ano'] = ano_selecionado
    else:
        ano_selecionado = request.session.get('boletim_ano', str(datetime.now().year))

    notas = _notas_do_aluno(request.user, ano_selecionado)

    turmas_aluno = Turma.objects.filter(matricula__aluno=request.user, matricula__ativo=True)

    # O ano atual sempre aparece, mesmo para aluno ainda sem turma.
    anos = sorted(set(turmas_aluno.values_list('ano', flat=True)) | {datetime.now().year}, reverse=True)

    total_aprovadas = notas.filter(situacao='aprovado').count()
    total_exame = notas.filter(situacao='exame').count()
    reprovadas = notas.filter(situacao='reprovado').count()
    total_cursando = notas.filter(situacao='cursando').count()

    todas_lancadas = total_cursando == 0
    if not todas_lancadas or notas.count() == 0 or total_exame > 0:
        situacao_final = 'em_andamento'
    else:
        situacao_final = 'reprovado' if reprovadas > 2 else 'aprovado'

    if ano_selecionado:
        turmas_aluno = turmas_aluno.filter(ano=ano_selecionado)
    turma_aluno = turmas_aluno.values_list('nome', flat=True).first()

    resumos_calculo = []
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
            resumos_calculo.append(resumo)

    return render(request, "aluno/minhas_notas.html", {
        "notas":           notas,
        "anos":            anos,
        "ano_selecionado": ano_selecionado,
        "situacao_final":  situacao_final,
        "ano_atual":       datetime.now().year,
        "total_aprovadas":   total_aprovadas,
        "total_exame":       total_exame,
        "total_reprovadas":  reprovadas,
        "total_cursando":    total_cursando,
        "turma_aluno":       turma_aluno,
        "resumos_calculo":   resumos_calculo,
    })

def deletar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    nota.delete()
    messages.success(request, "Nota deletada com sucesso!")
    return redirect("lista-notas")

def editar_nota(request, id):
    nota = get_object_or_404(Nota.objects.select_related('matricula__turma__disciplina', 'matricula__aluno'), id=id)
    avaliacoes = _avaliacoes_da_turma(nota.turma).order_by('id')

    if request.method == "POST":
        valores = {}
        erro = None
        for avaliacao in avaliacoes:
            valor = request.POST.get(f'nota_avaliacao_{avaliacao.id}')
            if valor:
                try:
                    valor_float = float(valor)
                except ValueError:
                    erro = f"Nota inválida em '{avaliacao.nome}'."
                    break
                teto = teto_nota_avaliacao(nota.disciplina, avaliacao)
                if valor_float < 0 or valor_float > teto:
                    erro = f"A nota de '{avaliacao.nome}' deve estar entre 0 e {teto}."
                    break
                valores[avaliacao.id] = valor_float
            else:
                valores[avaliacao.id] = None

        if erro:
            messages.error(request, erro)
        else:
            for avaliacao_id, valor_float in valores.items():
                nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(nota=nota, avaliacao_id=avaliacao_id)
                nota_avaliacao.nota_obtida = valor_float
                nota_avaliacao.save()

            _recalcular_media_disciplina(nota.matricula)
            messages.success(request, "Nota editada com sucesso!")
            return redirect("lista-notas")

    turma_do_aluno = nota.turma

    notas_lancadas = {
        na.avaliacao_id: na.nota_obtida
        for na in NotaAvaliacao.objects.filter(nota=nota, avaliacao__in=avaliacoes)
    }
    avaliacoes_com_nota = [
        {'avaliacao': av, 'valor': notas_lancadas.get(av.id)}
        for av in avaliacoes
    ]

    aluno_nome = User.objects.filter(id=nota.aluno_id).first()

    return render(request, "professor/cadastrar_notas.html", {
        'aluno_nome': aluno_nome.first_name,
        "nota": nota,
        "editando": True,
        "turma_do_aluno": turma_do_aluno,
        "avaliacoes_com_nota": avaliacoes_com_nota,
        "sem_avaliacoes": not avaliacoes.exists(),
    })

def cadastrar_notas(request):
    if request.GET.get('limpar'):
        request.session.pop('cadastro_notas_disciplina', None)
        request.session.pop('cadastro_notas_turma', None)
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

    disciplina_id = request.session.get('cadastro_notas_disciplina', '')
    turma_id      = request.session.get('cadastro_notas_turma', '')

    if request.method == 'POST' and request.POST.get('acao') == 'salvar_notas':
        aluno_id = request.POST.get('aluno')

        if not aluno_id:
            messages.error(request, "Selecione o aluno antes de salvar.")
        else:
            disciplina = get_object_or_404(Disciplina, id=disciplina_id)
            turma = get_object_or_404(Turma, id=turma_id, disciplina_id=disciplina_id)
            matricula = _matricula_ativa(aluno_id, turma)
            nota, _ = Nota.objects.get_or_create(matricula=matricula, defaults={'situacao': 'cursando'})
            valores = {}
            erro = None
            for avaliacao in _avaliacoes_da_turma(turma):
                valor = request.POST.get(f'nota_avaliacao_{avaliacao.id}')
                if valor:
                    try:
                        valor_float = float(valor)
                    except ValueError:
                        erro = f"Nota inválida em '{avaliacao.nome}'."
                        break
                    teto = teto_nota_avaliacao(disciplina, avaliacao)
                    if valor_float < 0 or valor_float > teto:
                        erro = f"A nota de '{avaliacao.nome}' deve estar entre 0 e {teto}."
                        break
                    valores[avaliacao.id] = valor_float
                else:
                    valores[avaliacao.id] = None

            if erro:
                messages.error(request, erro)
            else:
                for avaliacao_id, valor_float in valores.items():
                    nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(nota=nota, avaliacao_id=avaliacao_id)
                    nota_avaliacao.nota_obtida = valor_float
                    nota_avaliacao.save()

                _recalcular_media_disciplina(matricula)
                messages.success(request, 'Notas cadastradas com sucesso!')
                return redirect('cadastrar-notas')

    disciplinas = Disciplina.objects.filter(ativo=True)
    turmas = Turma.objects.filter(disciplina_id=disciplina_id, ativo=True).order_by('-ano', 'nome') if disciplina_id else Turma.objects.none()

    disciplina_obj = Disciplina.objects.filter(id=disciplina_id).first() if disciplina_id else None
    turma_obj = Turma.objects.filter(id=turma_id).first() if turma_id else None

    avaliacoes = []
    alunos = []
    sem_avaliacoes = False
    notas_por_aluno_json = '{}'

    if turma_obj and str(turma_obj.disciplina_id) == str(disciplina_id):
        avaliacoes = list(_avaliacoes_da_turma(turma_obj).order_by('id'))
        sem_avaliacoes = len(avaliacoes) == 0

        if not sem_avaliacoes:
            alunos = User.objects.filter(
                matricula__turma_id=turma_id, matricula__turma__ativo=True, matricula__ativo=True
            ).exclude(groups__name='professor').distinct().order_by('first_name')

            # Pré-carrega as notas já lançadas de cada aluno para não apagar
            # dados existentes quando o formulário for salvo com campos em branco.
            notas_por_aluno = {aluno.id: {} for aluno in alunos}
            for na in NotaAvaliacao.objects.filter(
                nota__matricula__turma=turma_obj, nota__matricula__ativo=True, avaliacao__in=avaliacoes
            ).select_related('nota__matricula'):
                aluno_da_nota = na.nota.matricula.aluno_id
                if aluno_da_nota in notas_por_aluno:
                    notas_por_aluno[aluno_da_nota][na.avaliacao_id] = na.nota_obtida
            notas_por_aluno_json = json.dumps(notas_por_aluno)

    return render(request, 'professor/cadastrar_notas.html', {
        "editando": False,
        "disciplinas": disciplinas,
        "turmas": turmas,
        "disciplina_obj": disciplina_obj,
        "turma_obj": turma_obj,
        "avaliacoes": avaliacoes,
        "alunos": alunos,
        "sem_avaliacoes": sem_avaliacoes,
        "notas_por_aluno_json": notas_por_aluno_json,
        "disciplina_selecionada": disciplina_id,
        "turma_selecionada": turma_id,
    })

def _validar_total_avaliacoes(disciplina, valor_novo, excluir_id=None):
    limite = disciplina.limite_valor_avaliacoes
    if limite is None:
        return None

    valor_novo = valor_novo or 0
    total_atual = disciplina.total_valor_avaliacoes(excluir_id=excluir_id)
    novo_total = round(total_atual + valor_novo, 2)

    if novo_total > limite:
        return f"A soma dos valores das avaliações não pode passar de {limite:g}. Ficaria em {novo_total:g}."
    return None

MODOS_CALCULO_VALIDOS = {'soma', 'ponderada', 'aritmetica'}

def configurar_avaliacoes(request):
    if request.GET.get('limpar'):
        request.session.pop('config_aval_disciplina', None)
        request.session.pop('config_aval_modo', None)
        return redirect('configurar-avaliacoes')

    if request.method == 'POST':
        disciplina_id = request.POST.get('disciplina', '')
        modo = request.POST.get('modo', '')
        request.session['config_aval_disciplina'] = disciplina_id
        request.session['config_aval_modo'] = modo
    else:
        disciplina_id = request.session.get('config_aval_disciplina', '')
        modo = request.session.get('config_aval_modo', '')

    # "Trocar": volta pro estado de seleção sem apagar o que já estava escolhido,
    # pra reabrir os selects pré-preenchidos em vez de zerar a disciplina/modo.
    voltando_para_selecao = request.GET.get('trocar') == '1'

    disciplina_atual = None
    avaliacoes = Avaliacao.objects.none()
    resumo_exemplo = None
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
            disciplina_atual.modo_calculo = modo
            disciplina_atual.save()

        avaliacoes = Avaliacao.objects.select_related('disciplina').filter(
            disciplina_id=disciplina_id
        ).order_by('id')

        if avaliacoes:
            notas_exemplo = [
                (av, round(teto_nota_avaliacao(disciplina_atual, av) * 0.8 * 2) / 2)
                for av in avaliacoes
            ]
            resumo_exemplo = montar_resumo_formula(disciplina_atual, notas_exemplo)

    disciplinas = Disciplina.objects.filter(ativo=True).annotate(
        total_avaliacoes=Count('avaliacao')
    )

    return render(request, 'professor/configurar_avaliacoes.html', {
        'avaliacoes': avaliacoes,
        'disciplinas': disciplinas,
        'disciplina_selecionada': disciplina_id,
        'modo_selecionado': modo,
        'disciplina_atual': disciplina_atual,
        'sem_avaliacoes': bool(disciplina_id and modo_valido and not avaliacoes),
        'resumo_exemplo': resumo_exemplo,
    })

@require_POST
def resetar_avaliacoes_modo(request, id):
    disciplina = get_object_or_404(Disciplina, id=id)
    Avaliacao.objects.filter(disciplina=disciplina).delete()

    request.session['config_aval_disciplina'] = str(disciplina.id)
    request.session.pop('config_aval_modo', None)

    messages.success(request, f"Avaliações de {disciplina.nome} apagadas. Escolha o novo modo de cálculo.")
    return redirect('configurar-avaliacoes')

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

        erro = _validar_total_avaliacoes(disciplina, float(valor) if valor else 0)
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
    avaliacao = get_object_or_404(Avaliacao, id=id)

    if request.method == "POST":
        valor = request.POST.get("valor") or None
        disciplina_id = request.POST.get("disciplina")
        disciplina = get_object_or_404(Disciplina, id=disciplina_id)

        erro = _validar_total_avaliacoes(disciplina, float(valor) if valor else 0, excluir_id=avaliacao.id)
        if erro:
            messages.error(request, erro)
            return render(request, "professor/editar_avaliacao.html", {
                "avaliacao": avaliacao,
                "disciplinas": Disciplina.objects.filter(ativo=True),
            })

        avaliacao.nome = request.POST.get("nome")
        avaliacao.tipo = request.POST.get("tipo")
        avaliacao.valor = valor
        avaliacao.disciplina_id = disciplina_id
        avaliacao.save()
        messages.success(request, "Avaliação editada com sucesso!")
        return redirect("configurar-avaliacoes")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "professor/editar_avaliacao.html", {
        "avaliacao": avaliacao,
        "disciplinas": disciplinas,
    })

def deletar_avaliacao(request, id):
    avaliacao = get_object_or_404(Avaliacao, id=id)
    avaliacao.delete()
    messages.success(request, "Avaliação excluída com sucesso!")
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
    erro = _validar_total_avaliacoes(disciplina, float(valor) if valor else 0)
    if erro:
        return JsonResponse({'ok': False, 'erro': erro})

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
    avaliacao = get_object_or_404(Avaliacao, id=request.POST.get('id'))
    campo = request.POST.get('campo')
    valor = request.POST.get('valor')

    if campo not in {'nome', 'tipo', 'valor'}:
        return JsonResponse({'ok': False, 'erro': 'Campo inválido'})

    if campo == 'valor':
        valor_float = float(valor) if valor else 0
        erro = _validar_total_avaliacoes(avaliacao.disciplina, valor_float, excluir_id=avaliacao.id)
        if erro:
            return JsonResponse({'ok': False, 'erro': erro})
        avaliacao.valor = valor_float if valor else None
    else:
        if not valor:
            return JsonResponse({'ok': False, 'erro': 'Esse campo não pode ficar vazio'})
        setattr(avaliacao, campo, valor)

    avaliacao.save()

    return JsonResponse({
        'ok': True,
        'tipo_display': avaliacao.get_tipo_display(),
    })

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
    if not turma_nome:
        return JsonResponse({'disciplinas': []})

    disciplinas = Disciplina.objects.filter(
        turma__nome=turma_nome,
        turma__ativo=True,
        ativo=True
    ).distinct().values('id', 'nome')

    return JsonResponse({'disciplinas': list(disciplinas)})

def turmas_por_disciplina(request):
    disciplina_id = request.GET.get('disciplina')
    if not disciplina_id:
        return JsonResponse({'turmas': []})

    turmas = Turma.objects.filter(
        disciplina_id=disciplina_id, ativo=True
    ).order_by('-ano', 'nome').values('id', 'nome', 'ano')

    return JsonResponse({'turmas': list(turmas)})

@require_POST
def editar_nota_avaliacao_ajax(request):
    aluno_id = request.POST.get('aluno')
    avaliacao_id = request.POST.get('avaliacao')
    valor = request.POST.get('valor')

    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    turma = get_object_or_404(Turma, id=request.POST.get('turma'), disciplina_id=avaliacao.disciplina_id)
    matricula = _matricula_ativa(aluno_id, turma)

    if valor:
        try:
            valor_float = float(valor)
        except ValueError:
            return JsonResponse({'ok': False, 'erro': 'Nota inválida.'})

        teto = teto_nota_avaliacao(avaliacao.disciplina, avaliacao)
        if valor_float < 0 or valor_float > teto:
            return JsonResponse({'ok': False, 'erro': f'A nota deve estar entre 0 e {teto}.'})
    else:
        valor_float = None

    nota, _ = Nota.objects.get_or_create(matricula=matricula, defaults={'situacao': 'cursando'})
    nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(nota=nota, avaliacao_id=avaliacao_id)
    nota_avaliacao.nota_obtida = valor_float
    nota_avaliacao.save()

    nota = _recalcular_media_disciplina(matricula)

    situacao_display = {
        'cursando': 'Cursando',
        'aprovado': 'Aprovado',
        'exame': 'Exame',
        'reprovado': 'Reprovado',
    }
    return JsonResponse({
        'ok': True,
        'media_final': nota.media_final,
        'situacao': situacao_display.get(nota.situacao, nota.situacao),
    })

@require_POST
def lancar_nota_exame_ajax(request):
    nota = get_object_or_404(Nota, id=request.POST.get('id'))
    valor = request.POST.get('valor')

    if nota.situacao != 'exame':
        return JsonResponse({'ok': False, 'erro': 'Essa nota não está em exame.'})

    if not valor:
        return JsonResponse({'ok': False, 'erro': 'Informe a nota do exame.'})

    try:
        valor_float = float(valor)
    except ValueError:
        return JsonResponse({'ok': False, 'erro': 'Nota inválida.'})

    if valor_float < 0 or valor_float > 10:
        return JsonResponse({'ok': False, 'erro': 'A nota do exame deve estar entre 0 e 10.'})

    _, situacao_final = calcular_pos_exame(nota.media_final, valor_float)
    nota.nota_exame = valor_float
    nota.situacao = situacao_final
    nota.save()

    situacao_display = {
        'aprovado': 'Aprovado',
        'reprovado': 'Reprovado',
    }
    return JsonResponse({
        'ok': True,
        'situacao': situacao_display[situacao_final],
    })

def gerar_relatorio(request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="boletim.pdf"'

    width, height = A4
    c = canvas.Canvas(response, pagesize=A4)

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
    y -= 25
    c.setFont("Helvetica", 10)

    for nota in notas:
        c.drawString(72,  y, str(nota.disciplina))
        c.drawString(370, y, str(nota.media_final if nota.media_final is not None else '-'))
        c.drawString(430, y, nota.get_situacao_display())
        y -= 20

        if y < 72:
            c.showPage()
            y = height - 72

    # Situação Final
    total_cursando = notas.filter(situacao='cursando').count()
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

    c.save()
    return response

def gestao_dashboard(request):
    return render(request, "gestao/dashboard.html")

def gestao_disciplinas(request):
    # 1. Busca inicial: todas as disciplinas ativas, já trazendo o professor junto
    disciplinas = Disciplina.objects.filter(ativo=True).select_related("professor")

    # 2. Botão "Limpar filtros": apaga a memória e recarrega a página limpa
    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_professor', None)
        request.session.pop('filtro_gestao_disciplina', None)
        return redirect('gestao-disciplinas')

    # 3. Lê o que foi escolhido nos dropdowns
    professor_id  = request.POST.get('professor', '')
    disciplina_id = request.POST.get('disciplina', '')

    # 4. Memória: se clicou em Filtrar (POST) anota na sessão; se só abriu a página (GET) relembra
    if request.method == 'POST':
        request.session['filtro_gestao_professor'] = professor_id
        request.session['filtro_gestao_disciplina'] = disciplina_id
    else:
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

    disciplina_id = request.POST.get('disciplina', '')
    turma_nome    = request.POST.get('turma', '')

    if request.method == 'POST':
        request.session['filtro_gestao_turma_disciplina'] = disciplina_id
        request.session['filtro_gestao_turma_nome'] = turma_nome
    else:
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


def cadastrar_turma(request):
    if request.method == 'POST':
        nome = request.POST.get('nome')
        disciplina_id = request.POST.get('disciplina')
        ano = request.POST.get('ano')

        if not nome or not disciplina_id or not ano:
            messages.error(request, "Preencha o nome, o ano e selecione a disciplina antes de salvar.")
            return render(request, "gestao/cadastrar_turma.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
                "ano": ano,
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

    if request.method == "POST":
        turma.nome = request.POST.get("nome")
        turma.disciplina_id = request.POST.get("disciplina")
        turma.ano = request.POST.get("ano")
        turma.save()
        messages.success(request, "Turma editada com sucesso!")
        return redirect("gestao-turmas")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "gestao/editar_turma.html", {
        "turma": turma,
        "disciplinas": disciplinas,
    })

def cadastrar_matricula(request):
    if request.method == 'POST':
        aluno_id = request.POST.get('aluno')
        turma_id = request.POST.get('turma')

        if not aluno_id or not turma_id:
            messages.error(request, "Selecione o aluno e a turma antes de salvar.")
            return render(request, "gestao/cadastrar_matricula.html", {
                "alunos": User.objects.filter(groups__name='aluno'),
                "turmas": Turma.objects.filter(ativo=True).select_related("disciplina"),
            })

        existe_matricula = Matricula.objects.filter(aluno_id=aluno_id, turma_id=turma_id, ativo=True).first()
        if existe_matricula:
            messages.error(request, "Esse aluno já está matriculado nessa turma!")
            return render(request, "gestao/cadastrar_matricula.html", {
                "alunos": User.objects.filter(groups__name='aluno'),
                "turmas": Turma.objects.filter(ativo=True).select_related("disciplina"),
            })

        Matricula.objects.create(aluno_id=aluno_id, turma_id=turma_id)
        messages.success(request, "Matrícula cadastrada com sucesso!")
        return redirect("gestao-matriculas")

    alunos = User.objects.filter(groups__name='aluno')
    turmas = Turma.objects.filter(ativo=True).select_related("disciplina")

    return render(request, "gestao/cadastrar_matricula.html", {
        "alunos": alunos,
        "turmas": turmas,
    })

def deletar_matricula(request, id):
    matricula = get_object_or_404(Matricula, id=id)
    matricula.ativo = False
    matricula.save()
    messages.success(request, "Matrícula excluída com sucesso!")
    return redirect("gestao-matriculas")

def editar_matricula(request, id):
    matricula = get_object_or_404(Matricula, id=id)

    if request.method == "POST":
        matricula.aluno_id = request.POST.get("aluno")
        matricula.turma_id = request.POST.get("turma")
        matricula.save()
        messages.success(request, "Matrícula editada com sucesso!")
        return redirect("gestao-matriculas")

    alunos = User.objects.filter(groups__name='aluno')
    turmas = Turma.objects.filter(ativo=True).select_related("disciplina")

    return render(request, "gestao/editar_matricula.html", {
        "matricula": matricula,
        "alunos": alunos,
        "turmas": turmas,
    })

def gestao_matriculas(request):
    matriculas = Matricula.objects.filter(ativo=True).select_related("aluno", "turma", "turma__disciplina")

    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_mat_turma', None)
        request.session.pop('filtro_gestao_mat_aluno', None)
        return redirect('gestao-matriculas')

    turma_id = request.POST.get('turma', '')
    aluno_id = request.POST.get('aluno', '')

    if request.method == 'POST':
        request.session['filtro_gestao_mat_turma'] = turma_id
        request.session['filtro_gestao_mat_aluno'] = aluno_id
    else:
        turma_id = request.session.get('filtro_gestao_mat_turma', '')
        aluno_id = request.session.get('filtro_gestao_mat_aluno', '')

    if turma_id:
        matriculas = matriculas.filter(turma_id=turma_id)

    if aluno_id:
        matriculas = matriculas.filter(aluno_id=aluno_id)

    todas_turmas = Turma.objects.filter(ativo=True).select_related("disciplina")
    alunos = User.objects.filter(groups__name='aluno')

    return render(request, "gestao/matriculas_lista.html", {
        "matriculas":          matriculas,
        "todas_turmas":        todas_turmas,
        "alunos":              alunos,
        "turma_selecionada":   turma_id,
        "aluno_selecionado":   aluno_id,
    })

def gestao_usuarios(request):
    usuarios = User.objects.filter(is_active=True, is_superuser=False, groups__isnull=False).prefetch_related("groups").distinct()

    if request.GET.get('limpar'):
        request.session.pop('filtro_gestao_usuario_grupo', None)
        return redirect('gestao-usuarios')

    grupo_nome = request.POST.get('grupo', '')

    if request.method == 'POST':
        request.session['filtro_gestao_usuario_grupo'] = grupo_nome
    else:
        grupo_nome = request.session.get('filtro_gestao_usuario_grupo', '')

    if grupo_nome:
        usuarios = usuarios.filter(groups__name=grupo_nome)

    grupos = Group.objects.filter(name__in=['aluno', 'professor', 'administrador'])

    return render(request, "gestao/usuarios_lista.html", {
        "usuarios":          usuarios,
        "grupos":            grupos,
        "grupo_selecionado": grupo_nome,
    })

def cadastrar_usuario(request):
    grupos = Group.objects.filter(name__in=['aluno', 'professor', 'administrador'])

    if request.method == 'POST':
        username   = request.POST.get('username')
        first_name = request.POST.get('first_name')
        password   = request.POST.get('password')
        grupo_nome = request.POST.get('grupo')

        if not username or not first_name or not password or not grupo_nome:
            messages.error(request, "Preencha todos os campos antes de salvar.")
            return render(request, "gestao/cadastrar_usuario.html", {"grupos": grupos})

        if User.objects.filter(username=username).exists():
            messages.error(request, "Já existe um usuário com esse nome de usuário!")
            return render(request, "gestao/cadastrar_usuario.html", {"grupos": grupos})

        usuario = User.objects.create(username=username, first_name=first_name)
        usuario.set_password(password)
        usuario.save()
        usuario.groups.add(Group.objects.get(name=grupo_nome))

        messages.success(request, "Usuário cadastrado com sucesso!")
        return redirect("gestao-usuarios")

    return render(request, "gestao/cadastrar_usuario.html", {"grupos": grupos})

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

        password = request.POST.get("password")
        if password:
            usuario.set_password(password)

        usuario.save()

        grupo_nome = request.POST.get("grupo")
        usuario.groups.clear()
        usuario.groups.add(Group.objects.get(name=grupo_nome))

        messages.success(request, "Usuário editado com sucesso!")
        return redirect("gestao-usuarios")

    grupo_atual = usuario.groups.first()

    return render(request, "gestao/editar_usuario.html", {
        "usuario":     usuario,
        "grupos":      grupos,
        "grupo_atual": grupo_atual,
    })
