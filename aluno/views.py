from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.shortcuts import render, redirect
from aluno.models import Disciplina

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("home")

        messages.error(request, "Usuário ou senha inválidos")

    return render(request, "aluno/login.html")


def listar_disciplinas(request):
    disciplinas = Disciplina.objects.filter(ativo=True)
    return render(request, 'aluno/lista.html', {'disciplinas': disciplinas})
