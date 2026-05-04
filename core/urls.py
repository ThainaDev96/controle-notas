"""
URL configuration for django-project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path
from aluno import views



urlpatterns = [
    path("admin/", admin.site.urls),
    path('disciplinas/', views.listar_disciplinas, name='disciplina-lista'),
    path('login/', views.login_view, name='login'),
    path("lista-notas/", views.lista_notas, name="lista-notas"),
    path("nota/editar/<int:id>/", views.editar_nota, name="editar-nota"),
    path("nota/deletar/<int:id>/", views.deletar_nota, name="deletar-nota"),
    path('notas/cadastrar/', views.cadastrar_notas, name='cadastrar-notas'), 
    path('alunos-por-turma/', views.alunos_por_turma, name='alunos-por-turma'),
    path('disciplinas-por-turma/', views.disciplinas_por_turma, name='disciplinas-por-turma'), 
    path('boletim-aluno/', views.boletim_aluno, name='minhas-notas')
]
