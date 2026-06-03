from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from aluno.models import Disciplina, Nota, Turma, Matricula
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.db.models import Min
from datetime import datetime
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

def lista_notas(request):
    notas = Nota.objects.filter(ativo=True).select_related('aluno', 'disciplina').prefetch_related('aluno__turmas')# Busca as notas com os dados em uma única query
    
     # Limpa sessão e redireciona sem filtro
    if request.GET.get('limpar'):
        request.session.pop('filtro_turma', None)
        request.session.pop('filtro_disciplina', None)
        request.session.pop('filtro_ano', None)
        return redirect('lista-notas')

    turma_nome    = request.POST.get('turma', '')
    disciplina_id = request.POST.get('disciplina', '')
    ano           = request.POST.get('ano', '')

    # salva na sessão se vieram filtros, senão usa o que já estava
    if request.method == 'POST':
        request.session['filtro_turma'] = turma_nome
        request.session['filtro_disciplina'] = disciplina_id
        request.session['filtro_ano'] = ano
    else:
        turma_nome    = request.session.get('filtro_turma', '')
        disciplina_id = request.session.get('filtro_disciplina', '')
        ano           = request.session.get('filtro_ano', '')

    if turma_nome:
        alunos_da_turma = User.objects.filter(turmas__nome=turma_nome)
        notas = notas.filter(aluno__in=alunos_da_turma)

    if disciplina_id:
        notas = notas.filter(disciplina_id=disciplina_id)

    if ano:
        notas = notas.filter(ano=ano)

    turmas      = Turma.objects.filter(ativo=True).values_list('nome', flat=True).distinct()
    disciplinas = Disciplina.objects.filter(ativo=True)
    anos = Nota.objects.filter(ativo=True, ano__isnull=False).values_list('ano', flat=True).distinct().order_by('-ano')

    return render(request, "aluno/lista_notas.html", {
        "notas":                  notas,
        "turmas":                 turmas,
        "disciplinas":            disciplinas,
        "anos":                   anos,
        "ano_selecionado":        ano,
        "turma_selecionada":      turma_nome,
        "disciplina_selecionada": disciplina_id,
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

    return render(request, "aluno/minhas_notas.html", {
        "notas":           notas,
        "anos":            anos,
        "ano_selecionado": ano_selecionado,
        "ano_atual":       datetime.now().year,
    })

def deletar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    nota.delete()
    messages.success(request, "Nota deletada com sucesso!")
    return redirect("lista-notas")

def editar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    
    if request.method == "POST":
        ##nota.aluno_id = request.POST.get("aluno")
        nota.disciplina_id = request.POST.get("disciplina")
        nota.nota_p1 = request.POST.get("nota_p1") or None
        nota.nota_p2 = request.POST.get("nota_p2") or None
        nota.nota_t1 = request.POST.get("nota_t1") or None
        nota.nota_t2 = request.POST.get("nota_t2") or None
        nota.media_final = request.POST.get("media_final") or None

        if nota.media_final is not None:
            media = float(nota.media_final)
            if media >= 7:
                situacao = "aprovado"
            elif media >= 5:
                situacao = "recuperacao"
            else:
                situacao = "reprovado"

            nota.situacao = situacao
        else:
            nota.situacao = "cursando"
        nota.save()
        messages.success(request, "Nota editada com sucesso!")
        return redirect("lista-notas")

    turma_do_aluno = Turma.objects.filter(alunos=nota.aluno,disciplina=nota.disciplina).first()

    turmas = Turma.objects.filter(ativo=True).values('nome').annotate(id=Min('id')).distinct()
    disciplinas = Disciplina.objects.all()

    aluno_nome = User.objects.filter(id=nota.aluno_id).first()

    return render(request, "aluno/cadastrar_notas.html", {
        'aluno_nome': aluno_nome.first_name,
        "turmas": turmas,
        "disciplinas": disciplinas,
        "nota": nota,
        "editando": True,
        "turma_do_aluno": turma_do_aluno
    })

def cadastrar_notas(request):
    if request.method == 'POST':
        aluno_id      = request.POST.get('aluno')
        disciplina_id = request.POST.get('disciplina')
        nota_p1       = request.POST.get('nota_p1') or None
        nota_p2       = request.POST.get('nota_p2') or None
        nota_t1       = request.POST.get('nota_t1') or None
        nota_t2       = request.POST.get('nota_t2') or None
        ano           = request.POST.get('ano') or None
        turma         = request.POST.get('turma') or None

        if turma and disciplina_id:
            request.session['ultima_turma'] = turma
            request.session['ultima_disciplina'] = disciplina_id

        # Não deixa criar um registro duplicado
        existe_nota = Nota.objects.filter(
            aluno=aluno_id, 
            disciplina=disciplina_id,
            ano=ano
        ).first()
        if existe_nota:
            contexto = {
                "turmas": Turma.objects.filter(ativo=True).values('nome').annotate(id=Min('id')).distinct(),
                "disciplinas": Disciplina.objects.all(),
                "editando": False,
                "ano_atual": datetime.now().year,
                "ultima_turma": request.session.get('ultima_turma', ''),
                "ultima_disciplina": request.session.get('ultima_disciplina', ''),
            }
            messages.error(request, "Esse aluno já possui um registro para essa disciplina nesse ano!")
            return render(request, "aluno/cadastrar_notas.html", contexto)

        # Calcula a média
        notas = [float(n) for n in [nota_p1, nota_p2, nota_t1, nota_t2] if n]
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

        Nota.objects.create(
            aluno_id=aluno_id,
            disciplina_id=disciplina_id,
            nota_p1=nota_p1,
            nota_p2=nota_p2,
            nota_t1=nota_t1,
            nota_t2=nota_t2,
            ano=ano,
            media_final=media_final,
            situacao=situacao,
        )

        turmas = Turma.objects.filter(ativo=True).values('nome').annotate(id=Min('id')).distinct()
        alunos = User.objects.filter(is_staff=False) 
        disciplinas = Disciplina.objects.filter(ativo=True)

        messages.success(request, 'Nota cadastrada com sucesso!')
        return render(request, 'aluno/cadastrar_notas.html', {
            'turmas': turmas,
            'alunos': alunos,
            'disciplinas': disciplinas,
            "editando": False,
            "ano_atual": datetime.now().year,
            "ultima_turma": request.session.get('ultima_turma', ''),
            "ultima_disciplina": request.session.get('ultima_disciplina', ''),
        })

    turmas = Turma.objects.filter(ativo=True).values('nome').annotate(id=Min('id')).distinct()
    alunos = User.objects.filter(is_staff=False) 
    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, 'aluno/cadastrar_notas.html', {
        'turmas': turmas,
        'alunos': alunos,
        'disciplinas': disciplinas,
        "editando": False,
        "ano_atual": datetime.now().year,
    })

def alunos_por_turma(request):
    turma_id = request.GET.get('turma_id')
    if not turma_id:
        return JsonResponse({'alunos': []})
    
    turma = get_object_or_404(Turma, id=turma_id)
    alunos = turma.alunos.all().values('id', 'first_name', 'username')
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

def gerar_relatorio(request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="boletim.pdf"'

    width, height = A4
    c = canvas.Canvas(response, pagesize=A4)

    # Cabeçalho
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 60, "Boletim Escolar")

    c.setFont("Helvetica", 12)
    c.drawString(72, height - 85, f"Aluno: {request.user.first_name}")

    ano = request.session.get('boletim_ano', str(datetime.now().year))
    c.drawString(72, height - 105, f"Ano: {ano}")

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

        if y < 72:  # nova página se acabar espaço
            c.showPage()
            y = height - 72

    c.save()
    return response
    
    