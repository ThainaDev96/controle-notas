from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from aluno.models import Disciplina, Nota, Turma
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.db.models import Min


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("admin:index")

        messages.error(request, "Usuário ou senha inválidos")

    return render(request, "aluno/login.html")


def listar_disciplinas(request):
    disciplinas = Disciplina.objects.filter(ativo=True)
    return render(request, 'aluno/lista.html', {'disciplinas': disciplinas})

def lista_notas(request):
    notas = Nota.objects.filter(ativo=True)
    return render(request, "aluno/lista_notas.html", {"notas": notas})

def deletar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    nota.delete()
    messages.success(request, "Nota deletada com sucesso!")
    return redirect("lista-notas")

def editar_nota(request, id):
    nota = get_object_or_404(Nota, id=id)
    # em breve
    return redirect("lista-notas")

def cadastrar_notas(request):
    if request.method == 'POST':
        aluno_id      = request.POST.get('aluno')
        disciplina_id = request.POST.get('disciplina')
        nota_p1       = request.POST.get('nota_p1') or None
        nota_p2       = request.POST.get('nota_p2') or None
        nota_t1       = request.POST.get('nota_t1') or None
        nota_t2       = request.POST.get('nota_t2') or None

        # Calcula a média no backend (regra de negócio real)
        notas = [float(n) for n in [nota_p1, nota_p2, nota_t1, nota_t2] if n]
        media_final = round(sum(notas) / len(notas), 2) if notas else 0

        # Situação automática pela média
        if media_final >= 7:
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
            media_final=media_final,
            situacao=situacao,
        )
        messages.success(request, 'Nota cadastrada com sucesso!')
        return redirect('lista-notas')

    turmas = Turma.objects.filter(ativo=True).values('nome').annotate(id=Min('id')).distinct()
    alunos = User.objects.filter(is_staff=False) 
    disciplinas = Disciplina.objects.filter(ativo=True)

    return render(request, 'aluno/cadastrar_notas.html', {
        'turmas': turmas,
        'alunos': alunos,
        'disciplinas': disciplinas,
    })

def alunos_por_turma(request):
    turma_id = request.GET.get('turma_id')
    if not turma_id:
        return JsonResponse({'alunos': []})
    
    turma = get_object_or_404(Turma, id=turma_id)
    alunos = turma.alunos.all().values('id', 'first_name', 'username')
    return JsonResponse({'alunos': list(alunos)})
    
    