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
    path("logout/", views.logout_view, name="logout"),
    path("lista-notas/", views.lista_notas, name="lista-notas"),
    path("nota/editar/<int:id>/", views.editar_nota, name="editar-nota"),
    path("nota/deletar/<int:id>/", views.deletar_nota, name="deletar-nota"),
    path('notas/cadastrar/', views.cadastrar_notas, name='cadastrar-notas'), 
    path('alunos-por-turma/', views.alunos_por_turma, name='alunos-por-turma'),
    path('disciplinas-por-turma/', views.disciplinas_por_turma, name='disciplinas-por-turma'), 
    path('boletim-aluno/', views.boletim_aluno, name='minhas-notas'),
    path('relatorio/', views.gerar_relatorio, name='gerar-relatorio'),
    path('gestao/', views.gestao_dashboard, name='gestao-dashboard'),
    path('gestao/disciplinas/', views.gestao_disciplinas, name='gestao-disciplinas'),
    path('gestao/turmas/', views.gestao_turmas, name='gestao-turmas'),
    path('gestao/matriculas/', views.gestao_matriculas, name='gestao-matriculas'),
    path('notas/editar-ajax/', views.editar_nota_ajax, name='editar-nota-ajax'),
    path("disciplina/editar/<int:id>/", views.editar_disciplina, name="editar-disciplina"),
    path("disciplina/cadastrar/", views.cadastrar_disciplina, name="cadastrar-disciplina"),
    path("disciplina/deletar/<int:id>/", views.deletar_disciplina, name="deletar-disciplina"),
    path("turma/cadastrar/", views.cadastrar_turma, name="cadastrar-turma"),
    path("turma/editar/<int:id>/", views.editar_turma, name="editar-turma"),
    path("turma/deletar/<int:id>/", views.deletar_turma, name="deletar-turma"),
    path("matricula/cadastrar/", views.cadastrar_matricula, name="cadastrar-matricula"),
    path("matricula/editar/<int:id>/", views.editar_matricula, name="editar-matricula"),
    path("matricula/deletar/<int:id>/", views.deletar_matricula, name="deletar-matricula"),
    path("gestao/usuarios/", views.gestao_usuarios, name="gestao-usuarios"),
    path("usuario/cadastrar/", views.cadastrar_usuario, name="cadastrar-usuario"),
    path("usuario/editar/<int:id>/", views.editar_usuario, name="editar-usuario"),
    path("usuario/deletar/<int:id>/", views.deletar_usuario, name="deletar-usuario"),
    path("professor/configurar_avaliacoes/", views.configurar_avaliacoes, name="configurar-avaliacoes"),
    path("avaliacao/cadastrar/", views.cadastrar_avaliacao, name="cadastrar-avaliacao"),
    path("avaliacao/editar/<int:id>/", views.editar_avaliacao, name="editar-avaliacao"),
    path("avaliacao/deletar/<int:id>/", views.deletar_avaliacao, name="deletar-avaliacao"),
    path("avaliacao/cadastrar-ajax/", views.cadastrar_avaliacao_ajax, name="cadastrar-avaliacao-ajax"),
    path("avaliacao/editar-ajax/", views.editar_avaliacao_ajax, name="editar-avaliacao-ajax"),
]
