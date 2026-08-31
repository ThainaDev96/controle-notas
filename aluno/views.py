from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from aluno.models import Disciplina, Nota, Turma, Matricula, Avaliacao, NotaAvaliacao
from django.contrib.auth.models import User, Group
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
                return redirect("lista-notas")

            return redirect("minhas-notas")

        messages.error(request, "Usuário ou senha inválidos")

    return render(request, "aluno/login.html")

def logout_view(request):
    logout(request)
    return render(request, "aluno/login.html")

def listar_disciplinas(request):
    disciplinas = Disciplina.objects.filter(ativo=True)
    return render(request, 'aluno/lista.html', {'disciplinas': disciplinas})

def _recalcular_media_disciplina(aluno_id, disciplina_id, ano=None):
    ano = ano or datetime.now().year
    avaliacoes = list(Avaliacao.objects.filter(disciplina_id=disciplina_id, ano=ano))
    notas_lancadas = {
        na.avaliacao_id: na.nota
        for na in NotaAvaliacao.objects.filter(aluno_id=aluno_id, avaliacao__in=avaliacoes)
        if na.nota is not None
    }

    nota, _ = Nota.objects.get_or_create(
        aluno_id=aluno_id, disciplina_id=disciplina_id, ano=ano,
        defaults={'situacao': 'cursando'}
    )

    if avaliacoes and len(notas_lancadas) == len(avaliacoes):
        soma_pesos = sum((av.valor or 0) for av in avaliacoes)
        if soma_pesos > 0:
            media = sum(notas_lancadas[av.id] * (av.valor or 0) for av in avaliacoes) / soma_pesos
        else:
            media = sum(notas_lancadas.values()) / len(avaliacoes)
        media = round(media, 2)

        if media >= 7:
            situacao = 'aprovado'
        elif media >= 5:
            situacao = 'recuperacao'
        else:
            situacao = 'reprovado'

        nota.media_final = media
        nota.situacao = situacao
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

    disciplina_id = request.POST.get('disciplina', '')
    turma_id      = request.POST.get('turma', '')

    if request.method == 'POST':
        request.session['filtro_notas_disciplina'] = disciplina_id
        request.session['filtro_notas_turma'] = turma_id
    else:
        disciplina_id = request.session.get('filtro_notas_disciplina', '')
        turma_id      = request.session.get('filtro_notas_turma', '')

    disciplinas = Disciplina.objects.filter(ativo=True)
    turmas = Turma.objects.filter(disciplina_id=disciplina_id, ativo=True) if disciplina_id else Turma.objects.none()

    avaliacoes = []
    alunos_notas = []
    sem_avaliacoes = False

    if disciplina_id and turma_id:
        avaliacoes = list(Avaliacao.objects.filter(disciplina_id=disciplina_id, ano=datetime.now().year).order_by('id'))
        sem_avaliacoes = len(avaliacoes) == 0

        if not sem_avaliacoes:
            alunos = User.objects.filter(
                turmas__id=turma_id, turmas__ativo=True
            ).exclude(groups__name='professor').distinct().order_by('first_name')

            notas_lancadas = {
                (na.aluno_id, na.avaliacao_id): na.nota
                for na in NotaAvaliacao.objects.filter(aluno__in=alunos, avaliacao__in=avaliacoes)
            }
            resumos = {
                n.aluno_id: n
                for n in Nota.objects.filter(aluno__in=alunos, disciplina_id=disciplina_id, ano=datetime.now().year)
            }

            # Garante um registro Nota pra cada aluno matriculado, mesmo sem nenhuma
            # nota lançada ainda, pra editar/excluir aparecerem em todas as linhas.
            for aluno in alunos:
                if aluno.id not in resumos:
                    resumos[aluno.id], _ = Nota.objects.get_or_create(
                        aluno_id=aluno.id, disciplina_id=disciplina_id, ano=datetime.now().year,
                        defaults={'situacao': 'cursando'}
                    )

            situacao_display = {
                'cursando': 'Cursando',
                'aprovado': 'Aprovado',
                'recuperacao': 'Recuperação',
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
                    'situacao_display': situacao_display.get(resumo.situacao, 'Cursando'),
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

def boletim_aluno(request):
    notas = Nota.objects.filter(ativo=True, aluno=request.user)

    ano_selecionado = request.POST.get('ano', '')

    if request.method == 'POST':
        request.session['boletim_ano'] = ano_selecionado
    else:
        ano_selecionado = request.session.get('boletim_ano', str(datetime.now().year))

    if ano_selecionado:
        notas = notas.filter(ano=ano_selecionado)

    anos = Nota.objects.filter(
        ativo=True, aluno=request.user, ano__isnull=False
    ).values_list('ano', flat=True).distinct().order_by('-ano')

    total_aprovadas = notas.filter(situacao='aprovado').count()
    total_recuperacao = notas.filter(situacao='recuperacao').count()
    reprovadas = notas.filter(situacao='reprovado').count()
    total_cursando = notas.filter(situacao='cursando').count()

    todas_lancadas = total_cursando == 0
    if not todas_lancadas or notas.count() == 0 or total_recuperacao > 0:
        situacao_final = 'em_andamento'
    else:
        situacao_final = 'reprovado' if reprovadas > 2 else 'aprovado'

    turma_aluno = Turma.objects.filter(
        alunos=request.user
    ).values_list('nome', flat=True).first()

    return render(request, "aluno/minhas_notas.html", {
        "notas":           notas,
        "anos":            anos,
        "ano_selecionado": ano_selecionado,
        "situacao_final":  situacao_final,
        "ano_atual":       datetime.now().year,
        "total_aprovadas":   total_aprovadas,
        "total_recuperacao": total_recuperacao,
        "total_reprovadas":  reprovadas,
        "total_cursando":    total_cursando,
        "turma_aluno":       turma_aluno,
    })

def deletar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    nota.delete()
    messages.success(request, "Nota deletada com sucesso!")
    return redirect("lista-notas")

def editar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    avaliacoes = Avaliacao.objects.filter(disciplina_id=nota.disciplina_id, ano=nota.ano).order_by('id')

    if request.method == "POST":
        for avaliacao in avaliacoes:
            valor = request.POST.get(f'nota_avaliacao_{avaliacao.id}')
            nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(aluno_id=nota.aluno_id, avaliacao_id=avaliacao.id)
            nota_avaliacao.nota = float(valor) if valor else None
            nota_avaliacao.save()

        _recalcular_media_disciplina(nota.aluno_id, nota.disciplina_id, ano=nota.ano)
        messages.success(request, "Nota editada com sucesso!")
        return redirect("lista-notas")

    turma_do_aluno = Turma.objects.filter(alunos=nota.aluno, disciplina=nota.disciplina).first()

    notas_lancadas = {
        na.avaliacao_id: na.nota
        for na in NotaAvaliacao.objects.filter(aluno_id=nota.aluno_id, avaliacao__in=avaliacoes)
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
            for avaliacao in Avaliacao.objects.filter(disciplina_id=disciplina_id, ano=datetime.now().year):
                valor = request.POST.get(f'nota_avaliacao_{avaliacao.id}')
                nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(aluno_id=aluno_id, avaliacao_id=avaliacao.id)
                nota_avaliacao.nota = float(valor) if valor else None
                nota_avaliacao.save()

            _recalcular_media_disciplina(aluno_id, disciplina_id)
            messages.success(request, 'Notas cadastradas com sucesso!')
            return redirect('cadastrar-notas')

    disciplinas = Disciplina.objects.filter(ativo=True)
    turmas = Turma.objects.filter(disciplina_id=disciplina_id, ativo=True) if disciplina_id else Turma.objects.none()

    disciplina_obj = Disciplina.objects.filter(id=disciplina_id).first() if disciplina_id else None
    turma_obj = Turma.objects.filter(id=turma_id).first() if turma_id else None

    avaliacoes = []
    alunos = []
    sem_avaliacoes = False
    notas_por_aluno_json = '{}'

    if disciplina_id and turma_id:
        avaliacoes = list(Avaliacao.objects.filter(disciplina_id=disciplina_id, ano=datetime.now().year).order_by('id'))
        sem_avaliacoes = len(avaliacoes) == 0

        if not sem_avaliacoes:
            alunos = User.objects.filter(
                turmas__id=turma_id, turmas__ativo=True
            ).exclude(groups__name='professor').distinct().order_by('first_name')

            # Pré-carrega as notas já lançadas de cada aluno para não apagar
            # dados existentes quando o formulário for salvo com campos em branco.
            notas_por_aluno = {aluno.id: {} for aluno in alunos}
            for na in NotaAvaliacao.objects.filter(aluno__in=alunos, avaliacao__in=avaliacoes):
                notas_por_aluno[na.aluno_id][na.avaliacao_id] = na.nota
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

def configurar_avaliacoes(request):
    if request.GET.get('limpar'):
        request.session.pop('filtro_avaliacao_disciplina', None)
        return redirect('configurar-avaliacoes')

    disciplina_id = request.POST.get('disciplina', '')

    if request.method == 'POST':
        request.session['filtro_avaliacao_disciplina'] = disciplina_id
    else:
        disciplina_id = request.session.get('filtro_avaliacao_disciplina', '')

    if disciplina_id:
        avaliacoes = Avaliacao.objects.select_related('disciplina').filter(
            disciplina_id=disciplina_id, ano=datetime.now().year
        )
    else:
        avaliacoes = Avaliacao.objects.none()

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, 'professor/configurar_avaliacoes.html', {
        'avaliacoes': avaliacoes,
        'disciplinas': disciplinas,
        'disciplina_selecionada': disciplina_id,
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

        Avaliacao.objects.create(nome=nome, tipo=tipo, valor=valor, disciplina_id=disciplina_id, ano=datetime.now().year)
        messages.success(request, "Avaliação cadastrada com sucesso!")
        return redirect("configurar-avaliacoes")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "professor/cadastrar_avaliacao.html", {
        "disciplinas": disciplinas,
    })

def editar_avaliacao(request, id):
    avaliacao = get_object_or_404(Avaliacao, id=id)

    if request.method == "POST":
        avaliacao.nome = request.POST.get("nome")
        avaliacao.tipo = request.POST.get("tipo")
        avaliacao.valor = request.POST.get("valor") or None
        avaliacao.disciplina_id = request.POST.get("disciplina")
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

    avaliacao = Avaliacao.objects.create(
        nome=nome, tipo=tipo, valor=valor, disciplina_id=disciplina_id, ano=datetime.now().year
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
        setattr(avaliacao, campo, float(valor) if valor else None)
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
        turmas__nome=turma_nome,
        turmas__ativo=True
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
    ).values('id', 'nome')

    return JsonResponse({'turmas': list(turmas)})

@require_POST
def editar_nota_ajax(request):
    nota = get_object_or_404(Nota, id=request.POST.get('id'))
    campo = request.POST.get('campo')
    valor = request.POST.get('valor')

    if campo not in {'nota_p1', 'nota_t1', 'nota_p2', 'nota_t2'}:
        return JsonResponse({'ok': False, 'erro': 'Campo inválido'})

    setattr(nota, campo, float(valor) if valor else None)

    # Recalcula a média igual ao cadastro
    notas = [n for n in [nota.nota_p1, nota.nota_p2, nota.nota_t1, nota.nota_t2] if n is not None]
    media_final = round(sum(notas) / len(notas), 2) if len(notas) == 4 else None

    # Situação automática pela média
    if media_final is None:
        situacao = 'cursando'
    elif media_final >= 7:
        situacao = 'aprovado'
    elif media_final >= 5:
        situacao = 'recuperacao'
    else:
        situacao = 'reprovado'

    nota.media_final = media_final
    nota.situacao = situacao
    nota.save()

    situacao_display = {
        'cursando': 'Cursando',
        'aprovado': 'Aprovado',
        'recuperacao': 'Recuperação',
        'reprovado': 'Reprovado',
    }
    return JsonResponse({
        'ok': True,
        'media_final': nota.media_final,
        'situacao': situacao_display.get(situacao, situacao)
    })

@require_POST
def editar_nota_avaliacao_ajax(request):
    aluno_id = request.POST.get('aluno')
    avaliacao_id = request.POST.get('avaliacao')
    valor = request.POST.get('valor')

    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)

    nota_avaliacao, _ = NotaAvaliacao.objects.get_or_create(aluno_id=aluno_id, avaliacao_id=avaliacao_id)
    nota_avaliacao.nota = float(valor) if valor else None
    nota_avaliacao.save()

    nota = _recalcular_media_disciplina(aluno_id, avaliacao.disciplina_id)

    situacao_display = {
        'cursando': 'Cursando',
        'aprovado': 'Aprovado',
        'recuperacao': 'Recuperação',
        'reprovado': 'Reprovado',
    }
    return JsonResponse({
        'ok': True,
        'media_final': nota.media_final,
        'situacao': situacao_display.get(nota.situacao, nota.situacao),
    })

def gerar_relatorio(request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="boletim.pdf"'

    width, height = A4
    c = canvas.Canvas(response, pagesize=A4)

    turma_aluno = Turma.objects.filter(
        alunos=request.user
    ).values_list('nome', flat=True).first()

    ano = request.session.get('boletim_ano', str(datetime.now().year))

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
    c.drawString(250, y, "P1")
    c.drawString(290, y, "P2")
    c.drawString(330, y, "T1")
    c.drawString(370, y, "T2")
    c.drawString(410, y, "MF")
    c.drawString(450, y, "Situação")

    c.line(72, y - 5, width - 72, y - 5)

    # Linhas de notas
    notas = Nota.objects.filter(ativo=True, aluno=request.user, ano=ano)
    y -= 25
    c.setFont("Helvetica", 10)

    for nota in notas:
        c.drawString(72,  y, str(nota.disciplina))
        c.drawString(250, y, str(nota.nota_p1 or '-'))
        c.drawString(290, y, str(nota.nota_p2 or '-'))
        c.drawString(330, y, str(nota.nota_t1 or '-'))
        c.drawString(370, y, str(nota.nota_t2 or '-'))
        c.drawString(410, y, str(nota.media_final or '-'))
        c.drawString(450, y, str(nota.situacao or '-'))
        y -= 20

        if y < 72:
            c.showPage()
            y = height - 72

    # Situação Final
    total_cursando = notas.filter(situacao='cursando').count()
    total_reprovadas = notas.filter(situacao='reprovado').count()
    total_aprovadas = notas.filter(situacao='aprovado').count()
    total_recuperacao = notas.filter(situacao='recuperacao').count()

    todas_lancadas = total_cursando == 0
    if not todas_lancadas or notas.count() == 0 or total_recuperacao > 0:
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
    resumo = f"{total_aprovadas} aprovadas  ·  {total_recuperacao} em recuperação  ·  {total_reprovadas} reprovadas"
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

        if not nome or not disciplina_id:
            messages.error(request, "Preencha o nome e selecione a disciplina antes de salvar.")
            return render(request, "gestao/cadastrar_turma.html", {
                "disciplinas": Disciplina.objects.filter(ativo=True),
                "nome": nome,
            })

        Turma.objects.create(nome=nome, disciplina_id=disciplina_id)
        messages.success(request, "Turma cadastrada com sucesso!")
        return redirect("gestao-turmas")

    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, "gestao/cadastrar_turma.html", {
        "disciplinas": disciplinas,
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
